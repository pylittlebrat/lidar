"""TechItem 统一数据模型测试。"""

from __future__ import annotations

from techradar.storage.models import TechItem, compute_id


def sample_item(**overrides) -> TechItem:
    values = dict(
        source_type="github",
        source_name="IsaacLab",
        title="Isaac Lab v2.0 Release",
        url="https://github.com/isaac-sim/IsaacLab/releases/tag/v2.0",
        authors=["NVIDIA"],
    )
    values.update(overrides)
    return TechItem(**values)


def test_auto_id_and_collected_at() -> None:
    item = sample_item()
    assert item.id
    assert item.collected_at is not None
    assert item.id == compute_id("github", item.url)


def test_url_canonicalization() -> None:
    item = sample_item(url="https://example.com/path/")
    assert item.url == "https://example.com/path"


def test_content_hash_deterministic() -> None:
    a = sample_item()
    b = sample_item()
    assert a.content_hash() == b.content_hash()


def test_url_hash_differs_by_url() -> None:
    a = sample_item(url="https://a.example/x", title="same title")
    b = sample_item(url="https://b.example/y", title="same title")
    assert a.content_hash() != b.content_hash()


def test_storage_round_trip() -> None:
    item = sample_item()
    restored = TechItem.from_storage(item.to_storage())
    assert restored == item


def test_to_dict_json_serializable() -> None:
    import json

    payload = json.dumps(sample_item().to_dict(), ensure_ascii=False)
    assert '"title"' in payload