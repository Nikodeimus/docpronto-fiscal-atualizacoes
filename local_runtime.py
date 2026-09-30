"""Local desktop process supervisor. Does not touch Docker or its data."""
import ctypes
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
import uuid
import webbrowser

ROOT = Path(__file__).resolve().parent
URL = 'http://127.0.0.1:8080'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
UPDATE_INTERVAL = 6 * 60 * 60


def launch_backup(mode, backup_id=None, automatic=False, root=None, state=None):
    if mode not in ('create','restore'):raise ValueError('Operação de backup inválida.')
    import local_backups
    root=Path(root or ROOT);state=Path(state or state_root())
    args=[str(root/'runtime/pythonw.exe'),str(root/'local_backups.py'),mode]
    if mode=='restore':args+=['--id',local_backups._id(backup_id)]
    if automatic:args+=['--automatic']
    if not (root/'runtime/pythonw.exe').is_file():raise ValueError('Instale o pacote Windows local para usar backups.')
    logs=state/'logs';logs.mkdir(parents=True,exist_ok=True)
    with (logs/'backups.log').open('ab') as log:
        process=subprocess.Popen(args,cwd=root,env={**os.environ,'LOCALAPPDATA':str(state.parent)},stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=NO_WINDOW)
    return process.pid


def automatic_backup_check(state, now=None):
    import local_backups
    now=time.time() if now is None else now
    config=local_backups.settings(state)
    if not config['automatic']:return False
    # Portable/development runs must never stop themselves to create installed backups.
    try:local_backups._installed(state,ROOT)
    except (OSError,ValueError):return False
    current=local_backups.status(state)
    if current['state'] in ('creating','restoring'):return False
    last=(current.get('last_backup') or {}).get('created',0)
    if now-last<config['interval_hours']*3600:return False
    if current['state']=='error' and now-current.get('attempted_at',0)<3600:return False
    launch_backup('create',automatic=True,state=state)
    return True


def launch_update(mode, root=None, state=None):
    """Separate process survives the site restart; never run inside a request."""
    if mode not in ('check', 'apply'):
        raise ValueError('Operação de atualização inválida.')
    root = root or ROOT
    state = state or state_root()
    logdir = state / 'logs'
    logdir.mkdir(parents=True, exist_ok=True)
    python = root / 'runtime' / 'pythonw.exe'
    if not python.exists():
        raise ValueError('Reinstale o pacote Windows local para habilitar atualizações.')
    with (logdir / 'updates.log').open('ab') as log:
        process = subprocess.Popen([str(python), str(root / 'local_updates.py'), mode],
                                   cwd=root, stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=log, creationflags=NO_WINDOW)
    return process.pid


def automatic_update_check(state, now=None):
    """Best effort: an unavailable release channel must never stop fiscal work."""
    import local_updates
    now = time.time() if now is None else now
    config = local_updates.settings(state)
    if not config.get('source') or not config.get('automatic'):
        return False
    current = local_updates.status(state, ROOT)
    if current.get('state') in ('checking', 'downloading', 'installing', 'ready'):
        return False
    if now - float(current.get('checked_at') or 0) < UPDATE_INTERVAL:
        return False
    launch_update('check', state=state)
    return True

def state_root():
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'DocProntoLocal'

def configure(state):
    data = state / 'data'
    data.mkdir(parents=True, exist_ok=True)
    config = state / 'local.json'
    if not config.exists():
        with config.open('x', encoding='utf-8') as stream:
            json.dump({'setup_token': secrets.token_urlsafe(32)}, stream)
    token = json.loads(config.read_text(encoding='utf-8'))['setup_token']
    if not isinstance(token, str) or len(token) < 32:
        raise RuntimeError('Configuracao local invalida. Preserve local.json e contate o suporte.')
    from app.local_network import settings as network_settings
    network=network_settings(state)
    # Explicitly isolate the local installation from legacy .env / Docker values.
    os.environ.update(DOCPRONTO_HOME=str(data), DATA_DIR=str(data / 'files'),
                      DATABASE_URL='sqlite:///' + (data / 'app.db').as_posix(),
                      SETUP_TOKEN=token, BIND_HOST='0.0.0.0' if network['enabled'] else '127.0.0.1', PORT='8080',
                      ALLOWED_HOSTS=','.join(['localhost','127.0.0.1']+(network['hosts'] if network['enabled'] else [])), COOKIE_SECURE='0',
                      DOCPRONTO_LAN_ENABLED='1' if network['enabled'] else '0',
                      DOCPRONTO_ADAPTER_COMMAND='', NFE_XSD_PATH='')
    tools = ROOT / 'tools'
    os.environ['PATH'] = os.pathsep.join([str(tools / 'poppler' / 'Library' / 'bin'),
                                        str(tools / 'tesseract'), os.environ.get('PATH', '')])
    os.environ['TESSDATA_PREFIX'] = str(tools / 'tesseract' / 'tessdata')
    return token

class InstanceLock:
    def __init__(self, path):
        self.file = path.open('a+b')
        self.file.seek(0)
        if os.name == 'nt':
            import msvcrt
            try: msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                self.file.close()
                raise
        else:
            import fcntl
            try: fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self.file.close()
                raise
    def close(self):
        self.file.close()

def status():
    # Never route localhost through a corporate HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(URL + '/api/status', timeout=2) as response:
        return json.load(response)

def wait_ready(instance, web, worker=None, timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if web.poll() is not None or (worker and worker.poll() is not None):
            raise RuntimeError('Um processo encerrou. Consulte a pasta logs do DocPronto Local.')
        try:
            result = status()
            if result.get('local_instance') == instance:
                return result
        except (OSError, ValueError):
            pass
        time.sleep(.3)
    raise RuntimeError('O DocPronto nao iniciou em 60 segundos. Consulte a pasta logs.')

def inform(message):
    if os.name == 'nt':
        ctypes.windll.user32.MessageBoxW(None, message, 'DocPronto Local', 0)
    else:
        print(message, file=sys.stderr)


def supervisor_log(state, message):
    logs = state / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / 'supervisor.log').open('a', encoding='utf-8') as stream:
        stream.write(time.strftime('%Y-%m-%d %H:%M:%S ') + message + '\n')


def recover_children(state, instance, children, handles, attempts):
    """Restart only exited children, under the existing exclusive instance lock."""
    stop = state / 'stop.request'
    if stop.exists():
        return False
    dead = [i for i, child in enumerate(children) if child.poll() is not None]
    if not dead:
        return True
    (state / 'ready.json').unlink(missing_ok=True)
    now = time.monotonic()
    attempts[:] = [stamp for stamp in attempts if now - stamp < 300]
    if len(attempts) >= 3:
        raise RuntimeError('Recuperacao interrompida apos 3 tentativas em 5 minutos. Consulte logs/supervisor.log e reabra o atalho.')
    attempts.append(now)
    supervisor_log(state, 'Processo encerrado: ' + ', '.join(
        ('site', 'fila')[i] + ' codigo=' + str(children[i].poll()) for i in dead))
    # Give Windows time to release resources; an explicit shutdown wins.
    for _ in range(10):
        if stop.exists():
            return False
        time.sleep(.5)
    commands = ([str(ROOT / 'run.py')], ['-m', 'app.worker'])
    for i in dead:
        if stop.exists():
            return False
        children[i] = subprocess.Popen([sys.executable, *commands[i]], cwd=ROOT,
            stdout=handles[i], stderr=handles[i], stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    try:
        wait_ready(instance, *children)
    except RuntimeError:
        if any(child.poll() is not None for child in children):
            return True  # Retry on the next iteration, within the same budget.
        raise
    if stop.exists():
        return False
    (state / 'ready.json').write_text(json.dumps({'instance': instance, 'root': str(ROOT)}))
    supervisor_log(state, 'Site e fila recuperados; resposta HTTP confirmada.')
    return True

def open_site(state, token, result):
    if result.get('needs_setup'):
        info = state / 'PRIMEIRO-ACESSO.txt'
        info.write_text('DocPronto Local\n\nCopie este codigo para o campo Token de instalacao:\n\n'
                        + token + '\n\nCrie sua conta com uma senha de pelo menos 12 caracteres.\n', encoding='utf-8')
        if os.name == 'nt':
            subprocess.Popen(['notepad.exe', str(info)])
    webbrowser.open(URL)

def backup(state):
    db = state / 'data' / 'app.db'
    if db.exists():
        dest = state / 'backups' / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
        dest.mkdir(parents=True)
        with sqlite3.connect(db) as src, sqlite3.connect(dest / 'app.db') as target:
            src.backup(target)
        # Files stay in the persistent data directory. This is a database-only
        # pre-update snapshot, not a complete disaster-recovery backup.

def main():
    state = state_root()
    state.mkdir(parents=True, exist_ok=True)
    mode = sys.argv[1] if len(sys.argv) > 1 else 'start'
    stop = state / 'stop.request'
    if mode == 'stop':
        stop.touch()
        return 0
    if mode == 'backup':
        backup(state)
        return 0
    if (state/'restore-journal.json').exists():
        from local_updates import _json
        authorized=_json(state/'restore-boot.json').get('token')
        if not authorized or os.getenv('DOCPRONTO_RESTORE_BOOT_TOKEN')!=authorized:
            import local_backups
            local_backups.recover_interrupted(state,ROOT)
    token = configure(state)
    if mode == 'check':
        import flask, waitress, sqlalchemy, lxml.etree, cryptography, reportlab, PIL.Image
        from app.server import create_app
        for command in (['pdfinfo', '-v'], ['pdftotext', '-v'], ['pdftoppm', '-v'], ['tesseract', '--version']):
            subprocess.run(command, check=True, capture_output=True, timeout=20, creationflags=NO_WINDOW)
        return 0
    try:
        lock = InstanceLock(state / 'running.lock')
    except OSError:
        # Existing supervisor owns the instance. Do not start a duplicate worker.
        try:
            ready = json.loads((state / 'ready.json').read_text())
            result = status()
            if result.get('local_instance') != ready['instance']:
                raise RuntimeError('Outra aplicacao ocupa a porta 8080.')
            if mode != 'serve': open_site(state, token, result)
            return 0
        except Exception:
            inform('DocPronto ja esta iniciando. Aguarde e abra o atalho novamente.')
            return 1
    children, handles = [], []
    ready_path = state / 'ready.json'
    try:
        stop.unlink(missing_ok=True)
        ready_path.unlink(missing_ok=True)
        with socket.socket() as probe:
            if os.name != 'nt':
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try: probe.bind((os.environ.get('BIND_HOST','127.0.0.1'), 8080))
            except OSError: raise RuntimeError('A porta 8080 esta ocupada. Encerre a outra instalacao do DocPronto antes de abrir esta.')
        from app.server import create_app
        app = create_app()
        app.engine.dispose()  # Complete schema initialization before either child.
        instance = uuid.uuid4().hex
        os.environ['DOCPRONTO_LOCAL_INSTANCE'] = instance
        logdir = state / 'logs'
        logdir.mkdir(exist_ok=True)
        for name, args in [('site', [str(ROOT / 'run.py')]), ('fila', ['-m', 'app.worker'])]:
            path = logdir / (name + '.log')
            if path.exists() and path.stat().st_size > 5 * 1024 * 1024:
                path.replace(path.with_suffix('.previous.log'))
            handle = path.open('ab')
            handles.append(handle)
            children.append(subprocess.Popen([sys.executable, *args], cwd=ROOT, stdout=handle,
                                             stderr=handle, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW))
        result = wait_ready(instance, *children)
        ready_path.write_text(json.dumps({'instance': instance, 'root': str(ROOT)}))
        if mode != 'serve': open_site(state, token, result)
        next_update_check = time.monotonic() + 5
        next_backup_check = time.monotonic() + 300
        restart_attempts = []
        while not stop.exists():
            if not recover_children(state, instance, children, handles, restart_attempts):
                break
            if time.monotonic() >= next_update_check:
                try:
                    automatic_update_check(state)
                except Exception as exc:
                    # Keep a diagnostic without raising an interactive dialog.
                    with (logdir / 'updates.log').open('a', encoding='utf-8') as log:
                        log.write('Verificação automática indisponível: ' + str(exc) + '\n')
                next_update_check = time.monotonic() + 60
            if time.monotonic()>=next_backup_check:
                try:automatic_backup_check(state)
                except Exception as exc:
                    with (logdir/'backups.log').open('a',encoding='utf-8') as log:log.write(str(exc)+'\n')
                next_backup_check=time.monotonic()+60
            time.sleep(1)
        return 0
    except Exception as exc:
        supervisor_log(state, str(exc))
        if mode != 'serve':
            inform(str(exc))
        return 1
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        for handle in handles: handle.close()
        ready_path.unlink(missing_ok=True)
        stop.unlink(missing_ok=True)
        lock.close()

if __name__ == '__main__':
    try: sys.exit(main())
    except Exception as exc:
        if len(sys.argv) > 1 and sys.argv[1] in ('check', 'backup', 'serve'):
            print('Falha: ' + str(exc), file=sys.stderr)
        else:
            inform('Falha ao abrir DocPronto Local: ' + str(exc))
        sys.exit(1)
