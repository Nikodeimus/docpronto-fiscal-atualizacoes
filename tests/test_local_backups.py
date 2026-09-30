import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace
import pytest
import local_backups as backups
import local_runtime


@pytest.fixture
def installation(tmp_path,monkeypatch):
    state=tmp_path/'DocProntoLocal';data=state/'data';files=data/'files'/'company'/'document'
    files.mkdir(parents=True)
    (files/'original.xml').write_bytes(b'<synthetic>original</synthetic>')
    (data/'files'/'certificate-vault.key').write_bytes(b'SYNTHETIC-VAULT-KEY')
    with closing(sqlite3.connect(data/'app.db')) as connection,connection:
        connection.execute('CREATE TABLE documents(id TEXT PRIMARY KEY,xml TEXT)')
        connection.execute("INSERT INTO documents VALUES('document','files/company/document/original.xml')")
    root=tmp_path/'release';root.mkdir();(root/'local-manifest.json').write_text(json.dumps({'version':'1.9.0'}))
    monkeypatch.setattr(backups,'_installed',lambda *args:None)
    monkeypatch.setattr(backups,'_stop',lambda *args:(SimpleNamespace(close=lambda:None),True))
    monkeypatch.setattr(backups,'_start',lambda *args,**kwargs:None)
    return state,root


def test_complete_snapshot_database_xml_and_vault(installation):
    state,root=installation
    assert backups.create(state,root)
    result=backups.status(state)
    assert result['state']=='completed' and result['last_backup']['files']==3
    folder,manifest=backups._validate(state,result['last_backup']['id'])
    assert (folder/'data/files/company/document/original.xml').read_bytes()==b'<synthetic>original</synthetic>'
    assert (folder/'data/files/certificate-vault.key').read_bytes()==b'SYNTHETIC-VAULT-KEY'
    with closing(sqlite3.connect(folder/'data/app.db')) as db:
        assert db.execute('SELECT * FROM documents').fetchone()==('document','files/company/document/original.xml')


def test_snapshot_includes_committed_wal(installation):
    state,root=installation
    db=sqlite3.connect(state/'data/app.db')
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute("INSERT INTO documents VALUES('wal','files/company/document/original.xml')");db.commit()
        result=backups._snapshot(state,root)
        folder,_=backups._validate(state,result['id'])
        with closing(sqlite3.connect(folder/'data/app.db')) as copy:assert copy.execute('SELECT count(*) FROM documents').fetchone()[0]==2
    finally:db.close()


def test_restore_makes_prebackup_and_replaces_data(installation):
    state,root=installation;assert backups.create(state,root)
    original=backups.status(state)['last_backup']['id']
    (state/'data/files/company/document/original.xml').write_bytes(b'changed')
    with closing(sqlite3.connect(state/'data/app.db')) as db,db:db.execute("INSERT INTO documents VALUES('later','later.xml')")
    assert backups.restore(state,root,original)
    assert (state/'data/files/company/document/original.xml').read_bytes()==b'<synthetic>original</synthetic>'
    with closing(sqlite3.connect(state/'data/app.db')) as db:assert db.execute('SELECT count(*) FROM documents').fetchone()[0]==1
    listing=backups.status(state)
    assert any(item['kind']=='before-restore' for item in listing['items'])
    assert listing['state']=='restored'
    assert not (state/'restore-journal.json').exists()


def test_failed_health_rolls_back_original_database_and_files(installation,monkeypatch):
    state,root=installation;assert backups.create(state,root)
    ident=backups.status(state)['last_backup']['id']
    (state/'data/files/company/document/original.xml').write_bytes(b'latest-original')
    with closing(sqlite3.connect(state/'data/app.db')) as db,db:db.execute("INSERT INTO documents VALUES('latest','latest.xml')")
    calls=[]
    def start(*args,**kwargs):
        calls.append(1)
        if len(calls)==1:raise RuntimeError('synthetic failed boot')
    monkeypatch.setattr(backups,'_start',start)
    assert not backups.restore(state,root,ident)
    assert (state/'data/files/company/document/original.xml').read_bytes()==b'latest-original'
    with closing(sqlite3.connect(state/'data/app.db')) as db:assert db.execute('SELECT count(*) FROM documents').fetchone()[0]==2
    assert len(calls)==2 and backups.status(state)['state']=='error'
    assert not (state/'restore-journal.json').exists()


def test_tampered_backup_refused_before_stop(installation,monkeypatch):
    state,root=installation;assert backups.create(state,root)
    ident=backups.status(state)['last_backup']['id']
    (backups._folder(state)/ident/'data/files/company/document/original.xml').write_bytes(b'corrupt')
    monkeypatch.setattr(backups,'_stop',lambda *args:pytest.fail('must validate before stop'))
    assert not backups.restore(state,root,ident)


@pytest.mark.parametrize('ident',['../data','C:/data','',None,'20260925-000000-../../'])
def test_restore_id_rejects_paths(installation,ident):
    state,root=installation
    assert not backups.restore(state,root,ident)
    assert (state/'data/app.db').exists()


def test_manifest_traversal_rejected(installation):
    state,root=installation;assert backups.create(state,root)
    ident=backups.status(state)['last_backup']['id'];manifest=backups._folder(state)/ident/'manifest.json'
    data=json.loads(manifest.read_text());data['entries'][0]['path']='../../outside'
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError):backups._validate(state,ident)


def test_interrupted_restore_journal_restores_previous_directory(installation):
    state,root=installation
    token='a'*32;journal={'previous':'.restore-previous-'+token,'staged':'.restore-staged-'+token}
    (state/'data').rename(state/journal['previous']);(state/'data').mkdir()
    backups._atomic(state/'restore-journal.json',journal)
    backups.recover_interrupted(state,root)
    assert (state/'data/app.db').exists()
    assert not (state/'restore-journal.json').exists()


def test_competing_operation_cannot_overwrite_live_status(installation):
    state,root=installation
    with backups._guard(state):
        backups._record(state,'creating','live backup')
        assert not backups.create(state,root)
        assert backups.status(state)['state']=='creating'


def test_daily_scheduler_and_error_backoff(installation,monkeypatch):
    state,root=installation;calls=[]
    monkeypatch.setattr(local_runtime,'launch_backup',lambda *args,**kwargs:calls.append((args,kwargs)))
    assert local_runtime.automatic_backup_check(state,now=100000)
    assert calls[0][1]['automatic'] is True
    backups.save_settings(state,False,24)
    assert not local_runtime.automatic_backup_check(state,now=100000)
    backups.save_settings(state,True,24)
    backups._record(state,'error','synthetic failure')
    assert not local_runtime.automatic_backup_check(state)

def test_verification_isolated_and_persistent(installation,monkeypatch):
    state,root=installation;assert backups.create(state,root)
    ident=backups.status(state)['last_backup']['id']
    active=(state/'data/app.db').read_bytes()
    monkeypatch.setattr(backups,'_stop',lambda *args:pytest.fail('verification must not stop app'))
    result=backups.verify(state,ident)
    assert result['ok'] and result['tables']==1 and result['files']==3
    assert (state/'data/app.db').read_bytes()==active
    assert backups.status(state)['last_verified']['backup_id']==ident
    assert not list(backups._folder(state).glob('.verify-*'))


def test_verification_corruption_and_busy(installation):
    state,root=installation;assert backups.create(state,root)
    ident=backups.status(state)['last_backup']['id']
    with backups._guard(state):
        with pytest.raises(backups.BackupBusy):backups.verify(state,ident)
    (backups._folder(state)/ident/'data/files/company/document/original.xml').write_bytes(b'broken')
    assert not backups.verify(state,ident)['ok']
    assert not backups.status(state)['verification']['ok']
    assert not list(backups._folder(state).glob('.verify-*'))


@pytest.mark.parametrize('ident',['../data','C:/data',None])
def test_verify_rejects_path(installation,ident):
    state,root=installation
    with pytest.raises(ValueError):backups.verify(state,ident)


def test_empty_database_cannot_pass_restore_rehearsal(installation):
    state,root=installation;assert backups.create(state,root)
    ident=backups.status(state)['last_backup']['id'];folder=backups._folder(state)/ident
    path=folder/'data/app.db'
    with closing(sqlite3.connect(path)) as connection,connection:connection.execute('DROP TABLE documents')
    manifest=json.loads((folder/'manifest.json').read_text())
    entry=next(item for item in manifest['entries'] if item['path']=='app.db')
    entry.update(sha256=backups._hash(path),size=path.stat().st_size)
    manifest['bytes']=sum(item['size'] for item in manifest['entries'])
    backups._atomic(folder/'manifest.json',manifest)
    result=backups.verify(state,ident)
    assert not result['ok'] and 'sem tabelas' in result['message']


def test_corrupt_sqlite_even_with_updated_hash_fails(installation):
    state,root=installation;assert backups.create(state,root)
    ident=backups.status(state)['last_backup']['id'];folder=backups._folder(state)/ident
    path=folder/'data/app.db';path.write_bytes(b'not sqlite')
    manifest=json.loads((folder/'manifest.json').read_text())
    entry=next(item for item in manifest['entries'] if item['path']=='app.db')
    entry.update(sha256=backups._hash(path),size=path.stat().st_size)
    manifest['bytes']=sum(item['size'] for item in manifest['entries'])
    backups._atomic(folder/'manifest.json',manifest)
    assert not backups.verify(state,ident)['ok']


def test_verification_maintenance_lock_preserves_previous_report(installation):
    state,root=installation;assert backups.create(state,root)
    ident=backups.status(state)['last_backup']['id']
    report=backups.status(state)['verification']
    with closing(local_runtime.InstanceLock(state/'updates'/'updates.lock')):
        with pytest.raises(backups.BackupBusy):backups.verify(state,ident)
    assert backups.status(state)['verification']==report


def test_backup_restarts_before_rehearsal_even_if_rehearsal_fails(installation,monkeypatch):
    state,root=installation;events=[]
    monkeypatch.setattr(backups,'_start',lambda *args,**kwargs:events.append('restart'))
    def rehearsal(*args):
        events.append('rehearsal')
        return {'ok':False,'message':'Synthetic rehearsal failure'}
    monkeypatch.setattr(backups,'_verify_locked',rehearsal)
    assert not backups.create(state,root)
    assert events==['restart','rehearsal']
    result=backups.status(state)
    assert result['state']=='error' and 'last_created' not in result
    assert result['last_backup'] is not None
