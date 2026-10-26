"""SQLite 数据库：初始化幂等、schema 版本管理。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "2"

SCHEMA = """
CREATE TABLE IF NOT EXISTS techradar_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL DEFAULT 'P1',
    enabled INTEGER NOT NULL DEFAULT 1,
    url TEXT NOT NULL DEFAULT '',
    metadata TEXT NOT NULL DEFAULT '{}',
    last_collected_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    authors TEXT NOT NULL DEFAULT '[]',
    published_at TEXT,
    collected_at TEXT NOT NULL,
    raw_text TEXT NOT NULL DEFAULT '',
    abstract TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    metadata TEXT NOT NULL DEFAULT '{}',
    content_hash TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_items_published_at ON items(published_at);
CREATE INDEX IF NOT EXISTS idx_items_source ON items(source_type, source_name);
CREATE INDEX IF NOT EXISTS idx_items_hash ON items(content_hash);

CREATE TABLE IF NOT EXISTS analysis (
    item_id TEXT PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    summary TEXT NOT NULL DEFAULT '',
    problem TEXT NOT NULL DEFAULT '',
    solution TEXT NOT NULL DEFAULT '',
    innovation TEXT NOT NULL DEFAULT '[]',
    technical_details TEXT NOT NULL DEFAULT '[]',
    comparison TEXT NOT NULL DEFAULT '',
    open_source INTEGER,
    maturity TEXT NOT NULL DEFAULT '',
    categories TEXT NOT NULL DEFAULT '[]',
    keywords TEXT NOT NULL DEFAULT '[]',
    related_projects TEXT NOT NULL DEFAULT '[]',
    platform_relevance TEXT NOT NULL DEFAULT '',
    recommended_action TEXT NOT NULL DEFAULT '',
    llm_status TEXT NOT NULL DEFAULT '',       -- llm / skipped / llm_failed / 空=旧数据未标记
    score REAL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS categories (
    item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    PRIMARY KEY (item_id, category)
);

CREATE TABLE IF NOT EXISTS tags (
    item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (item_id, tag)
);

CREATE TABLE IF NOT EXISTS relevance (
    item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    project_key TEXT NOT NULL,
    score REAL NOT NULL,
    PRIMARY KEY (item_id, project_key)
);

CREATE TABLE IF NOT EXISTS radar (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tech_name TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'WATCH',
    reason TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT '',
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    report_date TEXT NOT NULL,
    path TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class Database:
    """SQLite 封装：每个操作用独立连接，init 幂等可重复调用。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        """打开新连接（自动创建父目录、启用 WAL 与外键）。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def init(self) -> None:
        """事务升级旧库；只有整个迁移成功后才更新版本。"""
        conn = self.connect()
        try:
            has_meta = conn.execute("SELECT 1 FROM sqlite_master WHERE name='techradar_meta'").fetchone()
            row = conn.execute("SELECT value FROM techradar_meta WHERE key='schema_version'").fetchone() if has_meta else None
            version = int(row[0]) if row else 0
            if version > int(SCHEMA_VERSION):
                raise RuntimeError(f"数据库版本 {version} 高于当前程序支持的 {SCHEMA_VERSION}")
            # executescript 不嵌套在 with conn 内，显式 BEGIN 保证 DDL 可回滚。
            conn.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
            if version < 2:
                self._upgrade_v2(conn)
            conn.execute("INSERT OR REPLACE INTO techradar_meta(key,value) VALUES('schema_version',?)", (SCHEMA_VERSION,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _add_columns(conn, table, columns):
        existing = {row['name'] for row in conn.execute(f'PRAGMA table_info({table})')}
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')

    def _upgrade_v2(self, conn):
        self._add_columns(conn, 'items', {
            'payload_hash': "TEXT NOT NULL DEFAULT ''", 'content_version': 'INTEGER NOT NULL DEFAULT 1',
            'updated_at': "TEXT NOT NULL DEFAULT ''",
        })
        self._add_columns(conn, 'analysis', {
            'llm_status': "TEXT NOT NULL DEFAULT ''", 'breakdown': "TEXT NOT NULL DEFAULT '{}'",
            'overall_relevance': 'REAL', 'attempts': 'INTEGER NOT NULL DEFAULT 0',
            'last_error': "TEXT NOT NULL DEFAULT ''", 'next_retry_at': 'TEXT',
            'model': "TEXT NOT NULL DEFAULT ''", 'prompt_version': "TEXT NOT NULL DEFAULT ''",
        })
        self._add_columns(conn, 'radar', {
            'suggested_status': "TEXT NOT NULL DEFAULT 'WATCH'", 'decision_status': "TEXT NOT NULL DEFAULT ''",
            'decision_reason': "TEXT NOT NULL DEFAULT ''", 'decision_at': 'TEXT',
        })
        conn.execute("UPDATE radar SET suggested_status=status WHERE status IN ('WATCH','INVESTIGATE','POC')")
        conn.execute("UPDATE radar SET decision_status=status,decision_reason=reason WHERE status IN ('ADOPT','ARCHIVE')")
        conn.execute('CREATE INDEX IF NOT EXISTS idx_items_collected_at ON items(collected_at)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_analysis_retry ON analysis(llm_status,next_retry_at)')
        conn.execute('''CREATE TABLE IF NOT EXISTS item_versions (
            item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE, version INTEGER NOT NULL,
            payload TEXT NOT NULL, analysis TEXT NOT NULL DEFAULT '{}', changed_at TEXT NOT NULL,
            PRIMARY KEY(item_id,version))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS sync_runs (
            id TEXT PRIMARY KEY, started_at TEXT NOT NULL, status TEXT NOT NULL, state TEXT NOT NULL)''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_sync_runs_started ON sync_runs(started_at DESC)')
        conn.execute('DELETE FROM reports WHERE id NOT IN (SELECT MAX(id) FROM reports GROUP BY kind,path)')
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_reports_kind_path ON reports(kind,path)')
        from techradar.storage.models import TechItem
        for row in conn.execute("SELECT * FROM items WHERE payload_hash='' ").fetchall():
            item = TechItem.from_storage(dict(row))
            conn.execute('UPDATE items SET payload_hash=?,updated_at=collected_at WHERE id=?', (item.payload_hash(),item.id))

    def status(self) -> dict[str, Any]:
        """返回数据库路径、schema 版本与各表行数。"""
        conn = self.connect()
        try:
            tables = [
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
                if not row["name"].startswith("sqlite_")  # 排除 sqlite_sequence 等内部表
            ]
            counts = {
                table: conn.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()["n"]
                for table in tables
            }
            meta = {
                row["key"]: row["value"]
                for row in conn.execute("SELECT key, value FROM techradar_meta")
            }
        finally:
            conn.close()
        return {
            "path": str(self.db_path.resolve()),
            "schema_version": meta.get("schema_version"),
            "tables": counts,
        }
