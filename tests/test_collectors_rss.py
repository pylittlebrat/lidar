"""RSSCollector 测试（monkeypatch requests.get，feedparser 解析内存 XML）。"""

from __future__ import annotations

import techradar.collectors.rss as rss_mod
from techradar.collectors.rss import RSSCollector

SAMPLE_RSS = b"""<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>NVIDIA Developer Blog</title>
    <item>
      <title>Accelerating Isaac Sim with Warp</title>
      <link>https://developer.nvidia.com/blog/isaac-warp</link>
      <pubDate>Mon, 01 Sep 2026 10:00:00 GMT</pubDate>
      <description>&lt;p&gt;Simulation news.&lt;/p&gt;</description>
    </item>
    <item>
      <title>Kinematic-Tree Tools in Isaac Lab</title>
      <link>https://developer.nvidia.com/blog/kinematic-tree</link>
      <pubDate>Mon, 02 Sep 2026 12:00:00 GMT</pubDate>
      <description>More news.</description>
    </item>
  </channel>
</rss>
"""


class FakeResp:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


def test_fetch_items(monkeypatch) -> None:
    cfg = {
        "feeds": [{"name": "NVIDIA Developer Blog", "url": "https://example.com/feed"}],
        "timeout_seconds": 10,
        "max_entries_per_feed": 10,
    }
    monkeypatch.setattr(rss_mod.requests, "get", lambda *a, **k: FakeResp(SAMPLE_RSS))
    items = RSSCollector(config=cfg).fetch()
    assert len(items) == 2
    first = items[0]
    assert first.source_type == "rss"
    assert first.source_name == "NVIDIA Developer Blog"
    assert first.title == "Accelerating Isaac Sim with Warp"
    assert first.published_at is not None
    assert first.url == "https://developer.nvidia.com/blog/isaac-warp"


def test_feed_failure_skipped(monkeypatch) -> None:
    cfg = {
        "feeds": [
            {"name": "broken", "url": "https://broken.example"},
            {"name": "ok", "url": "https://ok.example"},
        ],
        "timeout_seconds": 10,
        "max_entries_per_feed": 10,
    }

    def flaky_get(url, timeout=0, headers=None):
        if "broken" in url:
            raise rss_mod.requests.RequestException("boom")
        return FakeResp(SAMPLE_RSS)

    monkeypatch.setattr(rss_mod.requests, "get", flaky_get)
    items = RSSCollector(config=cfg).fetch()
    # 单个 feed 失败不影响其他 feed
    assert len(items) == 2