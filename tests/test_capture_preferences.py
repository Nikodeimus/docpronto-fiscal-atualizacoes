from datetime import datetime,timezone
import json,time
from test_app import env
from test_daily_capture import configured
from app.capture_schedule import first_daily_run
from app.distribution import CaptureBatch,DistributionState
from sqlalchemy import select

def test_custom_daily_hour():
    now=datetime(2026,9,29,15,tzinfo=timezone.utc).timestamp()
    assert first_daily_run(now,6)==datetime(2026,9,30,9,tzinfo=timezone.utc).timestamp()

def test_schedule_saved_and_validated(env):
    app,c,h,cid=env;m,auth,poll,bid=configured(env)
    saved=c.get('/api/registrations/'+cid+'/capture').json['settings']
    payload={**saved,'schedule_hour':6,'interval_hours':4,'priority':1}
    r=c.post('/api/registrations/'+cid+'/capture',json=payload,headers=h)
    assert r.status_code==200,r.json
    settings=c.get('/api/registrations/'+cid+'/capture').json['settings']
    assert settings['schedule_hour']==6 and settings['interval_hours']==4 and settings['priority']==1
    with app.session_factory() as s:
        active=s.scalar(select(CaptureBatch).where(CaptureBatch.company_id==cid,CaptureBatch.state=='active'))
        assert json.loads(active.options)['priority']==1
    for field,value in [('schedule_hour',24),('interval_hours',0),('priority',2),('interval_hours',True)]:
        assert c.post('/api/registrations/'+cid+'/capture',json={**payload,field:value},headers=h).status_code==400

def test_long_interval_preserves_next_time(env):
    import base64
    from test_new_features import response
    app,c,h,cid=env;m,auth,poll,bid=configured(env,'hourly')
    with app.session_factory.begin() as s:
        b=s.get(CaptureBatch,bid);o=json.loads(b.options);o['interval_hours']=4;b.options=json.dumps(o)
    task=m.post('/api/agent/poll',json=poll,headers=auth).json['task']
    now=time.time()
    r=m.post('/api/agent/distribution-result',json={'id':task['id'],'ok':True,'response':base64.b64encode(response(status='137',last='000000000000123',maximum='000000000000123')).decode()},headers=auth)
    assert r.status_code==200,r.json
    with app.session_factory.begin() as s:
        assert json.loads(s.get(CaptureBatch,bid).options)['next_run']>=now+4*3600
        s.get(DistributionState,cid).next_allowed=0
    assert m.post('/api/agent/poll',json=poll,headers=auth).json['task'] is None

def test_priority_does_not_starve_waiting_company(env):
 from app.agent_bridge import AgentCompanyLink,AgentDevice
 from app.distribution import DistributionTask
 app,c,h,cid=env;m,auth,poll,bid=configured(env,'hourly')
 other=c.post('/api/companies',json={'name':'Fila sintética','document':'11222333000181'},headers=h).json['id']
 with app.session_factory.begin() as s:
  b=s.get(CaptureBatch,bid);dev=s.get(AgentDevice,b.device_id)
  s.add(AgentCompanyLink(device_id=dev.id,company_id=other,created_by=dev.created_by));s.add(DistributionState(company_id=other))
  opts=json.loads(b.options);opts.update(document='11222333000181',priority=1,last_dispatched=time.time())
  high=CaptureBatch(company_id=other,device_id=dev.id,options=json.dumps(opts),mode='history');s.add(high);s.flush();high_id=high.id
  opts=json.loads(b.options);opts['last_dispatched']=time.time()-60;b.options=json.dumps(opts)
 task=m.post('/api/agent/poll',json=poll,headers=auth).json['task'];assert task['document']=='11222333000181'
 with app.session_factory.begin() as s:
  s.get(DistributionTask,task['id']).state='completed'
  b=s.get(CaptureBatch,bid);opts=json.loads(b.options);opts['last_dispatched']=time.time()-600;b.options=json.dumps(opts)
 assert m.post('/api/agent/poll',json=poll,headers=auth).json['task']['document']!='11222333000181'
