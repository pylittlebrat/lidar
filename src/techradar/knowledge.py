"""知识图谱（Knowledge Graph）：从情报库抽取实体与关系，构建技术知识网络。

设计目标：把"散落的条目"变成"可探索的技术网络"——看到某项技术属于哪些方向、
被哪些内部项目关注、与哪些论文/仓库同源共现。

实体（node）：
- item      情报条目（技术项 / 论文 / 仓库）
- repo      GitHub 仓库（大型项目聚合节点）
- category  技术分类（taxonomy）
- keyword   LLM 抽取的关键词
- project   内部项目（FunLab / Real2Sim / ...）
- source    信息来源（github / arxiv / rss）

关系（edge）：
- item  belongs_to  category
- item  mentions    keyword
- item  relates_to  project（relevance >= 阈值）
- item  from        source
- item  implements  repo（github 条目归属仓库）
- keyword co_occurs  keyword（同一批文档共现，权重=共现次数）

实现要点：纯 SQLite + 内存聚合，不依赖图数据库；返回可直接喂给
pyvis / networkx 的 {nodes, edges} 结构。
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import timedelta
from typing import Any

from techradar.config import Config
from techradar.storage.database import Database
from techradar.utils.time import utcnow

# 内部项目显示名（缺失时回退到原始 key）
PROJECT_LABELS = {
    "funlab": "FunLab",
    "real2sim": "Real2Sim",
    "simulation_platform": "仿真平台",
    "engineering_machinery": "工程机械",
    "robot_learning": "机器人学习",
}

SOURCE_LABELS = {
    "github": "GitHub",
    "arxiv": "arXiv",
    "rss": "行业动态",
    "document": "文档资料",
    "web": "网页",
}

# 分类显示名（中文），节点统一使用配置 ID 作为身份，避免同一方向出现多个节点
CATEGORY_LABELS = {
    "simulation_engine": "仿真引擎",
    "robot_learning": "机器人学习",
    "real2sim": "Real2Sim",
    "sim2real": "Sim2Real",
    "embodied_ai": "具身智能",
    "system_identification": "系统辨识",
    "asset_generation": "资产生成",
    "world_model": "世界模型",
    "humanoid": "人形机器人",
    "manipulation": "操作与抓取",
    "engineering_machinery": "工程机械",
    "simulation_validation": "仿真验证",
}


def _category_id(value: str, valid: set[str]) -> str | None:
    """把 LLM 返回的分类名归一到配置 ID（兼容中文显示名与大小写/空格差异）。"""
    if not value:
        return None
    key = value.strip()
    if key in valid:
        return key
    lowered = re.sub(r"[\s\-]+", "_", key.lower())
    if lowered in valid:
        return lowered
    # 反向匹配中文显示名
    for cid, label in CATEGORY_LABELS.items():
        if label.lower() == key.lower() and cid in valid:
            return cid
    return None

NODE_STYLE: dict[str, dict[str, Any]] = {
    "item": {"color": "#5b8ff9", "shape": "dot", "size": 12},
    "repo": {"color": "#f6bd16", "shape": "square", "size": 20},
    "category": {"color": "#5ad8a6", "shape": "hexagon", "size": 24},
    "keyword": {"color": "#e8684a", "shape": "dot", "size": 10},
    "project": {"color": "#9270ca", "shape": "star", "size": 28},
    "source": {"color": "#6dc8ec", "shape": "triangle", "size": 18},
}


def _load_items(db: Database, days: int | None, min_score: float, limit: int | None,
                *, include_unanalyzed: bool = False) -> list[dict[str, Any]]:
    """读取用于建图的条目（含分析字段与相关度）。"""
    conditions = [] if include_unanalyzed else ["a.item_id IS NOT NULL"]
    params: list[Any] = []
    if days:
        conditions.append("i.collected_at >= ?")
        params.append((utcnow() - timedelta(days=days)).isoformat())
    if min_score > 0:
        conditions.append("coalesce(a.score, 0) >= ?")
        params.append(min_score)
    sql = (
        "SELECT i.id, i.title, i.url, i.source_type, i.source_name, i.metadata,"
        " a.score, a.summary, a.categories, a.keywords, a.related_projects"
        " FROM items i LEFT JOIN analysis a ON a.item_id = i.id"
    )
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY coalesce(a.score, 0) DESC, i.collected_at DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(max(1, int(limit)))
    conn = db.connect()
    try:
        rows = conn.execute(sql, params).fetchall()
        entries = []
        for row in rows:
            record = dict(row)
            record["categories"] = _safe_list(record.get("categories"))
            record["keywords"] = _safe_list(record.get("keywords"))
            record["related_projects"] = _safe_list(record.get("related_projects"))
            record["repo"] = _repo_of(record.get("metadata"))
            record["relevance"] = {}
            entries.append(record)
        # 相关度（仅取 >=3，避免噪声边）
        if entries:
            ids = [e["id"] for e in entries]
            by_id = {entry["id"]: entry for entry in entries}
            # 全库模式不受 SQLite 单条语句参数上限影响。
            for offset in range(0, len(ids), 900):
                batch = ids[offset:offset + 900]
                placeholders = ",".join("?" * len(batch))
                for rrow in conn.execute(
                    f"SELECT item_id, project_key, score FROM relevance"
                    f" WHERE item_id IN ({placeholders}) AND score >= 3", batch,
                ).fetchall():
                    by_id[rrow["item_id"]]["relevance"][rrow["project_key"]] = rrow["score"]
        return entries
    finally:
        conn.close()


def _safe_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    try:
        parsed = json.loads(value or "[]")
        return [str(v) for v in parsed] if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def _repo_of(metadata: Any) -> str:
    try:
        meta = json.loads(metadata or "{}") if isinstance(metadata, str) else (metadata or {})
    except ValueError:
        meta = {}
    return str(meta.get("repo") or "")


def build_graph(
    config: Config,
    db: Database,
    *,
    days: int | None = 180,
    min_score: float = 50,
    limit: int | None = 300,
    keyword_limit: int = 40,
    cooccurrence_limit: int = 60,
    include_keywords: bool = True,
    include_unanalyzed: bool = False,
) -> dict[str, Any]:
    """构建知识图谱。

    :param days: lookback 窗口（None = 全库）
    :param min_score: 只纳入相关度 >= min_score 的条目
    :param limit: 最多纳入的条目数，None 表示不截断
    :param include_unanalyzed: 包含尚未分析的条目，供全库星图展示
    :param keyword_limit: 保留出现次数最多的前 N 个关键词节点
    :param cooccurrence_limit: 关键词共现边上限
    :return: {"nodes": [...], "edges": [...], "stats": {...}}
    """
    entries = _load_items(db, days, min_score, limit, include_unanalyzed=include_unanalyzed)
    valid_categories = {name for name, _terms in config.categories()}
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str], dict[str, Any]] = {}

    def add_node(node_id: str, label: str, kind: str, **extra: Any) -> None:
        if node_id not in nodes:
            style = NODE_STYLE.get(kind, NODE_STYLE["item"])
            nodes[node_id] = {
                "id": node_id,
                "label": label,
                "kind": kind,
                **style,
                **extra,
            }

    def add_edge(source: str, target: str, relation: str, weight: float = 1.0) -> None:
        key = (source, target, relation)
        if key in edges:
            edges[key]["weight"] += weight
            edges[key]["value"] = edges[key]["weight"]
        else:
            edges[key] = {
                "from": source, "to": target, "relation": relation,
                "weight": weight, "value": weight,
            }

    keyword_counter: Counter[str] = Counter()
    keyword_of_item: dict[str, list[str]] = {}

    # 分类显示名
    category_labels = {name: name for name, _terms in config.categories()}

    for entry in entries:
        item_id = f"item:{entry['id']}"
        title = entry["title"] or ""
        score = entry.get("score")
        add_node(item_id, title, "item", score=score, url=entry.get("url", ""),
                 source_type=entry.get("source_type", ""), summary=entry.get("summary") or "")

        # 来源
        source_type = entry.get("source_type") or "other"
        source_id = f"source:{source_type}"
        add_node(source_id, SOURCE_LABELS.get(source_type, source_type), "source")
        add_edge(item_id, source_id, "from")

        # 仓库（GitHub 聚合）
        repo = entry.get("repo")
        if repo:
            repo_id = f"repo:{repo}"
            add_node(repo_id, repo, "repo")
            add_edge(item_id, repo_id, "implements")

        # 分类（归一到配置 ID，避免中英文重复节点）
        for category in entry["categories"]:
            cid_key = _category_id(category, valid_categories)
            if cid_key is None:
                continue
            cid = f"category:{cid_key}"
            add_node(cid, CATEGORY_LABELS.get(cid_key, category_labels.get(cid_key, cid_key)),
                     "category")
            add_edge(item_id, cid, "belongs_to")

        # 内部项目（relevance >= 3）
        for project, value in entry["relevance"].items():
            pid = f"project:{project}"
            add_node(pid, PROJECT_LABELS.get(project, project), "project")
            add_edge(item_id, pid, "relates_to", weight=float(value) / 5.0)

        # 关键词（先累计词频，稍后只保留 top-N）
        if include_keywords:
            kws = [k.strip() for k in entry["keywords"] if k.strip()][:8]
            keyword_of_item[item_id] = kws
            for kw in kws:
                keyword_counter[kw] += 1

    # 关键词节点 + 条目→关键词边
    top_keywords = {kw for kw, _ in keyword_counter.most_common(keyword_limit)}
    for item_id, kws in keyword_of_item.items():
        for kw in kws:
            if kw not in top_keywords:
                continue
            kid = f"keyword:{kw}"
            add_node(kid, kw, "keyword", count=keyword_counter[kw])
            add_edge(item_id, kid, "mentions")

    # 关键词共现（同一条目内）
    cooc: Counter[tuple[str, str]] = Counter()
    for kws in keyword_of_item.values():
        kept = sorted({k for k in kws if k in top_keywords})
        for i, a in enumerate(kept):
            for b in kept[i + 1:]:
                cooc[(a, b)] += 1
    for (a, b), count in cooc.most_common(cooccurrence_limit):
        if count < 2:
            break
        add_edge(f"keyword:{a}", f"keyword:{b}", "co_occurs", weight=float(count))

    stats = {
        "items": len(entries),
        "nodes": len(nodes),
        "edges": len(edges),
        "by_kind": dict(Counter(n["kind"] for n in nodes.values())),
        "top_keywords": keyword_counter.most_common(20),
        "top_projects": Counter(
            p for e in entries for p in e["relevance"]
        ).most_common(),
        "top_repos": Counter(
            e["repo"] for e in entries if e["repo"]
        ).most_common(10),
    }
    return {"nodes": list(nodes.values()), "edges": list(edges.values()), "stats": stats}


def build_cooccurrence_graph(
    config: Config,
    db: Database,
    *,
    days: int | None = 365,
    min_score: float = 50,
    limit: int = 400,
    min_cooccur: int = 3,
    keyword_limit: int = 30,
    include_keywords: bool = True,
) -> dict[str, Any]:
    """骨架图：只保留"方向 / 仓库 / 内部项目 / 关键词"节点。

    节点间的边来自共现——若多个实体共同出现在同一批情报中，则彼此相连，
    权重 = 共现情报数。这样能一眼看清"哪些方向/项目/仓库彼此关联"，
    而不被上百个情报节点淹没。
    """
    entries = _load_items(db, days, min_score, limit)
    valid_categories = {name for name, _terms in config.categories()}
    entities_of_item: list[set[str]] = []
    labels: dict[str, tuple[str, str]] = {}          # id -> (label, kind)
    counts: Counter[str] = Counter()

    for entry in entries:
        current: set[str] = set()
        for category in entry["categories"]:
            cid_key = _category_id(category, valid_categories)
            if cid_key is None:
                continue
            node_id = f"category:{cid_key}"
            labels.setdefault(node_id, (CATEGORY_LABELS.get(cid_key, cid_key), "category"))
            current.add(node_id)
        for project in entry["relevance"]:
            node_id = f"project:{project}"
            labels.setdefault(node_id, (PROJECT_LABELS.get(project, project), "project"))
            current.add(node_id)
        repo = entry.get("repo")
        if repo:
            node_id = f"repo:{repo}"
            labels.setdefault(node_id, (repo, "repo"))
            current.add(node_id)
        if include_keywords:
            for keyword in [k.strip() for k in entry["keywords"] if k.strip()][:4]:
                node_id = f"keyword:{keyword}"
                labels.setdefault(node_id, (keyword, "keyword"))
                current.add(node_id)
        for node_id in current:
            counts[node_id] += 1
        entities_of_item.append(current)

    # 关键词只保留高频 Top-N，避免长尾污染骨架
    keyword_keep = {
        node_id for node_id in labels
        if labels[node_id][1] != "keyword"
    } | {
        node_id for node_id, _ in counts.most_common()
        if labels[node_id][1] == "keyword"
    }
    if include_keywords:
        top_keyword_ids = {
            node_id for node_id, count in counts.most_common()
            if labels[node_id][1] == "keyword"
        }
        ordered_keywords = sorted(
            top_keyword_ids, key=lambda nid: -counts[nid]
        )[:keyword_limit]
        keyword_keep = top_keyword_ids & set(ordered_keywords)
    keep = {
        node_id for node_id in labels
        if labels[node_id][1] != "keyword" or node_id in keyword_keep
    }

    cooc: Counter[tuple[str, str]] = Counter()
    for current in entities_of_item:
        kept = sorted(current & keep)
        for i, a in enumerate(kept):
            for b in kept[i + 1:]:
                cooc[(a, b)] += 1

    nodes: dict[str, dict[str, Any]] = {}
    for node_id, (label, kind) in labels.items():
        if node_id not in keep:
            continue
        style = NODE_STYLE.get(kind, NODE_STYLE["item"])
        nodes[node_id] = {
            "id": node_id, "label": label, "kind": kind,
            "count": counts[node_id], **style,
        }

    edges: list[dict[str, Any]] = []
    for (a, b), count in cooc.most_common():
        if count < min_cooccur:
            continue
        edges.append({
            "from": a, "to": b, "relation": "co_occurs",
            "weight": float(count), "value": float(count),
        })

    stats = {
        "items": len(entries),
        "nodes": len(nodes),
        "edges": len(edges),
        "by_kind": dict(Counter(n["kind"] for n in nodes.values())),
        "top_keywords": [],
        "top_projects": counts.most_common() and [
            (k.split(":", 1)[1], v) for k, v in counts.most_common()
            if k.startswith("project:")
        ],
        "top_repos": [
            (k.split(":", 1)[1], v) for k, v in counts.most_common()
            if k.startswith("repo:")
        ][:10],
    }
    return {"nodes": list(nodes.values()), "edges": edges, "stats": stats}


def build_neighborhood(
    config: Config,
    db: Database,
    entity_id: str,
    *,
    days: int | None = 365,
    limit: int = 400,
) -> dict[str, Any]:
    """以某个实体为中心抽取一跳邻域（用于"探索某项技术"）。"""
    graph = build_graph(config, db, days=days, min_score=0, limit=limit,
                        keyword_limit=60, cooccurrence_limit=80)
    keep_ids: set[str] = {entity_id}
    for edge in graph["edges"]:
        if edge["from"] == entity_id:
            keep_ids.add(edge["to"])
        elif edge["to"] == entity_id:
            keep_ids.add(edge["from"])
    nodes = [n for n in graph["nodes"] if n["id"] in keep_ids]
    edges = [e for e in graph["edges"] if e["from"] in keep_ids and e["to"] in keep_ids]
    return {"nodes": nodes, "edges": edges, "stats": graph["stats"], "center": entity_id}
