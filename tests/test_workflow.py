import io,json
from pathlib import Path
from sqlalchemy import select
from test_app import env,RAW,KEY,valid_test_pdf
from app.db import Document,Job
from app.fiscal import E,N,flatten,build_reconstructed
from app.worker import DocumentProcessingService

def prepare(c,h,cid):
    c.post('/api/keys',json={'company':cid,'keys':KEY},headers=h)
    did=c.get('/api/documents?company='+cid).json['items'][0]['id']
    c.post('/api/documents/'+did+'/pdf',data={'file':(io.BytesIO(valid_test_pdf()),'nota.pdf')},headers=h)
    groups=flatten(E.fromstring(RAW).find('n:NFe/n:infNFe',N));groups['det']['@nItem']='1'
    return did,{'key':KEY,'groups':groups}

def test_manual_fields_have_manual_provenance(env):
    app,c,h,cid=env;did,invoice=prepare(c,h,cid)
    with app.session_factory.begin() as s:
        doc=s.get(Document,did);doc.draft=app.storage.save(cid,did,json.dumps({'invoice':invoice,'evidence':{'groups.emit.xNome':{'source':'pdf_text','confidence':0.96,'excerpt':invoice['groups']['emit']['xNome']}}}).encode(),'json')
    invoice['groups']['emit']['xNome']='NOME CONFERIDO MANUALMENTE'
    r=c.post('/api/documents/'+did+'/draft',json={'invoice':invoice},headers=h);assert r.status_code==200,r.json
    draft=c.get('/api/documents/'+did).json['draft'];evidence=draft['evidence']['groups.emit.xNome']
    assert evidence['source']=='user_review' and evidence['original_evidence']['source']=='pdf_text'

def test_worker_does_not_publish_old_review(env,monkeypatch):
    app,c,h,cid=env;did,invoice=prepare(c,h,cid)
    assert c.post('/api/documents/'+did+'/draft',json={'invoice':invoice},headers=h).status_code==200
    def concurrent_edit(old,company):
        new=json.loads(json.dumps(invoice));new['groups']['emit']['xNome']='REVISAO MAIS RECENTE'
        assert c.post('/api/documents/'+did+'/draft',json={'invoice':new},headers=h).status_code==200
        return build_reconstructed(old,company)
    monkeypatch.setattr('app.worker.build_reconstructed',concurrent_edit)
    assert DocumentProcessingService(app.session_factory,app.storage).once()
    doc=c.get('/api/documents/'+did).json
    assert not doc['has_xml'] and doc['draft']['invoice']['groups']['emit']['xNome']=='REVISAO MAIS RECENTE'
    with app.session_factory() as s:assert s.scalar(select(Job).where(Job.document_id==did)).state=='pending'
