import os

import pytest
from cryptography.fernet import Fernet
from app import certificate_vault as vault


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI integration')
def test_windows_new_vault_and_legacy_migration_preserve_ciphertexts(tmp_path):
    first = vault.vault_cipher(tmp_path)
    token = first.encrypt(b'synthetic-test-only')
    path = tmp_path / 'certificate-vault.key'
    assert path.read_bytes().startswith(vault.PREFIX)
    assert vault.vault_cipher(tmp_path).decrypt(token) == b'synthetic-test-only'
    legacy = Fernet.generate_key()
    old_token = Fernet(legacy).encrypt(b'synthetic-legacy-only')
    path.write_bytes(legacy)
    assert vault.vault_cipher(tmp_path).decrypt(old_token) == b'synthetic-legacy-only'
    assert path.read_bytes().startswith(vault.PREFIX)
    assert legacy not in path.read_bytes()
    assert vault.vault_cipher(tmp_path).decrypt(old_token) == b'synthetic-legacy-only'
    assert not list(tmp_path.glob('vault-key-*'))


def test_corrupt_key_not_replaced(tmp_path):
    path = tmp_path / 'certificate-vault.key'
    path.write_bytes(b'invalid-key')
    with pytest.raises(ValueError):
        vault.vault_cipher(tmp_path)
    assert path.read_bytes() == b'invalid-key'


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI migration')
def test_failed_protection_preserves_legacy(tmp_path, monkeypatch):
    path = tmp_path / 'certificate-vault.key'
    key = Fernet.generate_key()
    path.write_bytes(key)
    def fail(*args, **kwargs):
        raise ValueError('synthetic DPAPI error')
    monkeypatch.setattr(vault, '_dpapi', fail)
    with pytest.raises(ValueError):
        vault.vault_cipher(tmp_path)
    assert path.read_bytes() == key


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI')
def test_corrupt_dpapi_not_replaced(tmp_path):
    path = tmp_path / 'certificate-vault.key'
    contents = vault.PREFIX + b'invalid-protected-key'
    path.write_bytes(contents)
    with pytest.raises(ValueError):
        vault.vault_cipher(tmp_path)
    assert path.read_bytes() == contents


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI migration')
def test_roundtrip_failure_preserves_legacy(tmp_path, monkeypatch):
    path = tmp_path / 'certificate-vault.key'
    key = Fernet.generate_key()
    path.write_bytes(key)
    monkeypatch.setattr(vault, '_dpapi', lambda raw, decrypt=False: b'wrong-value')
    with pytest.raises(ValueError, match='verificação'):
        vault.vault_cipher(tmp_path)
    assert path.read_bytes() == key


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI migration')
def test_replace_failure_preserves_legacy(tmp_path, monkeypatch):
    path = tmp_path / 'certificate-vault.key'
    key = Fernet.generate_key()
    path.write_bytes(key)
    def fail(*args):
        raise OSError('synthetic disk failure')
    monkeypatch.setattr(vault.os, 'replace', fail)
    with pytest.raises(OSError):
        vault.vault_cipher(tmp_path)
    assert path.read_bytes() == key
    assert not list(tmp_path.glob('vault-key-*'))


def test_new_nonwindows_vault_requires_external_key(tmp_path, monkeypatch):
    # Patch only the vault module's OS view, not pathlib's global platform.
    from types import SimpleNamespace
    monkeypatch.setattr(vault, 'os', SimpleNamespace(name='posix', environ={}))
    with pytest.raises(ValueError, match='DOCPRONTO_CERTIFICATE_VAULT_KEY'):
        vault.vault_cipher(tmp_path)
    key = Fernet.generate_key()
    vault.os.environ['DOCPRONTO_CERTIFICATE_VAULT_KEY'] = key.decode()
    token = vault.vault_cipher(tmp_path).encrypt(b'synthetic')
    assert Fernet(key).decrypt(token) == b'synthetic'
    assert not (tmp_path / 'certificate-vault.key').exists()
