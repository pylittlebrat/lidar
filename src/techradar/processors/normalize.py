"""Normalizer：对 TechItem 做统一字段清洗（HTML 去标签、空白折叠）。

模型层 __post_init__ 已做 trim/URL canonical，这里补充内容级清洗。
"""

from __future__ import annotations

import html
import re

from techradar.storage.models import TechItem

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_text(text: str) -> str:
    """HTML 解义 -> 去标签 -> 空白折叠。"""
    text = html.unescape(text or "")
    text = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


class Normalizer:
    """内容归一化器（无状态，可复用）。"""

    def normalize(self, item: TechItem) -> TechItem:
        """就地清洗 title / abstract / raw_text 并返回同一对象。"""
        item.title = clean_text(item.title)
        item.abstract = clean_text(item.abstract)
        item.raw_text = clean_text(item.raw_text)
        return item