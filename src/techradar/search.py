"""检索与技术问答（Phase 8，V3 Agent 的轻量版）。

- keyword_search: SQLite LIKE 关键词检索（无需 LLM）
- ask: 检索候选 -> 拼上下文 -> LLM 生成带来源的回答（RAG 最小闭环）
后续可升级：embedding 向量检索 + 知识图谱 + 自动 PoC 提案。
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from techradar.llm.client import LLMClient, LLMError
from techradar.llm.prompts import build_qa_system, build_qa_user
from techradar.storage.database import Database
from techradar.storage.repository import ItemRepository
from techradar.storage.models import TechItem
from techradar.utils.logging import get_logger

logger = get_logger("techradar.search")

_TOKEN_RE = re.compile(r"[\s\u3000，。？！、；：:,.;:?!/\\\"'()\[\]{}]+")

_EXPAND_SYSTEM_PROMPT = """你是机器人仿真领域检索关键词提取器。
根据用户的技术问题，提取 3-8 个英文检索关键词（仿真 / 机器人领域术语，如 physics engine、simulation backend、real2sim、digital twin、world model、Gaussian splatting）。
只输出一个 JSON 对象：{"keywords": ["keyword1", "keyword2", ...]}
"""


def _expand_query(client: LLMClient, question: str) -> list[str]:
    """用 LLM 把（可能为中文的）问题转成英文检索关键词（查询扩展）。"""
    raw = client.chat_json(_EXPAND_SYSTEM_PROMPT, f"问题: {question}", json_mode=False)
    keywords = raw.get("keywords", [])
    return [str(kw).strip() for kw in keywords if str(kw).strip()]


def keyword_search(db: Database, query: str, limit: int = 20) -> list[Any]:
    """SQLite LIKE 关键词检索（标题/摘要/正文）。"""
    return ItemRepository(db).search(query, limit=limit)


def _extract_tokens(question: str) -> list[str]:
    """把自然语言问题切分成检索 token（去标点与空白，保留 >=2 字符的词）。"""
    return [token for token in _TOKEN_RE.split(question) if len(token) >= 2]


def ranked_search(db: Database, tokens: list[str], limit: int = 80) -> list[TechItem]:
    """Query matches take precedence; platform scores only break relevance ties."""
    terms = list(dict.fromkeys(t.strip().lower() for t in tokens if t.strip()))[:14]
    if not terms:
        return []
    weights, matches, params = [], [], []
    for term in terms:
        columns = ("i.title", "i.abstract", "i.raw_text")
        hits = [f"(instr(lower({column}), ?) > 0)" for column in columns]
        weights.append(f"(4 * {hits[0]} + 2 * {hits[1]} + {hits[2]})")
        params.extend([term] * 3)
        matches.append("(instr(lower(i.title || ' ' || i.abstract || ' ' || i.raw_text), ?) > 0)")
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT i.*, " + " + ".join(weights) + " AS match_score"
            " FROM items i LEFT JOIN analysis a ON a.item_id = i.id WHERE (" + " OR ".join(matches) + ")"
            " ORDER BY match_score DESC, a.score DESC, i.published_at DESC, i.collected_at DESC, i.id LIMIT ?",
            [*params, *terms, max(1, min(int(limit), 200))],
        ).fetchall()
    finally:
        conn.close()
    return [TechItem.from_storage(dict(row)) for row in rows]


def evidence_excerpt(item: TechItem, tokens: list[str], limit: int = 900) -> str:
    """Include the relevant raw-text passage even when the abstract is unrelated."""
    excerpts = []
    if item.abstract.strip():
        excerpts.append(item.abstract.strip()[:300])
    raw = item.raw_text.strip()
    if raw:
        positions = [raw.lower().find(t.lower()) for t in tokens if t.strip()]
        positions = [p for p in positions if p >= 0]
        start = max(0, min(positions) - 160) if positions else 0
        passage = raw[start:start + max(300, limit - 300)]
        if start:
            passage = "…" + passage
        if start + len(passage) < len(raw):
            passage += "…"
        if passage not in excerpts:
            excerpts.append(passage)
    return "\n".join(excerpts)[:limit]


def _insufficient(candidate_count: int = 0) -> dict[str, Any]:
    return {
        "answer": "资料不足：知识库中没有检索到相关资料或可核查的有效引用。请补充资料或调整问题后再试。",
        "sources": [], "candidate_count": candidate_count, "status": "insufficient_evidence",
    }


def _group_candidates(candidates: list[Any]) -> list[tuple[Any, int]]:
    """Group ranked candidates, preserving the strongest matching evidence."""
    best: dict[str, tuple[Any, int]] = {}
    order: list[str] = []
    for item in candidates:
        repo = item.metadata.get("repo") or ""
        key = f"repo:{repo}" if repo else f"item:{item.url}"
        if key not in best:
            best[key] = (item, 1)
            order.append(key)
            continue
        current, count = best[key]
        best[key] = (current, count + 1)
    return [best[key] for key in order]


def ask(config, db: Database, question: str, top: int = 10) -> dict[str, Any]:
    """技术问答：检索候选 -> LLM 生成回答与来源列表。

    检索策略：先分词逐词检索并合并去重（自然语言问题），整句检索兜底。
    :return: {"answer": str, "sources": [{"title", "url"}], "candidate_count": int}
    """
    if not question.strip():
        return _insufficient()
    tokens = (_extract_tokens(question) or [question])[:8]
    top = max(1, min(int(top), 20))
    candidates = ranked_search(db, tokens, limit=top * 8)

    # 首次检索为空（如中文问题 vs 英文语料）时，用 LLM 做查询扩展再检索
    client = None
    if not candidates:
        try:
            client = LLMClient(config=config)
            expansions = _expand_query(client, question)
        except LLMError:
            expansions = []
        tokens = list(dict.fromkeys([*tokens, *expansions[:6]]))
        candidates = ranked_search(db, tokens, limit=top * 8)

    if not candidates:
        return _insufficient()
    context_lines: list[str] = []
    candidates = [item for item in candidates
                  if urlparse(item.url).scheme in ("http", "https")
                  and urlparse(item.url).netloc and (item.abstract.strip() or item.raw_text.strip())]
    groups = _group_candidates(candidates)[:top]
    if not groups:
        return _insufficient()
    for idx, (item, count) in enumerate(groups, start=1):
        abstract = evidence_excerpt(item, tokens)
        extra = f"\n    （该仓库共 {count} 个版本，此处为匹配度最高的代表版本）" if count > 1 else ""
        context_lines.append(
            f"[{idx}] {item.title}（{item.source_type}/{item.source_name}）{extra}\n"
            f"    链接: {item.url}\n"
            f"    摘要: {abstract or '(无正文)'}"
        )
    context = "\n\n".join(context_lines)

    client = client or LLMClient(config=config)
    raw = client.chat_json(build_qa_system(), build_qa_user(question, context))
    answer = str(raw.get("answer") or "").strip()
    raw_sources = raw.get("source_urls", [])
    source_urls = {str(url).strip() for url in raw_sources if url} if isinstance(raw_sources, list) else set()
    sources = [
        {"title": item.title, "url": item.url}
        for item, _count in groups
        if item.url in source_urls or item.title in source_urls
    ]
    logger.info("ask: candidates=%d groups=%d sources=%d",
                len(candidates), len(groups), len(sources))
    if not answer or not sources:
        return _insufficient(len(groups))
    return {"answer": answer, "sources": sources, "candidate_count": len(groups), "status": "answered"}
