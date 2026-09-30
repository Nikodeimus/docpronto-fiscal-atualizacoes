"""Updater acceptance tests; all packages/installers/data are synthetic and temporary."""
import hashlib
import json
from pathlib import Path
import stat
import zipfile
import pytest
import local_updates as updates
from local_runtime import InstanceLock


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def package(path, version='1.9.0', entries=None, extra=None, prefix='release/'):
    content = {name: ('synthetic ' + name).encode() for name in updates.REQUIRED}
    if entries:
        content.update(entries)
    manifest = {'version': version, 'files': [{'path': name, 'sha256': hashlib.sha256(raw).hexdigest()} for name, raw in content.items()]}
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, raw in content.items():
            archive.writestr(prefix + name, raw)
        archive.writestr(prefix + 'local-manifest.json', json.dumps(manifest))
        if extra:
            archive.writestr(*extra)
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def release(tmp_path):
    state = tmp_path / 'DocProntoLocal'
    root = tmp_path / 'installed'
    channel = tmp_path / 'channel'
    channel.mkdir()
    write_json(root / 'local-manifest.json', {'version': '1.8.19', 'files': []})
    archive = channel / 'release.zip'
    digest = package(archive)
    feed = channel / 'latest.json'
    write_json(feed, {'version': '1.9.0', 'package': 'release.zip', 'sha256': digest})
    return state, root, feed, archive


def configure(release):
    state, root, feed, archive = release
    updates.save_settings(state, str(feed), True)
    return state, root, feed, archive


def test_without_source_never_uses_network(release, monkeypatch):
    state, root, _, _ = release
    monkeypatch.setattr(updates, '_download', lambda *args: pytest.fail('unexpected download'))
    assert updates.settings(state) == {'source': '', 'automatic': True}
    assert updates.check(state, root)['state'] == 'not_configured'


@pytest.mark.parametrize('source', ['http://example.test/latest.json', 'file:///C:/release.json', '../latest.json', 'https://user:password@example.test/latest.json', 'https://example.test/latest.json#fragment'])
def test_rejects_unsafe_source(release, source):
    state, _, _, _ = release
    with pytest.raises(ValueError):
        updates.save_settings(state, source, True)


@pytest.mark.parametrize('host', ['127.0.0.1', 'localhost', '[::1]'])
def test_explicit_loopback_http_source_allowed(release, host):
    state, _, _, _ = release
    source = 'http://' + host + ':8092/latest.json'
    assert updates.save_settings(state, source, True)['source'] == source


@pytest.mark.parametrize('host', ['localhost.evil.test', '127.0.0.1.evil.test', '192.168.1.2', '127.1'])
def test_nonloopback_http_and_redirect_rejected(host):
    with pytest.raises(ValueError):
        updates._https('http://' + host + '/app.zip')
    with pytest.raises(ValueError):
        updates._SecureRedirect().redirect_request(None, None, 302, '', {}, 'http://' + host + '/app.zip')


def test_directory_source_and_complete_staging_never_installs(release, monkeypatch):
    state, root, feed, archive = release
    updates.save_settings(state, str(feed.parent), True)
    monkeypatch.setattr(updates, '_run_installer', lambda *args: pytest.fail('check must not install'))
    result = updates.check(state, root)
    assert result['state'] == 'ready', result
    stage = updates._json(state / 'updates/stage.json')
    assert stage['version'] == '1.9.0'
    assert Path(stage['root']).is_relative_to(state / 'updates/staged')
    assert (Path(stage['root']) / 'scripts/install-local.ps1').is_file()
    assert result['current_version'] == '1.8.19'
    assert updates._json(root / 'local-manifest.json')['version'] == '1.8.19'


def test_https_feed_and_package_use_mock_download(release, monkeypatch):
    state, root, feed, archive = release
    digest = updates._digest(archive)
    calls = []
    def mocked(source, target, limit):
        calls.append(source)
        raw = json.dumps({'version': '1.9.0', 'package': 'https://releases.test/app.zip', 'sha256': digest}).encode() if source.endswith('.json') else archive.read_bytes()
        target.write_bytes(raw)
    monkeypatch.setattr(updates, '_download', mocked)
    updates.save_settings(state, 'https://releases.test/latest.json', True)
    assert updates.check(state, root)['state'] == 'ready'
    assert calls == ['https://releases.test/latest.json', 'https://releases.test/app.zip']


def test_available_only_does_not_download_zip(release):
    state, root, _, _ = configure(release)
    assert updates.check(state, root, download=False)['state'] == 'available'
    assert not (state / 'updates/stage.json').exists()
    assert not list((state / 'updates/staged').glob('*/package.zip'))


def test_bad_sha_rejected_without_stage(release):
    state, root, feed, _ = configure(release)
    value = updates._json(feed)
    value['sha256'] = '0' * 64
    write_json(feed, value)
    result = updates.check(state, root)
    assert result['state'] == 'error' and 'SHA-256' in result['message']
    assert result['checked_at'] > 0
    assert not (state / 'updates/stage.json').exists()


@pytest.mark.parametrize('extra', [('../escape.txt', b'bad'), ('release/CON.txt', b'bad'), ('release/app:stream', b'bad'), ('release/unlisted.txt', b'bad')])
def test_unsafe_zip_entries_or_unlisted_files_rejected(release, extra):
    state, root, feed, archive = configure(release)
    digest = package(archive, extra=extra)
    write_json(feed, {'version': '1.9.0', 'package': 'release.zip', 'sha256': digest})
    assert updates.check(state, root)['state'] == 'error'
    assert not (state / 'updates/stage.json').exists()
    assert not (state / 'updates/staged/escape.txt').exists()


def test_zip_symlink_rejected(release):
    state, root, feed, archive = configure(release)
    item = zipfile.ZipInfo('release/link')
    item.create_system = 3
    item.external_attr = (stat.S_IFLNK | 0o777) << 16
    digest = package(archive, extra=(item, b'../../outside'))
    write_json(feed, {'version': '1.9.0', 'package': 'release.zip', 'sha256': digest})
    assert updates.check(state, root)['state'] == 'error'


def test_fiscal_data_never_in_update_package(release):
    state, root, feed, archive = configure(release)
    digest = package(archive, entries={'data/app.db': b'synthetic-not-a-database'})
    write_json(feed, {'version': '1.9.0', 'package': 'release.zip', 'sha256': digest})
    result = updates.check(state, root)
    assert result['state'] == 'error' and 'Dados de aplicação' in result['message']


def test_manifest_version_and_file_hash_validated(release):
    state, root, feed, archive = configure(release)
    digest = package(archive, version='1.8.20')
    write_json(feed, {'version': '1.9.0', 'package': 'release.zip', 'sha256': digest})
    assert updates.check(state, root)['state'] == 'error'


def test_extraction_limit(release, monkeypatch):
    state, root, _, _ = configure(release)
    monkeypatch.setattr(updates, 'MAX_EXPANDED', 5)
    assert updates.check(state, root)['state'] == 'error'


def test_apply_mock_installer_and_preserve_data(release, monkeypatch):
    state, root, _, _ = configure(release)
    fiscal = state / 'data/app.db'
    fiscal.parent.mkdir()
    fiscal.write_bytes(b'SYNTHETIC-DATA-UNCHANGED')
    assert updates.check(state, root)['state'] == 'ready'
    calls = []
    monkeypatch.setattr(updates, '_run_installer', lambda script, state, log: calls.append(script) or 0)
    assert updates.apply(state, root) is True
    assert calls[0].name == 'install-local.ps1'
    assert updates.status(state, root)['state'] == 'updated'
    assert fiscal.read_bytes() == b'SYNTHETIC-DATA-UNCHANGED'
    assert not (state / 'updates/stage.json').exists()


def test_failed_installer_returns_false_and_keeps_stage(release, monkeypatch):
    state, root, _, _ = configure(release)
    updates.check(state, root)
    monkeypatch.setattr(updates, '_run_installer', lambda *args: 1)
    assert updates.apply(state, root) is False
    assert updates.status(state, root)['state'] == 'error'
    assert (state / 'updates/stage.json').exists()
    assert updates._current_version(root) == '1.8.19'


def test_tampered_staged_file_refuses_install(release, monkeypatch):
    state, root, _, _ = configure(release)
    updates.check(state, root)
    stage = updates._json(state / 'updates/stage.json')
    (Path(stage['root']) / 'scripts/install-local.ps1').write_bytes(b'changed')
    monkeypatch.setattr(updates, '_run_installer', lambda *args: pytest.fail('tampered installer invoked'))
    assert updates.apply(state, root) is False


def test_source_change_invalidates_old_stage(release, monkeypatch):
    state, root, _, _ = configure(release)
    updates.check(state, root)
    updates.save_settings(state, 'https://new-channel.test/latest.json', False)
    assert not (state / 'updates/stage.json').exists()
    monkeypatch.setattr(updates, '_run_installer', lambda *args: pytest.fail('old channel invoked'))
    assert updates.apply(state, root) is False


def test_already_current_and_older_release_not_offered(release):
    state, root, _, _ = configure(release)
    write_json(root / 'local-manifest.json', {'version': '2.0.0'})
    assert updates.check(state, root)['state'] == 'current'


def test_busy_lock_does_not_override_status(release):
    state, root, _, _ = configure(release)
    lock = InstanceLock(state / 'updates/updates.lock')
    try:
        assert updates.check(state, root)['state'] == 'busy'
        assert updates.apply(state, root) is False
        with pytest.raises(OSError):
            updates.save_settings(state, '', False)
    finally:
        lock.close()


def test_interrupted_operation_recovers_to_error(release):
    state, root, _, _ = configure(release)
    updates._record(state, 'checking', 'Synthetic interrupted check')
    assert updates.status(state, root)['state'] == 'error'
    assert updates.check(state, root)['state'] == 'ready'


def test_current_manifest_matching_stage_is_reported_updated(release):
    state, root, _, _ = configure(release)
    updates.check(state, root)
    write_json(root / 'local-manifest.json', {'version': '1.9.0'})
    assert updates.status(state, root)['state'] == 'updated'
