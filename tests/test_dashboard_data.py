"""Phase 7 Dashboard 数据层测试（不依赖 streamlit）。"""

from __future__ import annotations

from techradar.dashboard import data as ddata
from techradar.storage.models import TechItem
from techradar.storage.repository import AnalysisRepository, ItemRepository


def _seed(database) -> None:
    irepo = ItemRepository(database)
    paper = TechItem(
        source_type="arxiv", source_name="arXiv", title="GPU Physics Simulation Paper",
        url="https://arxiv.org/abs/1", abstract="physics engine real2sim",
    )
    repo = TechItem(
        source_type="github", source_name="realsim", title="A new real2sim toolkit",
        url="https://github.com/acme/realsim", abstract="real2sim",
    )
    watched_release = TechItem(
        source_type="github", source_name="IsaacLab", title="Isaac Lab v2.0 Release",
        url="https://github.com/isaac-sim/IsaacLab/releases/tag/v2.0",
        abstract="physics engine", metadata={"watched": True},
    )
    for item in (paper, repo, watched_release):
        irepo.insert(item)
    arepo = AnalysisRepository(database)
    arepo.upsert_analysis(paper.id, {"summary": "s1", "categories": ["simulation_engine"],
                                     "score": 88, "recommended_action": "INVESTIGATE"})
    arepo.upsert_analysis(repo.id, {"summary": "s2", "categories": ["real2sim"],
                                    "score": 72, "recommended_action": "POC"})


def test_build_overview(config, database) -> None:
    _seed(database)
    overview = ddata.build_overview(config, database)
    assert overview["collected"] == 3
    assert overview["analyzed"] >= 2
    assert overview["top_items"][0]["score"] == 88


def test_build_latest_includes_unanalyzed(config, database) -> None:
    _seed(database)
    latest = ddata.build_latest(config, database, limit=10)
    # watched release 未分析，也应出现在列表里
    titles = {e["title"] for e in latest}
    assert "Isaac Lab v2.0 Release" in titles


def test_build_papers_and_github_new(config, database) -> None:
    _seed(database)
    papers = ddata.build_papers(config, database)
    assert [p["title"] for p in papers] == ["GPU Physics Simulation Paper"]
    github_new = ddata.build_github_new(config, database)
    assert [g["title"] for g in github_new] == ["A new real2sim toolkit"]


def test_build_trend_table_and_search(config, database) -> None:
    _seed(database)
    rows = ddata.build_trend_table(config, database)
    assert rows and all(r["direction"] in ("up", "down", "flat", "new", "insufficient") for r in rows)
    hits = ddata.search(config, database, "real2sim")
    assert hits and hits[0]["title"] == "A new real2sim toolkit"
    scored = {h["title"]: h["score"] for h in hits}
    assert scored["A new real2sim toolkit"] == 72.0


def test_library_filters_before_pagination(database):
    _seed(database)
    result = ddata.browse(database, source="arxiv", page_size=1)
    assert result["total"] == 1
    assert result["entries"][0]["title"] == "GPU Physics Simulation Paper"
    pending = ddata.browse(database, status="pending")
    assert pending["total"] == 1
    assert pending["entries"][0]["analysis_id"] is None
    assert ddata.browse(database, status="focus")["total"] == 1
    assert ddata.browse(database, query="REAL2SIM")["total"] == 2
    assert ddata.browse(database, query="%'")["total"] == 0
    assert ddata.browse(database, category="simulation_engine")["total"] == 1
    first = ddata.browse(database, order="score", page_size=1)
    second = ddata.browse(database, order="score", page_size=1, page=2)
    assert first["entries"][0]["score"] == 88
    assert second["entries"][0]["score"] == 72
    assert ddata.browse(database, page_size=1, page=999)["page"] == 3


def test_library_stats_empty_and_populated(database):
    assert ddata.library_stats(database)["total"] == 0
    assert ddata.browse(database)["pages"] == 1
    _seed(database)
    stats = ddata.library_stats(database)
    assert (stats["total"], stats["analyzed"], stats["pending"], stats["high"]) == (3, 2, 1, 1)
    assert stats["sources"] == {"arxiv": 1, "github": 2}


def test_browse_groups_same_repo_versions(database) -> None:
    """同一 repo 的多个版本在 browse 中合并且展开，不再散落。"""
    irepo = ItemRepository(database)
    found = []
    for tag in ("v1.4.0", "v1.5.0", "v1.6.0"):
        found.append(TechItem(
            source_type="github", source_name="newton",
            title=f"newton-physics/newton {tag} Release",
            url=f"https://github.com/newton-physics/newton/releases/tag/{tag}",
            abstract="physics engine robot learning",
            metadata={"repo": "newton-physics/newton", "watched": True},
        ))
    for item in found:
        irepo.insert(item)
    result = ddata.browse(database, page_size=50)
    groups = [e for e in result["entries"]
              if e.get("grouped") and e.get("repo") == "newton-physics/newton"]
    assert len(groups) == 1
    assert groups[0]["version_count"] == 3
    assert len(groups[0]["versions"]) == 3
    # 关闭聚合时散开为 3 条
    raw = ddata.browse(database, page_size=50, group=False)
    newton_rows = [e for e in raw["entries"] if e.get("repo") == "newton-physics/newton"]
    assert len(newton_rows) == 3


def test_browse_min_score_hides_low_relevance(database) -> None:
    """默认隐藏已分析且相关度 <40 的条目（LLM 判无关），传 0 可看全部。"""
    _seed(database)  # paper 88 / repo 72 / watched 未分析
    low = TechItem(
        source_type="arxiv", source_name="arXiv", title="Irrelevant Game Paper",
        url="https://arxiv.org/abs/low", abstract="video game universe sandbox free setup",
    )
    ItemRepository(database).insert(low)
    AnalysisRepository(database).upsert_analysis(low.id, {
        "summary": "无关内容", "categories": [], "score": 30,
        "recommended_action": "ARCHIVE",
    })
    default = ddata.browse(database, source="arxiv")
    assert default["total"] == 1  # 88 分论文保留，30 分被隐藏
    assert default["entries"][0]["title"] == "GPU Physics Simulation Paper"
    all_rows = ddata.browse(database, source="arxiv", min_score=0)
    assert all_rows["total"] == 2
    # 未分析的条目不因 min_score 受影响
    assert ddata.browse(database, status="pending", min_score=40)["total"] == 1


def test_status_counts_and_failure_filters(database):
    for index, status in enumerate(("llm", "skipped", "llm_failed", "", "pending")):
        item = TechItem(source_type="arxiv", source_name="test", title=f"Status {index}", url=f"https://example.com/status/{index}")
        ItemRepository(database).insert(item)
        AnalysisRepository(database).upsert_analysis(item.id, {"llm_status": status, "score": 20 if status == "llm_failed" else 70})
    stats = ddata.library_stats(database)
    assert [stats[key] for key in ("llm", "skipped", "llm_failed", "unknown", "pending")] == [1, 1, 1, 1, 1]
    for status in ("llm", "skipped", "llm_failed", "unknown", "pending"):
        assert ddata.browse(database, status=status)["total"] == 1
    assert ddata.browse(database, status="analyzed")["total"] == 3


def test_pagination_counts_library_over_ten_thousand(database):
    conn = database.connect()
    try:
        with conn:
            conn.executemany(
                "INSERT INTO items(id,title,url,source_type,source_name,collected_at,content_hash) VALUES(?,?,?,'arxiv','test',?,?)",
                [(str(n), f"Simulation {n}", f"https://example.com/{n}", "2026-09-29T00:00:00+00:00", f"hash-{n}") for n in range(10005)],
            )
    finally:
        conn.close()
    result = ddata.browse(database, page=101, page_size=100)
    assert (result["total"], result["pages"], len(result["entries"])) == (10005, 101, 5)


def test_group_representative_keeps_identity_and_batch_queries(database, monkeypatch):
    from datetime import timedelta
    from techradar.utils.time import utcnow
    older = TechItem(source_type="github", source_name="test", title="Old high-score version", url="https://example.com/old", collected_at=utcnow() - timedelta(days=2), metadata={"repo": "acme/sim"})
    newer = TechItem(source_type="github", source_name="test", title="Newest version", url="https://example.com/new", metadata={"repo": "acme/sim"})
    ItemRepository(database).insert_many([older, newer])
    arepo = AnalysisRepository(database)
    arepo.upsert_analysis(older.id, {"score": 90, "summary": "Older evidence", "llm_status": "llm"})
    arepo.upsert_analysis(newer.id, {"score": 60, "llm_status": "llm"})
    arepo.upsert_relevance(older.id, {"funlab": 5})
    statements = []
    original = database.connect
    def traced():
        connection = original()
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(database, "connect", traced)
    result = ddata.browse(database)
    entry = result["entries"][0]
    assert entry["id"] == older.id
    assert entry["collected_at"] == older.collected_at.isoformat()
    assert entry["_latest_title"] == newer.title
    assert entry["versions"][0]["url"] == newer.url
    assert entry["relevance"] == {"funlab": 5}
    assert len(statements) <= 5  # read transaction plus four batch queries


def test_source_health_distinguishes_unavailable_and_partial(config):
    config.sources = {"collections": {"github": {"enabled": True, "events": ["release", "push"]}, "engineering_machinery": {"enabled": True}}}
    rows = {row["source"]: row for row in ddata.source_health(config, {"collect": {"github": {"status": "partial", "attempted": 10, "succeeded": 8}}})}
    assert rows["engineering_machinery"]["label"] == "未接入"
    assert rows["github"]["label"] == "部分失败"
    assert rows["github"]["unsupported_events"] == ["push"]
    assert rows["arxiv"]["label"] == "待检测"
