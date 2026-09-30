import base64,time,json
import pytest
from test_app import env,RAW
from test_agent_bridge import pair
from test_new_features import response
from app.distribution import DistributionState,DistributionTask,migrate_local_waits
from app.db import Setting


def setup_query(env,key=''):
    app,c,h,cid=env
    m,auth,device,_=pair(app,c,h,cid)
    cert={'thumbprint':'A'*40,'store':'CurrentUser','has_private_key':True}
    poll={'certificates':[cert],'capabilities':['distribution']}
    m.post('/api/agent/poll',json=poll,headers=auth)
    req={'company':cid,'device':device,**cert,'uf':'35','key':key}
    assert c.post('/api/distribution',json=req,headers=h).status_code==200
    assert c.get('/api/distribution?company='+cid).json['next_allowed']==0
    task=m.post('/api/agent/poll',json=poll,headers=auth).json['task']
    return m,auth,poll,req,task


def test_transport_failure_can_retry_without_hour(env):
    app,c,h,cid=env
    m,auth,poll,req,task=setup_query(env)
    assert c.post('/api/distribution',json=req,headers=h).status_code==400
    assert m.post('/api/agent/distribution-result',json={'id':task['id'],'ok':False,'error':'http_transport_failed'},headers=auth).status_code==200
    assert c.post('/api/distribution',json=req,headers=h).status_code==200


@pytest.mark.parametrize('status,maximum,wait',[('137','000000000000001',True),('656','000000000000001',True),('138','000000000000001',True),('138','000000000000002',False)])
def test_wait_requires_fiscal_response(env,status,maximum,wait):
    app,c,h,cid=env
    m,auth,poll,req,task=setup_query(env)
    start=time.time()
    assert m.post('/api/agent/distribution-result',json={'id':task['id'],'ok':True,'response':base64.b64encode(response(status=status,maximum=maximum)).decode()},headers=auth).status_code==200
    state=c.get('/api/distribution?company='+cid).json
    if wait:
        assert start+3599<state['next_allowed']<time.time()+3601
        assert c.post('/api/distribution',json=req,headers=h).status_code==400
    else:
        assert state['next_allowed']==0
        follow=m.post('/api/agent/poll',json=poll,headers=auth).json['task']
        assert follow and follow['nsu']=='000000000000001'


def test_point_queries_have_no_fixed_delay_but_keep_sefaz_quota(env):
    from lxml import etree
    app,c,h,cid=env
    key=etree.fromstring(RAW).find('.//{http://www.portalfiscal.inf.br/nfe}infNFe').get('Id')[3:]
    m,auth,poll,req,task=setup_query(env,key)
    for i in range(20):
        assert m.post('/api/agent/distribution-result',json={'id':task['id'],'ok':True,'response':base64.b64encode(response()).decode()},headers=auth).status_code==200
        state=c.get('/api/distribution?company='+cid).json
        if i<19:
            assert state['next_allowed']==0
            assert c.post('/api/distribution',json=req,headers=h).status_code==200
            task=m.post('/api/agent/poll',json=poll,headers=auth).json['task']
        else:
            assert state['next_allowed']>time.time()+3500
            assert c.post('/api/distribution',json=req,headers=h).status_code==400


@pytest.mark.parametrize('receipt,state_name,cleared',[(None,'failed',True),(None,'running',True),(None,'cancelled',True),('receipt.xml','completed',False),('receipt.xml','failed',False)])
def test_upgrade_releases_only_legacy_local_reservations(env,receipt,state_name,cleared):
    app,c,h,cid=env
    m,auth,poll,req,task=setup_query(env)
    with app.session_factory() as s:
        s.delete(s.get(Setting,'distribution:response-waits-v1'))
        state=s.get(DistributionState,cid);state.next_allowed=time.time()+3600
        saved=s.get(DistributionTask,task['id']);saved.state=state_name;saved.receipt=receipt
        s.flush();migrate_local_waits(s);s.commit()
        assert (state.next_allowed==0)==cleared
        migrate_local_waits(s)  # Idempotent upgrade.
