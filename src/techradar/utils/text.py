"""文本匹配工具：词边界匹配，避免短词子串误伤。

背景：过滤/分类此前用 `term in text` 子串匹配，导致 hil（HIL）命中
“while simulating”、rl 命中 “world” 等误伤。改为词边界匹配：
对 ASCII 字母数字词（含空格/连字符的短语），要求命中时前后都不是字母数字。
"""

from __future__ import annotations

import re


def match_term(term: str, text: str) -> bool:
    """判断 term 是否以词边界出现在 text（均小写）。

    非 ASCII 字母数字术语（如中文）退化为子串匹配。
    """
    lowered = term.lower()
    if not lowered:
        return False
    if re.search(r"[0-9a-z]", lowered) is None:
        return lowered in text
    pattern = r"(?<![0-9a-z])" + re.escape(lowered) + r"(?![0-9a-z])"
    return re.search(pattern, text) is not None


def count_matches(terms: list[str], text: str) -> int:
    """统计 terms 中在 text 词边界命中的个数。"""
    return sum(1 for term in terms if match_term(term, text))


def filter_matches(terms: list[str], text: str) -> list[str]:
    """返回 terms 中在 text 词边界命中的原词列表。"""
    return [term for term in terms if match_term(term, text)]