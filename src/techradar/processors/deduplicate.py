"""Deduplicator：URL/content_hash + 标题相似度去重（跨来源、跨天）。

第一阶段策略：
1. content_hash（source_type + url）精确去重 —— 数据库层有唯一约束兜底。
2. 标题相似度（difflib）> threshold 视为同一事件的不同报道。
3. 同时与数据库已有 hash / 近 90 天标题比较，避免跨天重复。
V2 将升级为 embedding 余弦相似度（阈值见 config/scoring.yaml）。
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from techradar.config import Config
from techradar.storage.models import TechItem

_ALNUM_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")


def normalized_title(title: str) -> str:
    """小写 + 仅保留字母数字与中文，用于相似度比较。"""
    return _ALNUM_RE.sub(" ", (title or "").lower()).strip()


def title_similarity(a: str, b: str) -> float:
    """0~1 标题相似度。"""
    return SequenceMatcher(None, normalized_title(a), normalized_title(b)).ratio()


class Deduplicator:
    """去重器（第一阶段：hash + 跨来源标题相似度）。

    标题相似度判重仅在**来源不同**时生效（同一事件的 GitHub Release / Blog / 新闻
    视为重复报道）；同一来源内的版本序列（如某仓库 v1.0 / v1.1 / v2.0）只做精确
    content_hash 去重，避免回填历史时互相误伤。
    """

    def __init__(self, config: Config) -> None:
        novelty = config.scoring.get("novelty", {})
        self.threshold = float(novelty.get("duplicate_threshold", 0.85))

    @staticmethod
    def _title_of(entry: str | tuple[str, str]) -> str:
        """兼容既有调用：existing_titles 元素可为标题字符串或 (source_type, title)。"""
        return entry[1] if isinstance(entry, tuple) else entry

    def deduplicate(
        self,
        items: list[TechItem],
        existing_hashes: set[str] | None = None,
        existing_titles: list[str | tuple[str, str]] | None = None,
    ) -> tuple[list[TechItem], int]:
        """去重后返回 (保留条目, 重复数)。

        :param existing_hashes: 数据库中已有的 content_hash 集合
        :param existing_titles: 数据库中近期标题列表（元素可为 str 或 (source_type, title)）
        """
        seen_hashes: set[str] = set(existing_hashes or ())
        seen_titles: list[tuple[str, str]] = [
            (entry[0] if isinstance(entry, tuple) else "", self._norm(self._title_of(entry)))
            for entry in (existing_titles or [])
        ]
        kept: list[TechItem] = []
        duplicates = 0
        for item in items:
            content_hash = item.content_hash()
            if content_hash in seen_hashes:
                duplicates += 1
                continue
            title = self._norm(item.title)
            # 只与不同来源的历史标题比较（同来源版本序列不判重）
            if any(
                source != item.source_type and title_similarity(title, other) > self.threshold
                for source, other in seen_titles
            ):
                duplicates += 1
                continue
            seen_hashes.add(content_hash)
            seen_titles.append((item.source_type, title))
            kept.append(item)
        return kept, duplicates

    @staticmethod
    def _norm(title: str) -> str:
        return normalized_title(title)