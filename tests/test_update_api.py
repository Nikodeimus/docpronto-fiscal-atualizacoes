import json
from types import SimpleNamespace

import pytest
from test_app import env
import local_runtime
import local_updates
from app import local_maintenance as maintenance


@pytest.fixture
def installed(env, tmp_path, monkeypatch):
    state = tmp_path / 'installed'
    state.mkdir()
    monkeypatch.setattr(local_runtime, 'state_root', lambda: state)
    monkeypatch.setattr(maintenance, 'installed_here', lambda *_: True)
    return env, state


def test_update_endpoints_require_auth_and_installation_admin(installed):
    (app, client, headers, cid), state = installed
    assert app.test_client().get('/api/updates').status_code == 401
    client.post('/api/users', json={'company': cid, 'email': 'operator@example.test',
        'password': 'long-operator-password', 'role': 'operator'}, headers=headers)
    operator = app.test_client()
    token = operator.post('/api/login', json={'email': 'operator@example.test',
        'password': 'long-operator-password'}).json['csrf']
    reply = operator.get('/api/updates')
    assert reply.status_code == 200 and reply.json['manageable'] is False
    for route in ('settings', 'check', 'apply'):
        assert operator.post('/api/updates/' + route, json={}, headers={'X-CSRF-Token': token}).status_code == 403
    assert not (state / 'updates').exists() or not list((state / 'updates').glob('*.zip'))


def test_update_api_disabled_for_uninstalled_copy(installed, monkeypatch):
    (app, client, headers, cid), state = installed
    monkeypatch.setattr(maintenance, 'installed_here', lambda *_: False)
    assert client.get('/api/updates').json['supported'] is False
    for route in ('settings', 'check', 'apply'):
        assert client.post('/api/updates/' + route, json={}, headers=headers).status_code == 409


def test_update_settings_and_no_source_no_launch(installed, tmp_path, monkeypatch):
    (app, client, headers, cid), state = installed
    calls = []
    monkeypatch.setattr(local_runtime, 'launch_update', lambda *a, **kw: calls.append((a, kw)))
    assert client.post('/api/updates/check', json={}, headers=headers).status_code == 400
    assert client.post('/api/updates/settings', json={'source': str(tmp_path), 'automatic': 'false'}, headers=headers).status_code == 400
    response = client.post('/api/updates/settings', json={'source': str(tmp_path), 'automatic': True}, headers=headers)
    assert response.status_code == 200, response.json
    assert local_updates.settings(state)['automatic'] is True
    assert client.post('/api/updates/check', json={}, headers=headers).status_code == 202
    assert len(calls) == 1 and calls[0][0] == ('check',)
    assert client.post('/api/updates/apply', json={}, headers=headers).status_code == 409


def test_update_apply_requires_ready_and_csrf(installed, monkeypatch):
    (app, client, headers, cid), state = installed
    calls = []
    monkeypatch.setattr(local_updates, 'status', lambda *_: {'state': 'ready'})
    monkeypatch.setattr(local_runtime, 'launch_update', lambda *a, **kw: calls.append(a))
    assert client.post('/api/updates/apply', json={}).status_code == 403
    assert client.post('/api/updates/apply', json={}, headers=headers).status_code == 202
    assert calls == [('apply',)]


def test_automatic_updates_wait_for_interval_and_never_install(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(local_updates, 'settings', lambda _: {'source': str(tmp_path), 'automatic': True})
    state = {'state': 'current', 'checked_at': 100}
    monkeypatch.setattr(local_updates, 'status', lambda *_: state)
    monkeypatch.setattr(local_runtime, 'launch_update', lambda *a, **kw: calls.append(a))
    assert not local_runtime.automatic_update_check(tmp_path, now=200)
    assert local_runtime.automatic_update_check(tmp_path, now=100 + local_runtime.UPDATE_INTERVAL)
    state.update(state='ready', checked_at=0)
    assert not local_runtime.automatic_update_check(tmp_path, now=100 + local_runtime.UPDATE_INTERVAL)
    assert calls == [('check',)]
    monkeypatch.setattr(local_updates, 'settings', lambda _: {'source': str(tmp_path), 'automatic': False})
    state['state'] = 'error'
    assert not local_runtime.automatic_update_check(tmp_path, now=100 + local_runtime.UPDATE_INTERVAL)


def test_update_launch_is_hidden_detached_from_http_request(tmp_path, monkeypatch):
    root = tmp_path / 'release'
    (root / 'runtime').mkdir(parents=True)
    (root / 'runtime/pythonw.exe').touch()
    calls = []
    def popen(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(pid=123)
    monkeypatch.setattr(local_runtime.subprocess, 'Popen', popen)
    assert local_runtime.launch_update('apply', root=root, state=tmp_path) == 123
    assert calls[0][0] == [str(root / 'runtime/pythonw.exe'), str(root / 'local_updates.py'), 'apply']
    assert calls[0][1]['creationflags'] == local_runtime.NO_WINDOW
    with pytest.raises(ValueError):
        local_runtime.launch_update('arbitrary', root=root, state=tmp_path)


def test_only_matching_active_installation_is_supported(tmp_path, monkeypatch):
    monkeypatch.setenv('DOCPRONTO_LOCAL_INSTANCE', 'test-instance')
    name = 'release-' + 'a' * 32
    (tmp_path / 'active.txt').write_text(name)
    root = tmp_path / 'releases' / name
    if maintenance.os.name == 'nt':
        assert maintenance.installed_here(tmp_path, root)
    assert not maintenance.installed_here(tmp_path, tmp_path / 'other')
    (tmp_path / 'active.txt').write_text('../../other')
    assert not maintenance.installed_here(tmp_path, root)


def test_background_failure_logs_and_releases_lock_without_dialog(tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(os, 'environ', os.environ.copy())
    monkeypatch.setattr(local_runtime, 'state_root', lambda: tmp_path)
    monkeypatch.setattr(local_runtime.sys, 'argv', ['local_runtime.py', 'serve'])
    def no_dialog(message):
        raise AssertionError('Background failure opened a modal dialog')
    monkeypatch.setattr(local_runtime, 'inform', no_dialog)
    class OccupiedPort:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def setsockopt(self, *args): pass
        def bind(self, *args): raise OSError('test port occupied')
    monkeypatch.setattr(local_runtime.socket, 'socket', OccupiedPort)
    assert local_runtime.main() == 1
    assert '8080' in (tmp_path / 'logs/supervisor.log').read_text(encoding='utf-8')
    local_runtime.InstanceLock(tmp_path / 'running.lock').close()
