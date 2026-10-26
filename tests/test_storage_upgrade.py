"""Migration, durable content versions and atomic analysis regression tests."""
import json
import sqlite3
from datetime import timedelta

import pytest

from techradar.storage.database import Database, SCHEMA
from techradar.storage.models import TechItem
from techradar.storage.repository import ItemRepository, AnalysisRepository
from techradar.utils.time import utcnow


def test_v1_migration_preserves_items_and_authors(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO techradar_meta VALUES('schema_version','1')")
    conn.execute("INSERT INTO items(id,title,url,source_type,source_name,authors,collected_at,content_hash) VALUES('old','Paper','https://e/p','arxiv','arxiv','[\"Alice\"]',?,'hash')",(utcnow().isoformat(),))
    conn.commit()
    conn.close()
    db=Database(db_path)
    db.init()
    assert ItemRepository(db).get('old').authors==['Alice']
    assert db.status()['schema_version']=='2'
    db.init()


def test_failed_migration_rolls_back_version_and_columns(db_path,monkeypatch):
    conn=sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO techradar_meta VALUES('schema_version','1')")
    conn.commit()
    conn.close()
    original=Database._upgrade_v2
    def fail(self,conn):
        original(self,conn)
        raise RuntimeError('test failure')
    monkeypatch.setattr(Database,'_upgrade_v2',fail)
    with pytest.raises(RuntimeError):
        Database(db_path).init()
    conn=sqlite3.connect(db_path)
    assert conn.execute("SELECT value FROM techradar_meta WHERE key='schema_version'").fetchone()[0]=='1'
    assert 'payload_hash' not in {r[1] for r in conn.execute('PRAGMA table_info(items)')}
    conn.close()


def test_content_update_archives_and_requeues(database):
    repo, analyses=ItemRepository(database),AnalysisRepository(database)
    original=TechItem(source_type='github',url='https://github.com/e/p',title='Project',abstract='physics engine',authors=['Alice'])
    assert repo.upsert_many([original])['inserted']==1
    analyses.save_analysis_result(original.id,{'summary':'old','llm_status':'llm','score':80},{'funlab':4})
    same=TechItem(source_type='github',url=original.url,title=original.title,abstract=original.abstract,metadata={'stars':200})
    assert repo.upsert_many([same])['unchanged']==1
    assert repo.get(original.id).authors==['Alice']
    revised=TechItem(source_type='github',url=original.url,title=original.title,abstract='physics engine with real2sim')
    assert repo.upsert_many([revised])['updated']==1
    assert repo.get(original.id).collected_at==original.collected_at
    assert [i.id for i in analyses.unanalyzed()]==[original.id]
    conn=database.connect()
    version=conn.execute('SELECT * FROM item_versions').fetchone()
    assert json.loads(version['analysis'])['summary']=='old'
    assert json.loads(version['analysis'])['relevance']=={'funlab':4}
    assert conn.execute('SELECT COUNT(*) FROM relevance').fetchone()[0]==0
    conn.close()


def test_stale_analysis_cannot_overwrite_changed_evidence(database):
    repo,analyses=ItemRepository(database),AnalysisRepository(database)
    item=TechItem(source_type='github',url='https://e/version',title='Version',abstract='old evidence')
    repo.upsert_many([item])
    expected=item.payload_hash()
    update=TechItem(source_type='github',url=item.url,title=item.title,abstract='new evidence')
    repo.upsert_many([update])
    assert analyses.save_analysis_result(item.id,{'summary':'stale'}, {},expected_payload_hash=expected) is False
    assert analyses.unanalyzed()[0].abstract=='new evidence'


def test_atomic_save_replaces_projects_and_rolls_back(database):
    item=TechItem(source_type='arxiv',url='https://e/p',title='Paper',authors=['Bob'])
    ItemRepository(database).insert(item)
    repo=AnalysisRepository(database)
    repo.save_analysis_result(item.id,{'summary':'first','llm_status':'llm'},{'a':5,'b':3})
    repo.save_analysis_result(item.id,{'summary':'second','llm_status':'llm','breakdown':{'novelty':75}},{'a':2})
    assert repo.scored_items()[0]['relevance']=={'a':2}
    with pytest.raises(ValueError):
        repo.save_analysis_result(item.id,{'summary':'bad'}, {'a':'invalid'})
    assert repo.scored_items()[0]['analysis']['summary']=='second'
    assert repo.scored_items()[0]['analysis']['breakdown']=={'novelty':75}


def test_retry_due_only_and_failure_not_counted_as_analyzed(database):
    repo=ItemRepository(database)
    analyses=AnalysisRepository(database)
    item=TechItem(source_type='arxiv',url='https://e/retry',title='Retry')
    repo.insert(item)
    analyses.save_analysis_result(item.id,{'llm_status':'llm_failed','attempts':1,'next_retry_at':(utcnow()+timedelta(hours=1)).isoformat()}, {})
    assert analyses.unanalyzed()==[]
    assert analyses.analyzed_count()==0
    analyses.save_analysis_result(item.id,{'llm_status':'llm_failed','attempts':1,'next_retry_at':(utcnow()-timedelta(minutes=1)).isoformat()}, {})
    assert analyses.unanalyzed()[0].id==item.id
    analyses.save_analysis_result(item.id,{'llm_status':'llm_failed','attempts':3},{})
    assert analyses.unanalyzed()==[]
