"""Shared manual/scheduled sync runner with cross-process exclusion and progress."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import re
import time
from uuid import uuid4

from techradar.config import Config
from techradar.pipeline import Pipeline
from techradar.reports.daily import DailyReportGenerator
from techradar.reports.weekly import WeeklyReportGenerator
from techradar.storage.database import Database
from techradar.utils.time import utcnow


class SyncBusy(RuntimeError):
    pass


class SyncLock:
    """OS-held lock: automatically released if the owning process exits."""
    def __init__(self, db: Database):
        self.path = db.db_path.with_suffix(".sync.lock")
        self.file = None

    def acquire(self, wait=False):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        if self.file.seek(0, 2) == 0:
            self.file.write(b"0")
            self.file.flush()
        while True:
            self.file.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if not wait:
                    self.file.close()
                    self.file = None
                    raise SyncBusy("已有同步正在执行")
                time.sleep(0.5)

    def release(self):
        if self.file:
            try:
                self.file.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            finally:
                self.file.close()
                self.file = None


def _path(db):
    return db.db_path.with_suffix(".sync.json")


def _safe_state(state):
    """旧 sidecar 与新历史统一展示边界，不回显 provider 原始异常。"""
    state = dict(state) if isinstance(state,dict) else {'status':'idle'}
    count_fields = ('collected','dropped','duplicates','inserted','updated','unchanged','attempted','succeeded')
    clean = {}
    for source,entry in state.get('collect',{}).items():
        if not re.fullmatch(r'[a-z_]{1,40}',source) or not isinstance(entry,dict):
            continue
        values = {key:entry[key] for key in count_fields if isinstance(entry.get(key),int)}
        if entry.get('status') in ('success','partial','failed'):
            values['status'] = entry['status']
        if entry.get('error'):
            values['status'] = 'failed' if not entry.get('succeeded') else 'partial'
        clean[source] = values
    state['collect'] = clean
    state['analyze'] = {key:value for key,value in state.get('analyze',{}).items() if isinstance(value,(int,float))}
    safe_warnings = []
    fixed = {'部分分析未完成，请检查分析服务', '采集最新资料失败，请检查服务配置或网络',
             '分析与相关度评分失败，请检查服务配置或网络','更新日报失败，请检查服务配置或网络',
             '更新周报失败，请检查服务配置或网络','分析期间原文已更新，旧结果已弃用，等待下次重新分析'}
    for warning in state.get('warnings',[]):
        if warning in fixed or re.fullmatch(r'[a-z_]{1,40} 采集未完成',str(warning)):
            safe_warnings.append(warning)
        else:
            safe_warnings.append('上次同步存在错误，请重新同步查看当前状态')
    state['warnings'] = list(dict.fromkeys(safe_warnings))
    reasons = {'来源请求失败或覆盖未完成','分析服务不可用或结果校验失败，条目保留待重试','步骤执行失败，请检查服务配置或网络'}
    failures = []
    for failure in state.get('failures',[]):
        if not isinstance(failure,dict):
            continue
        value = {'stage':failure.get('stage') if failure.get('stage') in ('collect','analyze','daily','weekly') else 'unknown',
                 'reason':failure.get('reason') if failure.get('reason') in reasons else '步骤执行失败，请检查服务配置或网络'}
        if re.fullmatch(r'[a-z_]{1,40}',str(failure.get('source',''))):
            value['source'] = failure['source']
        failures.append(value)
    state['failures'] = failures
    return state


def _save(db, state):
    state = _safe_state(state)
    destination = _path(db)
    temporary = destination.with_name(destination.name + "." + uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, destination)
    conn = db.connect()
    try:
        with conn:
            conn.execute('''INSERT INTO sync_runs(id,started_at,status,state) VALUES(?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET status=excluded.status,state=excluded.state''',
                (state['id'],state['started_at'],state['status'],json.dumps(state,ensure_ascii=False)))
    finally:
        conn.close()


def get_history(db, limit=10):
    conn = db.connect()
    try:
        rows = conn.execute('SELECT state FROM sync_runs ORDER BY started_at DESC LIMIT ?', (max(1,int(limit)),)).fetchall()
        return [_safe_state(json.loads(row['state'])) for row in rows]
    finally:
        conn.close()


def _process_alive(pid):
    if not pid:
        return False
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.argtypes = (wintypes.DWORD,wintypes.BOOL,wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD))
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000,False,int(pid))
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(handle,ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(int(pid),0)
        return True
    except OSError:
        return False


def get_status(db):
    probe = SyncLock(db)
    try:
        probe.acquire()
    except SyncBusy:
        state = _read_state(db) or {'status':'idle'}
        if state.get("status") != "running":
            return {**state, "status": "running", "stage": "正在准备同步"}
        return state
    else:
        try:
            state = _read_state(db) or {'status':'idle'}
            if state.get("status") == "running" and not _process_alive(state.get('worker_pid')):
                state = {**state, "status": "interrupted", "stage": "上次同步被中断，可以重新同步"}
                _save(db,state)
            return state
        finally:
            probe.release()


def _initial(trigger):
    return {"id": uuid4().hex, "status": "running", "trigger": trigger,
            "started_at": utcnow().isoformat(), "stage": "准备同步",
            "progress": 0, "warnings": [], "failures": [], "collect": {}, "analyze": {}, "reports": {}}


def _execute(cfg, db, lock, state, limit=None, out_dir=None):
    start = time.monotonic()
    try:
        pipeline = Pipeline(cfg, db)
        jobs = [
            ("采集最新资料", lambda: pipeline.collect(limit=limit), "collect"),
            ("分析与相关度评分", lambda: pipeline.analyze(limit=limit), "analyze"),
            ("更新日报", lambda: str(DailyReportGenerator(cfg, db).generate(out_dir=out_dir)), "daily"),
            ("更新周报", lambda: str(WeeklyReportGenerator(cfg, db).generate(out_dir=out_dir)), "weekly"),
        ]
        completed = 0
        for index, (label, job, key) in enumerate(jobs):
            state.update(stage=label, progress=index / len(jobs))
            _save(db, state)
            try:
                result = job()
                if key in ("daily", "weekly"):
                    if not Path(result).is_file() or Path(result).stat().st_size == 0:
                        raise RuntimeError('report not generated')
                    state["reports"][key] = result
                    completed += 1
                else:
                    state[key] = result
                if key == "collect":
                    for source, values in result.items():
                        if "error" in values or values.get('status') in ('partial','failed') or values.get('failures'):
                            state["warnings"].append(f"{source} 采集未完成")
                            state['failures'].append({'stage':'collect','source':source,'reason':'来源请求失败或覆盖未完成'})
                    allowed = ('collected','dropped','duplicates','inserted','updated','unchanged','attempted','succeeded','status')
                    state[key] = {source: {k: v for k, v in values.items() if k in allowed} for source, values in result.items()}
                    if any('error' not in v and v.get('status') != 'failed' for v in result.values()):
                        completed += 1
                elif key == "analyze":
                    if result.get("error") or result.get("llm_errors", 0):
                        state["warnings"].append("部分分析未完成，请检查分析服务")
                        state['failures'].append({'stage':'analyze','reason':'分析服务不可用或结果校验失败，条目保留待重试'})
                    if result.get('stale_skipped',0):
                        state['warnings'].append('分析期间原文已更新，旧结果已弃用，等待下次重新分析')
                    if not result.get('error'):
                        completed += 1
                    state[key] = {k: v for k, v in result.items() if k != "error"}
            except Exception:
                state["warnings"].append(f"{label}失败，请检查服务配置或网络")
                state['failures'].append({'stage':key,'reason':'步骤执行失败，请检查服务配置或网络'})
        state.update(status=("failed" if completed == 0 else "partial" if state["warnings"] else "success"),
                     stage="同步完成", progress=1,
                     finished_at=utcnow().isoformat(), duration=round(time.monotonic() - start, 1))
        _save(db, state)
        return state
    except Exception:
        state.update(status="failed", stage="同步中断，请重试", finished_at=utcnow().isoformat())
        try:
            _save(db, state)
        except OSError:
            pass
        return state
    finally:
        lock.release()


def run_sync(cfg, db, *, trigger="scheduled", limit=None, out_dir=None):
    """CLI waits for a manual run, preserving the scheduled run instead of dropping it."""
    lock = SyncLock(db).acquire(wait=True)
    try:
        state = _initial(trigger)
        _save(db, state)
    except Exception:
        lock.release()
        raise
    return _execute(cfg, db, lock, state, limit, out_dir)


def start_sync(cfg, db):
    """独立进程执行；关闭页面或重启 UI 不会取消刷新任务。"""
    lock = SyncLock(db).acquire()
    try:
        if get_status(db).get('status') == 'running' and _process_alive(_read_state(db).get('worker_pid')):
            raise SyncBusy('已有同步正在执行')
        state = _initial("manual")
        _save(db, state)
        process = _spawn_worker(cfg,db,state)
        state['worker_pid'] = process.pid
        _save(db,state)
        return state["id"]
    except Exception:
        if 'state' in locals():
            state.update(status='failed',stage='后台任务启动失败',finished_at=utcnow().isoformat())
            _save(db,state)
        raise
    finally:
        lock.release()


def _read_state(db):
    try:
        return _safe_state(json.loads(_path(db).read_text(encoding='utf-8')))
    except (OSError,ValueError):
        return {}


def _spawn_worker(cfg,db,state):
    command = [sys.executable,'-m','techradar.sync','--db',str(db.db_path.resolve()),
               '--config-dir',str(cfg.config_dir.resolve()),'--run-id',state['id']]
    kwargs = {'stdin':subprocess.DEVNULL,'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL,'close_fds':True}
    if os.name == 'nt':
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs['start_new_session'] = True
    return subprocess.Popen(command,**kwargs)


def _worker_main():
    import argparse
    from dotenv import load_dotenv
    from techradar.config import project_root
    parser = argparse.ArgumentParser()
    parser.add_argument('--db',required=True)
    parser.add_argument('--config-dir',required=True)
    parser.add_argument('--run-id',required=True)
    args = parser.parse_args()
    db = Database(args.db)
    lock = SyncLock(db).acquire(wait=True)
    state = _read_state(db)
    if state.get('id') != args.run_id:
        state = _initial('manual')
        state['id'] = args.run_id
    state['worker_pid'] = os.getpid()
    try:
        load_dotenv(project_root()/'.env')
        cfg = Config.load(args.config_dir)
        db.init()
        _save(db,state)
    except Exception:
        state.update(status='failed',stage='配置加载失败',finished_at=utcnow().isoformat())
        _save(db,state)
        lock.release()
        return 1
    result = _execute(cfg,db,lock,state)
    return 1 if result['status']=='failed' else 0


if __name__ == '__main__':
    raise SystemExit(_worker_main())

