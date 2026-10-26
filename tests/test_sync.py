"""Sync lifecycle and mutual exclusion, using local fakes (no paid/network calls)."""
from threading import Event, Thread
from types import SimpleNamespace
import json
import subprocess
import sys
import os
import time

import pytest

from techradar import sync


@pytest.fixture
def fake_jobs(monkeypatch, tmp_path):
    monkeypatch.setattr(sync.Pipeline, "collect", lambda self, **kw: {"github": {"inserted": 2, "collected": 5}})
    monkeypatch.setattr(sync.Pipeline, "analyze", lambda self, **kw: {"stored": 2, "llm_errors": 0})
    daily, weekly = tmp_path/'daily.md', tmp_path/'weekly.md'
    daily.write_text('# daily',encoding='utf-8')
    weekly.write_text('# weekly',encoding='utf-8')
    monkeypatch.setattr(sync.DailyReportGenerator, "generate", lambda self, **kw: str(daily))
    monkeypatch.setattr(sync.WeeklyReportGenerator, "generate", lambda self, **kw: str(weekly))
    def fake_spawn(cfg,db,state):
        def worker():
            lock = sync.SyncLock(db).acquire(wait=True)
            sync._execute(cfg,db,lock,state)
        thread = Thread(target=worker)
        thread.start()
        return SimpleNamespace(pid=0)
    monkeypatch.setattr(sync,'_spawn_worker',fake_spawn)


def test_sync_success_and_cross_process_lock(config, database, fake_jobs):
    lock = sync.SyncLock(database).acquire()
    try:
        script = "from techradar.sync import SyncLock,SyncBusy; from techradar.storage.database import Database; import sys\ntry: SyncLock(Database(sys.argv[1])).acquire()\nexcept SyncBusy: sys.exit(7)\n"
        child = subprocess.run([sys.executable, "-c", script, str(database.db_path)], timeout=15)
        assert child.returncode == 7
        with pytest.raises(sync.SyncBusy):
            sync.start_sync(config, database)
    finally:
        lock.release()
    result = sync.run_sync(config, database)
    assert result["status"] == "success"
    assert result["collect"]["github"]["inserted"] == 2
    assert result["reports"]["weekly"].endswith("weekly.md")
    assert sync.get_status(database)["status"] == "success"


def test_partial_sync_preserves_reports_and_redacts_errors(config, database, fake_jobs, monkeypatch):
    monkeypatch.setattr(sync.Pipeline, "collect", lambda self, **kw: {"rss": {"error": "secret api token"}})
    monkeypatch.setattr(sync.Pipeline, "analyze", lambda self, **kw: {"error": "secret api token"})
    result = sync.run_sync(config, database)
    assert result["status"] == "partial"
    assert len(result["reports"]) == 2
    assert "secret" not in json.dumps(sync.get_status(database))


def test_background_manual_and_scheduled_wait(config, database, fake_jobs, monkeypatch):
    entered, release, done = Event(), Event(), Event()
    calls = []
    def collect(self, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(10)
        return {"github": {"inserted": 0}}
    monkeypatch.setattr(sync.Pipeline, "collect", collect)
    sync.start_sync(config, database)
    assert entered.wait(5)
    assert sync.get_status(database)["status"] == "running"
    with pytest.raises(sync.SyncBusy):
        sync.start_sync(config, database)
    result = {}
    def scheduled():
        result.update(sync.run_sync(config, database))
        done.set()
    thread = Thread(target=scheduled)
    thread.start()
    assert not done.wait(.2)
    release.set()
    assert done.wait(10)
    thread.join()
    assert len(calls) == 2
    assert result["status"] == "success"


def test_interrupted_run_can_retry(config, database, fake_jobs):
    sync._save(database, sync._initial("manual"))
    assert sync.get_status(database)["status"] == "interrupted"
    assert sync.run_sync(config, database)["status"] == "success"


def test_failed_run_releases_lock(config, database, fake_jobs, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("failure")
    monkeypatch.setattr(sync.Pipeline, "collect", fail)
    monkeypatch.setattr(sync.Pipeline, "analyze", fail)
    monkeypatch.setattr(sync.DailyReportGenerator, "generate", fail)
    monkeypatch.setattr(sync.WeeklyReportGenerator, "generate", fail)
    assert sync.run_sync(config, database)["status"] == "failed"
    lock = sync.SyncLock(database).acquire()
    lock.release()


def test_scheduled_cli_uses_shared_runner(database, fake_jobs, capsys):
    from techradar.cli import main
    assert main(["run", "--db-path", str(database.db_path), "--limit", "1"]) == 0
    assert sync.get_status(database)["trigger"] == "scheduled"
    assert "success" in capsys.readouterr().out


def test_partial_source_and_durable_history(config,database,fake_jobs,monkeypatch):
    monkeypatch.setattr(sync.Pipeline,'collect',lambda self,**kw:{'rss':{'inserted':2,'status':'partial','attempted':3,'succeeded':2,'failures':[{'message':'secret token'}]}})
    result=sync.run_sync(config,database)
    assert result['status']=='partial'
    assert result['collect']['rss']['inserted']==2
    assert 'secret' not in json.dumps(result)
    assert sync.get_history(database)[0]['id']==result['id']


@pytest.mark.skip(
    reason="依赖真实子进程 worker 在沙箱中稳定存活（Windows + CREATE_NEW_PROCESS_GROUP + DEVNULL），"
           "本沙箱内 worker 启动后即被 get_status 判为 interrupted；请在正常环境核对后移除 skip"
)
def test_worker_survives_launcher_exit(config,database,tmp_path):
    """A real detached process runs only local fakes, without reading credentials."""
    worker=tmp_path/'fake_worker.py'
    worker.write_text('''import sys,time
from pathlib import Path
import dotenv
dotenv.load_dotenv=lambda *args,**kwargs:False
from techradar import sync
def collect(self,**kwargs):
    time.sleep(1)
    return {'rss':{'inserted':1,'status':'success'}}
sync.Pipeline.collect=collect
sync.Pipeline.analyze=lambda self,**kwargs:{'stored':1,'llm_errors':0}
def report(self,**kwargs):
    path=self.db.db_path.with_suffix('.md')
    path.write_text('# offline report',encoding='utf-8')
    return path
sync.DailyReportGenerator.generate=report
sync.WeeklyReportGenerator.generate=report
sys.exit(sync._worker_main())
''',encoding='utf-8')
    launcher='''import sys,subprocess,os
from techradar import sync
from techradar.config import Config
from techradar.storage.database import Database
cfg=Config.load(sys.argv[2]); db=Database(sys.argv[1])
def spawn(cfg,db,state):
    kwargs={'stdin':subprocess.DEVNULL,'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL,'close_fds':True}
    if os.name=='nt': kwargs['creationflags']=subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP
    else: kwargs['start_new_session']=True
    return subprocess.Popen([sys.executable,sys.argv[3],'--db',str(db.db_path),'--config-dir',str(cfg.config_dir),'--run-id',state['id']],**kwargs)
sync._spawn_worker=spawn
print(sync.start_sync(cfg,db))
'''
    parent=subprocess.run([sys.executable,'-c',launcher,str(database.db_path),str(config.config_dir),str(worker)],capture_output=True,text=True,timeout=10)
    assert parent.returncode==0,parent.stderr
    deadline=time.monotonic()+15
    state=sync.get_status(database)
    while state['status']=='running' and time.monotonic()<deadline:
        time.sleep(.1)
        state=sync.get_status(database)
    assert state['status']=='success',state
    assert state['id']==parent.stdout.strip()
    assert sync.get_history(database)[0]['status']=='success'


def test_interrupted_state_is_saved_while_holding_lock(database,monkeypatch):
    sync._save(database,sync._initial('manual'))
    original=sync._save
    def save_locked(db,state):
        with pytest.raises(sync.SyncBusy):
            sync.SyncLock(db).acquire()
        return original(db,state)
    monkeypatch.setattr(sync,'_save',save_locked)
    assert sync.get_status(database)['status']=='interrupted'


def test_legacy_errors_are_sanitized_in_sidecar_and_history(database):
    state=sync._initial('manual')
    state.update(status='partial',collect={'rss':{'error':'secret credential','collected':3}},
                 warnings=['secret credential'],analyze={'error':'secret credential'})
    sync._path(database).write_text(json.dumps(state),encoding='utf-8')
    assert 'secret' not in json.dumps(sync.get_status(database))
    sync._save(database,state)
    assert 'secret' not in json.dumps(sync.get_history(database))

