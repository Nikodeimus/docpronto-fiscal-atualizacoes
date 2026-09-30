import base64
import json
import time
from types import SimpleNamespace as NS

from sqlalchemy import select
from test_app import env,KEY
from test_agent_bridge import pair
from test_new_features import response
from app.db import Company,Setting
from app.distribution import CaptureBatch,CaptureItem,DistributionState
from app.capture_progress import build_progress


def fixture_progress(**changes):
    values=dict(batch=NS(total=5,mode='keys',state='active',created=900),counts={'completed':2,'failed':1,'pending':2},
                device=NS(revoked='0',token_hash='synthetic',expires=2000,last_seen=990),state=NS(next_allowed=0),
                samples=[{'seconds':s,'at':990} for s in [4,5,6]],now=1000)
    values.update(changes)
    return build_progress(**values)


def test_known_total_counts_failures_without_calling_them_successes():
    result=fixture_progress()
    assert result['done']==3 and result['succeeded']==2 and result['failed']==1
    assert result['percent']==60 and result['total']==5
    assert result['eta_seconds']==10 and result['eta_reason']=='recent_samples'
    assert result['elapsed_seconds']==100
    assert fixture_progress(samples=[])['eta_seconds'] is None


def test_unknown_history_wait_offline_and_future_quota_have_no_fake_eta():
    history=fixture_progress(batch=NS(mode='history',total=0,state='completed',created=900))
    assert history['total'] is history['done'] is history['percent'] is history['eta_seconds'] is None
    waiting=fixture_progress(state=NS(next_allowed=4600))
    assert waiting['wait_seconds']==3600 and waiting['status']=='waiting_sefaz'
    assert waiting['eta_seconds'] is None and waiting['next_poll_at']==4600
    offline=fixture_progress(device=None)
    assert offline['status']=='offline' and offline['eta_seconds'] is None and offline['next_poll_at'] is None
    quota=fixture_progress(recent_responses=[999]*19)
    assert quota['eta_seconds'] is None and quota['eta_reason']=='future_sefaz_limit'
    expired=fixture_progress(last_task=NS(state='running',expires=999))
    assert expired['status']=='needs_attention' and expired['eta_seconds'] is None


def test_progress_endpoint_company_isolation_and_read_only_wait(env):
    app,c,h,cid=env;machine,auth,device,_=pair(app,c,h,cid);until=time.time()+3600
    with app.session_factory() as s:
        s.add(Company(id='progress-foreign',name='Foreign',document='12345678000195'));s.flush()
        s.add(DistributionState(company_id=cid,next_allowed=until))
        batch=CaptureBatch(id='own-progress',company_id=cid,device_id=device,mode='keys',total=2,options='{}')
        s.add(batch);s.flush()
        s.add(CaptureItem(batch_id=batch.id,key=KEY,state='failed'))
        s.add(CaptureItem(batch_id=batch.id,key='9'*44,state='pending'))
        s.add(CaptureBatch(id='foreign-progress',company_id='progress-foreign',device_id=device,mode='history',options='{}'))
        s.commit()
    result=c.get('/api/capture/batches',query_string={'company':cid})
    assert result.status_code==200,result.json
    assert [b['id'] for b in result.json['items']]==['own-progress']
    progress=result.json['items'][0]['progress']
    assert progress['percent']==50 and progress['failed']==1 and progress['succeeded']==0
    assert 3595<=progress['wait_seconds']<=3600 and progress['eta_seconds'] is None
    assert c.get('/api/capture/batches?company=progress-foreign').status_code==400
    assert app.test_client().get('/api/capture/batches?company='+cid).status_code==401
    with app.session_factory() as s:assert s.get(DistributionState,cid).next_allowed==until


def test_real_result_records_sample_once_and_confirmed_response(env):
    app,c,h,cid=env;machine,auth,device,_=pair(app,c,h,cid)
    cert={'thumbprint':'A'*40,'store':'CurrentUser','has_private_key':True}
    poll={'certificates':[cert],'capabilities':['distribution']}
    machine.post('/api/agent/poll',json=poll,headers=auth)
    created=c.post('/api/distribution',json={'company':cid,'device':device,**cert,'uf':'35','batch':True,'keys':KEY},headers=h)
    assert created.status_code==200,created.json
    task=machine.post('/api/agent/poll',json=poll,headers=auth).json['task']
    body={'id':task['id'],'ok':True,'response':base64.b64encode(response()).decode()}
    assert machine.post('/api/agent/distribution-result',json=body,headers=auth).status_code==200
    assert machine.post('/api/agent/distribution-result',json=body,headers=auth).status_code==200
    with app.session_factory() as s:
        samples=json.loads(s.get(Setting,'capture-progress:'+created.json['id']).value)
        assert len(samples)==1 and samples[0]['seconds']>0
        confirmed=json.loads(s.get(Setting,'distribution:last-response:'+cid).value)
        assert confirmed['status']=='138' and confirmed['task_id']==task['id']
    progress=c.get('/api/capture/batches?company='+cid).json['items'][0]['progress']
    assert progress['percent']==100 and progress['done']==1 and progress['eta_seconds']==0
