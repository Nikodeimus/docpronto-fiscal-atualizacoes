import pytest
from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql

from test_app import env
from test_teams import team, invite, bind_company
from app.db import Company, Member, ServiceProvider, User
from app.teams import ProviderMember, TeamInvite, grant_company_members


def setup_member(env, role='admin'):
    app,owner,headers,cid=env
    pid=team(owner,headers)
    bind_company(app,pid,cid)
    offered=invite(owner,headers,pid,role=role)
    old_link=invite(owner,headers,pid,role=role)
    client=app.test_client()
    result=client.post('/api/invites/'+offered['token']+'/accept',json={'email':'guest@example.test','password':'guest-long-password'})
    assert result.status_code==200,result.json
    with app.session_factory() as session:
        uid=session.scalar(select(User.id).where(User.email=='guest@example.test'))
    return pid,uid,client,{'X-CSRF-Token':result.json['csrf']},old_link


def test_change_role_updates_current_and_future_companies_preserves_other_org(env):
    app,owner,headers,cid=env
    pid,uid,guest,gh,_=setup_member(env)
    other=team(owner,headers,'Other')
    other_co=owner.post('/api/companies',json={'organization_id':other,'name':'Other','document':'12345678000195'},headers=headers).json['id']
    offered=invite(owner,headers,other,role='operator')
    assert guest.post('/api/invites/'+offered['token']+'/accept',json={},headers=gh).status_code==200
    response=owner.patch(f'/api/teams/{pid}/members/{uid}',json={'role':'viewer'},headers=headers)
    assert response.status_code==200,response.json
    assert guest.get('/api/documents',query_string={'company':cid}).status_code==200
    assert guest.post('/api/queue',json={'company':cid,'action':'pause'},headers=gh).status_code==400
    future=owner.post('/api/companies',json={'organization_id':pid,'name':'Future','document':'12345678000195'},headers=headers)
    assert future.status_code==200,future.json
    with app.session_factory() as session:
        assert session.get(ProviderMember,(uid,pid)).role=='viewer'
        assert session.get(Member,(uid,cid)).role=='viewer'
        assert session.get(Member,(uid,future.json['id'])).role=='viewer'
        assert session.get(Member,(uid,other_co)).role=='operator'
        assert session.get(User,uid).role=='user'
    assert owner.patch(f'/api/teams/{pid}/members/{uid}',json={'role':'operator'},headers=headers).status_code==200
    assert guest.post('/api/queue',json={'company':cid,'action':'pause'},headers=gh).status_code==200


def test_removed_member_loses_current_future_access_and_old_links_but_keeps_other_org(env):
    app,owner,headers,cid=env
    pid,uid,guest,gh,old_link=setup_member(env)
    other=team(owner,headers,'Other')
    offered=invite(owner,headers,other,role='viewer')
    assert guest.post('/api/invites/'+offered['token']+'/accept',json={},headers=gh).status_code==200
    other_co=owner.post('/api/companies',json={'organization_id':other,'name':'Other','document':'12345678000195'},headers=headers).json['id']
    result=owner.delete(f'/api/teams/{pid}/members/{uid}',headers=headers)
    assert result.status_code==200,result.json
    assert guest.get('/api/me').status_code==200
    assert guest.get('/api/documents',query_string={'company':cid}).status_code==400
    assert guest.get('/api/documents',query_string={'company':other_co}).status_code==200
    assert guest.get('/api/teams/'+pid+'/members').status_code==400
    assert guest.post('/api/invites/'+old_link['token']+'/accept',json={},headers=gh).status_code==400
    future=owner.post('/api/companies',json={'organization_id':pid,'name':'Future','document':'12345678000195'},headers=headers).json['id']
    with app.session_factory() as session:
        grant_company_members(session,cid,pid);session.commit()
        assert session.get(ProviderMember,(uid,pid)) is None
        assert session.get(Member,(uid,cid)) is None
        assert session.get(Member,(uid,future)) is None
        assert session.get(Member,(uid,other_co)).role=='viewer'
        assert session.get(TeamInvite,old_link['id']).revoked
    assert guest.get('/api/documents',query_string={'company':future}).status_code==400


def test_viewer_no_mutation_owner_protected_and_scope_checked(env):
    app,owner,headers,cid=env
    pid,uid,guest,gh,_=setup_member(env,'viewer')
    other=team(owner,headers,'Other')
    with app.session_factory() as session:owner_id=session.get(ServiceProvider,pid).owner_id
    for actor,auth,target_pid,target_uid in [(guest,gh,pid,uid),(owner,headers,pid,owner_id),(owner,headers,other,uid)]:
        assert actor.patch(f'/api/teams/{target_pid}/members/{target_uid}',json={'role':'admin'},headers=auth).status_code==400
        assert actor.delete(f'/api/teams/{target_pid}/members/{target_uid}',headers=auth).status_code==400
    for role in ['superadmin',None,[],{}]:
        assert owner.patch(f'/api/teams/{pid}/members/{uid}',json={'role':role},headers=headers).status_code==400
    assert owner.delete(f'/api/teams/{pid}/members/{uid}').status_code==403


@pytest.mark.parametrize('method',['patch','delete'])
def test_member_mutation_locks_org_before_authorization_snapshot_and_writes(env,method):
    app,owner,headers,cid=env
    pid,uid,guest,gh,_=setup_member(env)
    statements=[]
    def capture(conn,clauseelement,multiparams,params,execution_options):
        statements.append(' '.join(str(clauseelement.compile(dialect=postgresql.dialect())).split()))
    event.listen(app.engine,'before_execute',capture)
    try:
        result=getattr(owner,method)(f'/api/teams/{pid}/members/{uid}',json={'role':'viewer'},headers=headers)
        assert result.status_code==200,result.json
    finally:event.remove(app.engine,'before_execute',capture)
    lock=next(i for i,sql in enumerate(statements) if 'FROM service_providers ' in sql and sql.endswith('FOR UPDATE'))
    sensitive=[i for i,sql in enumerate(statements) if 'FROM provider_members ' in sql or 'UPDATE members ' in sql or 'DELETE FROM members ' in sql]
    assert sensitive and all(i>lock for i in sensitive)
