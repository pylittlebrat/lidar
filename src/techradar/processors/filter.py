"""KeywordFilter：按 config/keywords.yaml 过滤条目，控制后续 LLM 调用量。

规则：
1. 命中 negative_terms 的条目直接丢弃（domain 无关噪音）。
2. metadata.watched=True 的条目（github watch 仓库）免关键词匹配。
3. 标题/摘要中 priority_terms 命中数 >= min_priority_matches 才保留。
"""

from __future__ import annotations

from typing import Any

from techradar.config import Config
from techradar.storage.models import TechItem
from techradar.utils.text import count_matches, filter_matches, match_term


class KeywordFilter:
    """关键词过滤器（词表全部来自配置，不 hardcode）。"""

    def __init__(self, config: Config) -> None:
        kw = config.keywords or {}
        self.case_sensitive = bool(kw.get("case_sensitive", False))
        policy = kw.get("filter", {}) if isinstance(kw.get("filter"), dict) else {}
        self.min_matches = int(policy.get("min_priority_matches", 1))
        self.drop_negative = bool(policy.get("drop_negative", True))
        self.watched_always_keep = bool(policy.get("watched_always_keep", True))
        self.priority_terms = self._normalize(kw.get("priority_terms", []))
        self.negative_terms = self._normalize(kw.get("negative_terms", []))

    def _normalize(self, terms: Any) -> list[str]:
        if not isinstance(terms, list):
            return []
        return [
            (term if self.case_sensitive else term.lower())
            for term in terms
            if isinstance(term, str) and term.strip()
        ]

    def _text(self, item: TechItem) -> str:
        text = " ".join((item.title or "", item.abstract or "", item.raw_text or ""))
        return text if self.case_sensitive else text.lower()

    def is_relevant(self, item: TechItem) -> bool:
        """判断条目是否值得保留（词边界匹配，避免短词子串误伤）。"""
        text = self._text(item)
        if self.drop_negative and any(match_term(term, text) for term in self.negative_terms):
            return False
        if self.watched_always_keep and item.metadata.get("watched") is True:
            return True
        return count_matches(self.priority_terms, text) >= self.min_matches

    def apply(self, items: list[TechItem]) -> tuple[list[TechItem], list[TechItem]]:
        """返回 (kept, dropped)。"""
        kept: list[TechItem] = []
        dropped: list[TechItem] = []
        for item in items:
            (kept if self.is_relevant(item) else dropped).append(item)
        return kept, dropped