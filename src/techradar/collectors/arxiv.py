"""arXiv 采集器：按分类 + 关键词组合查询 arXiv API（Atom feed）。

注意：arXiv 边缘（WAF）对某些请求间歇性返回 406，因此查询带指数退避重试。
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import timedelta
from typing import Any

import requests

from techradar.collectors.base import BaseCollector
from techradar.storage.models import TechItem
from techradar.utils.retry import retry
from techradar.utils.time import parse_datetime, utcnow

_ARXIV_API = "https://export.arxiv.org/api/query"
_NS = {"atom": "http://www.w3.org/2005/Atom"}

# arXiv 间歇性拒绝的 HTTP 状态码（可安全重试）
_RETRYABLE_STATUS = (406, 429, 500, 502, 503, 504)


class ArxivRetryableError(RuntimeError):
    """arXiv 返回了可重试的错误状态码。"""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ArxivCollector(BaseCollector):
    """基于 arXiv API 的论文采集器。"""

    source_type = "arxiv"
    source_name = "arXiv"

    def fetch(self) -> list[TechItem]:
        self.reset_result()
        config = self.config if isinstance(self.config, dict) else {}
        categories = config.get("categories", ["cs.RO", "cs.AI", "cs.CV", "cs.LG"])
        keywords = config.get("search_keywords", [])
        limits = config.get("limits", {}) if isinstance(config.get("limits"), dict) else {}
        max_queries = int(limits.get("max_queries", 5))
        max_results = int(limits.get("max_results", 15))
        sleep_seconds = float(limits.get("sleep_seconds", 3))
        timeout = float(limits.get("request_timeout_seconds", 30))
        # 历史回填模式：跨时间段分页采样（如近两年不同月份的论文）
        history_days = int(limits.get("history_days", 0))
        history_pages = int(limits.get("history_pages", 4))

        cat_expr = " OR ".join(f"cat:{c}" for c in categories)
        items: list[TechItem] = []
        for keyword in self.select_keywords(keywords, max_queries):
            query = f'({cat_expr}) AND (all:"{keyword}")'
            if history_days > 0:
                page_size = max(1, max_results)
                for page in range(max(1, history_pages)):
                    params = {
                        "search_query": query,
                        "start": page * page_size,
                        "max_results": page_size,
                        "sortBy": "submittedDate",
                        "sortOrder": "descending",
                    }
                    self.record_attempt()
                    try:
                        xml_text = self._query(params, timeout)
                    except (ArxivRetryableError, requests.RequestException) as exc:
                        self.record_failure(f"query:{keyword}:page:{page}", exc,
                                            "rate_limit" if getattr(exc, "status_code", None) == 429 else "network")
                        self.logger.warning("arXiv 历史查询 '%s'(p%d) 失败（已重试）: %s",
                                            keyword, page, type(exc).__name__)
                        break
                    page_items = self._parse_feed(xml_text, keyword)
                    if not self._feed_valid:
                        items.extend(i for i in page_items if i.published_at and
                                     i.published_at >= utcnow() - timedelta(days=history_days))
                        break
                    self.record_success()
                    if not page_items:
                        break
                    # 该页全部早于窗口则停止向后翻页
                    earliest = min(
                        (i.published_at for i in page_items if i.published_at),
                        default=None,
                    )
                    time.sleep(sleep_seconds)
                    if history_days > 0 and earliest is not None:
                        if earliest < utcnow() - timedelta(days=history_days):
                            items.extend(i for i in page_items if i.published_at and
                                         i.published_at >= utcnow() - timedelta(days=history_days))
                            break
                    items.extend(page_items)
            else:
                params = {
                    "search_query": query,
                    "start": 0,
                    "max_results": max_results,
                    "sortBy": "submittedDate",
                    "sortOrder": "descending",
                }
                self.record_attempt()
                try:
                    xml_text = self._query(params, timeout)
                except (ArxivRetryableError, requests.RequestException) as exc:
                    self.record_failure(f"query:{keyword}", exc,
                                        "rate_limit" if getattr(exc, "status_code", None) == 429 else "network")
                    self.logger.warning("arXiv 查询 '%s' 失败（已重试）: %s", keyword, type(exc).__name__)
                    continue
                parsed = self._parse_feed(xml_text, keyword)
                items.extend(parsed)
                if self._feed_valid:
                    self.record_success()
                time.sleep(sleep_seconds)
        return items

    @retry(
        max_attempts=4,
        base_delay=0.5,
        max_delay=5.0,
        exceptions=(ArxivRetryableError, requests.RequestException),
    )
    def _query(self, params: dict[str, Any], timeout: float) -> str:
        """查询 arXiv API，返回 Atom XML 文本；间歇性状态码自动重试。"""
        resp = requests.get(
            _ARXIV_API,
            params=params,
            timeout=timeout,
            headers={
                "User-Agent": "simulation-tech-radar/0.1",
                # arXiv API 对非 application/atom+xml 的 Accept 返回 406
                "Accept": "application/atom+xml",
            },
        )
        if resp.status_code in _RETRYABLE_STATUS:
            raise ArxivRetryableError(f"arXiv 返回 {resp.status_code}", resp.status_code)
        resp.raise_for_status()
        return resp.text

    def _parse_feed(self, xml_text: str, query: str) -> list[TechItem]:
        """解析 arXiv Atom feed -> TechItem 列表。"""
        items: list[TechItem] = []
        self._feed_valid = False
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            self.record_failure(f"query:{query}", exc, "parse")
            self.logger.warning("arXiv feed 解析失败: %s", exc)
            return items
        if root.tag != "{http://www.w3.org/2005/Atom}feed":
            self.record_failure(f"query:{query}", "Invalid Atom feed", "parse")
            return items
        self._feed_valid = True
        for entry in root.findall("atom:entry", _NS):
            url = self._text(entry, "atom:id")
            title = " ".join((self._text(entry, "atom:title") or "").split())
            if not url or url.startswith("http://arxiv.org/api/errors") or url.startswith("https://arxiv.org/api/errors"):
                self._feed_valid = False
                self.record_failure(f"query:{query}", "arXiv returned an API error entry", "parse")
                continue
            summary = " ".join((self._text(entry, "atom:summary") or "").split())
            authors = [
                self._text(a, "atom:name")
                for a in entry.findall("atom:author", _NS)
                if self._text(a, "atom:name")
            ]
            published = parse_datetime(self._text(entry, "atom:published"))
            arxiv_id = url.rsplit("/", 1)[-1] if url else ""
            items.append(
                TechItem(
                    source_type=self.source_type,
                    source_name="arXiv",
                    title=title,
                    url=url or f"https://arxiv.org/abs/{arxiv_id}",
                    authors=authors,
                    published_at=published,
                    abstract=summary,
                    tags=["arxiv", "paper"],
                    metadata={"arxiv_id": arxiv_id, "query": query},
                )
            )
        return items

    @staticmethod
    def _text(element: ET.Element, path: str) -> str:
        node = element.find(path, _NS)
        return (node.text or "").strip() if node is not None else ""
