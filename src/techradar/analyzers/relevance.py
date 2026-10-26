"""RelevanceEvaluator：内部项目相关度评分（方案第 16 节）。

FinalScore = w_k*Keyword + w_s*Source + w_l*LLMRelevance + w_n*Novelty
权重来自 config/scoring.yaml；缺失分量时自动按剩余权重归一化。
分数含义（0-100）：>=90 强烈关注 / >=75 重点关注 / >=60 值得阅读 / >=40 普通 / <40 不进日报。

价值排序（scoring.yaml routine_update）：
大型框架（Isaac Lab / MuJoCo 等）的例行小版本 release 会被识别并降权，
让具体开源工作 / 论文 / 新代码仓库占据雷达重点。
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from techradar.config import Config
from techradar.processors.deduplicate import title_similarity
from techradar.storage.models import TechItem


class RelevanceEvaluator:
    """四维加权相关度评分器。"""

    def __init__(self, config: Config) -> None:
        scoring = config.scoring or {}
        self.weights = {
            "keyword_score": float(scoring.get("weights", {}).get("keyword_score", 0.25)),
            "source_score": float(scoring.get("weights", {}).get("source_score", 0.25)),
            "llm_relevance": float(scoring.get("weights", {}).get("llm_relevance", 0.30)),
            "novelty_score": float(scoring.get("weights", {}).get("novelty_score", 0.20)),
        }
        self.source_weights = scoring.get("source_weights", {})
        self.keyword_bands = {
            int(str(key)): int(value)
            for key, value in (scoring.get("keyword_bands", {}) or {}).items()
        }
        routine = scoring.get("routine_update", {}) or {}
        self.routine_enabled = bool(routine.get("enabled", True))
        self.routine_penalty = int(routine.get("source_score_penalty", 50))
        self.routine_threshold = float(routine.get("title_similarity_against_history", 0.8))
        self.version_pattern = str(routine.get("version_pattern", r"\d+\.\d+"))
        self.major_change_exempt = bool(routine.get("major_change_exempt", True))
        self.milestone_markers = [
            re.compile(rf"\b{re.escape(str(m))}\b", re.IGNORECASE)
            for m in routine.get("milestone_markers", [])
        ]

    def keyword_score(self, hits: int) -> int:
        """命中优先级词数 -> 关键词得分（见 scoring.yaml keyword_bands）。"""
        return self.keyword_bands.get(min(hits, max(self.keyword_bands, default=0)), 0)

    @staticmethod
    def _repo(item: TechItem) -> str:
        repo = str(item.metadata.get("repo") or "").strip().lower()
        if repo:
            return repo
        parsed = urlparse(item.url)
        if parsed.hostname and parsed.hostname.lower() == "github.com":
            parts = parsed.path.strip("/").split("/")
            if len(parts) >= 2:
                return "/".join(parts[:2]).lower()
        return f"{item.source_type}:{item.source_name}".lower()

    @classmethod
    def _titles(cls, existing_titles: list | None, item: TechItem) -> list[str]:
        """同仓库历史用于版本判断；保留字符串历史的兼容入口。"""
        titles: list[str] = []
        for entry in existing_titles or []:
            if isinstance(entry, TechItem):
                if entry.id == item.id or cls._repo(entry) != cls._repo(item):
                    continue
                title = entry.title
            elif isinstance(entry, tuple):
                if entry[0] and entry[0] != item.source_type:
                    continue
                title = entry[1]
            else:
                title = entry
            if isinstance(title, str) and title.strip():
                titles.append(title)
        return titles

    _VERSION_RE = re.compile(r"(?:^|[\s/_-])v?(\d+)\.(\d+)(?:\.(\d+))?")

    @classmethod
    def _version(cls, title: str) -> tuple[int, int, int] | None:
        """从标题提取 (major, minor, patch)；无版本号返回 None。"""
        match = cls._VERSION_RE.search(title or "")
        if not match:
            return None
        return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))

    def is_routine_update(self, item: TechItem, existing_titles: list | None = None) -> bool:
        """判断是否为大型框架的例行版本更新（watch 仓库 + 版本号 + 与同源历史标题高度相似）。

        豁免（视为重大发布，不降权）：
        - 主版本变化（如 v2.x -> v3.x，历史中没出现过该主版本）；
        - 标题含里程碑标记（EA / RC / Preview / Early Access）。
        例行更新（如 MuJoCo 3.13 -> 3.14、IsaacLab 补丁版）降权，避免挤占雷达重点。
        """
        if not self.routine_enabled:
            return False
        if item.metadata.get("watched") is not True:
            return False
        current = self._version(item.title or "")
        if current is None:
            return False
        titles = self._titles(existing_titles, item)
        if not titles:
            return False
        if any(marker.search(item.title or "") for marker in self.milestone_markers):
            return False  # EA / RC / Preview：重要发布
        if self.major_change_exempt:
            historical_majors = {
                version[0]
                for title in titles
                if (version := self._version(title)) is not None
            }
            if current[0] not in historical_majors:
                return False  # 新主版本线：不判例行
        return any(
            title_similarity(item.title, title) > self.routine_threshold
            for title in titles
        )

    def source_score(self, item: TechItem, existing_titles: list | None = None) -> int:
        """来源权威性得分；watch 仓库满分，例行版本更新降权。"""
        if self.is_routine_update(item, existing_titles):
            return self.routine_penalty
        if item.metadata.get("watched") is True:
            return 100
        return int(
            self.source_weights.get(item.source_type, self.source_weights.get("default", 40))
        )

    def evaluate(
        self,
        item: TechItem,
        keyword_hits: int,
        llm_relevance: int | None = None,
        novelty: int | None = None,
        existing_titles: list | None = None,
    ) -> dict[str, Any]:
        """计算最终得分（0-100）与分量明细。

        :param existing_titles: 库中历史标题（str 或 (source_type, title)），
            用于识别例行版本更新降权。
        """
        components = {
            "keyword_score": self.keyword_score(keyword_hits),
            "source_score": self.source_score(item, existing_titles),
            "llm_relevance": llm_relevance,
            "novelty_score": novelty,
        }
        available = {key: value for key, value in components.items() if value is not None}
        weight_sum = sum(self.weights[key] for key in available)
        if not available or weight_sum <= 0:
            return {"score": 0, "breakdown": {}, "weights": self.weights}
        final = round(
            sum(self.weights[key] * value for key, value in available.items()) / weight_sum
        )
        return {"score": max(0, min(100, final)), "breakdown": available, "weights": self.weights}
