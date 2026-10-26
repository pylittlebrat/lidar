"""GitHubCollector 测试（FakeSession 注入，不打真实网络）。"""

from __future__ import annotations

import pytest
import requests
from techradar.utils.time import utcnow

from techradar.collectors.github import GitHubCollector, GitHubRateLimitError


class FakeResponse:
    def __init__(self, payload=None, status: int = 200, headers: dict | None = None) -> None:
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}
        self.text = ""  # GitHubCollector 限速日志会读取响应体

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            err = requests.HTTPError(f"{self.status_code} error")
            err.response = self
            raise err

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, *responses: FakeResponse) -> None:
        self._queue = list(responses)
        self.calls: list[str] = []
        self.headers: dict = {}  # 模拟 requests.Session.headers

    def get(self, url: str, params=None, timeout=None) -> FakeResponse:
        self.calls.append(url)
        if not self._queue:
            raise AssertionError("FakeSession 响应队列已空")
        response = self._queue.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


RELEASE_PAYLOAD = {
    "tag_name": "v2.1.0",
    "html_url": "https://github.com/isaac-sim/IsaacLab/releases/tag/v2.1.0",
    "published_at": utcnow().isoformat(),
    "body": "Adds multi-physics backend support.",
}
REPO_PAYLOAD = {"stargazers_count": 12345, "forks_count": 2000}


def make_config(**overrides) -> dict:
    cfg = {
        "watch_repositories": ["isaac-sim/IsaacLab"],
        "search_keywords": [],
        "limits": {
            "max_releases": 5,
            "fetch_repo_meta": True,
            "request_timeout_seconds": 10,
            "search_sleep_seconds": 0,
        },
    }
    cfg.update(overrides)
    return cfg


def test_watch_release_item() -> None:
    session = FakeSession(
        FakeResponse([RELEASE_PAYLOAD]),
        FakeResponse(REPO_PAYLOAD),
    )
    collector = GitHubCollector(make_config(), session=session)
    items = collector.fetch()
    assert len(items) == 1
    item = items[0]
    assert item.source_type == "github"
    assert "v2.1.0" in item.title
    assert item.url.endswith("v2.1.0")
    assert item.published_at is not None
    assert item.metadata["watched"] is True
    assert item.metadata["stars"] == 12345
    assert item.metadata["forks"] == 2000
    assert len(session.calls) == 2  # release + repo meta


def test_no_release_is_skipped() -> None:
    session = FakeSession(FakeResponse(None, status=404))
    collector = GitHubCollector(make_config(), session=session)
    assert collector.fetch() == []


def test_repo_meta_optional() -> None:
    session = FakeSession(FakeResponse([RELEASE_PAYLOAD]))
    collector = GitHubCollector(
        make_config(limits={"fetch_repo_meta": False, "request_timeout_seconds": 10}),
        session=session,
    )
    items = collector.fetch()
    assert items[0].metadata["stars"] == 0
    assert len(session.calls) == 1


def test_rate_limit_is_reported_without_discarding_other_sources() -> None:
    session = FakeSession(
        FakeResponse(None, status=403, headers={"X-RateLimit-Remaining": "0"})
    )
    collector = GitHubCollector(make_config(), session=session)
    assert collector.collect() == []
    assert collector.last_result["status"] == "failed"
    assert collector.last_result["failures"][0]["kind"] == "rate_limit"


def test_keyword_search_items() -> None:
    search_payload = {
        "items": [
            {
                "full_name": "mystery/realsim",
                "html_url": "https://github.com/mystery/realsim",
                "description": "A new real2sim toolkit",
                "stargazers_count": 88,
                "forks_count": 10,
                "created_at": "2026-09-10T00:00:00Z",
            }
        ]
    }
    session = FakeSession(FakeResponse(search_payload))
    collector = GitHubCollector(
        make_config(
            watch_repositories=[],  # 跳过 Watch Release 阶段，只测搜索
            search_keywords=["real2sim"],
            limits={
                "max_searches": 1,
                "search_per_page": 5,
                "search_sleep_seconds": 0,
                "request_timeout_seconds": 10,
            },
        ),
        session=session,
    )
    items = collector.fetch()
    assert len(items) == 1
    assert items[0].metadata["watched"] is False
    assert items[0].metadata["stars"] == 88
    assert "real2sim" in items[0].metadata["note"]


def test_history_days_fetches_release_list() -> None:
    """history_days > 0：拉近 N 天全部 releases，只保留窗口内条目。"""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    releases = [
        # 近期 release
        {"tag_name": "v2.1.0", "html_url": "https://github.com/isaac-sim/IsaacLab/releases/tag/v2.1.0",
         "published_at": (now - timedelta(days=3)).isoformat(), "body": "recent"},
        # 窗口外（100 天前）
        {"tag_name": "v1.9.0", "html_url": "https://github.com/isaac-sim/IsaacLab/releases/tag/v1.9.0",
         "published_at": (now - timedelta(days=100)).isoformat(), "body": "old"},
    ]
    session = FakeSession(FakeResponse(releases))
    collector = GitHubCollector(
        make_config(limits={
            "history_days": 30,
            "fetch_repo_meta": False,
            "request_timeout_seconds": 10,
        }),
        session=session,
    )
    items = collector.fetch()
    assert len(items) == 1  # 100 天前的被窗口过滤
    assert items[0].url.endswith("v2.1.0")
    assert items[0].metadata["watched"] is True
    assert len(session.calls) == 1  # 只请求 releases 列表，不请求 meta
