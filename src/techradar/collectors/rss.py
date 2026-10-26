"""RSS 采集器：官方技术博客（NVIDIA / DeepMind / Unity ...）等 feed。"""

from __future__ import annotations

import datetime as dt
from typing import Any

import feedparser
import requests

from techradar.collectors.base import BaseCollector
from techradar.storage.models import TechItem


class RSSCollector(BaseCollector):
    """基于 feedparser 的 RSS/Atom 采集器。单个 feed 失败只记日志。"""

    source_type = "rss"
    source_name = "RSS"

    def fetch(self) -> list[TechItem]:
        self.reset_result()
        config = self.config if isinstance(self.config, dict) else {}
        feeds = config.get("feeds", [])
        timeout = float(config.get("timeout_seconds", 15))
        max_entries = int(config.get("max_entries_per_feed", 10))
        items: list[TechItem] = []
        for feed in feeds:
            name = feed.get("name", "unknown feed")
            url = feed.get("url", "")
            self.record_attempt()
            try:
                resp = requests.get(url, timeout=timeout, headers={"User-Agent": "simulation-tech-radar/0.1"})
                resp.raise_for_status()
                parsed = feedparser.parse(resp.content)
            except requests.RequestException as exc:
                self.record_failure(name, exc)
                self.logger.warning("RSS feed '%s' 抓取失败: %s", name, type(exc).__name__)
                continue
            if parsed.bozo and not parsed.entries:
                self.record_failure(name, parsed.bozo_exception, "parse")
                self.logger.warning("RSS feed '%s' 解析异常", name)
                continue
            self.record_success()
            if parsed.bozo:
                self.record_failure(name, parsed.bozo_exception, "parse")
            for entry in parsed.entries[:max_entries]:
                published = self._published_at(entry)
                item = TechItem(
                    source_type=self.source_type,
                    source_name=name,
                    title=(entry.get("title") or "").strip(),
                    url=(entry.get("link") or "").strip(),
                    published_at=published,
                    abstract=(entry.get("summary") or entry.get("description") or "")[:4000],
                    raw_text="\n".join(content.get("value", "") for content in entry.get("content", [])),
                    tags=["rss", "blog"],
                    metadata={"feed_name": name, "feed_url": url},
                )
                if item.title or item.url:
                    items.append(item)
        return items

    @staticmethod
    def _published_at(entry: Any):
        """entry.published_parsed / updated_parsed -> datetime（UTC）。"""
        from techradar.utils.time import parse_datetime

        for key in ("published_parsed", "updated_parsed"):
            value = entry.get(key)
            if value:
                try:
                    return dt.datetime(*value[:6], tzinfo=dt.timezone.utc)
                except (TypeError, ValueError):
                    continue
        return parse_datetime(entry.get("published") or entry.get("updated"))
