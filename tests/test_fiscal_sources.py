import io,json,zipfile
from test_app import env,RAW,KEY
from app.db import Setting
from app.fiscal_history import FiscalArchive
from sqlalchemy import select

def test_import_old_original_with_provenance_and_duplicate(env):
 app,c,h,cid=env
 def upload():return c.post('/api/history/import',data={'company':cid,'file':(io.BytesIO(RAW),'antiga.xml')},headers=h)
 r=upload();assert r.status_code==200,r.json
 assert r.json['added']==1 and r.json['failed']==0
 row=r.json['items'][0]
 assert c.get('/api/history/'+row['id']+'/xml').data==RAW
 assert upload().json['duplicates']==1
 with app.session_factory() as s:
  source=json.loads(s.get(Setting,'history:origin:'+row['id']).value);assert source['source']=='manual_import'
 assert c.get('/api/history?company='+cid+'&view=notes').json['total']==1

def test_zip_report_and_cross_company_rejection(env):
 app,c,h,cid=env
 zipped=io.BytesIO()
 with zipfile.ZipFile(zipped,'w') as z:
  z.writestr('older.xml',RAW);z.writestr('broken.xml',b'<x/>');z.writestr('readme.txt','info')
 r=c.post('/api/history/import',data={'company':cid,'file':(io.BytesIO(zipped.getvalue()),'old.zip')},headers=h)
 assert r.status_code==200,r.json
 assert r.json['added']==1 and r.json['failed']==1
 other=c.post('/api/companies',json={'name':'Outra empresa','document':'28988409000187'},headers=h).json['id']
 r=c.post('/api/history/import',data={'company':other,'file':(io.BytesIO(RAW),'old.xml')},headers=h)
 assert r.json['failed']==1
 assert c.get('/api/history?company='+other).json['total']==0

def test_sources_cover_27_ufs_honestly(env):
 app,c,h,cid=env;r=c.get('/api/fiscal/sources?company='+cid)
 assert r.status_code==200,r.json
 assert len(r.json['states'])==27
 assert r.json['types']['nfe']['automatic'] is True
 assert r.json['types']['cte']['automatic'] is True
 assert c.get('/api/fiscal/sources?company=other').status_code in (400,403,404)

def test_history_import_requires_admin_and_csrf(env):
 app,c,h,cid=env
 assert c.post('/api/history/import',data={'company':cid,'file':(io.BytesIO(RAW),'a.xml')}).status_code==403
 c.post('/api/users',json={'company':cid,'email':'reader@example.test','password':'long-viewer-password','role':'viewer'},headers=h)
 viewer=app.test_client();token=viewer.post('/api/login',json={'email':'reader@example.test','password':'long-viewer-password'}).json['csrf']
 assert viewer.post('/api/history/import',data={'company':cid,'file':(io.BytesIO(RAW),'a.xml')},headers={'X-CSRF-Token':token}).status_code in (400,403)

def test_unlinked_event_is_not_archived(env):
 app,c,h,cid=env
 event=('<procEventoNFe xmlns="http://www.portalfiscal.inf.br/nfe"><evento><infEvento><chNFe>'+KEY+'</chNFe></infEvento></evento></procEventoNFe>').encode()
 r=c.post('/api/history/import',data={'company':cid,'file':(io.BytesIO(event),'event.xml')},headers=h)
 assert r.json['failed']==1
 zipped=io.BytesIO()
 with zipfile.ZipFile(zipped,'w') as z:z.writestr('first-event.xml',event);z.writestr('later-note.xml',RAW)
 r=c.post('/api/history/import',data={'company':cid,'file':(io.BytesIO(zipped.getvalue()),'bundle.zip')},headers=h)
 assert r.json['added']==2 and r.json['failed']==0


def test_authorized_third_party_import_keeps_original_and_separate_flow(env):
 from lxml import etree as E
 from app.fiscal import NS,N
 app,c,h,cid=env
 third=c.post('/api/companies',json={'name':'Transportadora teste','document':'28988409000187'},headers=h).json['id']
 root=E.fromstring(RAW);inf=root.find('n:NFe/n:infNFe',N)
 auth=E.SubElement(inf,'{'+NS+'}autXML');E.SubElement(auth,'{'+NS+'}CNPJ').text='28988409000187'
 raw=E.tostring(root)
 result=c.post('/api/history/import',data={'company':third,'file':(io.BytesIO(raw),'autorizada.xml')},headers=h)
 assert result.status_code==200 and result.json['added']==1,result.json
 row=result.json['items'][0]
 assert c.get('/api/history/'+row['id']+'/xml').data==raw
 notes=c.get('/api/history?company='+third+'&view=notes').json
 assert notes['items'][0]['data']['flow']=='terceiro'
 assert notes['items'][0]['data']['participation']==['autorizado_xml']
 plain=c.post('/api/history/import',data={'company':third,'file':(io.BytesIO(RAW),'sem-vinculo.xml')},headers=h)
 assert plain.json['failed']==1
