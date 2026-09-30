import hashlib
import time

import pytest
from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql
from werkzeug.security import check_password_hash, generate_password_hash

from test_app import env
from app.db import Company, Member, ServiceProvider, User
from app.teams import ProviderMember, TeamInvite, grant_company_members, lock_provider, provider_role


def team(c,h,name='Organization'):
    result=c.post('/api/teams',json={'name':name},headers=h)
    assert result.status_code==200,result.json
    return result.json['id']


def invite(c,h,pid,email='guest@example.test',role='viewer'):
    result=c.post('/api/teams/'+pid+'/invites',json={'email':email,'role':role},headers=h)
    assert result.status_code==200,result.json
    return result.json


def bind_company(app,pid,cid):
    with app.session_factory() as s:
        s.get(Company,cid).organization_id=pid
        s.commit()


def test_signup_creates_private_ordinary_account_and_organization(env):
    app,c,h,cid=env
    original=team(c,h,'Original')
    new=app.test_client()
    result=new.post('/api/signup',json={'name':'New organization','email':' New@Example.Test ','password':'new-long-password','role':'superadmin'})
    assert result.status_code==200,result.json
    assert result.json['csrf']
    pid=result.json['team']['id']
    assert new.get('/api/teams').json==[{'id':pid,'name':'New organization','role':'admin','owner_id':new.get('/api/teams').json[0]['owner_id']}]
    assert new.get('/api/teams/'+original+'/members').status_code==400
    assert new.get('/api/documents',query_string={'company':cid}).status_code==400
    assert new.get('/api/me').json['role']=='user'
    with app.session_factory() as s:
        user=s.scalar(select(User).where(User.email=='new@example.test'))
        assert user.role=='user'
        assert check_password_hash(user.password,'new-long-password')
        assert provider_role(s,user,pid)=='admin'
        assert provider_role(s,user,original) is None
    assert app.test_client().post('/api/signup',json={'name':'Duplicate','email':'new@example.test','password':'other-long-password'}).status_code==409
    app.config['ALLOW_SIGNUP']=False
    assert app.test_client().post('/api/signup',json={'name':'Disabled','email':'another@example.test','password':'other-long-password'}).status_code==403


def test_signup_cannot_replace_initial_setup(env,tmp_path):
    from app.server import create_app
    empty=create_app('sqlite:///'+str(tmp_path/'empty.db'),str(tmp_path/'empty-files'),True)
    result=empty.test_client().post('/api/signup',json={'name':'Early','email':'early@example.test','password':'early-long-password'})
    assert result.status_code==409
    with empty.session_factory() as s:assert s.scalar(select(User)) is None


def test_new_user_invite_single_use_hash_only_and_company_grant(env):
    app,c,h,cid=env;pid=team(c,h);bind_company(app,pid,cid)
    unrelated=team(c,h,'Unrelated')
    with app.session_factory() as s:
        s.add(Company(id='unrelated-company',name='Unrelated company',document='12345678000195',organization_id=unrelated));s.commit()
    row=invite(c,h,pid,role='operator');token=row['token']
    anonymous=app.test_client()
    preview=anonymous.get('/api/invites/'+token)
    assert preview.status_code==200
    assert set(preview.json)=={'name','role','expires'}
    assert 'guest@example.test' not in preview.text
    assert '#invite='+token in row['link']
    with app.session_factory() as s:
        saved=s.get(TeamInvite,row['id'])
        assert saved.token_hash==hashlib.sha256(token.encode()).hexdigest()
        assert token not in repr(saved.__dict__)
    accepted=anonymous.post('/api/invites/'+token+'/accept',json={'email':'GUEST@example.test','password':'guest-long-password','role':'superadmin'})
    assert accepted.status_code==200,accepted.json
    assert anonymous.get('/api/me').json['role']=='user'
    assert anonymous.get('/api/documents',query_string={'company':cid}).status_code==200
    assert anonymous.get('/api/documents',query_string={'company':'unrelated-company'}).status_code==400
    assert anonymous.get('/api/teams/'+pid+'/members').status_code==400
    with app.session_factory() as s:
        user=s.scalar(select(User).where(User.email=='guest@example.test'))
        assert s.get(ProviderMember,(user.id,pid)).role=='operator'
        assert s.get(Member,(user.id,cid)).role=='operator'
        assert s.get(Member,(user.id,'unrelated-company')) is None
        assert s.get(TeamInvite,row['id']).used_at is not None
        owner=s.get(ServiceProvider,pid).owner_id
        assert s.get(Member,(owner,cid)).role=='admin'
    assert app.test_client().get('/api/invites/'+token).status_code==400
    assert anonymous.post('/api/invites/'+token+'/accept',json={},headers={'X-CSRF-Token':accepted.json['csrf']}).status_code==400


def test_existing_user_requires_login_csrf_and_matching_email_without_password_reset(env):
    app,c,h,cid=env;pid=team(c,h);bind_company(app,pid,cid)
    with app.session_factory() as s:
        existing=User(email='existing@example.test',password=generate_password_hash('existing-password'),role='user')
        s.add(existing);s.commit();user_id=existing.id;old_hash=existing.password
    row=invite(c,h,pid,email='existing@example.test',role='admin');url='/api/invites/'+row['token']+'/accept'
    client=app.test_client()
    assert client.post(url,json={'email':'existing@example.test','password':'replacement-password'}).status_code==401
    login=client.post('/api/login',json={'email':'existing@example.test','password':'existing-password'})
    assert login.status_code==200
    assert client.post(url,json={}).status_code==403
    assert c.post(url,json={'email':'existing@example.test'},headers=h).status_code==403
    result=client.post(url,json={},headers={'X-CSRF-Token':login.json['csrf']})
    assert result.status_code==200,result.json
    assert result.json['csrf']==login.json['csrf']
    with app.session_factory() as s:
        assert s.get(User,user_id).password==old_hash
        assert s.get(User,user_id).role=='user'
        assert s.get(Member,(user_id,cid)).role=='admin'
        assert s.get(ServiceProvider,pid).owner_id!=user_id
    assert client.get('/api/teams/'+pid+'/members').status_code==200
    assert c.post('/api/teams/'+pid+'/invites',json={'email':'existing@example.test','role':'viewer'},headers=h).status_code==400


def test_invite_expiry_revoke_isolation_and_wrong_email(env):
    app,c,h,cid=env;pid=team(c,h);other=team(c,h,'Other organization')
    row=invite(c,h,pid);url='/api/invites/'+row['token']
    public=app.test_client()
    assert public.post(url+'/accept',json={'email':'wrong@example.test','password':'guest-long-password'}).status_code==403
    assert c.post('/api/teams/'+other+'/invites/'+row['id']+'/revoke',headers=h).status_code==400
    assert c.post('/api/teams/'+pid+'/invites/'+row['id']+'/revoke',headers=h).status_code==200
    assert public.get(url).status_code==400
    assert public.post(url+'/accept',json={'email':'guest@example.test','password':'guest-long-password'}).status_code==400
    expired=invite(c,h,pid,'expired@example.test')
    with app.session_factory() as s:s.get(TeamInvite,expired['id']).expires=time.time()-1;s.commit()
    assert public.get('/api/invites/'+expired['token']).status_code==400
    assert public.post('/api/invites/'+expired['token']+'/accept',json={'email':'expired@example.test','password':'guest-long-password'}).status_code==400
    assert c.post('/api/teams/'+pid+'/invites',json={'email':'guest@example.test','role':'superadmin'},headers=h).status_code==400
    with app.session_factory() as s:
        company=s.get(Company,cid);company.organization_id=pid;s.flush()
        with pytest.raises(ValueError):grant_company_members(s,cid,other)
        assert company.organization_id==pid
        assert s.scalar(select(User).where(User.email=='wrong@example.test')) is None


@pytest.mark.parametrize('invalid',[
    {'name':[],'email':'test@example.test','password':'long-test-password'},
    {'name':'Organization','email':[],'password':'long-test-password'},
    {'name':'Organization','email':'a b@example.test','password':'long-test-password'},
    {'name':'Organization','email':'test@example.test','password':[]},
    {'name':'Organization','email':'test@example.test','password':'short'},
])
def test_signup_rejects_invalid_shapes(env,invalid):
    app,c,h,cid=env
    assert app.test_client().post('/api/signup',json=invalid).status_code==400


@pytest.mark.parametrize('accept_first',[True,False])
def test_company_and_invitation_share_provider_lock_and_both_orders_grant_access(env,accept_first):
    """Check lock order using executed SQL expressions compiled for PostgreSQL.

    The fixture runs SQLite, so this verifies the shared synchronization contract
    and both serialized outcomes, not live PostgreSQL blocking behavior.
    """
    app,c,h,cid=env;pid=team(c,h)
    offered=invite(c,h,pid,'concurrent@example.test','viewer')
    guest=app.test_client();statements=[];traces={}
    def capture(conn,clauseelement,multiparams,params,execution_options):
        statements.append(' '.join(str(clauseelement.compile(dialect=postgresql.dialect())).split()))
    event.listen(app.engine,'before_execute',capture)
    try:
        def accept():
            statements.clear()
            result=guest.post('/api/invites/'+offered['token']+'/accept',json={'email':'concurrent@example.test','password':'concurrent-test-password'})
            assert result.status_code==200,result.json
            traces['accept']=list(statements)
        def create():
            statements.clear()
            result=c.post('/api/companies',json={'organization_id':pid,'name':'Concurrent company','document':'12345678000195'},headers=h)
            assert result.status_code==200,result.json
            traces['create']=list(statements)
            return result.json['id']
        if accept_first:accept();created=create()
        else:created=create();accept()
    finally:event.remove(app.engine,'before_execute',capture)
    for action,sql in traces.items():
        lock=next(i for i,q in enumerate(sql) if 'FROM service_providers ' in q and q.endswith('FOR UPDATE'))
        sensitive=[i for i,q in enumerate(sql) if 'FROM provider_members ' in q or 'INSERT INTO companies ' in q or ('FROM companies ' in q and 'organization_id' in q)]
        assert sensitive,action
        assert all(lock<i for i in sensitive),(action,sql)
        if action=='accept':
            invitation_lock=next(i for i,q in enumerate(sql) if 'FROM team_invites ' in q and q.endswith('FOR UPDATE'))
            assert lock<invitation_lock
    with app.session_factory() as s:
        user=s.scalar(select(User).where(User.email=='concurrent@example.test'))
        assert s.get(Member,(user.id,created)).role=='viewer'
    assert guest.get('/api/documents',query_string={'company':created}).status_code==200


def test_provider_lock_precedes_pending_company_autoflush(env):
    app,c,h,cid=env;pid=team(c,h);statements=[]
    def capture(conn,clauseelement,multiparams,params,execution_options):
        statements.append(' '.join(str(clauseelement.compile(dialect=postgresql.dialect())).split()))
    event.listen(app.engine,'before_execute',capture)
    try:
        with app.session_factory() as s:
            s.add(Company(name='Pending company',document='12345678000195',organization_id=pid))
            lock_provider(s,pid)
            assert not any(q.startswith('INSERT INTO companies ') for q in statements)
            assert statements[-1].endswith('FOR UPDATE')
            s.rollback()
    finally:event.remove(app.engine,'before_execute',capture)


def test_signup_configuration_reads_environment_after_database_initialization(tmp_path,monkeypatch):
    from app import db
    from app.server import create_app
    monkeypatch.delenv('DOCPRONTO_ALLOW_SIGNUP',raising=False)
    monkeypatch.setattr(db,'load_env',lambda:monkeypatch.setenv('DOCPRONTO_ALLOW_SIGNUP','0'))
    app=create_app('sqlite:///'+str(tmp_path/'signup-config.db'),str(tmp_path/'signup-config-files'),True)
    assert app.config['ALLOW_SIGNUP'] is False
    app.engine.dispose()
