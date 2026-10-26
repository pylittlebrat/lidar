"""Dashboard 数据层（Phase 7）。

不依赖 streamlit，便于单元测试；app.py 只做渲染。
"""

from __future__ import annotations

from datetime import timedelta
import json
from typing import Any

from techradar.analyzers.trends import TrendDetector
from techradar.collectors.registry import collector_availability
from techradar.config import Config
from techradar.storage.database import Database
from techradar.storage.repository import AnalysisRepository, ItemRepository
from techradar.utils.time import utcnow

TREND_GROUPS = (("keywords", "关键词"), ("categories", "分类"), ("sources", "来源"))

ANALYSIS_LABELS = {
    "llm": "模型分析", "skipped": "规则评分", "llm_failed": "失败待重试",
    "unknown": "历史未标记", "pending": "待分析",
}


def source_health(config: Config, latest_run: dict[str, Any]) -> list[dict[str, Any]]:
    """Combine configured capabilities with the most recent observed source result."""
    rows = []
    for source, capability in collector_availability(config).items():
        result = latest_run.get("collect", {}).get(source, {})
        status = capability["status"]
        if status == "available":
            status = result.get("status") or "unknown"
        label = {"disabled": "已停用", "unavailable": "未接入", "success": "正常", "partial": "部分失败", "failed": "失败", "unknown": "待检测"}.get(status, "待检测")
        rows.append({"source": source, "status": status, "label": label,
                     "attempted": result.get("attempted"), "succeeded": result.get("succeeded"),
                     "unsupported_events": capability.get("unsupported_events", [])})
    return rows


def analysis_status(entry: dict[str, Any]) -> str:
    if entry.get("analysis_id") is None:
        return "pending"
    return entry.get("llm_status") or "unknown"


def browse(db: Database, *, query: str = "", source: str = "", status: str = "",
           category: str = "", days: int = 0, focus: int = 75,
           order: str = "recent", page: int = 1, page_size: int = 12,
           group: bool = True, min_score: int = 40) -> dict[str, Any]:
    """Filter the complete library before pagination, including unanalysed items.

    默认把大型项目（同一 repo 的多个版本 / release）聚合为一条项目卡片，
    避免 Newton / IsaacLab 等版本序列在情报库中散落。

    min_score：默认隐藏综合评分低于 40 的已分析条目；传入 0 可看全部。
    待分析与失败待重试始终保留，方便跟进处理。
    """
    conditions, params = [], []
    if query.strip():
        conditions.append("(instr(lower(i.title || ' ' || i.abstract || ' ' || i.raw_text || ' ' || coalesce(a.summary, '')), lower(?)) > 0)")
        params.append(query.strip())
    if source:
        conditions.append("i.source_type = ?")
        params.append(source)
    if category:
        conditions.append("EXISTS (SELECT 1 FROM json_each(coalesce(a.categories, '[]')) WHERE value = ?)")
        params.append(category)
    if days:
        conditions.append("i.collected_at >= ?")
        params.append((utcnow() - timedelta(days=days)).isoformat())
    if status == "focus":
        conditions.append("a.score >= ? AND a.llm_status NOT IN ('pending', 'llm_failed')")
        params.append(focus)
    elif status == "pending":
        conditions.append("(a.item_id IS NULL OR a.llm_status = 'pending')")
    elif status == "analyzed":
        conditions.append("a.item_id IS NOT NULL AND a.llm_status NOT IN ('pending', 'llm_failed')")
    elif status in ("llm", "skipped", "llm_failed"):
        conditions.append("a.llm_status = ?")
        params.append(status)
    elif status == "unknown":
        conditions.append("a.item_id IS NOT NULL AND coalesce(a.llm_status, '') = ''")
    join = " FROM items i LEFT JOIN analysis a ON a.item_id = i.id"
    if min_score and min_score > 0:
        conditions.append("(a.item_id IS NULL OR a.llm_status IN ('pending', 'llm_failed') OR a.score IS NULL OR a.score >= ?)")
        params.append(min_score)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    repo_sql = "CASE WHEN json_valid(i.metadata) THEN coalesce(json_extract(i.metadata, '$.repo'), '') ELSE '' END"
    entity_sql = f"CASE WHEN ({repo_sql}) <> '' THEN 'repo:' || lower({repo_sql}) ELSE 'item:' || i.id END" if group else "'item:' || i.id"
    cte = (
        "WITH filtered AS (SELECT i.id, i.title, i.url, i.source_type, i.source_name, i.abstract,"
        " i.published_at, i.collected_at, i.updated_at AS content_updated_at, i.metadata, a.item_id AS analysis_id, a.summary, a.score, a.categories,"
        " a.recommended_action, a.problem, a.solution, a.innovation, a.maturity,"
        " a.platform_relevance, a.comparison, a.llm_status, a.breakdown, a.attempts,"
        " a.last_error, a.next_retry_at, a.model, a.prompt_version, a.updated_at AS analysis_updated_at,"
        f" {repo_sql} AS repo, {entity_sql} AS entity_key" + join + where + "),"
        " ranked AS (SELECT *, row_number() OVER (PARTITION BY entity_key"
        " ORDER BY score DESC, collected_at DESC, id) AS representative_rank,"
        " count(*) OVER (PARTITION BY entity_key) AS version_count,"
        " max(collected_at) OVER (PARTITION BY entity_key) AS _latest_collected,"
        " first_value(title) OVER (PARTITION BY entity_key ORDER BY collected_at DESC, id) AS _latest_title,"
        " first_value(url) OVER (PARTITION BY entity_key ORDER BY collected_at DESC, id) AS _latest_url"
        " FROM filtered) "
    )
    sort = "score DESC, collected_at DESC, id" if order == "score" else "_latest_collected DESC, id"
    conn = db.connect()
    try:
        conn.execute("BEGIN")  # count, page and enrichment share a single read snapshot
        total = conn.execute(cte + "SELECT count(DISTINCT entity_key) FROM filtered", params).fetchone()[0]
        page_size = max(1, min(int(page_size), 100))
        pages = max(1, (total + page_size - 1) // page_size)
        page = max(1, min(int(page), pages))
        rows = conn.execute(
            cte + "SELECT * FROM ranked WHERE representative_rank = 1 ORDER BY " + sort + " LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        ids = [r["id"] for r in rows]
        relevance: dict[str, dict[str, float]] = {}
        versions: dict[str, list[dict[str, Any]]] = {}
        if ids:
            placeholders = ",".join("?" for _ in ids)
            for r in conn.execute(
                f"SELECT item_id, project_key, score FROM relevance WHERE item_id IN ({placeholders}) ORDER BY score DESC", ids
            ):
                relevance.setdefault(r["item_id"], {})[r["project_key"]] = r["score"]
            if group:
                keys = [r["entity_key"] for r in rows]
                for version in conn.execute(
                    cte + ", version_rows AS (SELECT entity_key, title, url, collected_at,"
                    " row_number() OVER (PARTITION BY entity_key ORDER BY collected_at DESC, id) AS version_rank"
                    f" FROM filtered WHERE entity_key IN ({placeholders}))"
                    " SELECT entity_key, title, url, collected_at FROM version_rows WHERE version_rank <= 5"
                    " ORDER BY entity_key, collected_at DESC", [*params, *keys]
                ):
                    value = dict(version)
                    versions.setdefault(value.pop("entity_key"), []).append(value)
        entries = []
        for row in rows:
            entry = dict(row)
            entry["categories"] = _json_value(entry["categories"], [])
            entry["innovation"] = _json_value(entry["innovation"], [])
            entry["breakdown"] = _json_value(entry["breakdown"], {})
            entry["relevance"] = relevance.get(entry["id"], {})
            entry["_collected"] = entry.get("collected_at") or ""
            entry["grouped"] = bool(group and entry["repo"])
            entry["versions"] = versions.get(entry["entity_key"], [])
            entry["analysis_status"] = analysis_status(entry)
            entries.append(entry)
        return {"entries": entries, "total": total, "page": page, "pages": pages}
    finally:
        conn.close()


def _json_value(raw: Any, fallback: Any) -> Any:
    try:
        value = json.loads(raw or "null")
        return value if isinstance(value, type(fallback)) else fallback
    except (TypeError, ValueError):
        return fallback


def library_stats(db: Database, focus: int = 75) -> dict[str, Any]:
    """Exact totals for the rolling 24-hour window and the whole library."""
    since = (utcnow() - timedelta(days=1)).isoformat()
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT count(*) AS total, coalesce(sum(i.collected_at >= ?), 0) AS recent,"
            " coalesce(sum(a.item_id IS NOT NULL AND a.llm_status NOT IN ('pending', 'llm_failed')), 0) AS analyzed,"
            " coalesce(sum(a.item_id IS NULL OR a.llm_status = 'pending'), 0) AS pending,"
            " coalesce(sum(a.llm_status = 'llm'), 0) AS llm,"
            " coalesce(sum(a.llm_status = 'skipped'), 0) AS skipped,"
            " coalesce(sum(a.llm_status = 'llm_failed'), 0) AS llm_failed,"
            " coalesce(sum(a.item_id IS NOT NULL AND coalesce(a.llm_status, '') = ''), 0) AS unknown,"
            " coalesce(sum(a.score >= ? AND a.llm_status NOT IN ('pending', 'llm_failed')), 0) AS high, max(i.collected_at) AS latest,"
            " min(i.collected_at) AS earliest FROM items i LEFT JOIN analysis a ON a.item_id = i.id",
            (since, focus),
        ).fetchone()
        result = dict(row)
        result["sources"] = dict(conn.execute("SELECT source_type, count(*) FROM items GROUP BY source_type").fetchall())
        result["categories"] = [r[0] for r in conn.execute(
            "SELECT DISTINCT value FROM analysis, json_each(analysis.categories) ORDER BY value"
        ).fetchall()]
        return result
    finally:
        conn.close()


def _entry_dict(entry: dict[str, Any]) -> dict[str, Any]:
    item = entry["item"]
    analysis = entry["analysis"]
    return {
        "title": item.title,
        "url": item.url,
        "source_type": item.source_type,
        "source_name": item.source_name,
        "published_at": (item.published_at or item.collected_at).isoformat()
        if (item.published_at or item.collected_at) else "",
        "score": analysis.get("score"),
        "categories": ", ".join(analysis.get("categories", [])),
        "recommended_action": analysis.get("recommended_action", ""),
        "summary": (analysis.get("summary") or "")[:120],
    }


def build_overview(config: Config, db: Database) -> dict[str, Any]:
    """今日概览数据。"""
    since = (utcnow() - timedelta(days=1)).isoformat()
    irepo = ItemRepository(db)
    arepo = AnalysisRepository(db)
    entries = arepo.scored_items(since=since, limit=100)
    focus = int(config.scoring.get("bands", {}).get("focus", 75))
    return {
        "collected": irepo.count_since(since=since),
        "by_source": irepo.count_by_source(since=since),
        "analyzed": sum(1 for e in entries if e["analysis"].get("score") is not None),
        "high": sum(1 for e in entries if (e["analysis"].get("score") or 0) >= focus),
        "top_items": [_entry_dict(e) for e in entries[:5]],
    }


def build_latest(config: Config, db: Database, limit: int = 20) -> list[dict[str, Any]]:
    """最近条目（已分析带评分；未分析的附在末尾）。"""
    since = (utcnow() - timedelta(days=30)).isoformat()
    arepo = AnalysisRepository(db)
    irepo = ItemRepository(db)
    entries = [_entry_dict(e) for e in arepo.scored_items(since=since, limit=limit)]
    scored_ids = {e["url"] for e in entries}
    for item in irepo.recent(limit=limit):
        if item.url not in scored_ids:
            entries.append(_entry_dict({"item": item, "analysis": {"score": None}}))
    return entries


def build_papers(config: Config, db: Database, limit: int = 50) -> list[dict[str, Any]]:
    return [
        e for e in _scored(config, db, limit)
        if e["source_type"] == "arxiv"
    ]


def build_graph(config: Config, db: Database, **kwargs) -> dict[str, Any]:
    """知识图谱数据（节点/边/统计）。参数见 techradar.knowledge.build_graph。"""
    from techradar.knowledge import build_graph as _build

    return _build(config, db, **kwargs)


def graph_entity_options(config: Config, db: Database, limit: int = 400) -> list[dict[str, str]]:
    """可选的中心实体（分类 / 仓库 / 内部项目 / 来源），用于邻域探索。"""
    graph = build_graph(config, db, days=365, min_score=0, limit=limit, include_keywords=False)
    options = [
        {"id": n["id"], "label": f"{KIND_LABELS.get(n['kind'], n['kind'])}：{n['label']}"}
        for n in graph["nodes"]
        if n["kind"] in ("category", "repo", "project", "source")
    ]
    options.sort(key=lambda o: o["label"])
    # 内部项目与分类优先展示
    priority = ("project", "category")
    options.sort(key=lambda o: 0 if any(p in o["id"] for p in priority) else 1)
    return options


KIND_LABELS = {
    "item": "情报",
    "repo": "仓库",
    "category": "方向",
    "keyword": "关键词",
    "project": "内部项目",
    "source": "来源",
}


def build_github_new(config: Config, db: Database, limit: int = 50) -> list[dict[str, Any]]:
    out = []
    for entry in _raw_scored(config, db, limit):
        if entry["item"].source_type == "github" and entry["item"].metadata.get("watched") is not True:
            out.append(_entry_dict(entry))
    return out


def build_trend_table(config: Config, db: Database, limit: int = 15) -> list[dict[str, Any]]:
    """趋势表格行（含升序变化）。"""
    trends = TrendDetector(config, db).trends(days=90, limit=limit)
    rows: list[dict[str, Any]] = []
    for group_key, group_label in TREND_GROUPS:
        for t in trends.get(group_key, []):
            rows.append({
                "group": group_label,
                "name": t["name"],
                "direction": t["direction"],
                "7d": t["counts"]["7d"],
                "30d": t["counts"]["30d"],
                "90d": t["counts"]["90d"],
                "change_pct": t["change_pct"],
                "observed_days": t.get("observed_days"),
                "previous_count": t.get("previous_count", 0),
            })
    return rows


def build_radar(db: Database) -> list[dict[str, Any]]:
    """技术雷达状态表（radar 表全量，按最近更新倒序）。"""
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT tech_name, status, suggested_status, decision_status, decision_reason, decision_at, reason, action, owner, first_seen, updated_at"
            " FROM radar ORDER BY updated_at DESC, tech_name"
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def search(config: Config, db: Database, query: str, limit: int = 20) -> list[dict[str, Any]]:
    """检索（命中项附带评分，如已分析）。"""
    items = ItemRepository(db).search(query, limit=limit)
    conn = db.connect()
    try:
        placeholders = ",".join("?" * len(items))
        rows = (
            conn.execute(
                f"SELECT item_id, score FROM analysis WHERE item_id IN ({placeholders})",
                [i.id for i in items],
            ).fetchall()
            if items else []
        )
    finally:
        conn.close()
    scores = {row["item_id"]: row["score"] for row in rows}
    return [
        {
            "title": item.title,
            "url": item.url,
            "source_type": item.source_type,
            "source_name": item.source_name,
            "published_at": (item.published_at or "").isoformat() if item.published_at else "",
            "score": scores.get(item.id),
            "abstract": (item.abstract or "")[:120],
        }
        for item in items
    ]


# ---------------- internal ----------------
def _scored(config: Config, db: Database, limit: int) -> list[dict[str, Any]]:
    return [_entry_dict(e) for e in _raw_scored(config, db, limit)]


def _raw_scored(config: Config, db: Database, limit: int):
    since = (utcnow() - timedelta(days=90)).isoformat()
    return AnalysisRepository(db).scored_items(since=since, limit=limit)
