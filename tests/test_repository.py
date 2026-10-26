"""ItemRepository：写入、去重与查询行为测试。"""

from __future__ import annotations

from techradar.storage.models import TechItem
from techradar.storage.repository import ItemRepository


def make_item(url: str) -> TechItem:
    return TechItem(source_type="github", source_name="x", title="t", url=url)


def test_insert_and_count(database) -> None:
    repo = ItemRepository(database)
    assert repo.insert(make_item("https://a.example/1")) is True
    assert repo.insert(make_item("https://a.example/2")) is True
    assert repo.count() == 2


def test_duplicate_not_inserted(database) -> None:
    repo = ItemRepository(database)
    assert repo.insert(make_item("https://a.example/same")) is True
    assert repo.insert(make_item("https://a.example/same")) is False
    assert repo.count() == 1


def test_same_url_trailing_slash_is_duplicate(database) -> None:
    """URL 尾部斜杠归一化后应命中去重。"""
    repo = ItemRepository(database)
    assert repo.insert(make_item("https://a.example/page")) is True
    assert repo.insert(make_item("https://a.example/page/")) is False
    assert repo.count() == 1


def test_insert_many_empty(database) -> None:
    result = ItemRepository(database).insert_many([])
    assert result == {"inserted": 0, "duplicates": 0}


def test_insert_many_mixed(database) -> None:
    repo = ItemRepository(database)
    items = [
        make_item("https://a.example/1"),
        make_item("https://a.example/2"),
        make_item("https://a.example/1"),  # 重复
    ]
    assert repo.insert_many(items) == {"inserted": 2, "duplicates": 1}


def test_get_and_recent(database) -> None:
    repo = ItemRepository(database)
    item = make_item("https://a.example/1")
    repo.insert(item)
    assert repo.get(item.id) == item
    assert [i.id for i in repo.recent(5)] == [item.id]