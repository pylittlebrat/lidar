"""采集器抽象基类。

Phase 2 的具体采集器（GitHub / arXiv / RSS / Web / Release）都必须继承
BaseCollector 并实现 fetch()，返回统一的 TechItem 列表。
"""

from __future__ import annotations

import abc
from datetime import date, datetime, timedelta, timezone
from typing import Any

from techradar.storage.models import TechItem
from techradar.utils.logging import get_logger
from techradar.utils.time import utcnow


def scheduled_keywords(
    keywords: list[str], limit: int, priority_count: int = 2,
    as_of: date | datetime | None = None,
) -> list[str]:
    """Keep priority directions daily and rotate the rest by Beijing date."""
    keywords = list(dict.fromkeys(keywords))
    limit = max(0, int(limit))
    if len(keywords) <= limit:
        return keywords
    if limit == 0:
        return []
    # Reserve one query for rotation even with a small request budget.
    pinned = min(max(0, priority_count), limit - 1)
    remaining = keywords[pinned:]
    slots = limit - pinned
    current = as_of if as_of is not None else utcnow()
    if isinstance(current, datetime):
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current = current.astimezone(timezone(timedelta(hours=8))).date()
    offset = current.toordinal() * slots % len(remaining)
    return keywords[:pinned] + [remaining[(offset + i) % len(remaining)] for i in range(slots)]


class BaseCollector(abc.ABC):
    """采集器基类：约定 source_type / source_name 与 fetch()/collect() 契约。"""

    source_type: str = "base"
    source_name: str = "Base"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = config or {}
        self.logger = get_logger(f"techradar.collector.{self.source_type}")
        self.reset_result()

    def reset_result(self) -> None:
        self.last_result: dict[str, Any] = {
            "attempted": 0, "succeeded": 0, "failures": [], "status": "success",
        }

    def record_attempt(self) -> None:
        self.last_result["attempted"] += 1

    def record_success(self) -> None:
        self.last_result["succeeded"] += 1
        self._update_status()

    def record_failure(self, resource: str, error: Exception | str, kind: str = "network") -> None:
        # Provider exception strings can contain credential-bearing URLs.
        message = type(error).__name__ if isinstance(error, Exception) else error
        self.last_result["failures"].append({"resource": resource, "error": message, "kind": kind})
        self._update_status()

    def _update_status(self) -> None:
        self.last_result["status"] = (
            "partial" if self.last_result["failures"] and self.last_result["succeeded"]
            else "failed" if self.last_result["failures"] else "success"
        )

    def select_keywords(self, keywords: list[str], limit: int, as_of: date | datetime | None = None) -> list[str]:
        limits = self.config.get("limits", {})
        return scheduled_keywords(keywords, limit, int(limits.get("priority_queries", 2)), as_of)

    @abc.abstractmethod
    def fetch(self) -> list[TechItem]:
        """从信息源抓取并返回 TechItem 列表（Phase 2 由具体采集器实现）。"""

    def collect(self) -> list[TechItem]:
        """采集入口：拉取原始条目并记录日志（后续接入处理流水线）。"""
        self.reset_result()
        try:
            items = self.fetch()
        except Exception as exc:
            if not self.last_result["failures"]:
                if not self.last_result["attempted"]:
                    self.record_attempt()
                self.record_failure(self.source_name, exc)
            raise
        if not self.last_result["attempted"] and items:
            # Third-party collectors implementing only fetch() stay compatible.
            self.record_attempt()
            self.record_success()
        self.logger.info("collected %d items from %s", len(items), self.source_name)
        return items
