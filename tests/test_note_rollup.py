import json,uuid
from test_app import env,KEY
from app.fiscal_history import FiscalArchive

def add(app,cid,kind,key=KEY,issued='2026-09-01T12:00:00-03:00',created=1):
    with app.session_factory.begin() as s:
        row=FiscalArchive(company_id=cid,key=key,kind=kind,sha256=uuid.uuid4().hex,path='test.xml',data=json.dumps({'issued_at':issued}),created=created)
        s.add(row);s.flush();return row.id

def test_summary_upgrades_without_duplicate_and_never_downgrades(env):
    app,c,h,cid=env
    add(app,cid,'resNFe')
    url='/api/history?company='+cid+'&view=notes&month=2026-09'
    first=c.get(url).json
    assert first['pending_xml']==1 and first['total']==1
    full=add(app,cid,'nfeProc',created=2)
    add(app,cid,'resNFe',created=3)
    add(app,cid,'procEventoNFe',created=4)
    upgraded=c.get(url).json
    assert upgraded['total']==1 and upgraded['complete_xml']==1 and upgraded['pending_xml']==0
    assert upgraded['items'][0]['id']==full
    assert upgraded['items'][0]['status']=='complete'
    assert c.get('/api/history?company='+cid).json['total']==4
    coverage=c.get('/api/history/coverage?company='+cid+'&month=2026-09').json['items'][0]
    assert coverage['notes']==1 and coverage['complete_notes']==1 and coverage['pending_notes']==0

def test_note_filter_uses_canonical_issue_date_and_company_scope(env):
    app,c,h,cid=env
    add(app,cid,'resNFe',issued='2026-08-31')
    add(app,cid,'nfeProc',issued='2026-09-01')
    add(app,cid,'resEvento',key='')
    assert c.get('/api/history?company='+cid+'&view=notes&month=2026-08').json['total']==0
    assert c.get('/api/history?company='+cid+'&view=notes&month=2026-09').json['total']==1
    assert c.get('/api/history?company=not-member&view=notes').status_code in (400,403,404)

def test_pending_queue_filter_and_invalid_view(env):
    app,c,h,cid=env
    add(app,cid,'resNFe')
    add(app,cid,'nfeProc',key='1'*44)
    r=c.get('/api/history?company='+cid+'&view=notes&pending=1').json
    assert r['total']==1 and r['items'][0]['key']==KEY
    assert c.get('/api/history?company='+cid+'&view=invalid').status_code==400
