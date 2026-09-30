import io,json,time,base64
from datetime import datetime,timedelta,timezone
from test_app import env,RAW
from test_agent_bridge import pair
from test_new_features import response
from app.db import Company,Member,ClientRegistration,Client,ServiceProvider
from app.agent_bridge import AgentCompanyLink
from app.distribution import DistributionState,CaptureBatch
from app.client_capture import StoredA1
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12

def pfx(document):
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME,'TEST')]);now=datetime.now(timezone.utc)
    other=x509.OtherName(x509.ObjectIdentifier('2.16.76.1.3.3'),b'\x04\x0e'+document.encode())
    cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=5)).add_extension(x509.SubjectAlternativeName([other]),False).sign(key,hashes.SHA256())
    return pkcs12.serialize_key_and_certificates(b'test',key,cert,None,serialization.BestAvailableEncryption(b'password'))

def test_history_survives_working_document_deletion(env):
    app,c,h,cid=env
    doc=c.post('/api/import',data={'company':cid,'file':(io.BytesIO(RAW),'n.xml')},headers=h).json['results'][0]['id']
    assert c.post('/api/history/archive-workspace',json={'company':cid},headers=h).status_code==200
    assert c.post('/api/history/archive-workspace',json={'company':cid},headers=h).status_code==200
    rows=c.get('/api/history?company='+cid).json
    assert rows['total']==1
    assert c.post('/api/my-data/deletion-password',json={'login_password':'long-password-test','new_password':'A1b2'},headers=h).status_code==200
    assert c.post('/api/documents/'+doc+'/delete',json={'deletion_password':'A1b2'},headers=h).status_code==200
    assert c.get('/api/history/'+rows['items'][0]['id']+'/xml').data==RAW
    assert c.get('/api/history?company=other').status_code==400

def test_a1_encrypted_bound_and_delivered_only_to_paired_agent(env):
    app,c,h,cid=env;m,auth,device,_=pair(app,c,h,cid)
    with app.session_factory() as s:document=s.get(Company,cid).document
    raw=pfx(document)
    assert c.post('/api/registrations/'+cid+'/a1',data={'file':(io.BytesIO(raw),'a.pfx'),'password':'bad'},headers=h).status_code==400
    assert c.post('/api/registrations/'+cid+'/a1',data={'file':(io.BytesIO(pfx('12345678000195')),'a.pfx'),'password':'password'},headers=h).status_code==400
    assert c.post('/api/registrations/'+cid+'/a1',data={'file':(io.BytesIO(raw),'a.pfx'),'password':'password'},headers=h).status_code==200
    with app.session_factory() as s:
        saved=s.get(StoredA1,cid);assert 'password' not in saved.encrypted and base64.b64encode(raw).decode() not in saved.encrypted
    r=c.post('/api/registrations/'+cid+'/capture',json={'device':device,'uf':'35','certificate':'a1','enabled':True},headers=h)
    assert r.status_code==200,r.json
    poll={'certificates':[],'capabilities':['distribution','stored_a1']}
    task=m.post('/api/agent/poll',json=poll,headers=auth).json['task']
    assert base64.b64decode(task['pfx'])==raw and task['pfx_password']=='password'
    assert 'pfx' not in c.get('/api/distribution?company='+cid).get_data(as_text=True)
    result=m.post('/api/agent/distribution-result',json={'id':task['id'],'ok':True,'response':base64.b64encode(response(files=[RAW])).decode()},headers=auth)
    assert result.status_code==200,result.json
    assert c.get('/api/history?company='+cid).json['total']==1
    assert c.get('/api/documents?company='+cid).json['total']==0
    assert not m.post('/api/agent/poll',json=poll,headers=auth).json.get('task')

def test_waiting_client_does_not_block_next_client(env):
    app,c,h,cid=env;m,auth,device,_=pair(app,c,h,cid)
    with app.session_factory() as s:
        first=s.get(Company,cid);owner=s.query(Member).filter_by(company_id=cid).first().user_id
        second=Company(id='second',name='Second',document='12345678000195');s.add(second);s.flush()
        s.add(AgentCompanyLink(device_id=device,company_id=second.id,created_by=owner))
        for co,wait in [(first,3600),(second,0)]:
            s.add(DistributionState(company_id=co.id,nsu='000000000000000',next_allowed=time.time()+wait-1))
            s.add(CaptureBatch(company_id=co.id,device_id=device,mode='history',options=json.dumps({'thumbprint':'A'*40,'store':'CurrentUser','document':co.document,'uf':'35','continuous':True})))
        s.commit()
    task=m.post('/api/agent/poll',json={'certificates':[{'thumbprint':'A'*40,'store':'CurrentUser','has_private_key':True}],'capabilities':['distribution']},headers=auth).json['task']
    assert task['document']=='12345678000195'

def test_queue_orders_provider_then_client(env):
    app,c,h,cid=env;m,auth,device,_=pair(app,c,h,cid)
    with app.session_factory() as s:
        owner=s.query(Member).filter_by(company_id=cid).first().user_id
        for i,pname,cname in [(0,'Raiva','A'),(1,'Alegria','Z'),(2,'Alegria','A')]:
            provider=ServiceProvider(id='p'+str(i),name=pname,owner_id=owner);s.add(provider);s.flush()
            client=Client(id='cl'+str(i),provider_id=provider.id,name=cname);s.add(client)
            co=Company(id='co'+str(i),name=cname,document=str(10000000000000+i));s.add(co);s.flush()
            s.add(ClientRegistration(company_id=co.id,client_id=client.id));s.add(AgentCompanyLink(device_id=device,company_id=co.id,created_by=owner))
            s.add(DistributionState(company_id=co.id,nsu='000000000000000',next_allowed=0))
            s.add(CaptureBatch(company_id=co.id,device_id=device,mode='history',options=json.dumps({'thumbprint':'A'*40,'store':'CurrentUser','document':co.document,'uf':'35'})))
        s.commit()
    task=m.post('/api/agent/poll',json={'certificates':[{'thumbprint':'A'*40,'store':'CurrentUser','has_private_key':True}],'capabilities':['distribution']},headers=auth).json['task']
    assert task['document']=='10000000000002'


def test_export_all_history_keys_scoped_unique_and_unpaginated(env):
    from app.fiscal_history import FiscalArchive
    app,c,h,cid=env
    expected=[str(10**43+i) for i in range(65)]
    with app.session_factory() as s:
        s.add(Company(id='keys-other',name='Other',document='12345678000195'))
        s.flush()
        for i,key in enumerate(expected+[expected[0],'','bad-key']):
            s.add(FiscalArchive(company_id=cid,key=key,kind='resNFe',sha256=str(i),path='',data='{}'))
        s.add(FiscalArchive(company_id='keys-other',key='9'*44,kind='nfeProc',sha256='other',path='',data='{}'))
        s.commit()
    r=c.get('/api/history/keys.txt?company='+cid+'&page=2')
    assert r.status_code==200
    assert r.data==('\r\n'.join(expected)+'\r\n').encode()
    assert 'attachment' in r.headers['Content-Disposition']
    assert 'historico-chaves.txt' in r.headers['Content-Disposition']
    assert 'no-store' in r.headers['Cache-Control']
    assert c.get('/api/history/keys.txt?company=keys-other').status_code==400
    assert app.test_client().get('/api/history/keys.txt?company='+cid).status_code==401


def test_export_empty_history_keys(env):
    app,c,h,cid=env
    r=c.get('/api/history/keys.txt?company='+cid)
    assert r.status_code==200 and r.data==b''


def test_history_deletion_security_snapshot_and_isolation(env):
    from app.fiscal_history import FiscalArchive
    app,c,h,cid=env
    did=c.post('/api/import',data={'company':cid,'file':(io.BytesIO(RAW),'n.xml')},headers=h).json['results'][0]['id']
    c.post('/api/history/archive-workspace',json={'company':cid},headers=h)
    hid=c.get('/api/history?company='+cid).json['items'][0]['id']
    with app.session_factory() as s:
        s.add(Company(id='delete-other',name='Other',document='12345678000195'));s.flush()
        s.add(FiscalArchive(id='other-history',company_id='delete-other',key='9'*44,kind='resNFe',sha256='other',path='',data='{}'))
        s.add(DistributionState(company_id=cid,nsu='000000000002976',next_allowed=1234567890));s.commit()
    assert c.post('/api/history/delete-preview',json={'company':cid,'scope':'selected','ids':[hid,'other-history']},headers=h).status_code==400
    assert c.post('/api/history/delete-preview',json={'company':'delete-other','scope':'all'},headers=h).status_code==400
    preview=c.post('/api/history/delete-preview',json={'company':cid,'scope':'selected','ids':[hid]},headers=h).json
    body={'company':cid,'token':preview['token'],'confirm_count':1,'deletion_password':'A1b2'}
    assert c.post('/api/history/delete-batch',json=body,headers=h).status_code==400
    c.post('/api/my-data/deletion-password',json={'login_password':'long-password-test','new_password':'A1b2'},headers=h)
    assert c.post('/api/history/delete-batch',json={**body,'deletion_password':'bad1'},headers=h).status_code==400
    assert c.post('/api/history/delete-batch',json={**body,'confirm_count':2},headers=h).status_code==400
    assert c.post('/api/history/delete-batch',json=body).status_code==403
    assert c.post('/api/history/delete-batch',json=body,headers=h).json['deleted']==1
    assert c.post('/api/history/delete-batch',json=body,headers=h).status_code==400
    assert c.get('/api/history?company='+cid).json['total']==0
    assert c.get('/api/history/keys.txt?company='+cid).data==b''
    assert c.get('/api/history/'+hid+'/xml').status_code==400
    assert c.get('/api/documents/'+did+'/download/xml').data==RAW
    with app.session_factory() as s:
        assert s.get(FiscalArchive,'other-history') is not None
        state=s.get(DistributionState,cid)
        assert state.nsu=='000000000002976' and state.next_allowed==1234567890


def test_history_delete_all_snapshot_and_permissions(env):
    from app.fiscal_history import FiscalArchive
    app,c,h,cid=env
    c.post('/api/my-data/deletion-password',json={'login_password':'long-password-test','new_password':'A1b2'},headers=h)
    with app.session_factory() as s:
        for i in range(65):s.add(FiscalArchive(id='hist'+str(i),company_id=cid,key=str(10**43+i),kind='resNFe',sha256=str(i),path='',data='{}'))
        s.commit()
    c.post('/api/users',json={'company':cid,'email':'viewer@example.test','password':'long-viewer-password','role':'viewer'},headers=h)
    viewer=app.test_client();vh={'X-CSRF-Token':viewer.post('/api/login',json={'email':'viewer@example.test','password':'long-viewer-password'}).json['csrf']}
    assert viewer.post('/api/history/delete-preview',json={'company':cid,'scope':'all'},headers=vh).status_code==400
    preview=c.post('/api/history/delete-preview',json={'company':cid,'scope':'all'},headers=h).json
    assert preview['count']==65
    body={'company':cid,'token':preview['token'],'confirm_count':65,'deletion_password':'A1b2'}
    assert viewer.post('/api/history/delete-batch',json=body,headers=vh).status_code==400
    with app.session_factory() as s:
        s.add(FiscalArchive(id='new-history',company_id=cid,key='8'*44,kind='resNFe',sha256='new',path='',data='{}'));s.commit()
    assert c.post('/api/history/delete-batch',json=body,headers=h).status_code==400
    assert c.get('/api/history?company='+cid).json['total']==66
    preview=c.post('/api/history/delete-preview',json={'company':cid,'scope':'all'},headers=h).json
    result=c.post('/api/history/delete-batch',json={**body,'token':preview['token'],'confirm_count':66},headers=h)
    assert result.status_code==200 and result.json['deleted']==66
