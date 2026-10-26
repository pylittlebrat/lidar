"""Frequency trends with disjoint periods and minimum sample checks.

Historical sample span is an estimate; it does not prove continuous source coverage.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import json
from typing import Any

from techradar.utils.time import parse_datetime, utcnow

WINDOWS = (("7d", 7), ("30d", 30), ("90d", 90))
UP_FACTOR, DOWN_FACTOR = 1.5, 0.67
MIN_SAMPLES, MIN_OBSERVED_DAYS = 3, 14


def _terms(value: str | None) -> set[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return set()
    if not isinstance(parsed, list):
        return set()
    return {term.strip() for term in parsed if isinstance(term, str) and term.strip()}


class TrendDetector:
    def __init__(self, config, db):
        self.config, self.db = config, db

    def trends(self, days: int = 90, limit: int = 15, as_of: datetime | None = None) -> dict[str, list[dict[str, Any]]]:
        now = parse_datetime(as_of) or utcnow()
        # Every exposed count must contain its entire advertised time window.
        since = now - timedelta(days=max(90, int(days)))
        conn = self.db.connect()
        try:
            # Only read small aggregation columns; no score ordering or arbitrary item cap.
            rows = conn.execute(
                """SELECT i.collected_at,i.source_type,a.categories,a.keywords
                   FROM items i JOIN analysis a ON a.item_id=i.id
                   WHERE julianday(i.collected_at)>=julianday(?) AND julianday(i.collected_at)<julianday(?)
                     AND a.llm_status NOT IN ('pending','running','llm_failed')""",
                (since.isoformat(), now.isoformat()),
            ).fetchall()
            oldest = conn.execute(
                "SELECT MIN(julianday(collected_at)) FROM items WHERE julianday(collected_at)<julianday(?)",
                (now.isoformat(),),
            ).fetchone()[0]
            endpoint = conn.execute("SELECT julianday(?)", (now.isoformat(),)).fetchone()[0]
        finally:
            conn.close()
        coverage = min(90, max(0, endpoint - oldest)) if oldest is not None else 0
        aggregates = {key: defaultdict(lambda: defaultdict(int)) for key in ("keywords", "categories", "sources")}
        for row in rows:
            collected = parse_datetime(row["collected_at"])
            if not collected:
                continue
            age = (now - collected).total_seconds() / 86400
            windows = [key for key, days in WINDOWS if 0 <= age < days]
            terms = {"keywords": _terms(row["keywords"]), "categories": _terms(row["categories"]),
                     "sources": {row["source_type"]}}
            for group, names in terms.items():
                for name in names:
                    for window in windows:
                        aggregates[group][name][window] += 1
        return {group: self._rank(aggregate, limit, coverage) for group, aggregate in aggregates.items()}

    def _rank(self, aggregate, limit: int, observed_days: float = 90) -> list[dict[str, Any]]:
        ranked = [self._build(name, counts, observed_days) for name, counts in aggregate.items()]
        priority = {"up": 0, "new": 1, "flat": 2, "down": 3, "insufficient": 4}
        ranked.sort(key=lambda trend: (priority[trend["direction"]], -trend["counts"]["7d"],
                                      -trend["counts"]["30d"], trend["name"]))
        return ranked[:max(0, int(limit))]

    @staticmethod
    def _build(name: str, counts, observed_days: float = 90) -> dict[str, Any]:
        c7, c30, c90 = (counts.get(window, 0) for window in ("7d", "30d", "90d"))
        previous = max(0, c30 - c7)
        previous_days = min(23, max(0, observed_days - 7))
        rate7 = c7 / min(7, max(1, observed_days))
        baseline = previous / previous_days if previous_days else 0
        change = None
        if observed_days < MIN_OBSERVED_DAYS or c30 < MIN_SAMPLES:
            direction = "insufficient"
        elif previous == 0:
            direction = "new" if c7 >= MIN_SAMPLES else "insufficient"
        else:
            change = (rate7 - baseline) / baseline * 100
            direction = "up" if rate7 >= UP_FACTOR * baseline else "down" if rate7 <= DOWN_FACTOR * baseline else "flat"
        return {"name": name, "counts": {"7d": c7, "30d": c30, "90d": c90},
                "previous_count": previous, "observed_days": round(observed_days, 1),
                "direction": direction, "change_pct": round(change, 1) if change is not None else None}
