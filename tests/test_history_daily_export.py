import io,json,zipfile
from test_app import env,KEY,RAW
from test_note_rollup import add
from app.fiscal_history import archive_xml,FiscalArchive
from app.db import Setting

def test_day_range_inclusive_and_validated(env):
 app,c,h,cid=env
 add(app,cid,'resNFe',issued='2026-09-28T23:59:59-03:00')
 add(app,cid,'nfeProc',issued='2026-09-29T00:00:00-03:00')
 add(app,cid,'resNFe',key='1'*44,issued='2026-09-30T23:59:59-03:00')
 url='/api/history?company='+cid+'&view=notes'
 assert c.get(url+'&date_from=2026-09-28').json['total']==0
 assert c.get(url+'&date_from=2026-09-29&date_to=2026-09-30').json['total']==2
 assert c.get(url+'&date_from=2026-09-30').json['total']==1
 coverage=c.get('/api/history/coverage?company='+cid+'&date_from=2026-09-30').json
 assert len(coverage['items'])==1 and coverage['items'][0]['notes']==1
 for query in ('date_to=2026-09-30','date_from=2026-02-30','date_from=2026-09-30&date_to=2026-09-29','date_from=2026-09-29&month=2026-09'):
  assert c.get(url+'&'+query).status_code==400
 r=c.post('/api/history/compare',json={'company':cid,'keys':KEY,'date_from':'2026-09-28'},headers=h)
 assert r.json['outside_period']==[KEY]
 assert c.get('/api/history/keys.txt?company='+cid+'&date_from=2026-09-30').data.strip()==b'1'*44

def test_organized_export_preserves_bytes_and_provenance(env):
 app,c,h,cid=env
 with app.session_factory.begin() as s:
  row=archive_xml(s,app.storage,cid,RAW,'11444777000161');rid=row.id;issued=json.loads(row.data)['issued_at'][:10]
  s.add(Setting(key='history:origin:'+rid,value=json.dumps({'source':'manual_import','filename':'client.xml'})))
 result=c.get('/api/history/export?company='+cid+'&organized=1&date_from='+issued)
 assert result.status_code==200
 with zipfile.ZipFile(io.BytesIO(result.data)) as z:
  manifest=json.loads(z.read('manifesto.json'));entry=manifest['files'][0]
  assert entry['path'].startswith('11444777000161/'+issued[:7]+'/nfeProc/')
  assert z.read(entry['path'])==RAW
  assert entry['origin']['source']=='manual_import' and entry['availability']=='complete'
  assert manifest['coverage']=='not_confirmed'
 with zipfile.ZipFile(io.BytesIO(c.get('/api/history/export?company='+cid).data)) as z:assert 'manifesto.json' not in z.namelist()
 with zipfile.ZipFile(io.BytesIO(c.get('/api/history/export?company='+cid+'&date_from=2000-01-01').data)) as z:assert not z.namelist()


def test_organized_export_rejects_corrupted_original(env):
 app,c,h,cid=env
 with app.session_factory.begin() as s:
  row=archive_xml(s,app.storage,cid,RAW,'11444777000161');path=row.path
 app.storage.resolve(path).write_bytes(b'corrupt')
 response=c.get('/api/history/export?company='+cid+'&organized=1')
 assert response.status_code==400 and 'integridade' in response.json['error']
