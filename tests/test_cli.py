"""CLI 冒烟测试：help / version / config / db init / db status。"""

from __future__ import annotations

import pytest

from techradar.cli import main


def test_help_exits_zero(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "techradar" in out
    assert "config" in out
    assert "db" in out


def test_version(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "0.1.0" in capsys.readouterr().out


def test_config_command(capsys) -> None:
    code = main(["config"])
    assert code == 0
    out = capsys.readouterr().out
    assert "配置目录" in out
    assert "taxonomy" in out


def test_db_init_and_status(tmp_path, capsys) -> None:
    db_file = tmp_path / "cli.db"
    assert main(["db", "init", "--db-path", str(db_file)]) == 0
    # 幂等：重复初始化不报错
    assert main(["db", "init", "--db-path", str(db_file)]) == 0
    assert main(["db", "status", "--db-path", str(db_file)]) == 0
    out = capsys.readouterr().out
    assert "items" in out
    assert "schema 版本: 2" in out


def test_all_commands_help(capsys) -> None:
    """所有命令都有 --help 且正常退出（无未实现命令）。"""
    for command in ("collect", "analyze", "daily", "weekly", "run", "backfill", "search", "ask", "dashboard"):
        with pytest.raises(SystemExit) as exc:
            main([command, "--help"])
        assert exc.value.code == 0
        assert "usage:" in capsys.readouterr().out


def test_cli_search(tmp_path, capsys) -> None:
    from techradar.storage.database import Database
    from techradar.storage.models import TechItem
    from techradar.storage.repository import ItemRepository

    db_file = tmp_path / "search.db"
    db = Database(db_file)
    db.init()
    ItemRepository(db).insert(TechItem(
        source_type="arxiv", source_name="arXiv", title="Real2Sim Benchmark Paper",
        url="https://arxiv.org/abs/1", abstract="real2sim benchmark",
    ))
    assert main(["search", "real2sim", "--db-path", str(db_file)]) == 0
    out = capsys.readouterr().out
    assert "命中 1 条" in out
    assert "Real2Sim Benchmark Paper" in out


def test_cli_ask(tmp_path, monkeypatch, capsys) -> None:
    """CLI ask：monkeypatch search.ask 返回固定结果。"""

    def fake_ask(config, db, question, top=10):
        return {
            "answer": "模拟回答：关注 Newton 后端。",
            "sources": [{"title": "Newton Physics", "url": "https://github.com/newton"}],
        }

    monkeypatch.setattr("techradar.search.ask", fake_ask)
    assert main(["ask", "哪个物理引擎值得调研？", "--db-path", str(tmp_path / "a.db")]) == 0
    out = capsys.readouterr().out
    assert "模拟回答" in out
    assert "来源（1 条）" in out


def test_cli_daily(tmp_path, capsys) -> None:
    """CLI daily：生成日报文件并打印路径。"""
    from techradar.storage.database import Database
    from techradar.storage.models import TechItem
    from techradar.storage.repository import AnalysisRepository, ItemRepository
    from datetime import datetime, timezone

    db_file = tmp_path / "daily.db"
    db = Database(db_file)
    db.init()
    item = TechItem(
        source_type="arxiv", source_name="arXiv", title="Real2Sim Benchmark Paper",
        url="https://arxiv.org/abs/2601.42", abstract="real2sim benchmark",
        collected_at=datetime(2026,9,28,0,tzinfo=timezone.utc),
    )
    ItemRepository(db).insert(item)
    AnalysisRepository(db).upsert_analysis(item.id, {
        "summary": "Real2Sim 基准", "categories": ["real2sim"],
        "score": 85, "recommended_action": "INVESTIGATE",
    })
    out_dir = tmp_path / "out"
    assert main(["daily", "--db-path", str(db_file), "--date", "2026-09-28",
                 "--out-dir", str(out_dir)]) == 0
    md = (out_dir / "2026-09-28.md").read_text(encoding="utf-8")
    assert "Real2Sim Benchmark Paper" in md
    assert "日报已生成" in capsys.readouterr().out


def test_cli_collect_end_to_end(tmp_path, monkeypatch, capsys) -> None:
    """CLI collect：注入假采集器，验证入库与汇总输出。"""
    from techradar.collectors.base import BaseCollector
    from techradar.storage.database import Database
    from techradar.storage.models import TechItem

    class FakeCollector(BaseCollector):
        source_type = "github"
        source_name = "GitHub"

        def fetch(self):
            return [
                TechItem(
                    source_type="github",
                    source_name="IsaacLab",
                    title="Isaac Lab v2.0 Release",
                    url="https://github.com/isaac-sim/IsaacLab/releases/tag/v2.0",
                    abstract="Multi-physics backend",
                )
            ]

    monkeypatch.setattr(
        "techradar.pipeline.build_collectors", lambda cfg, sources: [FakeCollector({})]
    )
    db_file = tmp_path / "cli-collect.db"
    assert main(["collect", "--db-path", str(db_file)]) == 0
    status = Database(db_file).status()
    assert status["tables"]["items"] == 1
    out = capsys.readouterr().out
    assert "采集汇总" in out
    assert "inserted=1" in out
