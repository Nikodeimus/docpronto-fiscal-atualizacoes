"""Configured release feed, verified staging, and explicit local installation.

This module never reads fiscal data or invents a release server. Checking only
prepares a package; installation is a separate operation using its installer.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import time
import urllib.parse
import urllib.request
import uuid
import zipfile

from local_runtime import InstanceLock, state_root

MAX_FEED = 128 * 1024
MAX_PACKAGE = 1024 * 1024 * 1024
MAX_EXPANDED = 3 * 1024 * 1024 * 1024
MAX_MEMBER = 768 * 1024 * 1024
MAX_FILES = 30000
MAX_MANIFEST = 16 * 1024 * 1024
REQUIRED = {'local_runtime.py', 'local_updates.py', 'run.py', 'scripts/install-local.ps1',
            'runtime/python.exe', 'runtime/pythonw.exe'}


class UpdateBusy(OSError):
    pass


def _folder(state):
    path = Path(state).resolve() / 'updates'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _json(path, default=None):
    if not path.exists():
        return {} if default is None else default
    if path.stat().st_size > MAX_MANIFEST:
        raise ValueError('Arquivo de configuração excede o limite.')
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('Configuração deve ser um objeto JSON.')
    return value


def _atomic(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _lock(state):
    try:
        guard = InstanceLock(_folder(state) / 'updates.lock')
    except OSError as exc:
        raise UpdateBusy('Outra operação de atualização está em andamento.') from exc
    try:
        yield
    finally:
        guard.close()


def _https(value):
    parsed = urllib.parse.urlsplit(value)
    permitted = parsed.scheme == 'https' or (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', '::1'))
    if not permitted or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Use HTTPS; HTTP é permitido apenas em 127.0.0.1, localhost ou ::1, sem credenciais ou fragmento.')
    parsed.port  # Validate a supplied port before using the address.
    return value


def _source(value):
    if not isinstance(value, str):
        raise ValueError('Informe uma fonte HTTPS ou um caminho absoluto local.')
    value = value.strip()
    if not value:
        return ''
    if value.lower().startswith(('https:', 'http:')):
        return _https(value)
    path = Path(value)
    if not path.is_absolute() or '://' in value:
        raise ValueError('A fonte deve ser HTTPS ou um caminho absoluto local.')
    if path.is_dir():
        path = path / 'latest.json'
    if path.suffix.lower() != '.json':
        raise ValueError('Selecione um arquivo JSON ou uma pasta com latest.json.')
    return str(path.resolve())


def settings(state):
    value = _json(_folder(state) / 'settings.json')
    return {'source': value.get('source', ''), 'automatic': value.get('automatic', True) is True}


def save_settings(state, source, automatic):
    source = _source(source)
    if not isinstance(automatic, bool):
        raise ValueError('A opção automática deve ser verdadeira ou falsa.')
    with _lock(state):
        previous = settings(state)
        result = {'source': source, 'automatic': automatic}
        _atomic(_folder(state) / 'settings.json', result)
        if source != previous['source']:
            # A package verified under the old source must not install under a new one.
            (_folder(state) / 'stage.json').unlink(missing_ok=True)
            _atomic(_folder(state) / 'status.json', {'state': 'idle' if source else 'not_configured',
                    'message': 'Fonte salva. Verifique atualizações.' if source else 'Fonte de atualização não configurada.'})
        return result


def _current_version(root):
    return str(_json(Path(root) / 'local-manifest.json').get('version', ''))


def status(state, root):
    config = settings(state)
    saved = _json(_folder(state) / 'status.json')
    result = {'configured': bool(config['source']), **config, 'current_version': _current_version(root),
              'state': 'idle' if config['source'] else 'not_configured',
              'message': 'Nenhuma verificação realizada.' if config['source'] else 'Fonte de atualização não configurada.',
              'available_version': ''}
    result.update({key: saved[key] for key in ('state', 'message', 'available_version', 'checked_at', 'applied_at') if key in saved})
    stage = _json(_folder(state) / 'stage.json')
    if stage.get('version') and stage['version'] == result['current_version']:
        result.update(state='updated', message='Atualização já instalada.', available_version='')
    elif result['state'] in ('checking', 'downloading', 'installing'):
        # An atomic status may survive a killed detached process; its lock does not.
        try:
            with _lock(state):
                result.update(_record(state, 'error', 'A operação anterior foi interrompida. Verifique novamente; consulte os logs se a instalação estava em andamento.'))
        except UpdateBusy:
            pass
    if not config['source']:
        result.update(state='not_configured', message='Fonte de atualização não configurada.', available_version='')
    return result


def _record(state, operation, message, **extra):
    value = {'state': operation, 'message': message, 'checked_at': time.time(), **extra}
    _atomic(_folder(state) / 'status.json', value)
    return value


class _SecureRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        _https(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _copy_stream(stream, destination, limit):
    size = 0
    started = time.monotonic()
    with destination.open('xb') as output:
        while True:
            if time.monotonic() - started > 600:
                raise ValueError('Transferência excedeu dez minutos.')
            chunk = stream.read(min(1024 * 1024, limit - size + 1))
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise ValueError('Download excede o limite permitido.')
            output.write(chunk)
    return size


def _download(source, destination, limit):
    if source.lower().startswith(('https://', 'http://')):
        _https(source)
        opener = urllib.request.build_opener(_SecureRedirect())
        request = urllib.request.Request(source, headers={'User-Agent': 'DocPronto-Local-Updates'})
        with opener.open(request, timeout=30) as stream:
            _https(stream.geturl())
            length = stream.headers.get('Content-Length')
            if length and int(length) > limit:
                raise ValueError('Download excede o limite permitido.')
            return _copy_stream(stream, destination, limit)
    path = Path(source)
    if not path.is_absolute() or not path.is_file():
        raise ValueError('Arquivo da atualização não encontrado.')
    with path.open('rb') as stream:
        return _copy_stream(stream, destination, limit)


def _digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _version(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9._-]+)?', value):
        raise ValueError('Versão inválida no feed ou manifesto.')
    core = value.split('-', 1)[0].split('+', 1)[0]
    return tuple(int(part) for part in core.split('.'))


def _safe_name(name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name or '\x00' in name:
        raise ValueError('Caminho inseguro no pacote.')
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ('', '.', '..') for part in name.rstrip('/').split('/')):
        raise ValueError('Caminho inseguro no pacote.')
    for part in path.parts:
        if part.endswith(('.', ' ')) or re.match(r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', part, re.I):
            raise ValueError('Nome de arquivo incompatível ou inseguro no pacote.')
    return path


def _validate_manifest(root, version):
    manifest_path = root / 'local-manifest.json'
    manifest = _json(manifest_path)
    if manifest.get('version') != version:
        raise ValueError('Versão do pacote difere da versão anunciada.')
    entries = manifest.get('files')
    if not isinstance(entries, list) or not entries or len(entries) > MAX_FILES:
        raise ValueError('Manifesto de arquivos inválido.')
    expected = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('Entrada inválida no manifesto.')
        name = str(_safe_name(entry.get('path')))
        key = name.casefold()
        if PurePosixPath(key).parts[0] in {'data', 'files', 'backups', 'updates', '.env', 'local.json', 'certificate-vault.key'} or key.endswith(('.pfx', '.p12', '.sqlite', '.db')):
            raise ValueError('Dados de aplicação ou certificados não pertencem ao pacote de atualização.')
        digest = entry.get('sha256')
        if key in expected or key == 'local-manifest.json' or not isinstance(digest, str) or not re.fullmatch('[0-9a-fA-F]{64}', digest):
            raise ValueError('Hash ou caminho duplicado no manifesto.')
        expected.add(key)
        file = root.joinpath(*PurePosixPath(name).parts)
        if file.is_symlink() or not file.is_file() or not file.resolve().is_relative_to(root.resolve()) or _digest(file) != digest.lower():
            raise ValueError('Integridade do pacote não confere: ' + name)
    actual = set()
    for file in root.rglob('*'):
        if file.is_symlink():
            raise ValueError('Links não são permitidos no pacote.')
        if file.is_file():
            actual.add(file.relative_to(root).as_posix().casefold())
    if actual != expected | {'local-manifest.json'} or not REQUIRED.issubset(expected):
        raise ValueError('Pacote incompleto ou com arquivos fora do manifesto.')


def _extract(package, destination, version):
    with zipfile.ZipFile(package) as archive:
        members = archive.infolist()
        if len(members) > MAX_FILES or sum(item.file_size for item in members) > MAX_EXPANDED:
            raise ValueError('Pacote descompactado excede os limites.')
        seen = set()
        manifests = []
        for item in members:
            path = _safe_name(item.filename)
            key = str(path).casefold()
            mode = item.external_attr >> 16
            if key in seen or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (stat.S_ISDIR(mode) or stat.S_ISREG(mode))) or item.flag_bits & 1:
                raise ValueError('Entrada duplicada, link ou arquivo criptografado no ZIP.')
            seen.add(key)
            if item.file_size > MAX_MEMBER:
                raise ValueError('Arquivo do pacote excede o limite.')
            if path.name == 'local-manifest.json' and not item.is_dir():
                manifests.append(path)
        if len(manifests) != 1:
            raise ValueError('O pacote deve conter um único local-manifest.json.')
        prefix = manifests[0].parent
        for item in members:
            path = PurePosixPath(item.filename)
            if not path.is_relative_to(prefix) and not (item.is_dir() and prefix.is_relative_to(path)):
                raise ValueError('Arquivo fora da raiz do pacote.')
            target = destination.joinpath(*path.parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as stream:
                _copy_stream(stream, target, min(item.file_size, MAX_MEMBER))
    root = destination.joinpath(*prefix.parts)
    _validate_manifest(root, version)
    return root


def _package_source(feed_source, value):
    if not isinstance(value, str) or not value:
        raise ValueError('Pacote ausente no feed.')
    if value.lower().startswith(('https://', 'http://')):
        return _https(value)
    if feed_source.lower().startswith(('https://', 'http://')):
        raise ValueError('O pacote de um feed remoto deve usar URL absoluta HTTPS (ou HTTP local).')
    relative = _safe_name(value)
    return str(Path(feed_source).parent.joinpath(*relative.parts).resolve())


def check(state, root, download=True):
    try:
        with _lock(state):
            config = settings(state)
            if not config['source']:
                return status(state, root)
            stage_dir = None
            _record(state, 'checking', 'Verificando a fonte de atualizações.', checked_at=time.time())
            try:
                source = _source(config['source'])
                staging = _folder(state) / 'staged'
                staging.mkdir(exist_ok=True)
                stage_dir = staging / uuid.uuid4().hex
                stage_dir.mkdir()
                feed_path = stage_dir / 'feed.json'
                _download(source, feed_path, MAX_FEED)
                feed = _json(feed_path)
                version = feed.get('version')
                proposed = _version(version)
                current = _current_version(root)
                current_core = _version(current) if current else None
                if version == current or (current_core and proposed < current_core):
                    (_folder(state) / 'stage.json').unlink(missing_ok=True)
                    _record(state, 'current', 'A versão instalada já está atualizada.', checked_at=time.time())
                else:
                    previous_stage = _json(_folder(state) / 'stage.json')
                    if previous_stage.get('version') != version:
                        (_folder(state) / 'stage.json').unlink(missing_ok=True)
                    package_source = _package_source(source, feed.get('package'))
                    digest = feed.get('sha256')
                    if not isinstance(digest, str) or not re.fullmatch('[0-9a-fA-F]{64}', digest):
                        raise ValueError('SHA-256 do pacote ausente ou inválido no feed.')
                    if not download:
                        _record(state, 'available', 'Há uma atualização disponível.', available_version=version, checked_at=time.time())
                    else:
                        _record(state, 'downloading', 'Baixando e verificando a atualização.', available_version=version)
                        package = stage_dir / 'package.zip'
                        _download(package_source, package, MAX_PACKAGE)
                        if _digest(package) != digest.lower():
                            raise ValueError('SHA-256 do pacote não confere; atualização recusada.')
                        extracted = _extract(package, stage_dir / 'content', version)
                        _atomic(_folder(state) / 'stage.json', {'root': str(extracted), 'version': version,
                                'package': str(package), 'sha256': digest.lower(), 'source': source})
                        _record(state, 'ready', 'Atualização verificada e pronta para instalar.', available_version=version, checked_at=time.time())
                        stage_dir = None
            except Exception as exc:
                _record(state, 'error', str(exc)[:500], checked_at=time.time())
            finally:
                if stage_dir is not None:
                    # Only a UUID folder created by this check is removed.
                    shutil.rmtree(stage_dir, ignore_errors=True)
            return status(state, root)
    except UpdateBusy:
        result = status(state, root)
        return {**result, 'state': 'busy', 'message': 'Outra operação de atualização está em andamento.'}


def _run_installer(script, state, log):
    if os.name != 'nt' or Path(state).name != 'DocProntoLocal':
        raise RuntimeError('Instalação automática disponível somente na instalação local Windows.')
    executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    environment = {**os.environ, 'LOCALAPPDATA': str(Path(state).resolve().parent)}
    with log.open('ab') as stream:
        result = subprocess.run([str(executable), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
                                 '-File', str(script), '-Worker', '-Background'], cwd=script.parent.parent, env=environment,
                                stdout=stream, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return result.returncode


def apply(state, root):
    try:
        with _lock(state):
            try:
                config = settings(state)
                stage = _json(_folder(state) / 'stage.json')
                if not config['source'] or stage.get('source') != _source(config['source']):
                    raise ValueError('Nenhuma atualização verificada para a fonte atual. Verifique novamente.')
                version = stage.get('version')
                _version(version)
                if version == _current_version(root):
                    (_folder(state) / 'stage.json').unlink(missing_ok=True)
                    _record(state, 'updated', 'Atualização já instalada.', available_version='', applied_at=time.time())
                    return True
                staging = (_folder(state) / 'staged').resolve()
                staged_root = Path(stage['root']).resolve()
                package = Path(stage['package']).resolve()
                if not staged_root.is_relative_to(staging) or not package.is_relative_to(staging) or staged_root == staging:
                    raise ValueError('Caminho da atualização preparada inválido.')
                if _digest(package) != stage['sha256']:
                    raise ValueError('Pacote preparado foi alterado. Verifique novamente.')
                _validate_manifest(staged_root, version)
                _record(state, 'installing', 'Instalando atualização verificada.', available_version=version)
                log = _folder(state) / ('install-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6] + '.log')
                code = _run_installer(staged_root / 'scripts/install-local.ps1', state, log)
                if code != 0:
                    raise RuntimeError('Instalação falhou (código ' + str(code) + '). Consulte ' + log.name + '.')
                (_folder(state) / 'stage.json').unlink(missing_ok=True)
                _record(state, 'updated', 'Atualização instalada com sucesso.', available_version=version, applied_at=time.time())
                return True
            except Exception as exc:
                _record(state, 'error', str(exc)[:500])
                return False
    except UpdateBusy:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('check', 'apply'))
    parser.add_argument('--state', type=Path, default=None)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    state = args.state or state_root()
    if args.operation == 'apply':
        return 0 if apply(state, args.root) else 1
    result = check(state, args.root)
    return 1 if result['state'] in ('error', 'busy') else 0


if __name__ == '__main__':
    raise SystemExit(main())
