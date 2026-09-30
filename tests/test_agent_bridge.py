import base64,io
import pytest
from datetime import datetime,timedelta,timezone
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa,padding
from cryptography.hazmat.primitives.serialization import Encoding
from test_app import env,RAW

def pair(app,c,h,cid):
    r=c.post('/api/certificates/agents',json={'company':cid},headers=h);assert r.status_code==200,r.json
    machine=app.test_client();p=machine.post('/api/agent/pair',json={'code':r.json['code'],'name':'Windows teste'})
    assert p.status_code==200,p.json
    return machine,{'Authorization':'Bearer '+p.json['token']},r.json['id'],r.json['code']
def test_pair_once_and_revocation(env):
    app,c,h,cid=env;m,auth,id,code=pair(app,c,h,cid)
    assert m.post('/api/agent/pair',json={'code':code}).status_code==400
    assert m.post('/api/agent/poll',json={'certificates':[]},headers=auth).status_code==200
    assert c.post('/api/certificates/agents/'+id+'/revoke',headers=h).status_code==200
    assert m.post('/api/agent/poll',json={'certificates':[]},headers=auth).status_code==401

def test_agent_never_accepts_cookie_as_token(env):
    app,c,h,cid=env
    assert c.post('/api/agent/poll',json={'certificates':[]},headers=h).status_code==401

@pytest.mark.parametrize('count',[141,1000])
def test_large_inventory_preserved(env,count):
    app,c,h,cid=env;m,auth,id,_=pair(app,c,h,cid)
    certs=[{'thumbprint':f'{i:040X}','subject':f'Test {i}',
            'store':'CurrentUser','has_private_key':True} for i in range(count)]
    assert m.post('/api/agent/poll',json={'certificates':certs},headers=auth).status_code==200
    device=next(d for d in c.get('/api/certificates/agents?company='+cid).json['items'] if d['id']==id)
    assert device['online']
    assert [x['thumbprint'] for x in device['certificates']]==[x['thumbprint'] for x in certs]
    assert c.post('/api/certificates/agents/'+id+'/test',json=certs[-1],headers=h).status_code==200
    task=m.post('/api/agent/poll',json={'certificates':certs},headers=auth).json['task']
    assert task['thumbprint']==certs[-1]['thumbprint']

def test_oversized_inventory_rejected(env):
    app,c,h,cid=env;m,auth,id,_=pair(app,c,h,cid)
    assert m.post('/api/agent/poll',json={'certificates':[{}]*1001},headers=auth).status_code==400

def test_agent_import_bound_company(env):
    app,c,h,cid=env;m,auth,id,code=pair(app,c,h,cid)
    r=m.post('/api/agent/import',data={'company_id':'other-company','file':(io.BytesIO(RAW),'nota.xml')},headers=auth)
    assert r.status_code==200,r.json
    assert c.get('/api/documents?company='+cid).json['total']==1
    r2=m.post('/api/agent/import',data={'file':(io.BytesIO(RAW),'nota.xml')},headers=auth)
    assert r2.json['id']==r.json['id']

def test_certificate_challenge_end_to_end(env):
    app,c,h,cid=env;m,auth,id,_=pair(app,c,h,cid)
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048);now=datetime.now(timezone.utc)
    name=x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME,'TEST')])
    cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(1).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1)).sign(key,hashes.SHA256())
    thumb=cert.fingerprint(hashes.SHA1()).hex().upper();meta={'thumbprint':thumb,'subject':'TEST','store':'CurrentUser','valid_until':cert.not_valid_after_utc.isoformat(),'has_private_key':True}
    assert m.post('/api/agent/poll',json={'certificates':[meta]},headers=auth).status_code==200
    r=c.post('/api/certificates/agents/'+id+'/test',json={'thumbprint':thumb,'store':'CurrentUser'},headers=h);assert r.status_code==200,r.json
    task=m.post('/api/agent/poll',json={'certificates':[meta]},headers=auth).json['task']
    sig=key.sign(base64.b64decode(task['challenge']),padding.PKCS1v15(),hashes.SHA256())
    body={'id':task['id'],'ok':True,'signature':base64.b64encode(sig).decode(),'certificate_der':base64.b64encode(cert.public_bytes(Encoding.DER)).decode(),'algorithm':'RSA-SHA256'}
    r=m.post('/api/agent/result',json=body,headers=auth);assert r.status_code==200 and r.json['ok'],r.json
    assert m.post('/api/agent/result',json=body,headers=auth).status_code==400
    data=c.get('/api/certificates/agents?company='+cid).json
    assert data['items'][0]['tasks'][0]['state']=='completed'
