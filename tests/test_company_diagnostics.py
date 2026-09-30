import json
import time
from datetime import datetime,timedelta,timezone

from sqlalchemy import select
from test_app import env
from test_agent_bridge import pair
from app.db import Company,Member,Setting
from app.agent_bridge import AgentDevice,CompanyCertificate
from app.distribution import DistributionState,DistributionTask
from app.client_capture import StoredA1


def test_diagnostics_unknown_is_not_real_sefaz_connection(env):
    app,c,h,cid=env
    result=c.get('/api/diagnostics?company='+cid)
    assert result.status_code==200,result.json
    assert not result.json['connector']['online']
    assert result.json['certificate']['validity']=='unknown'
    assert result.json['sefaz']['status']=='unknown'
    assert result.json['sefaz']['live_test'] is False
    assert result.json['sefaz']['last_response'] is None
    with app.session_factory() as s:assert s.scalar(select(DistributionTask)) is None


def test_diagnostics_checks_validity_private_key_wait_and_company_scope(env):
    app,c,h,cid=env;machine,auth,device,_=pair(app,c,h,cid)
    with app.session_factory() as s:
        owner=s.scalar(select(Member.user_id).where(Member.company_id==cid))
        dev=s.get(AgentDevice,device)
        dev.certificates=json.dumps([{'thumbprint':'A'*40,'store':'CurrentUser','has_private_key':False,'valid_until':(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()}])
        s.add(CompanyCertificate(company_id=cid,device_id=device,thumbprint='A'*40,store='CurrentUser',updated_by=owner))
        s.add(DistributionState(company_id=cid,next_allowed=time.time()+3600))
        s.add(Company(id='diagnostic-other',name='Foreign',document='12345678000195'))
        s.commit()
    result=c.get('/api/diagnostics?company='+cid).json
    assert result['connector']['online']
    assert result['certificate']['validity']=='expired'
    assert result['certificate']['has_private_key'] is False
    assert result['certificate']['available'] is False
    assert result['sefaz']['status']=='waiting' and result['sefaz']['wait_seconds']>3590
    assert c.get('/api/diagnostics?company=diagnostic-other').status_code==400
    assert app.test_client().get('/api/diagnostics?company='+cid).status_code==401
    c.post('/api/users',json={'company':cid,'email':'diagnostic-viewer@example.test','password':'long-viewer-password','role':'viewer'},headers=h)
    viewer=app.test_client();viewer.post('/api/login',json={'email':'diagnostic-viewer@example.test','password':'long-viewer-password'})
    assert viewer.get('/api/diagnostics?company='+cid).status_code==400


def test_stored_a1_diagnostics_never_decrypt_or_expose_secret(env):
    app,c,h,cid=env;machine,auth,device,_=pair(app,c,h,cid)
    with app.session_factory() as s:
        s.add(StoredA1(company_id=cid,encrypted='SECRET-DO-NOT-DECRYPT',metadata_json=json.dumps({'valid_until':(datetime.now(timezone.utc)+timedelta(days=3)).isoformat()})))
        s.add(Setting(key='capture-registration:'+cid,value=json.dumps({'device':device,'certificate':'a1'})))
        s.add(Setting(key='agentcaps:'+device,value=json.dumps(['distribution','stored_a1'])))
        s.commit()
    result=c.get('/api/diagnostics?company='+cid)
    assert result.status_code==200 and 'SECRET' not in result.text
    assert result.json['certificate']['validity']=='valid' and result.json['certificate']['available']
    assert result.json['sefaz']['live_test'] is False
