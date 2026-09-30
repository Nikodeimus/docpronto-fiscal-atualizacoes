import json
import pytest
from test_app import env
from app.db import User
from sqlalchemy import select
import app.local_backups_api as api
import local_backups


@pytest.fixture
def local_context(env,tmp_path,monkeypatch):
    app,client,headers,company=env
    state=tmp_path/'DocProntoLocal'
    monkeypatch.setattr(api.local_runtime,'state_root',lambda:state)
    monkeypatch.setattr(api,'installed_here',lambda *args:True)
    calls=[]
    monkeypatch.setattr(api.local_runtime,'launch_backup',lambda *args,**kwargs:calls.append((args,kwargs)))
    return app,client,headers,state,calls


def test_backup_requires_login_and_csrf(local_context):
    app,client,headers,state,calls=local_context
    assert app.test_client().get('/api/backups').status_code==401
    assert client.post('/api/backups/create',json={}).status_code==403
    assert not calls


def test_backup_controls_reject_organization_admin(local_context):
    app,client,headers,state,calls=local_context
    with app.session_factory.begin() as session:session.scalar(select(User)).role='admin'
    assert client.get('/api/backups').status_code==403
    assert client.post('/api/backups/create',json={},headers=headers).status_code==403
    assert not calls


def test_backup_status_settings_and_async_create(local_context):
    app,client,headers,state,calls=local_context
    result=client.get('/api/backups')
    assert result.status_code==200,result.json
    assert result.json['automatic'] is True and result.json['interval_hours']==24
    assert client.post('/api/backups/settings',json={'automatic':False,'interval_hours':48},headers=headers).status_code==200
    assert local_backups.settings(state)=={'automatic':False,'interval_hours':48}
    result=client.post('/api/backups/create',json={},headers=headers)
    assert result.status_code==202
    assert calls[0][0]==('create',)


def test_restore_requires_id_confirmation_and_current_password(local_context):
    app,client,headers,state,calls=local_context
    ident='20260925-121212-'+'a'*12
    folder=local_backups._folder(state)/ident;folder.mkdir();(folder/'manifest.json').write_text('{}')
    base={'backup_id':ident,'confirm':ident,'login_password':'wrong'}
    assert client.post('/api/backups/restore',json=base,headers=headers).status_code==403
    assert client.post('/api/backups/restore',json={**base,'login_password':'long-password-test','confirm':'wrong'},headers=headers).status_code==400
    assert client.post('/api/backups/restore',json={**base,'backup_id':'../../data'},headers=headers).status_code==400
    assert not calls
    result=client.post('/api/backups/restore',json={**base,'login_password':'long-password-test'},headers=headers)
    assert result.status_code==202,result.json
    assert calls[0][0]==('restore',) and calls[0][1]['backup_id']==ident


def test_unsupported_installation_cannot_launch(local_context,monkeypatch):
    app,client,headers,state,calls=local_context
    monkeypatch.setattr(api,'installed_here',lambda *args:False)
    assert client.post('/api/backups/create',json={},headers=headers).status_code==409
    assert not calls


def test_verify_access_audit_and_busy(local_context,monkeypatch):
    from app.db import Audit
    app,client,headers,state,calls=local_context
    ident='20260925-121212-'+'a'*12
    result={'backup_id':ident,'ok':True,'message':'Cópia isolada conferida.'}
    monkeypatch.setattr(local_backups,'verify',lambda *args:result)
    assert app.test_client().post('/api/backups/verify',json={'backup_id':ident}).status_code==401
    assert client.post('/api/backups/verify',json={'backup_id':ident}).status_code==403
    response=client.post('/api/backups/verify',json={'backup_id':ident},headers=headers)
    assert response.status_code==200 and response.json['ok']
    with app.session_factory() as session:
        assert session.scalar(select(Audit).where(Audit.action=='backup_verificado'))
    def blocked(*args):raise local_backups.BackupBusy('Operação em andamento')
    monkeypatch.setattr(local_backups,'verify',blocked)
    assert client.post('/api/backups/verify',json={'backup_id':ident},headers=headers).status_code==409
    with app.session_factory.begin() as session:session.scalar(select(User)).role='admin'
    assert client.post('/api/backups/verify',json={'backup_id':ident},headers=headers).status_code==403
