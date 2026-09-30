import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('update_server', Path(__file__).parents[1] / 'scripts' / 'update-server.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def channel(tmp_path, monkeypatch):
    monkeypatch.delenv('DOCPRONTO_UPDATE_TOKEN', raising=False)
    payload = b'PK synthetic package bytes'
    filename = 'DocPronto-Fiscal-1.8.20-test.zip'
    (tmp_path / filename).write_bytes(payload)
    feed = {'version': '1.8.20', 'package': filename, 'sha256': hashlib.sha256(payload).hexdigest(), 'published_at': 123}
    (tmp_path / 'latest.json').write_text(json.dumps(feed), encoding='utf-8')
    return tmp_path, feed, payload


def test_local_feed_and_stream_checksum(channel):
    root, original, payload = channel
    client = module.create_update_server(root).test_client()
    response = client.get('/latest.json', base_url='http://untrusted.example:8092')
    feed = response.json
    assert feed['package'] == 'http://127.0.0.1:8092/packages/' + original['package']
    assert feed['sha256'] == original['sha256']
    assert feed['published_at'] == 123
    response = client.get('/packages/' + original['package'], buffered=False)
    assert response.is_streamed
    assert hashlib.sha256(response.data).hexdigest() == original['sha256']
    assert response.data == payload
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    response.close()


def test_https_proxy_prefix(channel):
    root, feed, _ = channel
    client = module.create_update_server(root, 'https://updates.example/fiscal/').test_client()
    assert client.get('/latest.json').json['package'] == 'https://updates.example/fiscal/packages/' + feed['package']


@pytest.mark.parametrize('url', ['http://example.com', 'https://user:pass@example.com', 'https://example.com?q=1', 'https://example.com/#token', 'https://bad host'])
def test_reject_invalid_public_url(channel, url):
    with pytest.raises(ValueError):
        module.create_update_server(channel[0], url)


def test_token_protection_and_public_health(channel, monkeypatch):
    root, feed, _ = channel
    monkeypatch.setenv('DOCPRONTO_UPDATE_TOKEN', 'synthetic-secret')
    client = module.create_update_server(root).test_client()
    assert client.get('/health').json == {'ok': True}
    for path in ['/latest.json', '/packages/' + feed['package']]:
        assert client.get(path).status_code == 401
        assert client.get(path, headers={'Authorization': 'Bearer wrong'}).status_code == 401
        assert client.get(path, headers={'Authorization': 'Bearer synthetic-secret'}).status_code == 200


def test_only_current_package_is_available(channel):
    root, feed, _ = channel
    (root / 'other.zip').write_bytes(b'private')
    (root / 'secret.txt').write_text('secret')
    client = module.create_update_server(root).test_client()
    for path in ['/packages/other.zip', '/packages/secret.txt', '/packages/../secret.txt', '/secret.txt', '/']:
        assert client.get(path).status_code == 404
    assert client.post('/latest.json', data=b'upload').status_code == 405
    assert client.post('/packages/' + feed['package'], data=b'upload').status_code == 405


@pytest.mark.parametrize('package', ['../outside.zip', 'C:/outside.zip', 'https://host/package.zip', 'folder/package.zip', '..\\outside.zip'])
def test_manifest_cannot_expose_other_paths(channel, package):
    root, feed, _ = channel
    feed['package'] = package
    (root / 'latest.json').write_text(json.dumps(feed))
    assert module.create_update_server(root).test_client().get('/latest.json').status_code == 503


def test_missing_or_malformed_feed_does_not_expose_details(tmp_path):
    client = module.create_update_server(tmp_path).test_client()
    assert client.get('/latest.json').status_code == 503
    (tmp_path / 'latest.json').write_text('{invalid')
    response = client.get('/latest.json')
    assert response.status_code == 503
    assert str(tmp_path).encode() not in response.data
