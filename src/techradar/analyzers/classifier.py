"""Classifier：基于配置的零成本预分类与关键词命中统计。

与 LLM 分析配合：先做廉价分类（taxonomy）与优先级词命中统计
（成本控制与关键词得分用），减少不必要的 LLM 调用（方案第 31 节）。
"""

from __future__ import annotations

from techradar.config import Config
from techradar.storage.models import TechItem
from techradar.utils.text import count_matches, match_term


class Classifier:
    """taxonomy 关键词分类器（无 LLM）。"""

    def __init__(self, config: Config) -> None:
        self.categories = config.categories()
        kw = config.keywords or {}
        case_sensitive = bool(kw.get("case_sensitive", False))
        self.priority_terms = [
            term if case_sensitive else term.lower()
            for term in kw.get("priority_terms", [])
            if isinstance(term, str) and term.strip()
        ]

    def classify(self, item: TechItem) -> list[str]:
        """返回命中的一级分类名（按命中关键词数降序，词边界匹配）。"""
        text = self._text(item)
        hits: dict[str, int] = {}
        for category, terms in self.categories:
            count = sum(1 for term in terms if match_term(term, text))
            if count:
                hits[category] = count
        return sorted(hits, key=hits.get, reverse=True)

    def keyword_hits(self, item: TechItem) -> int:
        """命中 priority_terms 的数量（决定是否调用 LLM 与关键词得分，词边界匹配）。"""
        text = self._text(item)
        return count_matches(self.priority_terms, text)

    def _text(self, item: TechItem) -> str:
        return " ".join((item.title or "", item.abstract or "", item.raw_text or "")).lower()