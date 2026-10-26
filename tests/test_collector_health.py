"""Offline regression tests for source health, request budgets and coverage."""

from datetime import date, datetime, timedelta, timezone

import requests

from techradar.collectors.arxiv import ArxivCollector, ArxivRetryableError
from techradar.collectors.base import scheduled_keywords
from techradar.collectors.github import GitHubCollector
from techradar.collectors.registry import build_collectors, collector_availability
from techradar.collectors.rss import RSSCollector
from techradar.config import Config
from techradar.utils.time import utcnow


class Response:
    def __init__(self, payload, status=200, headers=None):
        self.payload = payload
        self.status_code = status
        self.headers = headers or {}

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("offline error")


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def release(tag):
    return {"tag_name": tag, "html_url": f"https://github.com/test/repo/releases/{tag}",
            "published_at": utcnow().isoformat(), "body": "robot simulation"}


def test_github_retains_success_before_later_timeout(monkeypatch):
    monkeypatch.setattr("techradar.collectors.github.time.sleep", lambda _: None)
    session = Session(Response([release("v1"), release("v2")]), requests.Timeout("token=do-not-expose"))
    collector = GitHubCollector({"watch_repositories": ["test/one", "test/two"],
                                 "limits": {"fetch_repo_meta": False}}, session=session)
    assert len(collector.collect()) == 2
    assert collector.last_result["attempted"] == 2
    assert collector.last_result["succeeded"] == 1
    assert collector.last_result["status"] == "partial"
    assert "do-not-expose" not in str(collector.last_result)


def test_github_retains_earlier_release_page_when_next_page_fails(monkeypatch):
    monkeypatch.setattr("techradar.collectors.github.time.sleep", lambda _: None)
    session = Session(Response([release("v1")]), requests.ConnectionError("offline"))
    collector = GitHubCollector({"watch_repositories": ["test/repo"],
                                 "limits": {"fetch_repo_meta": False, "release_per_page": 1}}, session=session)
    assert len(collector.collect()) == 1
    assert [params["page"] for _, params in session.calls] == [1, 2]
    assert collector.last_result["status"] == "partial"


def test_github_page_budget_marks_incomplete_window(monkeypatch):
    monkeypatch.setattr("techradar.collectors.github.time.sleep", lambda _: None)
    session = Session(Response([release("v1")]))
    collector = GitHubCollector({"watch_repositories": ["test/repo"],
                                 "limits": {"fetch_repo_meta": False, "release_per_page": 1,
                                            "release_max_pages": 1}}, session=session)
    assert len(collector.collect()) == 1
    assert collector.last_result["failures"][0]["kind"] == "incomplete_window"


def test_github_stops_requesting_a_rate_limited_resource(monkeypatch):
    monkeypatch.setattr("techradar.collectors.github.time.sleep", lambda _: None)
    session = Session(Response([], status=429))
    collector = GitHubCollector({"watch_repositories": ["test/one", "test/two"],
                                 "search_keywords": ["simulation"],
                                 "limits": {"fetch_repo_meta": False}}, session=session)
    assert collector.collect() == []
    assert len(session.calls) == 1
    assert collector.last_result["status"] == "failed"
    assert len(collector.last_result["failures"]) == 3


def test_github_emerging_queries_share_daily_search_budget(monkeypatch):
    monkeypatch.setattr("techradar.collectors.github.time.sleep", lambda _: None)
    session = Session(*(Response({"items": []}) for _ in range(8)))
    collector = GitHubCollector({"search_keywords": [f"query {i}" for i in range(14)],
                                 "emerging_repo": {"enabled": True, "keywords": [f"new {i}" for i in range(7)]},
                                 "limits": {"search_sleep_seconds": 0}}, session=session)
    assert collector.collect() == []
    assert len(session.calls) == 8
    assert collector.last_result["status"] == "success"


def test_all_rss_feeds_failed_is_not_empty_success_and_state_resets(monkeypatch):
    collector = RSSCollector({"feeds": [{"name": "test", "url": "https://example.invalid/rss"}]})
    def fail(*args, **kwargs):
        raise requests.Timeout("offline")
    monkeypatch.setattr("techradar.collectors.rss.requests.get", fail)
    assert collector.collect() == []
    assert collector.last_result["status"] == "failed"
    collector.config["feeds"] = []
    assert collector.collect() == []
    assert collector.last_result == {"attempted": 0, "succeeded": 0, "failures": [], "status": "success"}


def test_arxiv_good_query_survives_failed_query(monkeypatch):
    collector = ArxivCollector({"search_keywords": ["robot", "real2sim"], "limits": {"sleep_seconds": 0}})
    xml = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/test</id><title>Robot</title></entry></feed>'
    replies = iter([xml, ArxivRetryableError("429", 429)])
    def query(*args):
        value = next(replies)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(collector, "_query", query)
    assert len(collector.collect()) == 1
    assert collector.last_result["status"] == "partial"
    assert collector.last_result["failures"][0]["kind"] == "rate_limit"


def test_invalid_arxiv_response_marks_failure(monkeypatch):
    collector = ArxivCollector({"search_keywords": ["robot"], "limits": {"sleep_seconds": 0}})
    monkeypatch.setattr(collector, "_query", lambda *args: "<html>Blocked</html>")
    assert collector.collect() == []
    assert collector.last_result["status"] == "failed"
    assert collector.last_result["failures"][0]["kind"] == "parse"


def test_keyword_rotation_covers_every_configured_direction():
    keywords = [f"direction {i}" for i in range(14)]
    covered = set()
    for day in range(4):
        selected = scheduled_keywords(keywords, 5, as_of=date(2026, 9, 30) + timedelta(days=day))
        assert selected[:2] == keywords[:2]
        assert len(selected) == len(set(selected)) == 5
        covered.update(selected)
    assert covered == set(keywords)


def test_keyword_rotation_uses_beijing_date_and_rotates_single_query():
    keywords = ["a", "b", "c"]
    instant = datetime(2026, 9, 29, 16, 30, tzinfo=timezone.utc)
    assert scheduled_keywords(keywords, 1, as_of=instant) == scheduled_keywords(keywords, 1, as_of=date(2026, 9, 30))
    assert {scheduled_keywords(keywords, 1, as_of=date(2026, 9, 30) + timedelta(days=d))[0]
            for d in range(3)} == set(keywords)


def test_unimplemented_enabled_source_is_explicitly_unavailable():
    cfg = Config(sources={"collections": {"github": {"enabled": False}, "arxiv": {"enabled": False},
                                          "rss": {"enabled": False}, "engineering_machinery": {"enabled": True}}})
    availability = collector_availability(cfg)
    assert availability["engineering_machinery"]["status"] == "unavailable"
    collectors = build_collectors(cfg)
    assert len(collectors) == 1
    assert collectors[0].collect() == []
    assert collectors[0].last_result["status"] == "failed"
    assert collectors[0].last_result["failures"][0]["kind"] == "unavailable"
