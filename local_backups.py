"""Complete local snapshots and offline restore. Never used for Docker databases."""
import argparse
from contextlib import contextmanager, ExitStack, closing
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid
import local_runtime
from local_updates import _atomic, _json, _safe_name, _current_version

ID_PATTERN = r'[0-9]{8}-[0-9]{6}-[a-f0-9]{12}'


class BackupBusy(OSError):pass


def _folder(state):
    folder=Path(state).resolve()/'backups'/'full'
    folder.mkdir(parents=True,exist_ok=True)
    return folder


def settings(state):
    saved=_json(_folder(state)/'settings.json')
    return {'automatic':saved.get('automatic',True) is True,'interval_hours':saved.get('interval_hours',24)}


def save_settings(state,automatic,interval_hours=24):
    if type(automatic) is not bool or type(interval_hours) is not int or not 6<=interval_hours<=168:
        raise ValueError('Escolha backup automático e intervalo de 6 a 168 horas.')
    with _guard(state):
        result={'automatic':automatic,'interval_hours':interval_hours}
        _atomic(_folder(state)/'settings.json',result)
        return result


@contextmanager
def _guard(state):
    try:lock=local_runtime.InstanceLock(_folder(state)/'backup.lock')
    except OSError as exc:raise BackupBusy('Outra operação de manutenção está em andamento.') from exc
    try:yield
    finally:lock.close()


@contextmanager
def _maintenance(state):
    # Same lock order as updates: updater, installer, then running supervisor.
    with _guard(state),ExitStack() as stack:
        updates=Path(state)/'updates';updates.mkdir(parents=True,exist_ok=True)
        for path in (updates/'updates.lock',Path(state)/'install.lock'):
            try:lock=local_runtime.InstanceLock(path)
            except OSError as exc:raise BackupBusy('Atualização ou instalação em andamento; tente depois.') from exc
            stack.callback(lock.close)
        yield


def _record(state,phase,message,**extra):
    old=_json(_folder(state)/'status.json')
    value={**old,'state':phase,'message':message,'attempted_at':time.time(),**extra}
    _atomic(_folder(state)/'status.json',value)
    return value


def status(state):
    value={'state':'idle','message':'Nenhum backup completo realizado.',**settings(state),**_json(_folder(state)/'status.json')}
    if value['state'] in ('creating','restoring'):
        try:
            with _guard(state):value.update(_record(state,'error','Operação interrompida. Os backups concluídos foram preservados.'))
        except OSError:pass
    items=[]
    for folder in _folder(state).iterdir():
        if folder.is_dir() and re.fullmatch(ID_PATTERN,folder.name):
            try:
                manifest=_json(folder/'manifest.json')
                if manifest.get('id')==folder.name:
                    items.append({key:manifest.get(key) for key in ('id','created','kind','bytes','files','version')})
            except (ValueError,OSError):continue
    items.sort(key=lambda item:item['created'],reverse=True)
    verification=_json(_folder(state)/'verification.json')
    value.update(items=items,last_backup=items[0] if items else None,
                 verification=verification.get('latest'),last_verified=verification.get('last_success'))
    value['next_due']=((items[0]['created'] if items else 0)+value['interval_hours']*3600) if value['automatic'] else None
    return value


def _id(value):
    if not isinstance(value,str) or not re.fullmatch(ID_PATTERN,value):raise ValueError('Backup inválido.')
    return value


def _hash(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source,'sha256').hexdigest()


def _regular_tree(root):
    if root.is_symlink() or (hasattr(root,'is_junction') and root.is_junction()):
        raise ValueError('Links não são permitidos nos dados do backup.')
    for path in root.rglob('*'):
        if path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction()):
            raise ValueError('Links não são permitidos nos dados do backup.')
        if path.is_file():yield path


def _database_ok(path):
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as connection:
        if connection.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Banco do backup não passou na verificação de integridade.')
        if connection.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('Banco do backup contém referências inconsistentes.')


def _snapshot(state,root,kind='manual'):
    """Caller must hold running.lock; snapshots include the A1 vault and all files."""
    state=Path(state).resolve();data=state/'data'
    if not (data/'app.db').is_file():raise ValueError('Banco local não encontrado.')
    paths=list(_regular_tree(data))
    size=sum(path.stat().st_size for path in paths)
    if shutil.disk_usage(_folder(state)).free<size+64*1024*1024:raise ValueError('Espaço insuficiente para o backup completo.')
    ident=time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:12]
    pending=_folder(state)/('.pending-'+ident);pending.mkdir()
    target=pending/'data';target.mkdir()
    # SQLite backup includes committed WAL transactions in one standalone database.
    with closing(sqlite3.connect((data/'app.db').as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(target/'app.db')) as destination:
        source.backup(destination)
    for path in paths:
        name=path.relative_to(data)
        if name.as_posix() in ('app.db','app.db-wal','app.db-shm'):continue
        out=target/name;out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,out)
    _database_ok(target/'app.db')
    entries=[]
    for path in _regular_tree(target):
        entries.append({'path':path.relative_to(target).as_posix(),'sha256':_hash(path),'size':path.stat().st_size})
    manifest={'format':1,'id':ident,'created':time.time(),'kind':kind,'version':_current_version(root),
              'bytes':sum(item['size'] for item in entries),'files':len(entries),'entries':entries}
    _atomic(pending/'manifest.json',manifest)
    pending.rename(_folder(state)/ident)
    return manifest


def _validate(state,ident):
    folder=_folder(state)/_id(ident)
    if folder.is_symlink() or (hasattr(folder,'is_junction') and folder.is_junction()) or not folder.resolve().is_relative_to(_folder(state).resolve()):raise ValueError('Pasta do backup inválida.')
    if (folder/'manifest.json').is_symlink():raise ValueError('Manifesto do backup inválido.')
    manifest=_json(folder/'manifest.json')
    if manifest.get('format')!=1 or manifest.get('id')!=ident or not isinstance(manifest.get('entries'),list):raise ValueError('Manifesto do backup inválido.')
    data=folder/'data';actual={p.relative_to(data).as_posix().casefold() for p in _regular_tree(data)};expected=set()
    for entry in manifest['entries']:
        name=str(_safe_name(entry.get('path')));key=name.casefold()
        path=data.joinpath(*name.split('/'))
        if key in expected or not path.resolve().is_relative_to(data.resolve()) or not path.is_file() or _hash(path)!=entry.get('sha256') or path.stat().st_size!=entry.get('size'):
            raise ValueError('Backup incompleto ou alterado: '+name)
        expected.add(key)
    if expected!=actual or 'app.db' not in expected:raise ValueError('Conteúdo do backup não confere com o manifesto.')
    if manifest.get('files')!=len(expected) or manifest.get('bytes')!=sum(entry['size'] for entry in manifest['entries']):
        raise ValueError('Totais do manifesto não conferem com o backup.')
    _database_ok(data/'app.db')
    return folder,manifest


def _schema(path):
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        schema=db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
        tables=[row[1] for row in schema if row[0]=='table']
        if not tables:raise ValueError('Banco do backup sem tabelas de aplicação.')
        for table in tables:
            db.execute('SELECT count(*) FROM "'+table.replace('"','""')+'"').fetchone()
        return schema,len(tables)


def _verify_locked(state,ident):
    """Restore rehearsal only: never opens the active database or starts the app."""
    started=time.time()
    result={'backup_id':ident,'checked_at':started,'ok':False}
    try:
        folder,manifest=_validate(state,ident)
        if shutil.disk_usage(_folder(state)).free<manifest['bytes']+64*1024*1024:
            raise ValueError('Espaço insuficiente para testar a cópia isolada.')
        schema,tables=_schema(folder/'data/app.db')
        with tempfile.TemporaryDirectory(prefix='.verify-',dir=_folder(state)) as temporary:
            # TemporaryDirectory cleans up only the directory it created.
            staged=Path(temporary)/'data'
            shutil.copytree(folder/'data',staged)
            for entry in manifest['entries']:
                path=staged.joinpath(*entry['path'].split('/'))
                if _hash(path)!=entry['sha256'] or path.stat().st_size!=entry['size']:
                    raise ValueError('A cópia de teste não confere com o backup.')
            _database_ok(staged/'app.db')
            if _schema(staged/'app.db')[0]!=schema:raise ValueError('Estrutura do banco alterada durante a cópia.')
        result.update(ok=True,files=manifest['files'],bytes=manifest['bytes'],tables=tables,
                      message='Cópia isolada conferida: arquivos, estrutura e integridade do banco válidos. Dados atuais preservados.')
    except (OSError,ValueError,sqlite3.Error,TypeError,KeyError) as exc:
        result['message']='Teste não concluído: '+str(exc)[:400]
    result['finished_at']=time.time()
    old=_json(_folder(state)/'verification.json')
    _atomic(_folder(state)/'verification.json',{'latest':result,'last_success':result if result['ok'] else old.get('last_success')})
    return result


def verify(state,ident):
    ident=_id(ident)
    with _maintenance(state):return _verify_locked(state,ident)


def _installed(state,root):
    state=Path(state).resolve();root=Path(root).resolve()
    name=(state/'active.txt').read_text(encoding='utf-8-sig').strip()
    if os.name!='nt' or state.name!='DocProntoLocal' or not re.fullmatch(r'release-[a-f0-9]{32}',name) or (state/'releases'/name).resolve()!=root:
        raise ValueError('Operação disponível somente na instalação Windows local ativa.')


def _stop(state):
    try:return local_runtime.InstanceLock(state/'running.lock'),False
    except OSError:pass
    (state/'stop.request').touch()
    until=time.monotonic()+60
    while time.monotonic()<until:
        try:return local_runtime.InstanceLock(state/'running.lock'),True
        except OSError:time.sleep(.25)
    raise RuntimeError('O fiscal ainda está em execução. Nenhum dado foi alterado.')


def _start(state,root,restore_token=None):
    (state/'stop.request').unlink(missing_ok=True)
    logdir=state/'logs';logdir.mkdir(exist_ok=True)
    environment={**os.environ,'LOCALAPPDATA':str(state.parent)}
    environment.pop('DOCPRONTO_RESTORE_BOOT_TOKEN',None)
    if restore_token:environment['DOCPRONTO_RESTORE_BOOT_TOKEN']=restore_token
    with (logdir/'backup-restart.log').open('ab') as log:
        process=subprocess.Popen([str(root/'runtime/pythonw.exe'),str(root/'local_runtime.py'),'serve'],cwd=root,
            env=environment,stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=local_runtime.NO_WINDOW)
    until=time.monotonic()+120
    while time.monotonic()<until:
        if process.poll() is not None:break
        try:
            ready=_json(state/'ready.json');health=local_runtime.status()
            if Path(ready.get('root','')).resolve()==root.resolve() and ready.get('instance')==health.get('local_instance'):return
        except (OSError,ValueError):pass
        time.sleep(.5)
    raise RuntimeError('O fiscal não confirmou a reinicialização. Consulte backup-restart.log.')


def create(state,root,kind='manual'):
    state=Path(state).resolve();root=Path(root).resolve()
    running=None;restart=False
    try:
        _installed(state,root)
        with _maintenance(state):
            _record(state,'creating','Preparando backup completo. O fiscal reiniciará brevemente.')
            try:
                running,restart=_stop(state)
                manifest=_snapshot(state,root,kind)
            finally:
                if running:running.close();running=None
                if restart:_start(state,root)
            # Snapshot is immutable; rehearse after the fiscal service is back.
            verification=_verify_locked(state,manifest['id'])
            if not verification['ok']:raise ValueError(verification['message'])
            _record(state,'completed','Backup completo concluído e recuperação conferida.',last_created=manifest['created'],last_id=manifest['id'])
        return True
    except BackupBusy:return False
    except Exception as exc:
        _record(state,'error',str(exc)[:500]);return False


def _journal_paths(state,journal):
    paths={}
    for key in ('previous','staged'):
        name=journal.get(key,'')
        if not re.fullmatch(r'\.restore-'+key+r'-[a-f0-9]{32}',name):raise ValueError('Diário de restauração inválido; preserve as pastas e contate o suporte.')
        paths[key]=state/name
    return paths


def _rollback(state,journal):
    paths=_journal_paths(state,journal)
    if paths['previous'].exists():
        if (state/'data').exists():(state/'data').rename(state/('.restore-failed-'+uuid.uuid4().hex))
        paths['previous'].rename(state/'data')
    (state/'restore-journal.json').unlink(missing_ok=True)


def recover_interrupted(state,root):
    """Supervisor invokes this before configure can create an empty data folder."""
    state=Path(state).resolve()
    if not (state/'restore-journal.json').exists():return
    with _maintenance(state):
        lock=local_runtime.InstanceLock(state/'running.lock')
        try:_rollback(state,_json(state/'restore-journal.json'));_record(state,'error','Restauração interrompida revertida; dados anteriores preservados.')
        finally:lock.close()


def restore(state,root,ident):
    state=Path(state).resolve();root=Path(root).resolve()
    lock=None;journal=None;swapped=False
    try:
        _installed(state,root)
        with _maintenance(state):
            source,manifest=_validate(state,ident)
            if shutil.disk_usage(state).free<manifest['bytes']*2+64*1024*1024:raise ValueError('Espaço insuficiente para restauração e backup preventivo.')
            _record(state,'restoring','Restaurando backup; aguarde o reinício do fiscal.')
            lock,was_running=_stop(state)
            try:
                before=_snapshot(state,root,'before-restore')
                token=uuid.uuid4().hex
                journal={'previous':'.restore-previous-'+token,'staged':'.restore-staged-'+token,'backup_id':ident,'previous_backup':before['id']}
                paths=_journal_paths(state,journal)
                shutil.copytree(source/'data',paths['staged'])
                _atomic(state/'restore-journal.json',journal)
                (state/'data').rename(paths['previous'])
                paths['staged'].rename(state/'data');swapped=True
                # A boot validation flag prevents recovery while testing this restore.
                _atomic(state/'restore-boot.json',{'token':token})
                lock.close();lock=None
                _start(state,root,restore_token=token)
                (state/'restore-journal.json').unlink(missing_ok=True)
                (state/'restore-boot.json').unlink(missing_ok=True)
                _record(state,'restored','Backup restaurado. Entre novamente no fiscal.',restored_id=ident,previous_backup=before['id'])
                return True
            except Exception:
                if swapped:
                    if lock is None:lock,_=_stop(state)
                    _rollback(state,journal)
                elif journal and (state/'restore-journal.json').exists():_rollback(state,journal)
                raise
            finally:
                (state/'restore-boot.json').unlink(missing_ok=True)
                if lock:lock.close();lock=None
                if not swapped and was_running:_start(state,root)
    except BackupBusy:return False
    except Exception as exc:
        _record(state,'error',str(exc)[:500])
        if swapped:
            try:_start(state,root)
            except Exception:pass
        return False


def main():
    parser=argparse.ArgumentParser();parser.add_argument('operation',choices=('create','restore'));parser.add_argument('--id');parser.add_argument('--automatic',action='store_true')
    args=parser.parse_args();state=local_runtime.state_root();root=Path(__file__).resolve().parent
    return 0 if (create(state,root,'automatic' if args.automatic else 'manual') if args.operation=='create' else restore(state,root,args.id)) else 1


if __name__=='__main__':raise SystemExit(main())
