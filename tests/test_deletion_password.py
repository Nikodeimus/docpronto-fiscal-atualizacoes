from test_app import env,imp

def test_password_settings_and_required_secret(env):
    app,c,h,cid=env
    assert c.get('/api/my-data').json['deletion_password_set'] is False
    imp(c,h,cid)
    preview=c.post('/api/documents/delete-preview',json={'company':cid,'scope':'all'},headers=h).json
    body={'company':cid,'token':preview['token'],'confirm_count':preview['count'],'confirm_company':preview['company_document']}
    assert c.post('/api/documents/delete-batch',json=body,headers=h).status_code==400
    assert c.post('/api/my-data/deletion-password',json={'login_password':'wrong','new_password':'A1b2'},headers=h).status_code==400
    assert c.post('/api/my-data/deletion-password',json={'login_password':'long-password-test','new_password':'A1b2'},headers=h).status_code==200
    assert c.get('/api/my-data').json['deletion_password_set'] is True
    assert 'A1b2' not in c.get('/api/my-data').text
    assert c.post('/api/documents/delete-batch',json=body,headers=h).status_code==400
    body['deletion_password']='A1b2'
    body['confirm_count']=999
    assert c.post('/api/documents/delete-batch',json=body,headers=h).status_code==400
    body['confirm_count']=preview['count']
    assert c.post('/api/documents/delete-batch',json=body,headers=h).json['deleted']==1


def set_password(c,h):
    assert c.post('/api/my-data/deletion-password',json={'login_password':'long-password-test','new_password':'A1b2'},headers=h).status_code==200


def test_single_delete_requires_password_and_keeps_files(env):
    from test_app import RAW,KEY
    app,c,h,cid=env
    did=imp(c,h,cid).json['results'][0]['id']
    url='/api/documents/'+did+'/delete'
    assert c.post(url,json={'confirm_key':KEY},headers=h).status_code==400
    set_password(c,h)
    for body in ({'confirm_key':KEY},{'deletion_password':'a1b2'},{'deletion_password':1234}):
        assert c.post(url,json=body,headers=h).status_code==400
        assert c.get('/api/documents/'+did+'/download/xml').data==RAW
    from app.db import Document
    with app.session_factory() as s:path=app.storage.resolve(s.get(Document,did).xml)
    assert c.post(url,json={'deletion_password':'A1b2'},headers=h).status_code==200
    assert c.get('/api/documents/'+did).status_code==400
    assert path.read_bytes()==RAW


def test_single_delete_refuses_running_worker(env):
    from test_app import KEY
    from app.worker import DocumentProcessingService
    from app.db import Document,Job
    app,c,h,cid=env;set_password(c,h)
    c.post('/api/keys',json={'company':cid,'keys':KEY},headers=h)
    did=c.get('/api/documents?company='+cid).json['items'][0]['id']
    jid,owner=DocumentProcessingService(app.session_factory,app.storage).claim()
    r=c.post('/api/documents/'+did+'/delete',json={'deletion_password':'A1b2'},headers=h)
    assert r.status_code==400 and 'processamento' in r.json['error']
    with app.session_factory() as s:
        assert s.get(Document,did) is not None
        assert s.get(Job,jid).owner==owner


def test_single_and_bulk_share_attempt_limit(env):
    app,c,h,cid=env;set_password(c,h)
    did=imp(c,h,cid).json['results'][0]['id']
    preview=c.post('/api/documents/delete-preview',json={'company':cid,'scope':'all'},headers=h).json
    for _ in range(10):
        assert c.post('/api/documents/'+did+'/delete',json={'deletion_password':'xxxx'},headers=h).status_code==400
    r=c.post('/api/documents/delete-batch',json={'company':cid,'token':preview['token'],'confirm_count':1,'deletion_password':'A1b2'},headers=h)
    assert r.status_code==400 and 'Muitas tentativas' in r.json['error']
    assert c.get('/api/documents/'+did).status_code==200
