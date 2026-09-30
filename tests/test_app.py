import io,json,time,zipfile
from pathlib import Path
import pytest
from sqlalchemy import select
from app.server import create_app
from app.db import Document,Job,User,Member,Login,Setting
from app.worker import DocumentProcessingService
RAW=(Path(__file__).parent/'fixtures/reference.xml').read_bytes();KEY='35251211222333000181551010000269501925017728'
def valid_test_pdf():
    # Minimal valid PDF solely for tests, with no claim of fiscal origin.
    stream=b'BT /F1 12 Tf 50 750 Td (TEST ONLY) Tj ET'
    objs=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    raw=b'%PDF-1.4\n';offsets=[0]
    for i,obj in enumerate(objs,1):offsets.append(len(raw));raw+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    xref=len(raw);raw+=b'xref\n0 6\n0000000000 65535 f \n'+b''.join(f'{off:010d} 00000 n \n'.encode() for off in offsets[1:])
    return raw+b'trailer\n<< /Root 1 0 R /Size 6 >>\nstartxref\n'+str(xref).encode()+b'\n%%EOF'

@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setenv('SETUP_TOKEN','test-setup-secret')
    app=create_app('sqlite:///'+str(tmp_path/'db.sqlite'),str(tmp_path/'files'),True)
    c=app.test_client()
    assert c.post('/api/bootstrap',json={'setup_token':'test-setup-secret','email':'admin@example.test','password':'long-password-test'}).status_code==200
    r=c.post('/api/login',json={'email':'admin@example.test','password':'long-password-test'})
    h={'X-CSRF-Token':r.json['csrf']}
    cid=c.post('/api/companies',json={'name':'Destinatário referência','document':'11444777000161'},headers=h).json['id']
    return app,c,h,cid

def imp(c,h,cid,raw=RAW):return c.post('/api/import',data={'company':cid,'file':(io.BytesIO(raw),'reference.xml')},headers=h)
def test_import_download_byte_identical(env):
    app,c,h,cid=env;r=imp(c,h,cid);assert r.status_code==200,r.json
    did=r.json['results'][0]['id'];out=c.get('/api/documents/'+did+'/download/xml')
    assert out.data==RAW
    assert imp(c,h,cid).json['results'][0]['duplicate'] is True
    assert c.get('/api/dashboard?company='+cid).json['total']==1
    z=c.post('/api/export',json={'company':cid,'ids':[did]},headers=h)
    with zipfile.ZipFile(io.BytesIO(z.data)) as f:
        assert f.read('importados/'+KEY+'.xml')==RAW
        assert json.loads(f.read('manifesto.json'))[0]['signature']=='NAO_VERIFICADA'
def test_csrf_origin_auth(env):
    app,c,h,cid=env
    assert c.post('/api/keys',json={'company':cid,'keys':KEY}).status_code==403
    assert c.post('/api/keys',json={'company':cid,'keys':KEY},headers={**h,'Origin':'https://evil.test'}).status_code==403
    assert app.test_client().get('/api/documents?company='+cid).status_code==401
    assert c.post('/api/bootstrap',json={}).status_code==409

def test_batch_invalid_duplicate_and_worker_manual(env):
    app,c,h,cid=env
    r=c.post('/api/keys',json={'company':cid,'keys':KEY+'\n'+KEY+'\n123'},headers=h)
    assert r.status_code==200,r.json
    assert (r.json['added'],r.json['duplicates'],r.json['invalid'])==(1,1,1)
    service=DocumentProcessingService(app.session_factory,app.storage);assert service.once()
    doc=c.get('/api/documents?company='+cid).json['items'][0]
    assert doc['status']=='acao_manual' and not doc['has_xml']
    assert not service.once()
    assert c.post('/api/documents/'+doc['id']+'/retry',headers=h).status_code==200
    assert service.once()
def test_isolation_viewer(env):
    app,c,h,cid=env
    r=imp(c,h,cid);did=r.json['results'][0]['id']
    c.post('/api/users',json={'company':cid,'email':'viewer@example.test','password':'long-viewer-password','role':'viewer'},headers=h)
    viewer=app.test_client();hr={'X-CSRF-Token':viewer.post('/api/login',json={'email':'viewer@example.test','password':'long-viewer-password'}).json['csrf']}
    assert viewer.get('/api/documents/'+did).status_code==200
    assert viewer.post('/api/keys',json={'company':cid,'keys':KEY},headers=hr).status_code==400
    other=c.post('/api/companies',json={'name':'Emitente referência','document':'11222333000181'},headers=h).json['id']
    assert viewer.get('/api/documents?company='+other).status_code==400
    assert viewer.get('/api/users?company='+cid).status_code==400
    assert viewer.post('/api/export',json={'company':other,'ids':[did]},headers=hr).status_code==400

def test_zip_partial_without_path_extraction(env):
    app,c,h,cid=env;buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as z:z.writestr('../../reference.xml',RAW);z.writestr('bad.xml','<NFe/>')
    r=c.post('/api/import',data={'company':cid,'file':(io.BytesIO(buf.getvalue()),'bundle.zip')},headers=h)
    assert r.status_code==200 and len(r.json['results'])==2
    assert 'error' in r.json['results'][1]
    assert c.get('/api/dashboard?company='+cid).json['xmls']==1

def test_pause_resume_cancel_and_expired_lease(env):
    app,c,h,cid=env;c.post('/api/keys',json={'company':cid,'keys':KEY},headers=h)
    service=DocumentProcessingService(app.session_factory,app.storage)
    c.post('/api/queue',json={'company':cid,'action':'pause'},headers=h);assert not service.once()
    c.post('/api/queue',json={'company':cid,'action':'resume'},headers=h)
    claim=service.claim();assert claim
    assert service.claim() is None
    with app.session_factory.begin() as s:s.get(Job,claim[0]).lease_until=time.time()-1
    assert service.once()
    c.post('/api/queue',json={'company':cid,'action':'cancel'},headers=h)
    assert c.get('/api/queue?company='+cid).json['counts']['cancelled']==1

def test_login_rate_limit(env):
    app,c,h,cid=env
    statuses=[c.post('/api/login',json={'email':'x','password':'bad'}).status_code for i in range(16)]
    assert statuses[-1]==429

def test_rebuild_review_gate(env):
    from app.fiscal import N,flatten,E
    app,c,h,cid=env;c.post('/api/keys',json={'company':cid,'keys':KEY},headers=h)
    did=c.get('/api/documents?company='+cid).json['items'][0]['id']
    inf=E.fromstring(RAW).find('n:NFe/n:infNFe',N);groups=flatten(inf);groups['det']['@nItem']='1'
    body={'confirmed':True,'invoice':{'key':KEY,'groups':groups}}
    assert c.post('/api/documents/'+did+'/reconstruct',json=body,headers=h).status_code==400
    c.post('/api/documents/'+did+'/pdf',data={'file':(io.BytesIO(valid_test_pdf()),'a.pdf')},headers=h)
    assert c.post('/api/documents/'+did+'/reconstruct',json=body,headers=h).status_code==200
    r=c.post('/api/export',json={'company':cid,'ids':[did]},headers=h);assert r.status_code==400
    r=c.post('/api/export',json={'company':cid,'ids':[did],'include_reconstructed':True},headers=h);assert r.status_code==200
    with zipfile.ZipFile(io.BytesIO(r.data)) as z:assert b'Signature' not in z.read('reconstruidos/'+KEY+'.xml')
def test_audit_optimistic_original_preservation(env):
    from sqlalchemy.orm.exc import StaleDataError
    app,c,h,cid=env;c.post('/api/keys',json={'company':cid,'keys':KEY},headers=h)
    did=c.get('/api/documents?company='+cid).json['items'][0]['id']
    s1=app.session_factory();s2=app.session_factory()
    d1=s1.get(Document,did);d2=s2.get(Document,did);s2.commit()
    d1.xml='first.xml';s1.commit()
    d2.xml='second.xml'
    with pytest.raises(StaleDataError):s2.commit()
    s1.close();s2.close()
    with app.session_factory() as s:assert s.get(Document,did).xml=='first.xml'

def test_audit_review_gate_adapter(env):
    app,c,h,cid=env;r=imp(c,h,cid);did=r.json['results'][0]['id']
    with app.session_factory.begin() as s:
        d=s.get(Document,did);d.source='RECONSTRUCTED';d.status='revisao'
    assert c.get('/api/documents/'+did+'/download/xml').status_code==400
    assert c.post('/api/export',json={'company':cid,'ids':[did],'include_reconstructed':True},headers=h).status_code==400
    assert c.post('/api/documents/'+did+'/approve',json={'confirmed':True},headers=h).status_code==200
    assert c.get('/api/documents/'+did+'/download/xml').status_code==200

def test_audit_zip_unsupported_method(env):
    import struct
    app,c,h,cid=env;buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as z:z.writestr('good.xml',RAW);z.writestr('bad.xml',b'<bad/>')
    raw=bytearray(buf.getvalue());pos=0
    while True:
        pos=raw.find(b'PK\x01\x02',pos)
        if pos<0:break
        n=struct.unpack_from('<H',raw,pos+28)[0]
        if raw[pos+46:pos+46+n]==b'bad.xml':struct.pack_into('<H',raw,pos+10,99)
        pos+=4
    r=c.post('/api/import',data={'company':cid,'file':(io.BytesIO(raw),'bundle.zip')},headers=h)
    assert r.status_code==200,r.json
    assert len(r.json['results'])==2 and 'error' in r.json['results'][1]
    assert c.get('/api/dashboard?company='+cid).json['xmls']==1


def test_invalid_pdf_rejected(env):
    app,c,h,cid=env;c.post('/api/keys',json={'company':cid,'keys':KEY},headers=h)
    did=c.get('/api/documents?company='+cid).json['items'][0]['id']
    r=c.post('/api/documents/'+did+'/pdf',data={'file':(io.BytesIO(b'%PDF-1.4 invalid'),'fake.pdf')},headers=h)
    assert r.status_code==400
    assert not c.get('/api/documents/'+did).json['has_pdf']
