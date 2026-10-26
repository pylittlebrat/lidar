"""时间工具：统一 UTC、ISO8601 解析。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any


def utcnow() -> datetime:
    """当前 UTC 时间（带时区）。"""
    return datetime.now(timezone.utc)


def parse_datetime(value: Any, default: datetime | None = None) -> datetime | None:
    """尽力解析 ISO8601 时间字符串 / datetime / 时间戳；失败返回 default。"""
    if value is None:
        return default
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if not isinstance(value, str):
        return default
    text = value.strip()
    if not text:
        return default
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return default


def to_iso(value: datetime) -> str:
    """datetime -> ISO8601 字符串（统一转 UTC）。"""
    v = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return v.astimezone(timezone.utc).isoformat()


BEIJING = timezone(timedelta(hours=8), name='Asia/Shanghai')


def local_today() -> date:
    return utcnow().astimezone(BEIJING).date()


def report_window(report_date: date, days: int = 1) -> tuple[datetime, datetime]:
    """北京时间自然日窗口 [start,end)，历史报告不会混入后来的条目。"""
    start = datetime.combine(report_date - timedelta(days=max(1,days)-1), time.min, BEIJING)
    end = datetime.combine(report_date + timedelta(days=1), time.min, BEIJING)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)
