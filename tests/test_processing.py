"""Processors 测试：Normalizer / KeywordFilter / Deduplicator。"""

from __future__ import annotations

from techradar.processors.deduplicate import Deduplicator, normalized_title, title_similarity
from techradar.processors.filter import KeywordFilter
from techradar.processors.normalize import Normalizer
from techradar.storage.models import TechItem


def make_item(title: str, url: str = "", abstract: str = "", source_type: str = "github", **meta) -> TechItem:
    return TechItem(
        source_type=source_type,
        source_name="x",
        title=title,
        url=url or f"https://example.com/{abs(hash(title))}",
        abstract=abstract,
        metadata=meta,
    )


# ---------------------------------------------------------------- Normalizer
def test_normalizer_strips_html_and_whitespace() -> None:
    item = make_item("<p>Isaac Lab &amp; Warp</p>\n  Release", abstract="a  <b>b</b>  c")
    Normalizer().normalize(item)
    assert item.title == "Isaac Lab & Warp Release"
    assert item.abstract == "a b c"


# ---------------------------------------------------------------- KeywordFilter
def test_filter_keeps_priority_and_drops_negative(config) -> None:
    filt = KeywordFilter(config)
    kept = make_item("Real2Sim asset generation via Gaussian Splatting", url="https://e/1")
    dropped_negative = make_item("Stock market update today", url="https://e/2")
    dropped_plain = make_item("Random cat video compilation", url="https://e/3")
    kept_items, dropped_items = filt.apply([kept, dropped_negative, dropped_plain])
    assert kept_items == [kept]
    assert set(id(i) for i in dropped_items) == {id(dropped_negative), id(dropped_plain)}


def test_filter_watched_always_kept(config) -> None:
    filt = KeywordFilter(config)
    item = make_item("Completely unrelated title", url="https://e/4", watched=True)
    kept, _dropped = filt.apply([item])
    assert kept == [item]


# ---------------------------------------------------------------- Deduplicator
def test_dedup_exact_hash(config) -> None:
    items = [
        make_item("Isaac Lab v2.0", url="https://e/same"),
        make_item("Isaac Lab v2.0", url="https://e/same"),  # 同 URL
        make_item("Different thing", url="https://e/diff"),
    ]
    kept, duplicates = Deduplicator(config).deduplicate(items)
    assert duplicates == 1
    assert len(kept) == 2


def test_dedup_title_similarity(config) -> None:
    """同一事件的 GitHub Release / Blog 标题接近 -> 视为重复（跨来源才判重）。"""
    items = [
        make_item("Isaac Lab v2.0 Release Announcement", url="https://e/github", source_type="github"),
        make_item("Isaac Lab v2.0 Release Announcement", url="https://e/blog", source_type="rss"),
        make_item("A totally different simulation toolkit", url="https://e/news", source_type="rss"),
    ]
    kept, duplicates = Deduplicator(config).deduplicate(items)
    assert duplicates == 1
    assert len(kept) == 2


def test_dedup_near_title(config) -> None:
    """一字之差（Announcement vs Announced）跨来源也应判重。"""
    items = [
        make_item("Isaac Lab v2.0 Release Announcement", url="https://e/1", source_type="github"),
        make_item("Isaac Lab v2.0 Release Announced", url="https://e/2", source_type="rss"),
    ]
    kept, duplicates = Deduplicator(config).deduplicate(items)
    assert duplicates == 1
    assert len(kept) == 1


def test_dedup_same_source_version_series_kept(config) -> None:
    """同一来源的版本序列（v1.0/v1.1/v2.0）不应互相判重（回填历史场景）。"""
    items = [
        make_item("isaac-sim/IsaacLab v1.0.0 Release", url="https://e/v100", source_type="github"),
        make_item("isaac-sim/IsaacLab v1.1.0 Release", url="https://e/v110", source_type="github"),
        make_item("isaac-sim/IsaacLab v2.0.0 Release", url="https://e/v200", source_type="github"),
    ]
    kept, duplicates = Deduplicator(config).deduplicate(items)
    assert duplicates == 0
    assert len(kept) == 3


def test_dedup_against_existing(config) -> None:
    items = [make_item("Isaac Lab v2.0 Release", url="https://e/1")]
    deduper = Deduplicator(config)
    first, _ = deduper.deduplicate(items)
    # 同一批次再次出现 -> duplicates=1（模拟跨天重复）
    second, duplicates = deduper.deduplicate(items, existing_hashes={first[0].content_hash()})
    assert duplicates == 1
    assert second == []


def test_title_similarity_helpers() -> None:
    # 标点被剥离，v2.0 -> "2.0" 中的点被替换为空格
    assert normalized_title("Isaac Lab v2.0 Release!") == "isaac lab v2 0 release"
    assert title_similarity("Isaac Lab v2.0 Release", "Isaac Lab v2.0 Release") == 1.0
    assert title_similarity("Isaac Lab physics", "Stock market today") < 0.5