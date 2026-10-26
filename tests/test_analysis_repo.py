"""AnalysisRepository 测试：analysis / relevance 表读写与未分析查询。"""

from __future__ import annotations

from techradar.storage.models import TechItem
from techradar.storage.repository import AnalysisRepository, ItemRepository


def make_item(url: str, title: str) -> TechItem:
    return TechItem(
        source_type="arxiv", source_name="arXiv", title=title, url=url,
        abstract="physics engine for robot learning",
    )


def test_upsert_analysis_and_unanalyzed(database) -> None:
    item = make_item("https://arxiv.org/abs/2601.1", "GPU Physics Simulation")
    ItemRepository(database).insert(item)
    arepo = AnalysisRepository(database)
    assert arepo.unanalyzed(10) == [item]
    assert arepo.analyzed_count() == 0

    arepo.upsert_analysis(
        item.id,
        {
            "summary": "GPU 物理仿真",
            "score": 88,
            "categories": ["simulation_engine"],
            "innovation": ["统一抽象"],
            "open_source": True,
        },
    )
    arepo.upsert_relevance(item.id, {"funlab": 5, "simulation_platform": "4"})

    assert arepo.unanalyzed(10) == []
    assert arepo.analyzed_count() == 1
    conn = database.connect()
    try:
        row = conn.execute(
            "SELECT score, categories, open_source FROM analysis WHERE item_id = ?", (item.id,)
        ).fetchone()
        assert row["score"] == 88
        assert "simulation_engine" in row["categories"]
        assert row["open_source"] == 1
        rel = conn.execute(
            "SELECT project_key, score FROM relevance WHERE item_id = ? ORDER BY project_key", (item.id,)
        ).fetchall()
    finally:
        conn.close()
    assert {r["project_key"]: r["score"] for r in rel} == {"funlab": 5.0, "simulation_platform": 4.0}


def test_upsert_analysis_overwrites(database) -> None:
    item = make_item("https://arxiv.org/abs/2601.2", "Real2Sim Pipeline")
    ItemRepository(database).insert(item)
    arepo = AnalysisRepository(database)
    arepo.upsert_analysis(item.id, {"summary": "v1", "score": 50})
    arepo.upsert_analysis(item.id, {"summary": "v2", "score": 90})
    assert arepo.analyzed_count() == 1
    conn = database.connect()
    try:
        row = conn.execute("SELECT summary, score FROM analysis WHERE item_id = ?", (item.id,)).fetchone()
    finally:
        conn.close()
    assert (row["summary"], row["score"]) == ("v2", 90)