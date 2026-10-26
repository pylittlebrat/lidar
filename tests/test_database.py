"""SQLite 数据库：幂等初始化与状态查询测试。"""

from __future__ import annotations

from techradar.storage.database import Database

EXPECTED_TABLES = {
    "techradar_meta",
    "sources",
    "items",
    "analysis",
    "categories",
    "tags",
    "relevance",
    "radar",
    "reports",
    "item_versions",
    "sync_runs",
}


def test_init_idempotent(db_path) -> None:
    db = Database(db_path)
    db.init()
    first = db.status()
    db.init()  # 第二次初始化必须幂等、不报错
    second = db.status()
    assert set(second["tables"]) == EXPECTED_TABLES
    assert first["schema_version"] == second["schema_version"] == "2"


def test_status_counts(db_path) -> None:
    db = Database(db_path)
    db.init()
    status = db.status()
    assert status["tables"]["items"] == 0
    assert status["path"].endswith("test.db")


def test_init_creates_parent_dir(tmp_path) -> None:
    nested = tmp_path / "nested" / "dir" / "radar.db"
    Database(nested).init()
    assert nested.is_file()
