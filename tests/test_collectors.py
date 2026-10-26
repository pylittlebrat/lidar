"""BaseCollector 抽象契约测试。"""

from __future__ import annotations

import pytest

from techradar.collectors.base import BaseCollector
from techradar.storage.models import TechItem


def test_abstract_cannot_instantiate() -> None:
    with pytest.raises(TypeError):
        BaseCollector()


class FakeCollector(BaseCollector):
    source_type = "fake"
    source_name = "Fake"

    def fetch(self) -> list[TechItem]:
        return [
            TechItem(
                source_type="fake",
                source_name="Fake",
                title="t",
                url="https://example.com/x",
            )
        ]


def test_concrete_collector_collect() -> None:
    collector = FakeCollector()
    items = collector.collect()
    assert len(items) == 1
    assert items[0].source_type == "fake"
    assert items[0].source_name == "Fake"


def test_source_attributes() -> None:
    assert FakeCollector.source_type == "fake"
    assert FakeCollector.source_name == "Fake"