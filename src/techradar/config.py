"""YAML 配置系统。

所有业务关键词、信息源、taxonomy、内部项目画像与评分权重都来自
config/*.yaml，禁止在 Python 代码中 hardcode 业务关键词。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REQUIRED_CONFIG_FILES: tuple[str, ...] = (
    "sources.yaml",
    "keywords.yaml",
    "taxonomy.yaml",
    "projects.yaml",
    "scoring.yaml",
    "llm.yaml",
)


class ConfigError(RuntimeError):
    """配置文件缺失或格式错误。"""


def project_root() -> Path:
    """项目根目录（由 src/techradar/config.py 向上推导）。"""
    return Path(__file__).resolve().parents[2]


def default_config_dir() -> Path:
    """解析配置目录：`TECHRADAR_CONFIG_DIR` 优先，其次项目根下的 config/。"""
    env_dir = os.environ.get("TECHRADAR_CONFIG_DIR")
    if env_dir:
        return Path(env_dir).resolve()
    project_config = project_root() / "config"
    if project_config.is_dir():
        return project_config
    return Path.cwd() / "config"


def default_data_dir() -> Path:
    """解析数据目录：`TECHRADAR_DATA_DIR` 优先，其次项目根下的 data/。"""
    env_dir = os.environ.get("TECHRADAR_DATA_DIR")
    if env_dir:
        return Path(env_dir).resolve()
    return project_root() / "data"


def load_yaml(path: Path) -> dict[str, Any]:
    """读取单个 YAML 文件；缺失或非法时抛出 ConfigError。"""
    if not path.is_file():
        raise ConfigError(f"配置文件不存在: {path}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML 解析失败 {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件顶层必须是 mapping: {path}")
    return data


@dataclass
class Config:
    """聚合后的项目配置（由 config/*.yaml 加载而来）。"""

    sources: dict[str, Any] = field(default_factory=dict)
    keywords: dict[str, Any] = field(default_factory=dict)
    taxonomy: dict[str, Any] = field(default_factory=dict)
    projects: dict[str, Any] = field(default_factory=dict)
    scoring: dict[str, Any] = field(default_factory=dict)
    llm: dict[str, Any] = field(default_factory=dict)
    config_dir: Path = field(default_factory=default_config_dir)

    @classmethod
    def load(cls, config_dir: str | Path | None = None) -> "Config":
        """加载全部必需配置文件并做基本校验（缺一报错，可重复调用）。"""
        base = Path(config_dir).resolve() if config_dir else default_config_dir()
        raw: dict[str, dict[str, Any]] = {}
        for name in REQUIRED_CONFIG_FILES:
            raw[name] = load_yaml(base / name)
        cfg = cls(
            sources=raw["sources.yaml"],
            keywords=raw["keywords.yaml"],
            taxonomy=raw["taxonomy.yaml"],
            projects=raw["projects.yaml"],
            scoring=raw["scoring.yaml"],
            llm=raw["llm.yaml"],
            config_dir=base,
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        """基本结构校验：taxonomy 分类非空、scoring 权重之和为 1。"""
        cats = self.taxonomy.get("categories")
        if not isinstance(cats, dict) or not cats:
            raise ConfigError("taxonomy.yaml 缺少非空的 categories 映射")
        weights = self.scoring.get("weights")
        if isinstance(weights, dict):
            total = sum(float(v) for v in weights.values())
            if abs(total - 1.0) > 1e-6:
                raise ConfigError(f"scoring.yaml 权重之和应为 1.0，实际 {total:.3f}")

    def categories(self) -> list[tuple[str, list[str]]]:
        """返回 [(分类名, [关键词, ...]), ...]。"""
        cats = self.taxonomy.get("categories", {})
        return [(name, list(terms)) for name, terms in cats.items()]

    def as_dict(self) -> dict[str, Any]:
        """导出为纯 dict（便于调试与序列化）。"""
        return {
            "config_dir": str(self.config_dir),
            "sources": self.sources,
            "keywords": self.keywords,
            "taxonomy": self.taxonomy,
            "projects": self.projects,
            "scoring": self.scoring,
            "llm": self.llm,
        }