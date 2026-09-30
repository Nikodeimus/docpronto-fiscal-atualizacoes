import json
from test_app import env,KEY,RAW
from test_note_rollup import add
from app.fiscal_history import archive_xml,FiscalArchive

def test_note_details_versions_evidence_and_scope(env):
 app,c,h,cid=env
 with app.session_factory.begin() as s: archive_xml(s,app.storage,cid,RAW,'11444777000161')
 add(app,cid,'resNFe')
 d=c.get('/api/history/notes/'+KEY+'?company='+cid)
 assert d.status_code==200
 assert len(d.json['versions'])==2 and d.json['availability']=='complete'
 assert d.json['signature_validation']=='not_verified'
 assert 'Situação' in d.json['message'] and 'não' in d.json['message'] and 'Ã' not in d.json['message']
 assert d.json['fiscal_status']=='authorized_in_file'
 assert c.get('/api/history/notes/'+KEY+'?company=other').status_code==400
 assert app.test_client().get('/api/history/notes/'+KEY+'?company='+cid).status_code==401

def test_search_and_comparison_use_canonical_period(env):
 app,c,h,cid=env
 add(app,cid,'resNFe',issued='2026-08-01')
 rid=add(app,cid,'nfeProc',issued='2026-09-01')
 with app.session_factory.begin() as s:
  row=s.get(FiscalArchive,rid);row.data=json.dumps({'issued_at':'2026-09-01','issuer_name':'Empresa Pesquisa','number':'123'})
 assert c.get('/api/history?company='+cid+'&view=notes&q=Pesquisa').json['total']==1
 assert c.get('/api/history?company='+cid+'&view=notes&q=missing').json['total']==0
 r=c.post('/api/history/compare',json={'company':cid,'keys':KEY+'\n'+KEY+'\ninvalid','month_from':'2026-09'},headers=h)
 assert r.status_code==200
 assert r.json['complete']==[KEY] and r.json['duplicates']==1 and r.json['invalid']==['invalid']
 assert r.json['coverage']=='not_confirmed'
 r=c.post('/api/history/compare',json={'company':cid,'keys':KEY,'month_from':'2026-08'},headers=h)
 assert r.json['outside_period']==[KEY] and not r.json['missing']

def test_unaccepted_cancel_does_not_mark_cancelled(env):
 app,c,h,cid=env
 raw=('''<procEventoNFe xmlns="http://www.portalfiscal.inf.br/nfe"><evento><infEvento><chNFe>'''+KEY+'''</chNFe><tpEvento>110111</tpEvento></infEvento></evento><retEvento><infEvento><chNFe>'''+KEY+'''</chNFe><tpEvento>110111</tpEvento><cStat>573</cStat><nProt>123</nProt></infEvento></retEvento></procEventoNFe>''').encode()
 with app.session_factory.begin() as s: archive_xml(s,app.storage,cid,raw,'11444777000161')
 d=c.get('/api/history/notes/'+KEY+'?company='+cid).json
 assert d['fiscal_status']=='unknown' and not d['events'][0]['accepted']
 with app.session_factory.begin() as s: archive_xml(s,app.storage,cid,raw.replace(b'573',b'135'),'11444777000161')
 assert c.get('/api/history/notes/'+KEY+'?company='+cid).json['fiscal_status']=='cancelled_in_file'

def test_integrity_mismatch_never_proves_authorization(env):
 app,c,h,cid=env
 with app.session_factory.begin() as s:
  row=archive_xml(s,app.storage,cid,RAW,'11444777000161');path=row.path
 app.storage.resolve(path).write_bytes(RAW+b' ')
 d=c.get('/api/history/notes/'+KEY+'?company='+cid).json
 assert d['fiscal_status']=='unknown' and not d['versions'][0]['available']
 assert d['availability']=='unavailable'

def test_comparison_rejects_other_company_and_large_input(env):
 app,c,h,cid=env
 assert c.post('/api/history/compare',json={'company':'other','keys':KEY},headers=h).status_code==400
 assert c.post('/api/history/compare',json={'company':cid,'keys':[KEY]*10001},headers=h).status_code==400
 assert c.post('/api/history/compare',json={'company':cid,'keys':KEY,'month_from':'2026-13'},headers=h).status_code==400


def test_cancelled_summary_overrides_full_authorized_xml(env):
 app,c,h,cid=env
 with app.session_factory.begin() as s:
  archive_xml(s,app.storage,cid,RAW,'11444777000161')
  archive_xml(s,app.storage,cid,('<resNFe xmlns="http://www.portalfiscal.inf.br/nfe"><chNFe>'+KEY+'</chNFe><cSitNFe>3</cSitNFe></resNFe>').encode(),'11444777000161')
 d=c.get('/api/history/notes/'+KEY+'?company='+cid).json
 assert d['fiscal_status']=='cancelled_in_file' and d['availability']=='complete'
 assert d['signature_validation']=='not_verified'


def test_cancel_event_summary_requires_protocol_and_integrity(env):
 app,c,h,cid=env
 raw=('<resEvento xmlns="http://www.portalfiscal.inf.br/nfe"><chNFe>'+KEY+'</chNFe><tpEvento>110111</tpEvento>{}</resEvento>')
 with app.session_factory.begin() as s:archive_xml(s,app.storage,cid,raw.format('').encode(),'11444777000161')
 assert c.get('/api/history/notes/'+KEY+'?company='+cid).json['fiscal_status']=='unknown'
 with app.session_factory.begin() as s:
  row=archive_xml(s,app.storage,cid,raw.format('<nProt>123456</nProt>').encode(),'11444777000161');path=row.path
 assert c.get('/api/history/notes/'+KEY+'?company='+cid).json['fiscal_status']=='cancelled_in_file'
 app.storage.resolve(path).write_bytes(b'changed')
 assert c.get('/api/history/notes/'+KEY+'?company='+cid).json['fiscal_status']=='unknown'


def test_history_and_zip_report_cancellation_outside_emission_period(env):
 import io,zipfile
 app,c,h,cid=env
 with app.session_factory.begin() as s:
  archive_xml(s,app.storage,cid,RAW,'11444777000161')
  archive_xml(s,app.storage,cid,('<resNFe xmlns="http://www.portalfiscal.inf.br/nfe"><chNFe>'+KEY+'</chNFe><dhEmi>2026-02-01T00:00:00-03:00</dhEmi><cSitNFe>3</cSitNFe></resNFe>').encode(),'11444777000161')
 result=c.get('/api/history?company='+cid+'&view=notes&month=2025-12').json
 assert result['total']==1 and result['items'][0]['fiscal_status']=='cancelled_in_file'
 response=c.get('/api/history/export?company='+cid+'&organized=1&month=2025-12')
 assert response.status_code==200
 with zipfile.ZipFile(io.BytesIO(response.data)) as z:
  manifest=json.loads(z.read('manifesto.json'))
  assert manifest['files'][0]['fiscal_status']=='cancelled_in_file'
  assert z.read(manifest['files'][0]['path'])==RAW
