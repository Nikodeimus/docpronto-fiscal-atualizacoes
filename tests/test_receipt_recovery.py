import base64,json,time
import pytest
from sqlalchemy import select
from test_app import env,RAW
from test_capture_audit_regressions import setup_capture
from test_new_features import response
from app.distribution import DistributionTask,DistributionState,CaptureBatch
from app.db import Setting


def prepare(env,batch=False):
    app,c,h,cid=env
    m,auth,dev,certs,poll,req=setup_capture(env)
    made=c.post('/api/distribution',json={**req,'batch':batch},headers=h)
    assert made.status_code==200
    task=m.post('/api/agent/poll',json=poll,headers=auth).json['task']
    body={'id':task['id'],'ok':True,'response':base64.b64encode(response(files=[RAW],maximum='000000000000002')).decode()}
    return m,auth,dev,poll,made.json['id'],task,body


@pytest.mark.parametrize('state',['running','failed'])
def test_expired_result_archives_without_regressing_or_continuing(env,state):
    app,c,h,cid=env
    m,auth,dev,poll,bid,task,body=prepare(env)
    future=time.time()+7200
    with app.session_factory.begin() as s:
        old=s.get(DistributionTask,task['id']);old.expires=time.time()-10;old.state=state
        cursor=s.get(DistributionState,cid);cursor.nsu='000000000000050';cursor.next_allowed=future
        s.add(DistributionTask(company_id=cid,device_id=dev,payload=json.dumps(task),state='running'))
    r=m.post('/api/agent/distribution-result',json=body,headers=auth)
    assert r.status_code==200,r.json
    assert r.json['recovered'] is True
    assert c.get('/api/documents?company='+cid).json['total']==1
    assert m.post('/api/agent/distribution-result',json=body,headers=auth).json['ok']
    with app.session_factory() as s:
        assert s.get(DistributionState,cid).nsu=='000000000000050'
        assert s.get(DistributionState,cid).next_allowed==future
        tasks=list(s.scalars(select(DistributionTask)))
        assert len(tasks)==2 and sum(t.state=='running' for t in tasks)==1


@pytest.mark.parametrize('action,state',[('pause','paused'),('cancel','cancelled')])
def test_late_result_preserves_batch_control(env,action,state):
    app,c,h,cid=env
    m,auth,dev,poll,bid,task,body=prepare(env,True)
    assert c.post('/api/capture/batches/'+bid+'/control',json={'company':cid,'action':action},headers=h).status_code==200
    with app.session_factory.begin() as s:s.get(DistributionTask,task['id']).expires=time.time()-1
    assert m.post('/api/agent/distribution-result',json=body,headers=auth).json['ok']
    with app.session_factory() as s:
        assert s.get(CaptureBatch,bid).state==state
        assert len(list(s.scalars(select(DistributionTask))))==1


def test_failed_receipt_replay_does_not_schedule_retry_twice(env):
    app,c,h,cid=env
    m,auth,dev,poll,bid,task,body=prepare(env,True)
    body={'id':task['id'],'ok':False,'error':'timeout'}
    assert m.post('/api/agent/distribution-result',json=body,headers=auth).json['ok']
    assert m.post('/api/agent/distribution-result',json=body,headers=auth).json['ok']
    with app.session_factory() as s:assert json.loads(s.get(CaptureBatch,bid).options)['retry_count']==1


def test_never_delivered_and_revoked_results_rejected(env):
    app,c,h,cid=env
    m,auth,dev,poll,bid,task,body=prepare(env)
    with app.session_factory.begin() as s:
        s.add(DistributionTask(id='never',company_id=cid,device_id=dev,payload=json.dumps(task),state='failed'))
    assert m.post('/api/agent/distribution-result',json={**body,'id':'never'},headers=auth).status_code==400
    assert m.post('/api/agent/distribution-result',json=body,headers=auth).json['ok']
    assert c.post('/api/certificates/agents/'+dev+'/revoke',json={'company':cid},headers=h).status_code==200
    assert m.post('/api/agent/distribution-result',json=body,headers=auth).status_code in (400,401)


@pytest.mark.parametrize('state',['pending','cancelled','failed'])
def test_unclaimed_task_never_accepts_receipt(env,state):
    app,c,h,cid=env
    m,auth,dev,poll,bid,task,body=prepare(env)
    with app.session_factory.begin() as s:
        s.add(DistributionTask(id='unclaimed',company_id=cid,device_id=dev,payload=json.dumps(task),state=state))
    assert m.post('/api/agent/distribution-result',json={**body,'id':'unclaimed'},headers=auth).status_code==400


def test_late_key_result_does_not_complete_replacement_item(env):
    from test_app import KEY
    from app.distribution import CaptureItem,CaptureDispatch
    app,c,h,cid=env
    m,auth,dev,certs,poll,req=setup_capture(env)
    bid=c.post('/api/distribution',json={**req,'batch':True,'keys':KEY},headers=h).json['id']
    task=m.post('/api/agent/poll',json=poll,headers=auth).json['task']
    with app.session_factory.begin() as s:
        old=s.get(DistributionTask,task['id']);old.state='failed';old.expires=time.time()-10
        dispatch=s.get(CaptureDispatch,task['id'])
        s.add(DistributionTask(id='replacement',company_id=cid,device_id=dev,payload=json.dumps(task),state='running'))
        s.flush()
        s.add(CaptureDispatch(task_id='replacement',batch_id=bid,item_id=dispatch.item_id))
    body={'id':task['id'],'ok':True,'response':base64.b64encode(response(files=[RAW])).decode()}
    assert m.post('/api/agent/distribution-result',json=body,headers=auth).json['ok']
    with app.session_factory() as s:
        assert s.get(DistributionTask,'replacement').state=='running'
        assert s.scalar(select(CaptureItem)).state=='running'
