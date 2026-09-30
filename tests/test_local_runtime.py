import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request

import pytest
import local_runtime as runtime

def test_config_preserves_token_and_isolates_docker(tmp_path, monkeypatch):
    monkeypatch.setattr(os, 'environ', os.environ.copy())
    monkeypatch.setenv('DATABASE_URL', 'postgresql://legacy-host/do-not-use')
    token = runtime.configure(tmp_path)
    assert runtime.configure(tmp_path) == token
    assert len(token) >= 32
    assert os.environ['DATABASE_URL'] == 'sqlite:///' + (tmp_path / 'data/app.db').as_posix()
    assert os.environ['BIND_HOST'] == '127.0.0.1'
    assert os.environ['DOCPRONTO_ADAPTER_COMMAND'] == ''

def test_exclusive_lock_released(tmp_path):
    first = runtime.InstanceLock(tmp_path / 'lock')
    with pytest.raises(OSError): runtime.InstanceLock(tmp_path / 'lock')
    first.close()
    runtime.InstanceLock(tmp_path / 'lock').close()

def test_preupdate_backup_preserves_database(tmp_path):
    (tmp_path / 'data').mkdir()
    with sqlite3.connect(tmp_path / 'data/app.db') as db:
        db.execute('create table sample(value text)')
        db.execute("insert into sample values('preserved')")
    runtime.backup(tmp_path)
    copies = list((tmp_path / 'backups').glob('*/app.db'))
    assert len(copies) == 1
    with sqlite3.connect(copies[0]) as db:
        assert db.execute('select value from sample').fetchone()[0] == 'preserved'

def test_health_rejects_other_instance(monkeypatch):
    monkeypatch.setattr(runtime, 'status', lambda: {'local_instance': 'other'})
    class Child:
        def poll(self): return None
    with pytest.raises(RuntimeError): runtime.wait_ready('wanted', Child(), timeout=.01)

def test_health_detects_dead_worker():
    class Child:
        def __init__(self, code): self.code = code
        def poll(self): return self.code
    with pytest.raises(RuntimeError): runtime.wait_ready('wanted', Child(None), Child(1))

def test_supervisor_real_http_restart_and_no_duplicate(tmp_path):
    with socket.socket() as probe:
        if os.name != 'nt': probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try: probe.bind(('127.0.0.1',8080))
        except OSError: pytest.skip('Port 8080 used by another test/service')
    env = os.environ.copy()
    env['LOCALAPPDATA'] = str(tmp_path)
    root = Path(runtime.__file__).parent
    state = tmp_path / 'DocProntoLocal'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def start():
        p = subprocess.Popen([sys.executable, str(root / 'local_runtime.py'), 'serve'], cwd=root, env=env)
        for _ in range(120):
            if p.poll() is not None: raise AssertionError('Supervisor exited early')
            if (state / 'ready.json').exists(): return p
            time.sleep(.1)
        p.terminate();p.wait();raise AssertionError('No readiness')
    p = start()
    try:
        with opener.open(runtime.URL+'/api/status') as response: initial=json.load(response)
        assert initial['mode']=='windows-local' and initial['needs_setup']
        token=json.loads((state/'local.json').read_text())['setup_token']
        request=urllib.request.Request(runtime.URL+'/api/bootstrap', data=json.dumps(dict(
            setup_token=token,email='local@example.test',password='local-test-password')).encode(),
            headers={'Content-Type':'application/json'})
        with opener.open(request) as response: assert response.status==200
        result=subprocess.run([sys.executable,str(root/'local_runtime.py'),'serve'],cwd=root,env=env,timeout=10)
        assert result.returncode==0
        with opener.open(runtime.URL+'/api/status') as response:
            assert json.load(response)['local_instance']==initial['local_instance']
    finally:
        (state/'stop.request').touch();p.wait(timeout=20)
    assert not (state/'ready.json').exists()
    p=start()
    try:
        with opener.open(runtime.URL+'/api/status') as response: assert json.load(response)['needs_setup'] is False
        assert json.loads((state/'local.json').read_text())['setup_token']==token
    finally:
        (state/'stop.request').touch();p.wait(timeout=20)
