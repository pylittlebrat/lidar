"""GitHub 采集器：模式 A（Watch List Release）+ 模式 B（关键词搜索）+ 模式 C（新仓库发现）。

GitHub Release 往往比新闻早数周，是技术雷达最重要的信息源之一。
未配置 GITHUB_TOKEN 时受到 GitHub 未认证限速（60 req/h；搜索 10 req/min），
因此所有请求都会检查 X-RateLimit-Remaining，被限速时抛出 GitHubRateLimitError，
由上层流水线记录后继续其他信息源。
"""

from __future__ import annotations

import datetime as dt
import os
import time
from typing import Any

import requests

from techradar.collectors.base import BaseCollector
from techradar.storage.models import TechItem
from techradar.utils.time import utcnow

_GITHUB_API = "https://api.github.com"
_UA = "simulation-tech-radar/0.1"


class GitHubRateLimitError(RuntimeError):
    """GitHub API 限速（未认证 60 req/h / 搜索 10 req/min）。"""


class GitHubCollector(BaseCollector):
    """基于 GitHub REST API 的采集器（三种模式见类文档）。"""

    source_type = "github"
    source_name = "GitHub"

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        token: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        super().__init__(config)
        self.token = token or os.environ.get("GITHUB_TOKEN") or ""
        self.session = session if session is not None else requests.Session()
        self.session.headers.update({"User-Agent": _UA, "Accept": "application/vnd.github+json"})
        if self.token:
            self.session.headers["Authorization"] = f"token {self.token}"
        limits = self.config.get("limits", {}) if isinstance(self.config, dict) else {}
        self.limits = limits if isinstance(limits, dict) else {}
        self.timeout = float(self.limits.get("request_timeout_seconds", 30))
        self._limited_scopes: set[str] = set()

    # ------------------------------------------------------------------ HTTP
    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET 一个 GitHub API 端点；遇限速抛 GitHubRateLimitError。"""
        scope = "search" if path.startswith("/search/") else "core"
        if "all" in self._limited_scopes or scope in self._limited_scopes:
            raise GitHubRateLimitError("GitHub request budget exhausted")
        resp = self.session.get(f"{_GITHUB_API}{path}", params=params, timeout=self.timeout)
        remaining = resp.headers.get("X-RateLimit-Remaining")
        if resp.status_code in (403, 429):
            if resp.status_code == 429 or remaining == "0" or resp.headers.get("Retry-After"):
                self._limited_scopes.add(scope if remaining == "0" else "all")
                reset = resp.headers.get("X-RateLimit-Reset", "?")
                self.logger.warning("GitHub 限速（%s %s）", path, reset)
                raise GitHubRateLimitError(f"GitHub 限速，reset={reset}")
            if resp.headers.get("X-RateLimit-Remaining") is None:
                self.logger.warning("GitHub 请求被拒（%s）", path)
                resp.raise_for_status()
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------ fetch
    def fetch(self) -> list[TechItem]:
        """聚合三种模式的采集结果。单个仓库失败只记日志，不影响整体。"""
        self.reset_result()
        self._limited_scopes.clear()
        items: list[TechItem] = []
        items.extend(self._fetch_watch_releases())
        items.extend(self._fetch_keyword_searches())
        items.extend(self._fetch_emerging())
        return items

    # ------------------------------------------- 模式 A：Watch List Releases
    def _fetch_watch_releases(self) -> list[TechItem]:
        """Read a bounded release window, retaining pages fetched before failure."""
        repos = self.config.get("watch_repositories", [])
        max_releases = int(self.limits.get("max_releases", 20))
        history_days = int(self.limits.get("history_days", 0))
        window_days = history_days or int(self.limits.get("release_window_days", 7))
        page_size = max(1, min(100, int(self.limits.get("release_per_page", 100 if history_days else 20))))
        max_pages = max(1, int(self.limits.get("release_max_pages", 5 if history_days else 2)))
        fetch_meta = bool(self.limits.get("fetch_repo_meta", True))
        since = utcnow() - dt.timedelta(days=max(1, window_days))
        items: list[TechItem] = []
        for repo in repos[:max_releases]:
            releases: list[dict[str, Any]] = []
            for page in range(1, max_pages + 1):
                self.record_attempt()
                try:
                    payload = self._get(f"/repos/{repo}/releases", params={"per_page": page_size, "page": page})
                    if not isinstance(payload, list) or any(not isinstance(r, dict) for r in payload):
                        raise ValueError("Invalid release response")
                except (requests.RequestException, GitHubRateLimitError, ValueError) as exc:
                    self.record_failure(f"release:{repo}:page:{page}", exc,
                                        "rate_limit" if isinstance(exc, GitHubRateLimitError) else "network")
                    self.logger.warning("获取 %s release 第 %d 页失败: %s", repo, page, type(exc).__name__)
                    break
                self.record_success()
                releases.extend(payload)
                reached_window = any(
                    (published := self._parse_time(r.get("published_at"))) is not None and published < since
                    for r in payload
                )
                if len(payload) < page_size or reached_window:
                    break
                if page == max_pages:
                    self.record_failure(f"release:{repo}", "Release window exceeded page budget", "incomplete_window")
            repo_meta: dict[str, Any] = {"stars": 0, "forks": 0}
            if fetch_meta and releases:
                self.record_attempt()
                try:
                    info = self._get(f"/repos/{repo}")
                    repo_meta = {
                        "stars": info.get("stargazers_count", 0),
                        "forks": info.get("forks_count", 0),
                    }
                    self.record_success()
                except (requests.RequestException, GitHubRateLimitError, ValueError, AttributeError) as exc:
                    self.record_failure(f"metadata:{repo}", exc,
                                        "rate_limit" if isinstance(exc, GitHubRateLimitError) else "network")
                    self.logger.warning("获取 %s 元数据失败: %s", repo, type(exc).__name__)
            for release in releases:
                published = self._parse_time(release.get("published_at"))
                if release.get("draft") or (published is not None and published < since):
                    continue
                meta: dict[str, Any] = {
                    "repo": repo,
                    "tag": release.get("tag_name", ""),
                    "watched": True,
                    **repo_meta,
                }
                items.append(
                    TechItem(
                        source_type=self.source_type,
                        source_name=repo.split("/")[-1],
                        title=f"{repo} {release.get('tag_name', '')} Release".strip(),
                        url=release.get("html_url", f"https://github.com/{repo}/releases"),
                        published_at=published,
                        abstract=(release.get("body") or "")[:4000],
                        raw_text=release.get("body") or "",
                        tags=["github", "release"],
                        metadata=meta,
                    )
                )
            time.sleep(0.3)
        return items

    # ------------------------------------------- 模式 B：关键词搜索
    def _fetch_keyword_searches(self) -> list[TechItem]:
        keywords = self.config.get("search_keywords", [])
        if not keywords:
            return []
        max_searches = int(self.limits.get("max_searches", 5))
        per_page = int(self.limits.get("search_per_page", 5))
        sleep_seconds = float(self.limits.get("search_sleep_seconds", 4))
        items: list[TechItem] = []
        for keyword in self.select_keywords(keywords, max_searches):
            self.record_attempt()
            try:
                payload = self._get(
                    "/search/repositories",
                    params={
                        "q": f'{keyword} in:name,description,readme',
                        "sort": "updated",
                        "order": "desc",
                        "per_page": per_page,
                    },
                )
                if not isinstance(payload, dict) or not isinstance(payload.get("items", []), list):
                    raise ValueError("Invalid repository response")
            except (requests.RequestException, GitHubRateLimitError, ValueError) as exc:
                self.record_failure(f"search:{keyword}", exc,
                                    "rate_limit" if isinstance(exc, GitHubRateLimitError) else "network")
                self.logger.warning("GitHub 搜索 '%s' 失败: %s", keyword, type(exc).__name__)
                continue
            self.record_success()
            for repo in payload.get("items", []):
                items.append(self._repo_to_item(repo, watched=False, note=f"search:{keyword}"))
            time.sleep(sleep_seconds)
        return items

    # ------------------------------------------- 模式 C：新仓库发现（Emerging）
    def _fetch_emerging(self) -> list[TechItem]:
        emerging = self.config.get("emerging_repo", {})
        if not emerging.get("enabled", False):
            return []
        keywords = emerging.get("keywords", [])
        created_within = int(emerging.get("created_within_days", 30))
        min_stars = int(emerging.get("min_stars", 50))
        per_page = int(self.limits.get("emerging_per_page", 3))
        since = (utcnow() - dt.timedelta(days=created_within)).strftime("%Y-%m-%d")
        items: list[TechItem] = []
        # Keyword + emerging searches share GitHub's search request budget.
        default_budget = max(0, 8 - int(self.limits.get("max_searches", 5)))
        max_queries = int(self.limits.get("max_emerging_searches", default_budget))
        for keyword in self.select_keywords(keywords, max_queries):
            query = f'{keyword} created:>{since} stars:>{min_stars}'
            self.record_attempt()
            try:
                payload = self._get(
                    "/search/repositories",
                    params={"q": query, "sort": "stars", "order": "desc", "per_page": per_page},
                )
                if not isinstance(payload, dict) or not isinstance(payload.get("items", []), list):
                    raise ValueError("Invalid repository response")
            except (requests.RequestException, GitHubRateLimitError, ValueError) as exc:
                self.record_failure(f"emerging:{keyword}", exc,
                                    "rate_limit" if isinstance(exc, GitHubRateLimitError) else "network")
                self.logger.warning("GitHub emerging 搜索 '%s' 失败: %s", keyword, type(exc).__name__)
                continue
            self.record_success()
            for repo in payload.get("items", []):
                items.append(self._repo_to_item(repo, watched=False, note=f"emerging:{keyword}"))
            time.sleep(2)
        return items

    # ------------------------------------------------------------------ utils
    def _repo_to_item(self, repo: dict[str, Any], watched: bool, note: str) -> TechItem:
        """搜索 / emerging 结果仓库 -> TechItem。"""
        full_name = repo.get("full_name", "")
        return TechItem(
            source_type=self.source_type,
            source_name=full_name.split("/")[-1] if full_name else "github",
            title=full_name,
            url=repo.get("html_url", f"https://github.com/{full_name}"),
            published_at=self._parse_time(repo.get("pushed_at") or repo.get("created_at")),
            abstract=(repo.get("description") or "")[:2000],
            tags=["github", "repository"],
            metadata={
                "repo": full_name,
                "watched": watched,
                "stars": repo.get("stargazers_count", 0),
                "forks": repo.get("forks_count", 0),
                "note": note,
            },
        )

    @staticmethod
    def _parse_time(value: str | None):
        from techradar.utils.time import parse_datetime

        return parse_datetime(value)
