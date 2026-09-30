import json,time
import pytest
from sqlalchemy import select
from test_app import env
from app.db import User,Member,Setting
from app.agent_bridge import AgentDevice
from app.distribution import DistributionState,DistributionTask
from app.capture_preflight import inspect_capture,UF_CODES


@pytest.fixture
def ready(env):
    app,client,headers,cid=env
    with app.session_factory.begin() as session:
        user=session.scalar(select(User))
        device=AgentDevice(id='preflight',company_id=cid,created_by=user.id,token_hash='synthetic',
            expires=time.time()+3600,last_seen=time.time(),certificates=json.dumps([{
                'thumbprint':'A'*40,'store':'CurrentUser','has_private_key':True,'valid_until':'2099-01-01T00:00:00Z'}]))
        session.add(device);session.add(Setting(key='agentcaps:preflight',value='["distribution"]'))
    return app,client,headers,cid,{'company':cid,'device':'preflight','thumbprint':'A'*40,'store':'CurrentUser','uf':'35'}


def test_ready_local_only_and_all_uf(ready):
    app,client,h,cid,options=ready
    with app.session_factory() as session:
        for uf in UF_CODES:
            result=inspect_capture(session,cid,{**options,'uf':uf})
            assert result['ready'] and result['live_test'] is False
            assert result['warnings'][-1]['code']=='network_not_tested'
        assert not inspect_capture(session,cid,{**options,'uf':'00'})['ready']


@pytest.mark.parametrize('change,code',[
    ({'last_seen':0},'connector_offline'),({'expires':0},'connector_offline'),
    ({'revoked':'1'},'connector_unlinked'),({'token_hash':None},'connector_offline'),
])
def test_unavailable_connector(ready,change,code):
    app,client,h,cid,options=ready
    with app.session_factory.begin() as session:
        device=session.get(AgentDevice,'preflight')
        for key,value in change.items():setattr(device,key,value)
        result=inspect_capture(session,cid,options)
        assert not result['ready'] and code in [item['code'] for item in result['blockers']]


@pytest.mark.parametrize('until,private,code,blocked',[
    ('2000-01-01T00:00:00Z',True,'certificate_expired',True),
    (None,True,'certificate_validity_unknown',False),
    ('2099-01-01T00:00:00Z',False,'certificate_private_key',True),
])
def test_certificate_states(ready,until,private,code,blocked):
    app,client,h,cid,options=ready
    with app.session_factory.begin() as session:
        device=session.get(AgentDevice,'preflight');cert=json.loads(device.certificates)[0]
        cert.update(valid_until=until,has_private_key=private);device.certificates=json.dumps([cert])
        result=inspect_capture(session,cid,options)
        assert result['ready'] is not blocked
        assert code in [item['code'] for item in result['blockers']+result['warnings']]


def test_cooldown_running_and_expired_task(ready):
    app,client,h,cid,options=ready;now=time.time()
    with app.session_factory.begin() as session:
        state=DistributionState(company_id=cid,next_allowed=now+3600);session.add(state)
        task=DistributionTask(company_id=cid,device_id='preflight',payload='{}',state='running',expires=now+60);session.add(task);session.flush()
        result=inspect_capture(session,cid,options,now)
        assert {'sefaz_cooldown','query_in_progress'}<={item['code'] for item in result['blockers']}
        state.next_allowed=0;task.expires=now-1;session.flush()
        assert inspect_capture(session,cid,options,now)['ready']


def test_endpoint_permissions_and_no_new_query(ready):
    app,client,h,cid,options=ready
    assert app.test_client().get('/api/capture/preflight',query_string=options).status_code==401
    response=client.get('/api/capture/preflight',query_string=options)
    assert response.status_code==200,response.json
    assert response.json['ready']
    assert client.get('/api/capture/preflight',query_string={**options,'company':'other'}).status_code==400
    with app.session_factory.begin() as session:
        assert session.scalar(select(DistributionTask)) is None
        session.scalar(select(Member).where(Member.company_id==cid)).role='viewer'
    assert client.get('/api/capture/preflight',query_string=options).status_code==400


def test_missing_capability_and_inventory_do_not_pass(ready):
    app,client,h,cid,options=ready
    with app.session_factory.begin() as session:
        session.get(Setting,'agentcaps:preflight').value='[]'
        session.get(AgentDevice,'preflight').certificates='[]'
        result=inspect_capture(session,cid,options)
        assert {'connector_unsupported','certificate_unavailable'}<={item['code'] for item in result['blockers']}
        foreign=inspect_capture(session,'different-company',options)
        assert [item['code'] for item in foreign['blockers']]==['connector_unlinked']
