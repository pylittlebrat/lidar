"""Phase 6 测试：TrendDetector 与 WeeklyReportGenerator。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from techradar.analyzers.trends import TrendDetector
from techradar.reports.weekly import WeeklyReportGenerator
from techradar.storage.models import TechItem
from techradar.storage.repository import AnalysisRepository, ItemRepository


NOW = datetime(2026, 9, 30, 2, tzinfo=timezone.utc)


def insert_analyzed(
    database, title: str, url: str, keywords: list[str], score: float,
    days_ago: int, source_type: str = "arxiv",
    llm_status: str = "llm",
) -> TechItem:
    item = TechItem(
        source_type=source_type,
        source_name=url.split("//")[1].split("/")[0],
        title=title,
        url=url,
        abstract="robot simulation physics engine real2sim",
        collected_at=NOW - timedelta(days=days_ago),
    )
    ItemRepository(database).insert(item)
    AnalysisRepository(database).upsert_analysis(item.id, {
        "summary": "s", "keywords": keywords, "categories": ["simulation_engine"],
        "score": score, "recommended_action": "INVESTIGATE",
        "llm_status": llm_status,
    })
    return item


def test_trend_detector_directions(config, database) -> None:
    insert_analyzed(database, "Physics backend A", "https://e/a", ["physics engine"], 80, days_ago=1)
    insert_analyzed(database, "Physics backend B", "https://e/b", ["physics engine"], 80, days_ago=2)
    insert_analyzed(database, "Physics backend C", "https://e/c", ["physics engine"], 70, days_ago=20)
    insert_analyzed(database, "Old topic A", "https://e/d", ["old topic"], 50, days_ago=25)
    insert_analyzed(database, "Old topic B", "https://e/e", ["old topic"], 50, days_ago=60)
    insert_analyzed(database, "Old topic C", "https://e/old-c", ["old topic"], 50, days_ago=21)
    insert_analyzed(database, "Old topic D", "https://e/old-d", ["old topic"], 50, days_ago=22)
    insert_analyzed(database, "Fresh topic", "https://e/f", ["fresh topic"], 55, days_ago=3)

    trends = TrendDetector(config, database).trends(days=90, as_of=NOW)
    by_name = {t["name"]: t for t in trends["keywords"]}
    # 近期 2/7 对比前期 1/23，避免比较重叠窗口。
    assert by_name["physics engine"]["direction"] == "up"
    assert by_name["physics engine"]["counts"]["7d"] == 2
    assert by_name["physics engine"]["previous_count"] == 1
    assert by_name["physics engine"]["change_pct"] == pytest.approx(557.1)
    # 近期无记录、前期三条记录，数据充分时判下降。
    assert by_name["old topic"]["direction"] == "down"
    # 单条记录只能作为线索，不能判定上升。
    assert by_name["fresh topic"]["direction"] == "insufficient"
    assert by_name["fresh topic"]["change_pct"] is None
    assert by_name["fresh topic"]["counts"]["7d"] == 1
    # 上升趋势排在列表前面
    assert trends["keywords"][0]["name"] == "physics engine"


def test_weekly_report(config, database, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("techradar.reports.weekly.utcnow", lambda: NOW + timedelta(seconds=1))
    insert_analyzed(database, "GPU Physics Simulation Paper", "https://arxiv.org/abs/1",
                    ["physics engine", "gpu simulation"], 88, days_ago=0)
    insert_analyzed(database, "Real2Sim Toolkit", "https://e/toolkit",
                    ["real2sim"], 60, days_ago=0, source_type="github", llm_status="skipped")
    path = WeeklyReportGenerator(config, database).generate(
        end_date=date(2026, 9, 30), top_n=8, out_dir=tmp_path
    )
    assert path.exists()
    assert path.name.startswith("20") and path.name.endswith(".md")
    text = path.read_text(encoding="utf-8")
    assert "# Simulation Technology Weekly" in text
    assert "## 本周概览" in text
    assert "## 趋势分析" in text
    assert "physics engine" in text
    assert "## 重点技术（本周高分）" in text
    assert "GPU Physics Simulation Paper" in text
    assert "https://arxiv.org/abs/1" in text
    assert "模型成功 1 / 规则评分 1 / 历史未知 0" in text
    assert "数据不足，暂不判断涨跌" in text
    # reports 表记录
    conn = database.connect()
    try:
        row = conn.execute("SELECT kind, summary FROM reports ORDER BY id DESC").fetchone()
    finally:
        conn.close()
    assert row["kind"] == "weekly"


def test_weekly_empty_db(config, database, tmp_path) -> None:
    path = WeeklyReportGenerator(config, database).generate(out_dir=tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "# Simulation Technology Weekly" in text
    assert "数据不足" in text


def test_cold_start_does_not_claim_rising_trends(config, database) -> None:
    for i in range(3):
        insert_analyzed(database, f"Cold start {i}", f"https://e/cold/{i}", ["cold start"], 80, days_ago=i + 1)
    trend = TrendDetector(config, database).trends(as_of=NOW)["keywords"][0]
    assert trend["counts"]["30d"] == 3
    assert trend["direction"] == "insufficient"
    assert trend["change_pct"] is None


def test_new_direction_requires_three_samples_and_previous_history(config, database) -> None:
    insert_analyzed(database, "History", "https://e/history", ["baseline"], 70, days_ago=40)
    for i in range(3):
        insert_analyzed(database, f"New direction {i}", f"https://e/new/{i}", ["new direction"], 80, days_ago=i + 1)
    trends = TrendDetector(config, database).trends(as_of=NOW)
    trend = next(t for t in trends["keywords"] if t["name"] == "new direction")
    assert trend["direction"] == "new"
    assert trend["previous_count"] == 0
    assert trend["change_pct"] is None


def test_trends_exclude_future_end_boundary_failed_and_outside_window(config, database) -> None:
    insert_analyzed(database, "Past", "https://e/past", ["past"], 80, days_ago=1)
    insert_analyzed(database, "Future", "https://e/future", ["future"], 80, days_ago=-1)
    insert_analyzed(database, "End boundary", "https://e/end", ["end boundary"], 80, days_ago=0)
    insert_analyzed(database, "Too old", "https://e/old", ["too old"], 80, days_ago=91)
    insert_analyzed(database, "Failed", "https://e/failed", ["failed"], 80, days_ago=2, llm_status="llm_failed")
    trends = TrendDetector(config, database).trends(as_of=NOW)
    assert {trend["name"] for trend in trends["keywords"]} == {"past"}
    assert trends["sources"][0]["counts"]["90d"] == 1


def test_weekly_window_and_generation_are_idempotent(config, database, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("techradar.reports.weekly.utcnow", lambda: NOW + timedelta(seconds=1))
    insert_analyzed(database, "Inside week", "https://e/inside", ["inside"], 88, days_ago=0, llm_status="")
    insert_analyzed(database, "Before week", "https://e/before", ["before"], 88, days_ago=7)
    insert_analyzed(database, "After week", "https://e/after", ["after"], 88, days_ago=-1)
    generator = WeeklyReportGenerator(config, database)
    first = generator.generate(end_date=date(2026, 9, 30), out_dir=tmp_path)
    first_text = first.read_text(encoding="utf-8")
    second = generator.generate(end_date=date(2026, 9, 30), out_dir=tmp_path)
    assert first == second
    assert second.read_text(encoding="utf-8") == first_text
    assert "新增: 1" in first_text
    assert "模型成功 0 / 规则评分 0 / 历史未知 1" in first_text
    assert "Inside week" in first_text
    assert "Before week" not in first_text
    assert "After week" not in first_text
    conn = database.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM reports WHERE kind='weekly'").fetchone()[0] == 1
    finally:
        conn.close()
