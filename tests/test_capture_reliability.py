import json,time
import pytest
from sqlalchemy import select
from test_app import env
from test_daily_capture import configured
from app.distribution import CaptureBatch,DistributionState

@pytest.mark.parametrize('code',['timeout','connection_failed','dns_failed','http_503'])
def test_transport_retries_are_delayed_and_bounded(env,code):
    app,c,h,cid=env;m,auth,poll,bid=configured(env,'hourly')
    for index,delay in enumerate([60,300,900]):
        task=m.post('/api/agent/poll',json=poll,headers=auth).json['task'];assert task
        before=time.time()
        assert m.post('/api/agent/distribution-result',json={'id':task['id'],'ok':False,'error':code},headers=auth).status_code==200
        with app.session_factory() as s:
            b=s.get(CaptureBatch,bid);o=json.loads(b.options)
            assert b.state=='active';assert o['retry_count']==index+1
            assert before+delay<=o['retry_at']<=time.time()+delay
        assert m.post('/api/agent/poll',json=poll,headers=auth).json['task'] is None
        with app.session_factory.begin() as s:
            b=s.get(CaptureBatch,bid);o=json.loads(b.options);o['retry_at']=0;b.options=json.dumps(o)
    task=m.post('/api/agent/poll',json=poll,headers=auth).json['task']
    m.post('/api/agent/distribution-result',json={'id':task['id'],'ok':False,'error':code},headers=auth)
    with app.session_factory() as s:assert s.get(CaptureBatch,bid).state=='paused'

@pytest.mark.parametrize('code',['certificate_key_failed','tls_failed','http_403','operation_failed'])
def test_permanent_failures_pause(env,code):
    app,c,h,cid=env;m,auth,poll,bid=configured(env,'hourly')
    task=m.post('/api/agent/poll',json=poll,headers=auth).json['task']
    m.post('/api/agent/distribution-result',json={'id':task['id'],'ok':False,'error':code},headers=auth)
    with app.session_factory() as s:assert s.get(CaptureBatch,bid).state=='paused'

@pytest.mark.parametrize('action',['pause','cancel'])
def test_transport_result_does_not_revive_user_stop(env,action):
    app,c,h,cid=env;m,auth,poll,bid=configured(env,'hourly')
    task=m.post('/api/agent/poll',json=poll,headers=auth).json['task']
    c.post('/api/capture/batches/'+bid+'/control',json={'company':cid,'action':action},headers=h)
    m.post('/api/agent/distribution-result',json={'id':task['id'],'ok':False,'error':'timeout'},headers=auth)
    assert m.post('/api/agent/poll',json=poll,headers=auth).json['task'] is None
    with app.session_factory() as s:assert s.get(CaptureBatch,bid).state=={'pause':'paused','cancel':'cancelled'}[action]

def test_round_robin_and_inflight_do_not_block_other_companies(env):
    from app.agent_bridge import AgentCompanyLink,AgentDevice
    from app.db import Company
    app,c,h,cid=env;m,auth,poll,bid=configured(env,'hourly')
    other=c.post('/api/companies',json={'name':'Second synthetic','document':'11222333000181'},headers=h).json['id']
    with app.session_factory.begin() as s:
        original=s.get(CaptureBatch,bid);dev=s.get(AgentDevice,original.device_id)
        s.add(AgentCompanyLink(device_id=dev.id,company_id=other,created_by=dev.created_by))
        s.add(DistributionState(company_id=other))
        opts=json.loads(original.options);opts['document']='11222333000181'
        second=CaptureBatch(company_id=other,device_id=dev.id,options=json.dumps(opts),mode='history');s.add(second);s.flush();second_id=second.id
    first=m.post('/api/agent/poll',json=poll,headers=auth).json['task'];assert first
    second=m.post('/api/agent/poll',json=poll,headers=auth).json['task'];assert second
    assert first['document']!=second['document']
    assert m.post('/api/agent/poll',json=poll,headers=auth).json['task'] is None
    with app.session_factory.begin() as s:
        from app.distribution import DistributionTask
        for taskid in [first['id'],second['id']]:s.get(DistributionTask,taskid).state='completed'
        b=s.get(CaptureBatch,bid);o=json.loads(b.options);o['last_dispatched']=time.time();b.options=json.dumps(o)
        b=s.get(CaptureBatch,second_id);o=json.loads(b.options);o['last_dispatched']=time.time()-60;b.options=json.dumps(o)
    assert m.post('/api/agent/poll',json=poll,headers=auth).json['task']['document']=='11222333000181'

def test_success_clears_retry_and_sefaz_wait_still_wins(env):
    import base64
    from test_new_features import response
    app,c,h,cid=env;m,auth,poll,bid=configured(env,'hourly')
    task=m.post('/api/agent/poll',json=poll,headers=auth).json['task']
    m.post('/api/agent/distribution-result',json={'id':task['id'],'ok':False,'error':'timeout'},headers=auth)
    with app.session_factory.begin() as s:
        b=s.get(CaptureBatch,bid);o=json.loads(b.options);o['retry_at']=0;b.options=json.dumps(o)
        s.get(DistributionState,cid).next_allowed=time.time()+3600
    assert m.post('/api/agent/poll',json=poll,headers=auth).json['task'] is None
    with app.session_factory.begin() as s:s.get(DistributionState,cid).next_allowed=0
    task=m.post('/api/agent/poll',json=poll,headers=auth).json['task']
    m.post('/api/agent/distribution-result',json={'id':task['id'],'ok':True,'response':base64.b64encode(response(status='137',last='000000000000000',maximum='000000000000000')).decode()},headers=auth)
    with app.session_factory() as s:
        o=json.loads(s.get(CaptureBatch,bid).options);assert not o.get('retry_count');assert not o.get('retry_at')
        assert s.get(DistributionState,cid).next_allowed>time.time()

def test_key_retry_preserves_exact_item(env):
    from test_app import KEY
    from test_capture_audit_regressions import setup_capture
    from app.distribution import CaptureItem
    app,c,h,cid=env;m,auth,dev,certs,poll,req=setup_capture(env)
    created=c.post('/api/distribution',json={**req,'batch':True,'keys':KEY},headers=h);assert created.status_code==200
    bid=created.json['id'];task=m.post('/api/agent/poll',json=poll,headers=auth).json['task']
    m.post('/api/agent/distribution-result',json={'id':task['id'],'ok':False,'error':'timeout'},headers=auth)
    with app.session_factory.begin() as s:
        item=s.scalar(select(CaptureItem).where(CaptureItem.batch_id==bid));assert item.state=='pending'
        b=s.get(CaptureBatch,bid);o=json.loads(b.options);o['retry_at']=0;b.options=json.dumps(o)
    next_task=m.post('/api/agent/poll',json=poll,headers=auth).json['task'];assert next_task['key']==KEY
