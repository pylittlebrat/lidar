"""配置系统测试：加载、校验、缺失与非法 YAML。"""

from __future__ import annotations

import pytest

from techradar import config as config_mod
from techradar.config import Config, ConfigError


def test_load_required_files(config: Config) -> None:
    assert isinstance(config.sources, dict)
    assert isinstance(config.keywords, dict)
    assert isinstance(config.taxonomy, dict)
    assert isinstance(config.projects, dict)
    assert isinstance(config.scoring, dict)


def test_taxonomy_categories(config: Config) -> None:
    cats = config.categories()
    assert len(cats) >= 12
    names = [name for name, _terms in cats]
    assert "simulation_engine" in names
    assert "engineering_machinery" in names
    assert "system_identification" in names
    assert "embodied_ai" in names
    for _name, terms in cats:
        assert terms, "每个分类必须包含关键词"


def test_scoring_weights_sum_to_one(config: Config) -> None:
    weights = config.scoring["weights"]
    total = sum(float(v) for v in weights.values())
    assert abs(total - 1.0) < 1e-6


def test_projects_defined(config: Config) -> None:
    projects = config.projects["projects"]
    assert "funlab" in projects
    assert "real2sim" in projects


def test_missing_config_file_raises(tmp_path) -> None:
    """缺少任一必需文件必须抛 ConfigError。"""
    (tmp_path / "sources.yaml").write_text("a: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        Config.load(tmp_path)


def test_invalid_yaml_raises(tmp_path) -> None:
    for name in config_mod.REQUIRED_CONFIG_FILES:
        (tmp_path / name).write_text(": broken [\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        Config.load(tmp_path)