from types import SimpleNamespace
import pytest
import local_runtime as runtime


class Child:
    def __init__(self, code=None):
        self.code = code
    def poll(self):
        return self.code


def test_restart_only_dead_child_and_confirm_readiness(tmp_path, monkeypatch):
    dead, alive, replacement = Child(1), Child(), Child()
    children = [dead, alive]
    launches, checks = [], []
    monkeypatch.setattr(runtime.subprocess, 'Popen', lambda *a, **k: launches.append(a) or replacement)
    monkeypatch.setattr(runtime, 'wait_ready', lambda instance, *p: checks.append(p) or {})
    monkeypatch.setattr(runtime.time, 'sleep', lambda _: None)
    attempts = []
    assert runtime.recover_children(tmp_path, 'instance', children, [None, None], attempts)
    assert children == [replacement, alive]
    assert len(launches) == 1 and checks == [(replacement, alive)]
    assert len(attempts) == 1
    assert (tmp_path / 'ready.json').exists()


def test_stop_during_backoff_does_not_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.time, 'sleep', lambda _: (tmp_path / 'stop.request').touch())
    monkeypatch.setattr(runtime.subprocess, 'Popen', lambda *a, **k: pytest.fail('restarted after stop'))
    assert not runtime.recover_children(tmp_path, 'instance', [Child(1), Child()], [None, None], [])


def test_repeated_crash_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.time, 'monotonic', lambda: 100)
    monkeypatch.setattr(runtime.subprocess, 'Popen', lambda *a, **k: pytest.fail('restart limit ignored'))
    with pytest.raises(RuntimeError, match='3'):
        runtime.recover_children(tmp_path, 'instance', [Child(1), Child()], [None, None], [90, 91, 92])


def test_healthy_children_and_explicit_stop_are_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.subprocess, 'Popen', lambda *a, **k: pytest.fail('unexpected launch'))
    assert runtime.recover_children(tmp_path, 'instance', [Child(), Child()], [None, None], [])
    (tmp_path / 'stop.request').touch()
    assert not runtime.recover_children(tmp_path, 'instance', [Child(1), Child()], [None, None], [])
