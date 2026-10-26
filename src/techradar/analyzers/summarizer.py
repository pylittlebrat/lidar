"""Summarizer：基于 LLM 的结构化分析（方案第 13/14 节）。

每个条目生成 summary/problem/solution/innovation/.../relevance 等字段，
并做强制类型化，保证下游（评分、日报）安全使用。
"""

from __future__ import annotations

from typing import Any

from techradar.config import Config
from techradar.llm.client import LLMClient, LLMError, LLMResponseError
from techradar.llm.prompts import build_analysis_system, build_analysis_user, content_evidence
from techradar.llm.schemas import AnalysisValidationError, PROMPT_VERSION, coerce_analysis
from techradar.storage.models import TechItem
from techradar.utils.logging import get_logger


class Summarizer:
    """LLM 结构化分析器。"""

    def __init__(self, client: LLMClient, config: Config) -> None:
        self.client = client
        self.config = config
        self.logger = get_logger("techradar.analyzer.summarizer")

    def analyze(self, item: TechItem) -> dict[str, Any]:
        """分析单个条目，返回规范化后的分析 dict（含 _usage）。

        :raises LLMError: LLM 调用彻底失败时（已尽力重试 / 关闭 json_mode 重试）。
        """
        system = build_analysis_system(self.config)
        user = build_analysis_user(item, abstract_limit=int(
            self.config.llm.get("split_abstract_chars", 4000)
        ))
        def validate(raw: dict[str, Any]) -> dict[str, Any]:
            """严格校验，但容忍“summary 缺失”等常见输出抖动：
            只要还有实质内容（problem/solution/innovation 等），用其它字段兜底
            summary 后仍算一次成功分析；完全空壳才判失败。
            """
            from techradar.llm.schemas import AnalysisValidationError, coerce_analysis

            try:
                return coerce_analysis(
                    raw,
                    categories=[name for name, _terms in self.config.categories()],
                    projects=self.config.projects.get("projects", {}).keys(),
                    category_aliases=self.config.taxonomy.get("aliases", {}),
                    strict=True,
                )
            except AnalysisValidationError:
                fallback = coerce_analysis(
                    raw,
                    categories=[name for name, _terms in self.config.categories()],
                    projects=self.config.projects.get("projects", {}).keys(),
                    category_aliases=self.config.taxonomy.get("aliases", {}),
                    strict=False,
                )
                has_content = bool(
                    fallback.get("summary")
                    or fallback.get("problem")
                    or fallback.get("solution")
                    or fallback.get("innovation")
                )
                if not has_content:
                    raise
                if not fallback.get("summary"):
                    fallback["summary"] = (
                        fallback.get("problem")
                        or fallback.get("solution")
                        or str(fallback.get("innovation", [""])[0])
                    )[:240]
                    fallback["validation_warnings"] = fallback.get("validation_warnings", []) + [
                        "summary 缺失，已用其他字段兜底"
                    ]
                return fallback

        try:
            raw = self.client.chat_json(system, user)
            analysis = validate(raw)
        except (LLMResponseError, AnalysisValidationError) as first_err:
            self.logger.warning("结构化输出不兼容（%s），改用普通模式重试", type(first_err).__name__)
            try:
                raw = self.client.chat_json(system, user, json_mode=False)
                analysis = validate(raw)
            except (LLMError, AnalysisValidationError) as second_err:
                if isinstance(second_err, (LLMResponseError, AnalysisValidationError)):
                    raise LLMResponseError("LLM 分析输出未通过结构校验") from second_err
                raise LLMError("LLM 分析服务请求失败") from second_err
        evidence = content_evidence(item, min_chars=int(self.config.llm.get("min_evidence_chars", 120)))
        if evidence["quality"] != "available":
            analysis["maturity"] = "unknown"
            analysis["open_source"] = None
            analysis["recommended_action"] = "WATCH"
            analysis["innovation"] = []
            analysis["technical_details"] = []
            analysis["comparison"] = ""
            analysis["summary"] += f"\n\n{evidence['note']}"
        analysis["evidence"] = evidence
        analysis["model"] = getattr(self.client, "model", str(self.config.llm.get("model", "")))
        analysis["prompt_version"] = PROMPT_VERSION
        usage = raw.get("_usage", {})
        if usage:
            analysis["_usage"] = usage
        return analysis
