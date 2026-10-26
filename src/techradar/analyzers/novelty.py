"""NoveltyScorer：与历史条目的相似度判断新颖度（方案第 17 节）。

V1 阶段使用标题相似度（difflib），V2 升级为 embedding 余弦相似度。
规则：与近 90 天已有标题的最大相似度 sim 越高越不新颖；
score = 100 * (1 - sim)。sim < novel_threshold 视为新技术方向。
"""

from __future__ import annotations

from techradar.config import Config
from techradar.processors.deduplicate import title_similarity
from techradar.storage.models import TechItem


class NoveltyScorer:
    """新颖度评分器（0-100，100 = 全新）。"""

    def __init__(self, config: Config) -> None:
        novelty = config.scoring.get("novelty", {})
        self.novel_threshold = float(novelty.get("novel_threshold", 0.65))

    @staticmethod
    def _titles(existing_titles: list, item: TechItem | None = None) -> list[str]:
        """读取历史条目，并在拥有身份信息时排除当前条目。"""
        titles: list[str] = []
        for entry in existing_titles:
            if isinstance(entry, TechItem):
                if item is not None and entry.id == item.id:
                    continue
                title = entry.title
            elif isinstance(entry, tuple):
                title = entry[1]
            else:
                title = entry
            if isinstance(title, str) and title.strip():
                titles.append(title)
        return titles

    def score(self, item: TechItem, existing_titles: list) -> int:
        """对条目打分。existing_titles 为库中近期标题列表（str 或 (source, title)）。"""
        titles = self._titles(existing_titles, item)
        if not titles:
            return 100
        similarity = max(title_similarity(item.title, title) for title in titles)
        return round(100 * max(0.0, min(1.0, 1.0 - similarity)))

    def is_novel(self, item: TechItem, existing_titles: list) -> bool:
        """相似度低于 novel_threshold 视为新技术方向。"""
        titles = self._titles(existing_titles, item)
        if not titles:
            return True
        similarity = max(title_similarity(item.title, title) for title in titles)
        return similarity < self.novel_threshold
