"""pytest 公共 fixtures。"""

from __future__ import annotations

import logging

import pytest

from techradar.config import Config


@pytest.fixture(autouse=True)
def _reset_logging() -> None:
    """每个测试后清空 root logger 的 handler，避免 CLI 测试残留 handler
    指向已关闭的 sys.stdout（pytest capsys 会引起 'I/O operation on closed file'）。"""
    yield
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)


@pytest.fixture()
def config() -> Config:
    """加载真实项目配置（<项目根>/config）。"""
    return Config.load()


@pytest.fixture()
def db_path(tmp_path):
    """临时 SQLite 文件路径。"""
    return tmp_path / "test.db"


@pytest.fixture()
def database(db_path):
    """已初始化的临时 Database 实例。"""
    from techradar.storage.database import Database

    db = Database(db_path)
    db.init()
    return db