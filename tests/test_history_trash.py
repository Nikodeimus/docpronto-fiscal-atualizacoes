from test_app import env,RAW
from app.fiscal_history import archive_xml,FiscalArchive

def setup_deleted(env):
 app,c,h,cid=env
 with app.session_factory.begin() as s:
  row=archive_xml(s,app.storage,cid,RAW,'11444777000161');rid=row.id;path=row.path
 c.post('/api/my-data/deletion-password',json={'login_password':'long-password-test','new_password':'A1b2'},headers=h)
 preview=c.post('/api/history/delete-preview',json={'company':cid,'scope':'all'},headers=h).json
 r=c.post('/api/history/delete-batch',json={'company':cid,'token':preview['token'],'confirm_count':1,'deletion_password':'A1b2'},headers=h)
 assert r.status_code==200
 batches=c.get('/api/history/trash?company='+cid).json['items']
 return batches[0]['id'],rid,path

def test_trash_restore_idempotent_and_byte_identical(env):
 app,c,h,cid=env;batch,rid,path=setup_deleted(env)
 url='/api/history/trash/'+batch+'/restore'
 assert c.post(url,json={'company':cid}).status_code==403
 first=c.post(url,json={'company':cid},headers=h)
 assert first.status_code==200 and first.json['restored']==1
 assert c.get('/api/history/'+rid+'/xml').data==RAW
 second=c.post(url,json={'company':cid},headers=h).json
 assert second['restored']==0 and second['already_restored']==1
 assert c.get('/api/history?company='+cid).json['total']==1

def test_missing_corrupt_and_duplicate_are_not_overwritten(env):
 app,c,h,cid=env;batch,rid,path=setup_deleted(env)
 original=app.storage.resolve(path);original.write_bytes(b'corrupt')
 url='/api/history/trash/'+batch+'/restore'
 bad=c.post(url,json={'company':cid},headers=h).json
 assert bad['unavailable']==1 and not bad['restored']
 original.unlink()
 assert c.post(url,json={'company':cid},headers=h).json['unavailable']==1
 original.write_bytes(RAW)
 with app.session_factory.begin() as s:archive_xml(s,app.storage,cid,RAW,'11444777000161')
 duplicate=c.post(url,json={'company':cid},headers=h).json
 assert duplicate['duplicates']==1 and duplicate['restored']==0
 assert c.get('/api/history?company='+cid).json['total']==1

def test_trash_requires_membership_admin(env):
 app,c,h,cid=env;batch,rid,path=setup_deleted(env)
 c.post('/api/users',json={'company':cid,'email':'viewer@example.test','password':'long-viewer-password','role':'viewer'},headers=h)
 viewer=app.test_client();vh={'X-CSRF-Token':viewer.post('/api/login',json={'email':'viewer@example.test','password':'long-viewer-password'}).json['csrf']}
 url='/api/history/trash/'+batch+'/restore'
 assert viewer.get('/api/history/trash?company='+cid).status_code==400
 assert viewer.post(url,json={'company':cid},headers=vh).status_code==400
 other=c.post('/api/companies',json={'name':'Other','document':'11222333000181'},headers=h).json['id']
 assert c.post(url,json={'company':other},headers=h).status_code==400
 assert app.test_client().get('/api/history/trash?company='+cid).status_code==401
 assert c.get('/api/history?company='+cid).json['total']==0

def test_partial_restore_retries_only_unavailable_items(env):
 app,c,h,cid=env
 with app.session_factory.begin() as s:
  archive_xml(s,app.storage,cid,RAW,'11444777000161')
  second=archive_xml(s,app.storage,cid,RAW+b'\n','11444777000161');missing_path=second.path
 c.post('/api/my-data/deletion-password',json={'login_password':'long-password-test','new_password':'A1b2'},headers=h)
 p=c.post('/api/history/delete-preview',json={'company':cid,'scope':'all'},headers=h).json
 removed=c.post('/api/history/delete-batch',json={'company':cid,'token':p['token'],'confirm_count':2,'deletion_password':'A1b2'},headers=h).json
 app.storage.resolve(missing_path).unlink()
 url='/api/history/trash/'+removed['trash_batch']+'/restore'
 first=c.post(url,json={'company':cid},headers=h).json
 assert first['restored']==1 and first['unavailable']==1
 app.storage.resolve(missing_path).write_bytes(RAW+b'\n')
 second=c.post(url,json={'company':cid},headers=h).json
 assert second['restored']==1 and second['already_restored']==1 and second['unavailable']==0
 assert c.get('/api/history?company='+cid).json['total']==2
