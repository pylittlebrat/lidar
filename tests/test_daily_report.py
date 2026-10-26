"""日报生成器测试。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from techradar.reports.daily import DailyReportGenerator
from techradar.storage.models import TechItem
from techradar.storage.repository import AnalysisRepository, ItemRepository


REPORT_DATE = date(2026, 9, 28)
SEED_TIME = datetime(2026, 9, 28, 12, tzinfo=timezone(timedelta(hours=8)))


def make_item(source_type: str, url: str, title: str, abstract: str = "", collected_at=SEED_TIME, **meta) -> TechItem:
    return TechItem(
        source_type=source_type,
        source_name=url.split("//")[1].split("/")[0],
        title=title,
        url=url,
        abstract=abstract,
        metadata=meta,
        collected_at=collected_at,
    )


def _seed(database) -> dict[str, TechItem]:
    irepo = ItemRepository(database)
    paper = make_item("arxiv", "https://arxiv.org/abs/2601.1",
                      "GPU Physics Simulation for Robot Learning", "physics engine real2sim")
    new_repo = make_item("github", "https://github.com/acme/realsim", "A new real2sim toolkit",
                         "real2sim", watched=False)
    rss = make_item("rss", "https://blog.example/isaac", "Isaac Lab v2.0 Released",
                    "physics engine")
    for item in (paper, new_repo, rss):
        irepo.insert(item)
    arepo = AnalysisRepository(database)
    arepo.upsert_analysis(paper.id, {
        "summary": "GPU 物理仿真框架，统一后端抽象",
        "solution": "多后端调度",
        "innovation": ["统一 API", "GPU 加速"],
        "categories": ["simulation_engine"],
        "recommended_action": "INVESTIGATE",
        "open_source": True,
        "maturity": "release",
        "score": 88,
        "llm_status": "llm",
        "breakdown": {"evidence": {"quality": "limited", "note": "采集文本有限，暂作线索"}},
    })
    arepo.upsert_relevance(paper.id, {"funlab": 5, "simulation_platform": 5, "real2sim": 1,
                                      "engineering_machinery": 2, "robot_learning": 4})
    arepo.upsert_analysis(new_repo.id, {
        "summary": "real2sim 工具链", "categories": ["real2sim"],
        "recommended_action": "POC", "score": 72,
        "llm_status": "skipped",
    })
    arepo.upsert_analysis(rss.id, {
        "summary": "Isaac Lab 版本更新", "categories": ["simulation_engine"],
        "recommended_action": "WATCH", "score": 60,
    })
    return {"paper": paper, "new_repo": new_repo, "rss": rss}


def test_generate_daily_report(config, database, tmp_path) -> None:
    _seed(database)
    path = DailyReportGenerator(config, database).generate(
        report_date=date(2026, 9, 28), days=7, top_n=1, out_dir=tmp_path
    )
    assert path.exists() and path.name == "2026-09-28.md"
    text = path.read_text(encoding="utf-8")
    # 头部与概览
    assert "# Simulation Technology Daily" in text
    assert "Date: 2026-09-28" in text
    assert "## 今日概览" in text
    assert "高相关" in text
    assert "模型成功 1 / 规则评分 1 / 历史未知 1" in text
    assert "2026-09-22 00:00 ~ 2026-09-29 00:00" in text
    # Top Technology：最高分论文（含结构化字段）
    assert "# 🔥 Top Technology" in text
    assert "GPU Physics Simulation for Robot Learning" in text
    assert "https://arxiv.org/abs/2601.1" in text
    assert "分析依据: 模型分析" in text
    assert "资料情况: 采集文本有限，暂作线索" in text
    assert "88 / 100" in text
    assert "INVESTIGATE" in text
    assert "funlab: 5/5" in text  # 对我们的影响（relevance top2）
    # 其余进分组
    assert "## New GitHub Projects（新发现仓库）" in text
    assert "A new real2sim toolkit" in text
    assert "## Product Updates（产品/版本更新）" in text
    assert "Isaac Lab v2.0 Released" in text
    assert "历史未知（分析方式未标记）" in text
    assert "规则评分" in text
    # reports 表记录
    conn = database.connect()
    try:
        row = conn.execute("SELECT kind, report_date, summary FROM reports").fetchone()
    finally:
        conn.close()
    assert row["kind"] == "daily"
    assert row["report_date"] == "2026-09-28"
    assert "高分" in row["summary"]


def test_daily_engineering_machinery_section(config, database, tmp_path) -> None:
    item = make_item("arxiv", "https://arxiv.org/abs/2601.9",
                     "Autonomous Excavator Simulation Framework",
                     "autonomous mining excavator simulation")
    ItemRepository(database).insert(item)
    AnalysisRepository(database).upsert_analysis(item.id, {
        "categories": [], "score": 80, "summary": "挖掘机自主作业仿真",
        "recommended_action": "INVESTIGATE",
    })
    path = DailyReportGenerator(config, database).generate(
        report_date=date(2026, 9, 28), days=7, top_n=0, out_dir=tmp_path
    )
    text = path.read_text(encoding="utf-8")
    assert "## Engineering Machinery（工程机械）" in text
    assert "Autonomous Excavator Simulation Framework" in text


def test_daily_empty_db(config, database, tmp_path) -> None:
    path = DailyReportGenerator(config, database).generate(
        report_date=date(2026, 9, 28), days=7, out_dir=tmp_path
    )
    text = path.read_text(encoding="utf-8")
    assert "# Simulation Technology Daily" in text
    assert "新增采集（近窗口）: 0" in text
    assert "# 🔥 Top Technology" not in text  # 无数据时不出 Top 段


def test_daily_window_excludes_older_and_future_items(config, database, tmp_path) -> None:
    _seed(database)
    start = SEED_TIME.replace(hour=0)
    for title, collected_at in (("Start included", start),
                                ("Old excluded", start - timedelta(seconds=1)),
                                ("Next day excluded", start + timedelta(days=1)),
                                ("Future excluded", start + timedelta(days=2))):
        item = make_item("rss", f"https://example.com/{title.replace(' ', '-')}", title,
                         collected_at=collected_at)
        ItemRepository(database).insert(item)
        AnalysisRepository(database).upsert_analysis(item.id, {"summary": title, "score": 80, "llm_status": "llm"})
    path = DailyReportGenerator(config, database).generate(report_date=REPORT_DATE, days=1, out_dir=tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "新增采集（近窗口）: 4" in text
    assert "模型成功 2 / 规则评分 1 / 历史未知 1" in text
    assert "Start included" in text
    assert "Old excluded" not in text
    assert "Next day excluded" not in text
    assert "Future excluded" not in text


def test_daily_generation_is_idempotent(config, database, tmp_path) -> None:
    _seed(database)
    generator = DailyReportGenerator(config, database)
    first = generator.generate(report_date=REPORT_DATE, out_dir=tmp_path)
    first_text = first.read_text(encoding="utf-8")
    second = generator.generate(report_date=REPORT_DATE, out_dir=tmp_path)
    assert first == second
    assert second.read_text(encoding="utf-8") == first_text
    conn = database.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM reports WHERE kind='daily'").fetchone()[0] == 1
    finally:
        conn.close()
