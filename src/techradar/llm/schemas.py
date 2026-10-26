"""LLM 结构化输出 Schema（方案第 13 节）。

定义分析输出字段、Prompt 用的字段说明、以及结果强制类型化（coerce）。
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

PROMPT_VERSION = "analysis-v2"
MATURITY_VALUES = {"unknown", "idea", "experimental", "release", "stable"}
ACTION_VALUES = {"WATCH", "INVESTIGATE", "POC", "ADOPT", "ARCHIVE"}


class AnalysisValidationError(ValueError):
    """模型输出缺少可用内容，不能作为一次成功分析。"""


def normalized_key(value: str) -> str:
    return re.sub(r"[\s_-]+", "_", value.strip().lower())


def canonical_keys(values: Iterable[str], allowed: Iterable[str],
                   aliases: dict[str, str] | None = None) -> list[str]:
    """把显示名称归一到配置 ID，未知键不进入统计与筛选。"""
    mapping = {normalized_key(key): key for key in allowed}
    for alias, target in (aliases or {}).items():
        if target in mapping.values():
            mapping[normalized_key(alias)] = target
    return list(dict.fromkeys(
        mapping[normalized_key(value)] for value in values
        if isinstance(value, str) and normalized_key(value) in mapping
    ))

# (字段名, 类型, 说明)
ANALYSIS_FIELDS: list[tuple[str, str, str]] = [
    ("summary", "str", "简短中文摘要（2-4 句）"),
    ("problem", "str", "该技术解决什么问题"),
    ("solution", "str", "核心技术路线 / 方案"),
    ("innovation", "list", "资料明确支持的创新点列表；资料不足时为空列表，不要凑数量"),
    ("technical_details", "list", "关键技术细节列表"),
    ("comparison", "str", "与已有方案相比的变化（若无则空字符串）"),
    ("open_source", "bool", "是否开源（true/false）；资料未说明时为 null"),
    ("maturity", "str", "技术成熟度，取值为 unknown/idea/experimental/release/stable 之一"),
    ("categories", "list", "所属技术分类（从给定分类中选择，1-3 个）"),
    ("keywords", "list", "3-8 个关键词"),
    ("related_projects", "list", "相关已知项目名（如 Isaac Lab、MuJoCo，可为空列表）"),
    ("platform_relevance", "str", "对机器人仿真平台的价值（3-5 句）"),
    ("recommended_action", "str", "建议动作，取值为 WATCH/INVESTIGATE/POC/ADOPT/ARCHIVE 之一"),
    ("overall_relevance", "int", "对内部项目的整体相关度，0-100 整数"),
    ("relevance", "dict", "各内部项目相关度 0-5 分映射（键见下）"),
]


def analysis_field_spec() -> str:
    """生成 Prompt 中使用的 JSON 字段说明。"""
    lines = ["{"]
    for name, _typ, desc in ANALYSIS_FIELDS:
        lines.append(f'  "{name}": <{desc}>,')
    lines.append("}")
    return "\n".join(lines)


def coerce_analysis(
    raw: dict[str, Any] | None,
    *,
    categories: Iterable[str] | None = None,
    projects: Iterable[str] | None = None,
    category_aliases: dict[str, str] | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """兼容常见类型差异，并约束分类、项目键及枚举。

    strict 用于真实模型结果；默认模式保留规则评分与旧数据入口。
    """
    if raw is not None and not isinstance(raw, dict):
        raise AnalysisValidationError("分析输出必须是 JSON 对象")
    data = raw or {}
    if strict and (not isinstance(data.get("summary"), str) or not data["summary"].strip()):
        raise AnalysisValidationError("分析输出缺少非空 summary")
    warnings: list[str] = []

    def _str(key: str) -> str:
        value = data.get(key)
        return value.strip() if isinstance(value, str) else ""

    def _list(key: str) -> list[str]:
        value = data.get(key)
        if isinstance(value, list):
            return list(dict.fromkeys(v.strip() for v in value if isinstance(v, str) and v.strip()))
        if isinstance(value, str):
            return list(dict.fromkeys(v.strip() for v in re.split(r"[,，;；]", value) if v.strip()))
        return []

    def _bool(key: str) -> bool | None:
        value = data.get(key)
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if value in (1, "1", "true", "True", "yes", "是", "开源"):
            return True
        if value in (0, "0", "false", "False", "no", "否", "不开源"):
            return False
        return None

    def _int_clamp(key: str, lo: int, hi: int, default: int) -> int:
        try:
            number = float(data.get(key, default))
            if not math.isfinite(number):
                return default
            return max(lo, min(hi, int(number)))
        except (TypeError, ValueError, OverflowError):
            return default

    maturity = _str("maturity").lower() or "unknown"
    if maturity not in MATURITY_VALUES:
        warnings.append("未知成熟度已设为 unknown")
        maturity = "unknown"
    action = _str("recommended_action").upper() or "WATCH"
    if action not in ACTION_VALUES:
        warnings.append("未知建议动作已设为 WATCH")
        action = "WATCH"
    category_values = _list("categories")
    if categories is not None:
        allowed_categories = canonical_keys(category_values, categories, category_aliases)
        if len(allowed_categories) != len(category_values):
            warnings.append("分类已按配置归一并过滤未知分类")
        category_values = allowed_categories

    analysis = {
        "summary": _str("summary"),
        "problem": _str("problem"),
        "solution": _str("solution"),
        "innovation": _list("innovation"),
        "technical_details": _list("technical_details"),
        "comparison": _str("comparison"),
        "open_source": _bool("open_source"),
        "maturity": maturity,
        "categories": category_values,
        "keywords": _list("keywords"),
        "related_projects": _list("related_projects"),
        "platform_relevance": _str("platform_relevance"),
        "recommended_action": action,
        "overall_relevance": _int_clamp("overall_relevance", 0, 100, 0),
        "llm_error": None,
    }
    relevance = data.get("relevance")
    analysis["relevance"] = {}
    project_mapping = {normalized_key(key): key for key in projects} if projects is not None else None
    for key, value in relevance.items() if isinstance(relevance, dict) else []:
        if not isinstance(key, str):
            continue
        project_key = project_mapping.get(normalized_key(key)) if project_mapping is not None else key
        if project_key is None:
            warnings.append("忽略未知内部项目键")
            continue
        try:
            number = float(value)
            if math.isfinite(number):
                analysis["relevance"][project_key] = max(0, min(5, int(number)))
        except (TypeError, ValueError, OverflowError):
            warnings.append("忽略无效项目相关度")
    analysis["validation_warnings"] = list(dict.fromkeys(warnings))
    return analysis
