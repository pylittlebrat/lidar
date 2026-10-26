"""backfill 历史回填测试：入库语义（collected_at=published_at）与每日统计隔离。"""

from __future__ import annotations

from datetime import timedelta

from techradar.collectors.base import BaseCollector
from techradar.pipeline import Pipeline
from techradar.storage.models import TechItem
from techradar.storage.repository import ItemRepository
from techradar.utils.time import utcnow


class FakeBackfillCollector(BaseCollector):
    def __init__(self, items: list[TechItem], config: dict | None = None,
                 source_type: str = "github") -> None:
        super().__init__(config)
        self._items = items
        self.source_type = source_type
        self.source_name = source_type

    def fetch(self) -> list[TechItem]:
        return self._items


def make_historical(url: str, title: str, days_ago: int) -> TechItem:
    return TechItem(
        source_type="github", source_name="x", title=title, url=url,
        abstract="physics engine system identification sim2real",
        published_at=utcnow() - timedelta(days=days_ago),
        metadata={"watched": True},
    )


def test_backfill_uses_publish_time(config, database, monkeypatch) -> None:
    month_ago = 30
    quarter_ago = 80
    old = make_historical("https://e/m30", "History Release A v1.0", month_ago)
    older = make_historical("https://e/m80", "History Release B v0.9", quarter_ago)
    monkeypatch.setattr(
        "techradar.pipeline.COLLECTORS",
        {"github": lambda config=None: FakeBackfillCollector([old, older], config=config)},
    )
    stats = Pipeline(config, database).backfill(days=90, sources=["github"])
    assert stats["github"]["inserted"] == 2

    conn = database.connect()
    try:
        rows = conn.execute("SELECT title, collected_at, published_at FROM items").fetchall()
    finally:
        conn.close()
    by_title = {r["title"]: r for r in rows}
    # collected_at 应为历史（等于 published_at），而非当前时刻
    assert by_title["History Release A v1.0"]["collected_at"].startswith(
        (utcnow() - timedelta(days=month_ago)).isoformat()[:10]
    )
    assert by_title["History Release A v1.0"]["collected_at"] == \
        by_title["History Release A v1.0"]["published_at"]
    assert by_title["History Release B v0.9"]["collected_at"].startswith(
        (utcnow() - timedelta(days=quarter_ago)).isoformat()[:10]
    )
    # 今日/近 1 天窗口统计应不含回填数据（不影响每日雷达）
    assert ItemRepository(database).count_since((utcnow() - timedelta(days=1)).isoformat()) == 0
    # 但全库可检索/可见
    assert ItemRepository(database).count() == 2


def test_backfill_dedup_across_runs(config, database, monkeypatch) -> None:
    item = make_historical("https://e/dup", "History Release C", 10)
    monkeypatch.setattr(
        "techradar.pipeline.COLLECTORS",
        {"github": lambda config=None: FakeBackfillCollector([item], config=config)},
    )
    pipeline = Pipeline(config, database)
    first = pipeline.backfill(days=90, sources=["github"])
    second = pipeline.backfill(days=90, sources=["github"])
    assert first["github"]["inserted"] == 1
    assert second["github"]["inserted"] == 0
    assert second["github"]["duplicates"] >= 1
    assert ItemRepository(database).count() == 1


def test_backfill_rss_not_included(config, database, monkeypatch) -> None:
    """backfill 只处理 github/arxiv，不处理无法回溯历史的 rss。"""
    monkeypatch.setattr(
        "techradar.pipeline.COLLECTORS",
        {
            "github": lambda config=None: FakeBackfillCollector(
                [make_historical("https://e/g", "GH", 5)], config=config),
            "arxiv": lambda config=None: FakeBackfillCollector(
                [TechItem(source_type="arxiv", source_name="arXiv", title="AX",
                          url="https://arxiv.org/abs/2", abstract="physics engine",
                          published_at=utcnow() - timedelta(days=5))],
                config=config, source_type="arxiv"),
        },
    )
    pipeline = Pipeline(config, database)
    stats = pipeline.backfill(days=90)
    assert set(stats) == {"github", "arxiv"}
    assert stats["github"]["inserted"] == 1
    assert stats["arxiv"]["inserted"] == 1
    assert ItemRepository(database).count() == 2