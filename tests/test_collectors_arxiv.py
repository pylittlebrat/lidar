"""ArxivCollector 测试（monkeypatch requests.get，不打真实网络）。"""

from __future__ import annotations

import techradar.collectors.arxiv as arxiv_mod
from techradar.collectors.arxiv import ArxivCollector

SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>ArXiv Query</title>
  <entry>
    <id>http://arxiv.org/abs/2601.00001v1</id>
    <title>GPU-Accelerated Physics Simulation for Robot Learning
    (Extended)</title>
    <summary>A summary with newlines
    and more text.</summary>
    <published>2026-09-01T10:00:00Z</published>
    <author><name>Alice Zhang</name></author>
    <author><name>Bob Li</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2601.00002v2</id>
    <title>Real2Sim Asset Generation from Gaussian Splatting</title>
    <summary>Another summary.</summary>
    <published>2026-09-02T12:30:00Z</published>
    <author><name>Carol Wang</name></author>
  </entry>
</feed>
"""


class FakeResp:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None


def _make_collector(monkeypatch) -> ArxivCollector:
    cfg = {
        "categories": ["cs.RO"],
        "search_keywords": ["robot simulation"],
        "limits": {"max_queries": 1, "max_results": 15, "sleep_seconds": 0, "request_timeout_seconds": 10},
    }
    monkeypatch.setattr(arxiv_mod.requests, "get", lambda *a, **k: FakeResp(SAMPLE_FEED))
    return ArxivCollector(config=cfg)


def test_parse_feed_items(monkeypatch) -> None:
    items = _make_collector(monkeypatch).fetch()
    assert len(items) == 2
    first = items[0]
    assert first.source_type == "arxiv"
    assert first.title == "GPU-Accelerated Physics Simulation for Robot Learning (Extended)"
    assert first.authors == ["Alice Zhang", "Bob Li"]
    assert first.published_at is not None
    assert first.metadata["arxiv_id"] == "2601.00001v1"
    assert first.url.startswith("http://arxiv.org/abs/")


def test_bad_xml_returns_empty(monkeypatch) -> None:
    collector = _make_collector(monkeypatch)
    assert collector._parse_feed("not xml at all <<<", "robot simulation") == []


def test_retries_on_intermittent_406(monkeypatch) -> None:
    """arXiv 间歇 406：应自动重试并最终成功。"""
    calls = {"n": 0}

    def flaky_get(url, params=None, timeout=None, headers=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            return FakeResp("", status_code=406)
        return FakeResp(SAMPLE_FEED)

    cfg = {
        "categories": ["cs.RO"],
        "search_keywords": ["robot simulation"],
        "limits": {"max_queries": 1, "max_results": 15, "sleep_seconds": 0, "request_timeout_seconds": 10},
    }
    monkeypatch.setattr(arxiv_mod.requests, "get", flaky_get)
    items = ArxivCollector(config=cfg).fetch()
    assert calls["n"] == 3  # 首次 + 2 次重试
    assert len(items) == 2


def test_gives_up_after_retries(monkeypatch) -> None:
    def always_406(url, params=None, timeout=None, headers=None):
        return FakeResp("", status_code=406)

    cfg = {
        "categories": ["cs.RO"],
        "search_keywords": ["robot simulation"],
        "limits": {"max_queries": 1, "max_results": 15, "sleep_seconds": 0, "request_timeout_seconds": 10},
    }
    monkeypatch.setattr(arxiv_mod.requests, "get", always_406)
    items = ArxivCollector(config=cfg).fetch()  # 重试耗尽后应跳过并返回空
    assert items == []


def test_history_sampling_across_pages(monkeypatch) -> None:
    """history_days>0：跨时间段分页采样，超出窗口后提前停止且裁剪窗口外条目。"""
    from datetime import timedelta

    from techradar.utils.time import utcnow

    def feed_for(days_ago: int) -> str:
        published = (utcnow() - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/26{days_ago:04d}</id>
    <title>Paper from {days_ago} days ago</title>
    <summary>physics engine robot learning</summary>
    <published>{published}</published>
    <author><name>Author</name></author>
  </entry>
</feed>"""

    calls = {"n": 0}

    def paged_get(url, params=None, timeout=None, headers=None):
        calls["n"] += 1
        page = (params or {}).get("start", 0)
        offset = page // 60
        return FakeResp(feed_for(10 + offset * 100))  # 第 1 页 10 天前，第 2 页 110 天前…

    cfg = {
        "categories": ["cs.RO"],
        "search_keywords": ["robot simulation"],
        "limits": {"max_queries": 1, "max_results": 60, "sleep_seconds": 0,
                   "request_timeout_seconds": 10, "history_days": 200, "history_pages": 4},
    }
    monkeypatch.setattr(arxiv_mod.requests, "get", paged_get)
    items = ArxivCollector(config=cfg).fetch()
    # 第 1 页（10 天前）在窗口内；第 2 页（110 天前）仍在 200 天内；第 3 页（210 天前）超出窗口 -> 停止
    assert calls["n"] == 3
    assert len(items) == 2  # 第 1、2 页条目被保留
    assert all(i.published_at is not None for i in items)