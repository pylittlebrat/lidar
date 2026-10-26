"""采集器注册表：source_type -> 采集器类，按配置逐个构建。"""

from __future__ import annotations

from typing import Sequence

from techradar.collectors.arxiv import ArxivCollector
from techradar.collectors.base import BaseCollector
from techradar.collectors.github import GitHubCollector
from techradar.collectors.rss import RSSCollector
from techradar.config import Config
from techradar.storage.models import TechItem

COLLECTORS: dict[str, type[BaseCollector]] = {
    "github": GitHubCollector,
    "arxiv": ArxivCollector,
    "rss": RSSCollector,
}


class UnavailableCollector(BaseCollector):
    """Expose an enabled but unimplemented source as a failed collection."""

    def __init__(self, source_type: str, config: dict) -> None:
        self.source_type = source_type
        self.source_name = source_type
        super().__init__(config)

    def fetch(self) -> list[TechItem]:
        self.reset_result()
        self.record_attempt()
        self.record_failure(self.source_type, "Collector is not implemented", "unavailable")
        return []


def collector_availability(config: Config, sources: Sequence[str] | None = None) -> dict[str, dict]:
    """Report effective capability instead of treating enabled config as support."""
    collections = config.sources.get("collections", {})
    result = {}
    for source_type in dict.fromkeys([*COLLECTORS, *collections]):
        if sources is not None and source_type not in sources:
            continue
        section = collections.get(source_type, {})
        enabled = bool(section.get("enabled", source_type in COLLECTORS))
        supported = source_type in COLLECTORS
        entry = {"enabled": enabled, "supported": supported,
                 "status": "disabled" if not enabled else "available" if supported else "unavailable"}
        if source_type == "github":
            entry["supported_events"] = ["release", "repository"]
            entry["unsupported_events"] = [event for event in section.get("events", [])
                                           if event not in entry["supported_events"]]
        result[source_type] = entry
    return result


def build_collectors(
    config: Config,
    sources: Sequence[str] | None = None,
) -> list[BaseCollector]:
    """按 sources.yaml 的 enabled 标记构建采集器。

    :param config: 项目配置（含 sources）
    :param sources: 要构建的 source_type 白名单（None = 全部启用项）
    """
    collections = config.sources.get("collections", {})
    built: list[BaseCollector] = []
    for source_type, availability in collector_availability(config, sources).items():
        if sources is not None and source_type not in sources:
            continue
        section = collections.get(source_type, {})
        if not availability["enabled"]:
            continue
        if availability["supported"]:
            built.append(COLLECTORS[source_type](config=section))
        else:
            built.append(UnavailableCollector(source_type, section))
    return built
