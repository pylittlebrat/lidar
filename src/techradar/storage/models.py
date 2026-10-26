"""统一数据模型 TechItem。

所有 Collector（GitHub / arXiv / RSS / Web）最终都必须输出 TechItem，
后续 Analyzer / 存储层不关心条目来源。设计目标：跨来源同构。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from techradar.utils.time import parse_datetime, to_iso, utcnow


def canonical_url(url: str) -> str:
    """URL 归一化：去空白、去尾部斜杠。"""
    return (url or "").strip().rstrip("/")


def compute_id(source_type: str, url: str) -> str:
    """确定性 id：source_type + 归一化 URL 的 sha1 前 16 位。"""
    digest = hashlib.sha1(f"{source_type}:{canonical_url(url)}".encode("utf-8"))
    return digest.hexdigest()[:16]


@dataclass
class TechItem:
    """跨来源统一技术条目。"""

    source_type: str = ""  # github / arxiv / rss / web / ...
    source_name: str = ""  # 例如 IsaacLab / arXiv / NVIDIA Blog
    title: str = ""
    url: str = ""
    authors: list[str] = field(default_factory=list)
    published_at: datetime | None = None
    collected_at: datetime | None = None
    raw_text: str = ""
    abstract: str = ""
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = ""

    def __post_init__(self) -> None:
        """字段归一化：trim、URL canonical、自动 id 与收集时间。"""
        self.source_type = (self.source_type or "").strip()
        self.source_name = (self.source_name or "").strip()
        self.title = (self.title or "").strip()
        self.url = canonical_url(self.url)
        self.raw_text = self.raw_text or ""
        self.abstract = self.abstract or ""
        if not self.id:
            self.id = compute_id(self.source_type, self.url)
        if self.collected_at is None:
            self.collected_at = utcnow()

    def content_hash(self) -> str:
        """去重键：source_type + (url 或 title) 的 sha1。"""
        base = self.url or self.title
        digest = hashlib.sha1(f"{self.source_type}:{base}".encode("utf-8"))
        return digest.hexdigest()

    def payload_hash(self) -> str:
        """内容版本指纹；星数、查询命中等观测变化不触发付费重分析。"""
        payload = {
            'title': self.title, 'abstract': self.abstract, 'raw_text': self.raw_text,
            'authors': self.authors, 'tags': sorted(self.tags),
        }
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """序列化为可 JSON 化的 dict（datetime -> ISO 字符串）。"""

        def conv(value: Any) -> Any:
            if isinstance(value, datetime):
                return to_iso(value)
            if isinstance(value, list):
                return [conv(v) for v in value]
            if isinstance(value, dict):
                return {k: conv(v) for k, v in value.items()}
            return value

        return conv(asdict(self))

    def to_storage(self) -> dict[str, Any]:
        """转成 items 表行（tags/metadata 为 JSON 字符串，时间为 ISO）。"""
        return {
            "id": self.id,
            "source_type": self.source_type,
            "source_name": self.source_name,
            "title": self.title,
            "url": self.url,
            "authors": json.dumps(self.authors, ensure_ascii=False),
            "published_at": to_iso(self.published_at) if self.published_at else None,
            "collected_at": to_iso(self.collected_at) if self.collected_at else None,
            "raw_text": self.raw_text,
            "abstract": self.abstract,
            "tags": json.dumps(self.tags, ensure_ascii=False),
            "metadata": json.dumps(self.metadata, ensure_ascii=False),
            "content_hash": self.content_hash(),
            "payload_hash": self.payload_hash(),
            "updated_at": to_iso(utcnow()),
        }

    @classmethod
    def from_storage(cls, row: dict[str, Any] | None) -> "TechItem | None":
        """从 items 表行还原 TechItem（row 为 None 时返回 None）。"""
        if row is None:
            return None
        return cls(
            id=str(row["id"]),
            source_type=str(row["source_type"]),
            source_name=str(row["source_name"]),
            title=str(row["title"]),
            url=str(row["url"]),
            authors=json.loads(row["authors"] or "[]"),
            published_at=parse_datetime(row["published_at"]),
            collected_at=parse_datetime(row["collected_at"]),
            raw_text=str(row["raw_text"] or ""),
            abstract=str(row["abstract"] or ""),
            tags=json.loads(row["tags"] or "[]"),
            metadata=json.loads(row["metadata"] or "{}"),
        )
