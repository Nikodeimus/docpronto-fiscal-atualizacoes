from test_app import env
from app.fiscal_channels import FiscalChannel, decode_fiscal_response
import pytest

def test_default_channels_and_consent(env):
    app,c,h,cid=env
    r=c.get('/api/fiscal/channels?company='+cid)
    assert r.status_code==200
    assert all(not x['enabled'] for x in r.json['items'])
    r=c.put('/api/fiscal/channels/manifest',json={'company':cid,'enabled':True},headers=h)
    assert r.status_code==400
    assert c.get('/api/fiscal/channels?company=missing').status_code in (400,403,404)

def test_cte_decode_bounds():
    with pytest.raises(ValueError):decode_fiscal_response('cte',b'<!DOCTYPE evil><x/>')
import base64,gzip,time
from sqlalchemy import select
from test_agent_bridge import pair
from app.agent_bridge import CompanyCertificate
from app.db import Setting
import json
from app.fiscal_channels import FiscalTask,FiscalFile

def ready(env,service='cte'):
    app,c,h,cid=env;m,auth,device,_=pair(app,c,h,cid)
    with app.session_factory.begin() as s:
        s.add(Setting(key='capture-registration:'+cid,value=json.dumps({'uf':'35','certificate':'installed','device':device})))
        s.add(CompanyCertificate(company_id=cid,device_id=device,thumbprint='A'*40,store='CurrentUser',updated_by='test'))
    assert c.put('/api/fiscal/channels/'+service,json={'company':cid,'enabled':True,'consent':True},headers=h).status_code==200
    return m,auth,device

def poll(m,auth):
    return m.post('/api/agent/poll',headers=auth,json={'certificates':[],'capabilities':['cte_distribution']}).json['task']

def response(nsu=1,status='138'):
    xml=b'<cteProc xmlns="http://www.portalfiscal.inf.br/cte"><CTe><infCte Id="CTe123"/></CTe></cteProc>'
    zipped=base64.b64encode(gzip.compress(xml)).decode()
    return f'<retDistDFeInt xmlns="http://www.portalfiscal.inf.br/cte"><cStat>{status}</cStat><ultNSU>{nsu:015d}</ultNSU><maxNSU>{nsu:015d}</maxNSU><loteDistDFeInt><docZip>{zipped}</docZip></loteDistDFeInt></retDistDFeInt>'.encode()

def test_cte_delivery_replay_scope_and_independent_cursor(env):
    app,c,h,cid=env;m,auth,device=ready(env)
    r=c.post('/api/fiscal/channels/cte/queue',json={'company':cid},headers=h);assert r.status_code==200,r.json
    task=poll(m,auth);assert task['service']=='cte' and task['nsu']=='0'*15
    data={'id':task['id'],'service':'cte','ok':True,'response':base64.b64encode(response()).decode()}
    with app.session_factory.begin() as s:s.get(FiscalTask,task['id']).expires=time.time()-999
    assert m.post('/api/agent/fiscal-result',json=data,headers=auth).status_code==200
    assert m.post('/api/agent/fiscal-result',json=data,headers=auth).json['replayed']
    files=c.get('/api/fiscal/files?company='+cid+'&service=cte').json['items'];assert len(files)==1
    assert c.get('/api/fiscal/files/'+files[0]['id']+'/download').status_code==200
    from app.distribution import DistributionState
    with app.session_factory() as s:
        assert s.get(FiscalChannel,(cid,'cte')).nsu=='000000000000001'
        assert s.get(DistributionState,cid) is None
    m2,auth2,_,_=pair(app,c,h,cid)
    assert m2.post('/api/agent/fiscal-result',json=data,headers=auth2).status_code==400

def test_pause_resume_preserves_wait_and_no_claim_without_capability(env):
    app,c,h,cid=env;m,auth,_=ready(env)
    c.post('/api/fiscal/channels/cte/queue',json={'company':cid},headers=h)
    assert m.post('/api/agent/poll',headers=auth,json={'certificates':[],'capabilities':[]}).json['task'] is None
    c.post('/api/fiscal/channels/cte/pause',json={'company':cid},headers=h)
    assert poll(m,auth) is None
    with app.session_factory.begin() as s:s.get(FiscalChannel,(cid,'cte')).next_allowed=time.time()+3600
    c.post('/api/fiscal/channels/cte/resume',json={'company':cid},headers=h)
    assert poll(m,auth) is None

def test_invalid_receipt_is_preserved_acknowledged_and_not_cursor_advance(env):
    app,c,h,cid=env;m,auth,_=ready(env)
    c.post('/api/fiscal/channels/cte/queue',json={'company':cid},headers=h);task=poll(m,auth)
    body={'id':task['id'],'service':'cte','ok':True,'response':base64.b64encode(b'<invalid/>').decode()}
    r=m.post('/api/agent/fiscal-result',json=body,headers=auth);assert r.json['rejected']
    with app.session_factory() as s:
        t=s.get(FiscalTask,task['id']);assert t.state=='failed' and app.storage.resolve(t.receipt).read_bytes()==b'<invalid/>'
        assert s.get(FiscalChannel,(cid,'cte')).nsu=='0'*15
    assert m.post('/api/agent/fiscal-result',json=body,headers=auth).json['replayed']

def test_nfse_available_and_csrf(env):
    app,c,h,cid=env
    assert c.put('/api/fiscal/channels/nfse',json={'company':cid,'enabled':True},headers=h).status_code==200
    assert c.put('/api/fiscal/channels/cte',json={'company':cid,'enabled':True}).status_code==403
    assert app.test_client().get('/api/fiscal/channels?company='+cid).status_code==401

def test_cte_rejects_missing_cursor_and_utf16_entities():
    with pytest.raises(ValueError):decode_fiscal_response('cte',b'<retDistDFeInt><cStat>138</cStat></retDistDFeInt>')
    raw='<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x [<!ENTITY x "bad">]><x>&x;</x>'.encode('utf-16')
    with pytest.raises(ValueError):decode_fiscal_response('cte',raw)

def test_manifest_requires_recipient_xml_and_stable_consent(env):
    app,c,h,cid=env;m,auth,_=ready(env,'manifest')
    from test_app import RAW,KEY
    from app.fiscal_history import archive_xml
    with app.session_factory.begin() as s:archive_xml(s,app.storage,cid,RAW,'11444777000161')
    r=c.post('/api/fiscal/channels/manifest/queue',json={'company':cid,'key':KEY},headers=h)
    assert r.status_code==200,r.json
    task=m.post('/api/agent/poll',headers=auth,json={'certificates':[],'capabilities':['manifest_science']}).json['task']
    assert task['consent'] is True and task['event_code']=='210210' and '.' not in task['event_time']
    c.post('/api/fiscal/channels/manifest/pause',json={'company':cid},headers=h)
    with app.session_factory() as s:assert s.get(FiscalTask,task['id']).payload.find('pfx')==-1
    reply=f'<retEnvEvento><retEvento><infEvento><tpAmb>1</tpAmb><cStat>135</cStat><chNFe>{KEY}</chNFe><tpEvento>210210</tpEvento><nSeqEvento>1</nSeqEvento><CNPJDest>11444777000161</CNPJDest></infEvento></retEvento></retEnvEvento>'.encode()
    r=m.post('/api/agent/fiscal-result',headers=auth,json={'id':task['id'],'service':'manifest','ok':True,'response':base64.b64encode(reply).decode()});assert r.status_code==200 and not r.json.get('rejected'),r.json
    c.post('/api/fiscal/channels/manifest/resume',json={'company':cid},headers=h)
    assert c.post('/api/fiscal/channels/manifest/queue',json={'company':cid,'key':KEY},headers=h).status_code==400

def test_manifest_existing_other_key_is_not_silently_accepted(env):
    app,c,h,cid=env;ready(env,'manifest')
    from test_app import RAW,KEY
    from app.fiscal_history import archive_xml
    with app.session_factory.begin() as s:archive_xml(s,app.storage,cid,RAW,'11444777000161')
    r=c.post('/api/fiscal/channels/manifest/queue',json={'company':cid,'key':KEY},headers=h);assert r.status_code==200
    again=c.post('/api/fiscal/channels/manifest/queue',json={'company':cid,'key':KEY},headers=h);assert again.json['id']==r.json['id']
    assert c.post('/api/fiscal/channels/manifest/queue',json={'company':cid,'key':'other-key'},headers=h).status_code==400

def test_manifest_rejects_tampered_archive(env):
    app,c,h,cid=env;ready(env,'manifest')
    from test_app import RAW,KEY
    from app.fiscal_history import archive_xml
    with app.session_factory.begin() as s:
        note=archive_xml(s,app.storage,cid,RAW,'11444777000161');path=app.storage.resolve(note.path)
    path.write_bytes(RAW+b' ')
    assert c.post('/api/fiscal/channels/manifest/queue',json={'company':cid,'key':KEY},headers=h).status_code==400

def test_invalid_base64_does_not_poison_durable_outbox(env):
    app,c,h,cid=env;m,auth,_=ready(env)
    c.post('/api/fiscal/channels/cte/queue',json={'company':cid},headers=h);task=poll(m,auth)
    body={'id':task['id'],'service':'cte','ok':True,'response':'not-base64!!!'}
    assert m.post('/api/agent/fiscal-result',headers=auth,json=body).json['rejected']
    assert m.post('/api/agent/fiscal-result',headers=auth,json=body).json['replayed']

def test_cte_rejects_valid_looking_dtd_and_multiple_returns():
    ret='<retDistDFeInt><cStat>137</cStat><ultNSU>000000000000000</ultNSU><maxNSU>000000000000000</maxNSU></retDistDFeInt>'
    raw=('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE retDistDFeInt>'+ret).encode('utf-16')
    with pytest.raises(ValueError):decode_fiscal_response('cte',raw)
    with pytest.raises(ValueError):decode_fiscal_response('cte',('<wrapper>'+ret+ret+'</wrapper>').encode())
