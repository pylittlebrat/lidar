"""Technology Radar（方案第 18 / 27 节）：把评分沉淀为每项技术的持续状态。

状态流转：WATCH（值得阅读）→ INVESTIGATE（重点关注）→ POC（值得动手）
→ ADOPT / ARCHIVE。V1 由评分自动推导，后续可人工接管（reason / action / owner）。

tech_name 聚合键：GitHub 条目用仓库名（isaac-sim/IsaacLab 等，版本系列聚合），
其余（论文/文档/RSS）用标题（去版本号）。
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from techradar.config import Config
from techradar.storage.database import Database
from techradar.storage.repository import AnalysisRepository
from techradar.utils.logging import get_logger
from techradar.utils.time import utcnow

# 状态阈值（与 scoring.yaml bands 对齐）
_POC = 85
_INVESTIGATE = 75
_WATCH = 60

_VERSION_RE = re.compile(r"\s+v?\d+(?:\.\d+)+(?:[-+].*)?\s*$", re.IGNORECASE)


def tech_key(entry: dict[str, Any]) -> str:
    """技术实体键：GitHub 用 repo；其他用去版本号的标题。"""
    item = entry["item"]
    repo = item.metadata.get("repo")
    if repo:
        return str(repo)
    return _VERSION_RE.sub("", item.title or "").strip() or item.title


def derive_status(score: float | None, bands: dict[str, Any] | None = None) -> str:
    """由评分推导雷达状态。"""
    bands = bands or {}
    poc = float(bands.get("poc", bands.get("strong_focus", _POC)))
    investigate = float(bands.get("focus", _INVESTIGATE))
    if score is None:
        return "WATCH"
    if score >= poc:
        return "POC"
    if score >= investigate:
        return "INVESTIGATE"
    return "WATCH"


class RadarService:
    """扫描分析数据，维护 radar 表（幂等 upsert）。"""

    def __init__(self, config: Config, db: Database) -> None:
        self.config = config
        self.db = db
        self.logger = get_logger("techradar.radar")

    def sync(self, days: int = 90) -> list[dict[str, Any]]:
        """聚合近 days 天已分析条目 -> 推导并 upsert 每项技术的状态。"""
        entries = AnalysisRepository(self.db).scored_items(
            since=(utcnow() - timedelta(days=days)).isoformat(), limit=None
        )
        # 按技术实体聚合：取最高分条目代表该技术
        best: dict[str, dict[str, Any]] = {}
        for entry in entries:
            key = tech_key(entry)
            score = entry["analysis"].get("score") or 0
            current = best.get(key)
            if current is None or score > (current["analysis"].get("score") or 0):
                best[key] = entry

        rows: list[dict[str, Any]] = []
        updates = []
        for key, entry in best.items():
            score = entry["analysis"].get("score") or 0
            status = derive_status(score, self.config.scoring.get("bands", {}))
            action = entry["analysis"].get("recommended_action") or ""
            reason = (
                f"评分 {round(score)}；分类 {', '.join(entry['analysis'].get('categories', []) or [])}；"
                f"LLM 建议 {action or '-'}"
            )
            updates.append((key, status, status, reason, action))
            rows.append({
                "tech_name": key,
                "status": status,
                "suggested_status": status,
                "score": round(score),
                "action": action,
                "source": f"{entry['item'].source_type}/{entry['item'].source_name}",
            })
        effective = self._save_suggestions(updates)
        for row in rows:
            row["status"] = effective[row["tech_name"]]
        rows.sort(key=lambda r: -r["score"])
        self.logger.info("radar sync: %d 项技术状态已更新", len(rows))
        return rows

    def _save_suggestions(self, updates: list[tuple[str, str, str, str, str]]) -> dict[str, str]:
        """Persist one radar scan atomically while preserving manual decisions."""
        if not updates:
            return {}
        conn = self.db.connect()
        try:
            with conn:
                conn.executemany(
                    """
                    INSERT INTO radar (tech_name, status, suggested_status, reason, action)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(tech_name) DO UPDATE SET
                        suggested_status = excluded.suggested_status,
                        status = CASE WHEN radar.decision_status <> '' THEN radar.decision_status
                            WHEN radar.status IN ('ADOPT', 'ARCHIVE') THEN radar.status ELSE excluded.status END,
                        reason = CASE WHEN radar.decision_status <> '' OR radar.status IN ('ADOPT', 'ARCHIVE')
                            THEN radar.reason ELSE excluded.reason END,
                        action = CASE WHEN radar.decision_status <> '' OR radar.status IN ('ADOPT', 'ARCHIVE')
                            THEN radar.action ELSE excluded.action END,
                        updated_at = datetime('now')
                    """,
                    updates,
                )
                effective = dict(conn.execute("SELECT tech_name, status FROM radar").fetchall())
            return effective
        finally:
            conn.close()

    def list_all(self) -> list[dict[str, Any]]:
        """读取雷达状态表（按最近更新倒序）。"""
        conn = self.db.connect()
        try:
            rows = conn.execute(
                "SELECT tech_name, status, suggested_status, decision_status, decision_reason, decision_at,"
                " reason, action, owner, first_seen, updated_at"
                " FROM radar ORDER BY updated_at DESC, tech_name"
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]
