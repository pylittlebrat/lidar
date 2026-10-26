"""统一日志：控制台 + 可选文件，初始化幂等。"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

DEFAULT_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_LEVELS: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def parse_level(level: str | int) -> int:
    """把名称（如 'INFO'）或数字转换为 logging 级别。"""
    if isinstance(level, int):
        return level
    name = (level or "").upper()
    if name not in _LEVELS:
        raise ValueError(
            f"未知日志级别: {level!r}（可选 DEBUG/INFO/WARNING/ERROR/CRITICAL）"
        )
    return _LEVELS[name]


def setup_logging(
    level: str | int = "INFO",
    log_file: str | Path | None = None,
) -> None:
    """配置 root logger；重复调用会先清空已有 handler，保证幂等。"""
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    root.setLevel(parse_level(level))

    formatter = logging.Formatter(DEFAULT_FORMAT, datefmt=DATE_FORMAT)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """获取命名 logger（统一入口）。"""
    return logging.getLogger(name)