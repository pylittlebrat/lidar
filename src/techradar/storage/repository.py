"""ItemRepository：items 表读写与去重写入。"""

from __future__ import annotations

import sqlite3
import json
from datetime import timedelta
from typing import Any

from techradar.storage.database import Database
from techradar.storage.models import TechItem
from techradar.utils.time import utcnow

_INSERT_SQL = """
INSERT INTO items
    (id, title, url, source_type, source_name, authors, published_at,
     collected_at, raw_text, abstract, tags, metadata, content_hash, payload_hash, updated_at)
VALUES
    (:id, :title, :url, :source_type, :source_name, :authors, :published_at,
     :collected_at, :raw_text, :abstract, :tags, :metadata, :content_hash, :payload_hash, :updated_at)
"""


class ItemRepository:
    """围绕 items 表的读写仓储（content_hash 唯一约束保证去重）。"""

    def __init__(self, db: Database) -> None:
        self.db = db

    def insert(self, item: TechItem) -> bool:
        """插入一条；若 content_hash 已存在（重复）则跳过并返回 False。"""
        conn = self.db.connect()
        try:
            try:
                with conn:
                    cursor = conn.execute(_INSERT_SQL, item.to_storage())
                return cursor.rowcount == 1
            except sqlite3.IntegrityError:
                if conn.execute('SELECT 1 FROM items WHERE id=? OR content_hash=?', (item.id,item.content_hash())).fetchone():
                    return False
                raise
        finally:
            conn.close()

    def insert_many(self, items: list[TechItem]) -> dict[str, int]:
        """批量插入，返回 {"inserted": n, "duplicates": n}。"""
        inserted = 0
        duplicates = 0
        for item in items:
            if self.insert(item):
                inserted += 1
            else:
                duplicates += 1
        return {"inserted": inserted, "duplicates": duplicates}

    def upsert_many(self, items: list[TechItem]) -> dict[str, int]:
        """幂等更新资源，正文变化留历史并重新排队分析，保留首次入库时间。"""
        stats = {'inserted': 0, 'updated': 0, 'unchanged': 0}
        conn = self.db.connect()
        try:
            with conn:
                for item in items:
                    old = conn.execute('SELECT * FROM items WHERE content_hash=?', (item.content_hash(),)).fetchone()
                    if old is None:
                        conn.execute(_INSERT_SQL,item.to_storage())
                        stats['inserted'] += 1
                        continue
                    # Sparse API responses must not erase previously obtained evidence.
                    for field in ('abstract','raw_text','authors'):
                        if not getattr(item,field):
                            value = old[field]
                            setattr(item,field,json.loads(value or '[]') if field=='authors' else value)
                    if item.published_at is None:
                        from techradar.utils.time import parse_datetime
                        item.published_at = parse_datetime(old['published_at'])
                    params = item.to_storage()
                    params['id'] = old['id']
                    changed = old['payload_hash'] != item.payload_hash()
                    if changed:
                        analysis = conn.execute('SELECT * FROM analysis WHERE item_id=?',(old['id'],)).fetchone()
                        snapshot = dict(analysis) if analysis else {}
                        snapshot['relevance'] = {row['project_key']:row['score'] for row in conn.execute('SELECT project_key,score FROM relevance WHERE item_id=?',(old['id'],))}
                        conn.execute('INSERT INTO item_versions(item_id,version,payload,analysis,changed_at) VALUES(?,?,?,?,?)',
                            (old['id'],old['content_version'],json.dumps(dict(old),ensure_ascii=False),
                             json.dumps(snapshot,ensure_ascii=False),utcnow().isoformat()))
                        conn.execute('DELETE FROM analysis WHERE item_id=?',(old['id'],))
                        conn.execute('DELETE FROM relevance WHERE item_id=?',(old['id'],))
                    conn.execute('''UPDATE items SET title=:title, url=:url,source_name=:source_name,
                        authors=:authors,published_at=:published_at,raw_text=:raw_text,abstract=:abstract,
                        tags=:tags,metadata=:metadata,payload_hash=:payload_hash,updated_at=:updated_at,
                        content_version=content_version+:changed WHERE id=:id''', {**params,'changed':int(changed)})
                    stats['updated' if changed else 'unchanged'] += 1
        finally:
            conn.close()
        return stats

    def analysis_history(self, item: TechItem, days: int = 90) -> list[TechItem]:
        """当前条目之前的历史，排除自身及将来的发布以避免评分信息泄漏。"""
        until = item.published_at or item.collected_at or utcnow()
        since = (until - timedelta(days=days)).isoformat()
        conn = self.db.connect()
        try:
            rows = conn.execute('''SELECT * FROM items WHERE id<>?
                AND julianday(COALESCE(published_at,collected_at))>=julianday(?)
                AND julianday(COALESCE(published_at,collected_at))<=julianday(?)
                ORDER BY COALESCE(published_at,collected_at) DESC''', (item.id,since,until.isoformat())).fetchall()
        finally:
            conn.close()
        return [TechItem.from_storage(dict(row)) for row in rows]

    def exists_hash(self, content_hash: str) -> bool:
        """判断 content_hash 是否已存在。"""
        conn = self.db.connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM items WHERE content_hash = ?", (content_hash,)
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def count(self) -> int:
        """items 总数。"""
        conn = self.db.connect()
        try:
            return conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
        finally:
            conn.close()

    def get(self, item_id: str) -> TechItem | None:
        """按 id 查询单条。"""
        conn = self.db.connect()
        try:
            row = conn.execute(
                "SELECT * FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return TechItem.from_storage(dict(row))

    def recent(self, limit: int = 50) -> list[TechItem]:
        """最近采集的条目（按 collected_at 倒序）。"""
        conn = self.db.connect()
        try:
            rows = conn.execute(
                "SELECT * FROM items ORDER BY collected_at DESC LIMIT ?", (limit,)
            ).fetchall()
        finally:
            conn.close()
        return [TechItem.from_storage(dict(row)) for row in rows]  # type: ignore[arg-type]

    def all_hashes(self, limit: int | None = None) -> set[str]:
        """数据库中已有的 content_hash 集合（供去重使用）。"""
        conn = self.db.connect()
        try:
            sql = "SELECT content_hash FROM items"
            params: tuple[Any, ...] = ()
            if limit is not None:
                sql += " LIMIT ?"
                params = (limit,)
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return {row["content_hash"] for row in rows}

    def recent_titles(self, days: int = 90, limit: int = 500) -> list[tuple[str, str]]:
        """近 days 天内的 (source_type, title) 列表（供跨来源标题去重/新颖度）。"""
        since = (utcnow() - timedelta(days=days)).isoformat()
        conn = self.db.connect()
        try:
            rows = conn.execute(
                "SELECT source_type, title FROM items WHERE collected_at >= ?"
                " ORDER BY collected_at DESC LIMIT ?",
                (since, limit),
            ).fetchall()
        finally:
            conn.close()
        return [(row["source_type"], row["title"]) for row in rows]

    def count_since(self, since: str | None = None, until: str | None = None) -> int:
        """since（ISO 时间字符串）以来的条目数。"""
        conn = self.db.connect()
        try:
            where, params = time_filter(since, until)
            row = conn.execute('SELECT COUNT(*) AS n FROM items' + where, params).fetchone()
        finally:
            conn.close()
        return row["n"]

    def count_by_source(self, since: str | None = None, until: str | None = None) -> dict[str, int]:
        """按来源统计条目数（可选时间段）。"""
        conn = self.db.connect()
        try:
            where, params = time_filter(since, until)
            rows = conn.execute('SELECT source_type, COUNT(*) AS n FROM items'+where+' GROUP BY source_type ORDER BY n DESC',params).fetchall()
        finally:
            conn.close()
        return {row["source_type"]: row["n"] for row in rows}

    def search(self, keyword: str, limit: int = 50) -> list[TechItem]:
        """LIKE 关键词检索（标题/摘要/正文），按采集时间倒序。"""
        pattern = f"%{keyword}%"
        conn = self.db.connect()
        try:
            rows = conn.execute(
                "SELECT * FROM items WHERE title LIKE ? OR abstract LIKE ? OR raw_text LIKE ?"
                " ORDER BY collected_at DESC LIMIT ?",
                (pattern, pattern, pattern, max(1, int(limit))),
            ).fetchall()
        finally:
            conn.close()
        return [TechItem.from_storage(dict(row)) for row in rows]  # type: ignore[arg-type]


def time_filter(since=None, until=None, prefix=''):
    clauses, params = [], []
    field = prefix + 'collected_at'
    if since:
        clauses.append(f'julianday({field})>=julianday(?)')
        params.append(since)
    if until:
        clauses.append(f'julianday({field})<julianday(?)')
        params.append(until)
    return (' WHERE ' + ' AND '.join(clauses) if clauses else ''), params


LIST_FIELDS = ('innovation', 'technical_details', 'categories', 'keywords', 'related_projects')
ANALYSIS_FIELDS = ('summary', 'problem', 'solution', 'innovation', 'technical_details', 'comparison',
    'open_source', 'maturity', 'categories', 'keywords', 'related_projects', 'platform_relevance',
    'recommended_action', 'llm_status', 'score', 'breakdown', 'overall_relevance', 'attempts',
    'last_error', 'next_retry_at', 'model', 'prompt_version')


def decode_analysis(row):
    result = {key: row[key] for key in ANALYSIS_FIELDS}
    for key in LIST_FIELDS:
        result[key] = json.loads(result[key] or '[]')
    result['breakdown'] = json.loads(result['breakdown'] or '{}')
    return result


class AnalysisRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _write_analysis(conn, item_id, analysis):
        params = {'item_id': item_id}
        for key in ANALYSIS_FIELDS:
            if key in LIST_FIELDS:
                params[key] = json.dumps(analysis.get(key, []), ensure_ascii=False)
            elif key == 'breakdown':
                params[key] = json.dumps(analysis.get(key, {}), ensure_ascii=False)
            elif key in ('score', 'open_source', 'overall_relevance', 'next_retry_at'):
                params[key] = analysis.get(key)
            elif key == 'attempts':
                params[key] = int(analysis.get(key, 0))
            else:
                params[key] = str(analysis.get(key) or '')
        names = ('item_id',) + ANALYSIS_FIELDS
        columns = ','.join(names)
        placeholders = ','.join(':'+key for key in names)
        updates = ','.join(f'{key}=excluded.{key}' for key in ANALYSIS_FIELDS)
        conn.execute(f'INSERT INTO analysis({columns}) VALUES({placeholders}) '
                     f"ON CONFLICT(item_id) DO UPDATE SET {updates},updated_at=datetime('now')", params)

    @staticmethod
    def _replace_relevance(conn, item_id, scores):
        conn.execute('DELETE FROM relevance WHERE item_id=?', (item_id,))
        for key, value in scores.items():
            score = float(value)
            if not 0 <= score <= 5:
                raise ValueError('Project relevance must be in 0..5')
            conn.execute('INSERT INTO relevance(item_id,project_key,score) VALUES(?,?,?)', (item_id,key,score))

    def upsert_analysis(self, item_id: str, analysis: dict[str, Any]) -> None:
        conn = self.db.connect()
        try:
            with conn:
                self._write_analysis(conn,item_id,analysis)
        finally:
            conn.close()

    def upsert_relevance(self, item_id: str, scores: dict[str, Any]) -> None:
        conn = self.db.connect()
        try:
            with conn:
                self._replace_relevance(conn,item_id,scores)
        finally:
            conn.close()

    def save_analysis_result(self, item_id, analysis, project_scores, expected_payload_hash=None):
        conn = self.db.connect()
        try:
            with conn:
                # Lock before checking: a collector cannot change the evidence mid-save.
                conn.execute('BEGIN IMMEDIATE')
                if expected_payload_hash is not None:
                    current = conn.execute('SELECT payload_hash FROM items WHERE id=?',(item_id,)).fetchone()
                    if current is None or current['payload_hash'] != expected_payload_hash:
                        return False
                self._write_analysis(conn,item_id,analysis)
                self._replace_relevance(conn,item_id,project_scores)
                return True
        finally:
            conn.close()

    def analysis_state(self, item_id):
        conn = self.db.connect()
        try:
            row = conn.execute('SELECT * FROM analysis WHERE item_id=?', (item_id,)).fetchone()
            return decode_analysis(row) if row else {'attempts': 0}
        finally:
            conn.close()

    def unanalyzed(self, limit: int = 20, force: bool = False) -> list[TechItem]:
        """返回需要 LLM 分析的条目。

        默认：无 analysis 行 / pending / 等待重试的 llm_failed。
        force=True：所有 llm_status != 'llm' 的条目（含仅关键词评分、旧数据未标记、
        llm_failed），用于“补全分析”。
        """
        conn = self.db.connect()
        try:
            if force:
                rows = conn.execute(
                    """SELECT i.* FROM items i LEFT JOIN analysis a ON a.item_id=i.id
                        WHERE coalesce(a.llm_status, '') != 'llm'
                        ORDER BY i.published_at DESC, i.collected_at DESC LIMIT ?""",
                    (max(0, int(limit)),),
                ).fetchall()
            else:
                rows = conn.execute("""SELECT i.* FROM items i LEFT JOIN analysis a ON a.item_id=i.id
                    WHERE a.item_id IS NULL OR a.llm_status='pending'
                       OR (a.llm_status='llm_failed' AND a.attempts<3
                           AND (a.next_retry_at IS NULL OR julianday(a.next_retry_at)<=julianday(?)))
                    ORDER BY CASE WHEN a.llm_status='llm_failed' THEN 1 ELSE 0 END,
                        i.published_at DESC,i.collected_at DESC LIMIT ?""",
                    (utcnow().isoformat(), max(0, int(limit)))).fetchall()
        finally:
            conn.close()
        return [TechItem.from_storage(dict(row)) for row in rows]

    def status_counts(self):
        conn = self.db.connect()
        try:
            rows = conn.execute("""SELECT CASE WHEN a.item_id IS NULL THEN 'pending'
                WHEN a.llm_status='' THEN 'legacy' ELSE a.llm_status END AS state,COUNT(*) n
                FROM items i LEFT JOIN analysis a ON a.item_id=i.id GROUP BY state""").fetchall()
            result = {key:0 for key in ('pending','legacy','llm','skipped','llm_failed')}
            result.update({row['state']:row['n'] for row in rows})
            return result
        finally:
            conn.close()

    def analyzed_count(self) -> int:
        states = self.status_counts()
        return sum(states[key] for key in ('legacy','llm','skipped'))

    def scored_items(self, since=None, limit=50, until=None):
        conn = self.db.connect()
        try:
            fields = ','.join('a.'+key for key in ANALYSIS_FIELDS)
            where, params = time_filter(since,until,prefix='i.')
            sql = 'SELECT i.*,'+fields+' FROM items i JOIN analysis a ON a.item_id=i.id'+where
            sql += (' AND ' if where else ' WHERE ')+"a.llm_status NOT IN ('pending','running','llm_failed')"
            sql += ' ORDER BY a.score DESC,i.collected_at DESC'
            if limit is not None:
                sql += ' LIMIT ?'
                params.append(max(0,int(limit)))
            rows = conn.execute(sql,params).fetchall()
            relevance = {}
            ids = [row['id'] for row in rows]
            for offset in range(0,len(ids),500):
                chunk=ids[offset:offset+500]
                placeholders=','.join('?' for _ in chunk)
                for r in conn.execute(f'SELECT item_id,project_key,score FROM relevance WHERE item_id IN ({placeholders})',chunk):
                    relevance.setdefault(r['item_id'],{})[r['project_key']]=r['score']
            return [{'item':TechItem.from_storage(dict(row)), 'analysis':decode_analysis(row),
                     'relevance':relevance.get(row['id'],{})} for row in rows]
        finally:
            conn.close()
