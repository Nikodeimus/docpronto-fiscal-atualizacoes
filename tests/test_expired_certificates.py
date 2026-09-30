import json,time
from datetime import datetime,timedelta,timezone
import pytest
from test_app import env
from test_agent_bridge import pair
from app.agent_bridge import AgentDevice,certificate_expired,AgentCompanyLink
from app.db import Company,Member,User
from werkzeug.security import generate_password_hash

def test_expired_dismissal_persists_and_is_company_scoped(env):
    app,c,h,cid=env
    machine,auth,did,_=pair(app,c,h,cid)
    cert={'thumbprint':'A'*40,'subject':'CN=Expired test','store':'CurrentUser','has_private_key':True,'valid_until':(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()}
    valid={**cert,'thumbprint':'B'*40,'valid_until':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}
    def poll():assert machine.post('/api/agent/poll',json={'certificates':[cert,valid]},headers=auth).status_code==200
    def listing(company):return c.get('/api/certificates/agents?company='+company).json['items'][0]['certificates']
    poll()
    assert [x['expired'] for x in listing(cid)]==[True,False]
    with app.session_factory() as s:
        owner=s.get(AgentDevice,did).created_by
        other=Company(name='Other test',document='11222333000181');s.add(other);s.flush();oid=other.id
        s.add(Member(user_id=owner,company_id=oid,role='admin'))
        s.add(AgentCompanyLink(device_id=did,company_id=oid,created_by=owner));s.commit()
    route='/api/certificates/agents/'+did+'/certificates/remove'
    assert c.post(route,json={**valid,'company':cid},headers=h).status_code==400
    assert c.post(route,json={**cert,'company':cid}).status_code==403
    assert c.post(route,json={**cert,'company':cid},headers=h).status_code==200
    assert c.post(route,json={**cert,'company':cid},headers=h).status_code==200
    poll()
    assert [x['thumbprint'] for x in listing(cid)]==['B'*40]
    assert len(listing(oid))==2
    with app.session_factory() as s:
        assert len(json.loads(s.get(AgentDevice,did).certificates))==2
        user=User(email='viewer@example.test',password=generate_password_hash('test-password-long'),role='user');s.add(user);s.flush()
        s.add(Member(user_id=user.id,company_id=oid,role='viewer'));s.commit()
    outsider=app.test_client();token=outsider.post('/api/login',json={'email':'viewer@example.test','password':'test-password-long'}).json['csrf']
    assert outsider.post(route,json={**cert,'company':oid},headers={'X-CSRF-Token':token}).status_code==400
    assert c.post(route,json={**cert,'company':'missing'},headers=h).status_code==400

@pytest.mark.parametrize('value',['','invalid','2020-01-01',None])
def test_unknown_date_not_expired(value):
    assert not certificate_expired({'valid_until':value})

def test_bulk_expired_preserves_valid_unknown_inventory_and_other_company(env):
    app,c,h,cid=env
    machine,auth,did,_=pair(app,c,h,cid)
    certs=[{'thumbprint':x*40,'store':'CurrentUser','subject':'CN=Test','has_private_key':True,'valid_until':date} for x,date in [('A','2020-01-01T00:00:00Z'),('B','2021-01-01T00:00:00Z'),('C','2099-01-01T00:00:00Z'),('D','')]]
    assert machine.post('/api/agent/poll',json={'certificates':certs},headers=auth).status_code==200
    with app.session_factory.begin() as s:
        owner=s.get(AgentDevice,did).created_by
        other=Company(name='Other bulk test',document='11222333000181');s.add(other);s.flush();oid=other.id
        s.add(Member(user_id=owner,company_id=oid,role='admin'))
        s.add(AgentCompanyLink(device_id=did,company_id=oid,created_by=owner))
    route='/api/certificates/agents/'+did+'/certificates/remove-expired'
    assert c.post(route,json={'company':cid,'count':2}).status_code==403
    assert c.post(route,json={'company':cid,'count':1},headers=h).status_code==400
    assert c.post(route,json={'company':'missing','count':2},headers=h).status_code==400
    result=c.post(route,json={'company':cid,'count':2},headers=h)
    assert result.status_code==200 and result.json['removed']==2
    assert c.post(route,json={'company':cid,'count':2},headers=h).json['removed']==0
    assert machine.post('/api/agent/poll',json={'certificates':certs},headers=auth).status_code==200
    listing=c.get('/api/certificates/agents?company='+cid).json['items'][0]['certificates']
    assert [x['thumbprint'] for x in listing]==['C'*40,'D'*40]
    assert len(c.get('/api/certificates/agents?company='+oid).json['items'][0]['certificates'])==4
    with app.session_factory() as s:assert len(json.loads(s.get(AgentDevice,did).certificates))==4

def test_expiry_offset_boundary(monkeypatch):
    monkeypatch.setattr('app.agent_bridge.time.time',lambda:datetime(2026,9,25,15,tzinfo=timezone.utc).timestamp())
    assert certificate_expired({'valid_until':'2026-09-25T12:00:00-03:00'})
    assert not certificate_expired({'valid_until':'2026-09-25T12:00:01-03:00'})
