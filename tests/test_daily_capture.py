import base64,json,time
from datetime import datetime,timezone
from sqlalchemy import select
from test_app import env
from test_capture_audit_regressions import setup_capture
from test_new_features import response
from app.distribution import CaptureBatch,DistributionState
from app.agent_bridge import CompanyCertificate
from app.capture_schedule import first_daily_run

def test_daily_next_morning():
    now=datetime(2026,9,29,15,tzinfo=timezone.utc).timestamp()
    assert first_daily_run(now)==datetime(2026,9,30,11,tzinfo=timezone.utc).timestamp()

def configured(env,schedule='daily'):
    app,c,h,cid=env
    m,auth,dev,certs,poll,req=setup_capture(env)
    assert c.post('/api/certificates/selection',json={'company':cid,'device_id':dev,**certs[0]},headers=h).status_code==200
    r=c.post('/api/registrations/'+cid+'/capture',json={'device':dev,'uf':'35','certificate':'installed','enabled':True,'schedule':schedule},headers=h)
    assert r.status_code==200,r.json
    with app.session_factory() as s:bid=s.scalar(select(CaptureBatch).where(CaptureBatch.state=='active')).id
    return m,auth,poll,bid

def test_daily_waits_then_continues_saved_nsu(env):
    app,c,h,cid=env;m,auth,poll,bid=configured(env)
    assert m.post('/api/agent/poll',json=poll,headers=auth).json['task'] is None
    with app.session_factory.begin() as s:
        b=s.get(CaptureBatch,bid);o=json.loads(b.options);o['next_run']=time.time()-1;b.options=json.dumps(o)
        s.get(DistributionState,cid).nsu='000000000000123'
    task=m.post('/api/agent/poll',json=poll,headers=auth).json['task']
    assert task['nsu']=='000000000000123'
    r=m.post('/api/agent/distribution-result',json={'id':task['id'],'ok':True,'response':base64.b64encode(response(status='137',last='000000000000123',maximum='000000000000123')).decode()},headers=auth)
    assert r.status_code==200,r.json
    with app.session_factory.begin() as s:
        b=s.get(CaptureBatch,bid);o=json.loads(b.options);assert o['next_run']>time.time();assert o['last_daily_target']
        s.get(DistributionState,cid).next_allowed=0
    assert m.post('/api/agent/poll',json=poll,headers=auth).json['task'] is None

def test_resume_automatically_dispatches_after_cooldown(env):
    app,c,h,cid=env;m,auth,poll,bid=configured(env,'hourly')
    with app.session_factory.begin() as s:s.get(DistributionState,cid).next_allowed=time.time()+3600
    for action in ['pause','resume']:
        assert c.post('/api/capture/batches/'+bid+'/control',json={'company':cid,'action':action},headers=h).status_code==200
    assert m.post('/api/agent/poll',json=poll,headers=auth).json['task'] is None
    with app.session_factory.begin() as s:s.get(DistributionState,cid).next_allowed=time.time()-1
    assert m.post('/api/agent/poll',json=poll,headers=auth).json['task']['kind']=='distribution'

def test_expired_alert_and_unknown_validity(env):
    app,c,h,cid=env;m,auth,poll,bid=configured(env)
    poll['certificates'][0]['valid_until']='2020-01-01T00:00:00Z'
    m.post('/api/agent/poll',json=poll,headers=auth)
    r=c.get('/api/certificates/alerts');assert r.status_code==200
    assert len(r.json['items'])==1 and r.json['items'][0]['company']==cid
    poll['certificates'][0]['valid_until']=''
    m.post('/api/agent/poll',json=poll,headers=auth)
    assert c.get('/api/certificates/alerts').json['items']==[]

def test_resume_recovers_task_that_expired_while_paused(env):
    from app.distribution import DistributionTask
    app,c,h,cid=env;m,auth,poll,bid=configured(env,'hourly')
    task=m.post('/api/agent/poll',json=poll,headers=auth).json['task']
    assert c.post('/api/capture/batches/'+bid+'/control',json={'company':cid,'action':'pause'},headers=h).status_code==200
    with app.session_factory.begin() as s:s.get(DistributionTask,task['id']).expires=time.time()-1
    assert c.post('/api/capture/batches/'+bid+'/control',json={'company':cid,'action':'resume'},headers=h).status_code==200
    next_task=m.post('/api/agent/poll',json=poll,headers=auth).json['task']
    assert next_task and next_task['id']!=task['id']
