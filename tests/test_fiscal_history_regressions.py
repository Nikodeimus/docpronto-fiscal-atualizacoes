import io,json
from sqlalchemy import select
from test_app import env,RAW
from app.fiscal_history import FiscalArchive
from app.document_management import filtered
from app.db import Document


def test_history_month_matches_text_json_on_sqlite(env):
    app,c,h,cid=env
    with app.session_factory() as s:
        for i,month in enumerate(['2026-09','2026-08']):
            s.add(FiscalArchive(id='month'+str(i),company_id=cid,key='',kind='resNFe',sha256=str(i),path='',data=json.dumps({'issued_at':month+'-12T10:00:00-03:00'})))
        s.commit()
    result=c.get('/api/history',query_string={'company':cid,'month':'2026-09'})
    assert result.status_code==200
    assert result.json['total']==1
    assert result.json['items'][0]['id']=='month0'
    assert c.get('/api/history',query_string={'company':cid,'month':'2026-99'}).status_code==400


def test_document_filters_match_text_json_on_sqlite(env):
    app,c,h,cid=env
    with app.session_factory() as s:
        s.add(Document(id='filter-doc',company_id=cid,key='1'*44,data=json.dumps({'issued_at':'2026-09-12T10:00:00','flow':'entrada','model':'55'})))
        s.commit()
        query=filtered(select(Document).where(Document.company_id==cid),{'date_from':'2026-09-01','date_to':'2026-09-30','direction':'entrada','model':'55'})
        assert [d.id for d in s.scalars(query)]==['filter-doc']


def test_history_export_missing_file_reports_recoverable_error(env):
    app,c,h,cid=env
    with app.session_factory() as s:
        s.add(FiscalArchive(id='missing-file',company_id=cid,key='',kind='resNFe',sha256='missing',path='missing.xml',data='{}'))
        s.commit()
    result=c.get('/api/history/export',query_string={'company':cid})
    assert result.status_code==400
    assert 'arquivo' in result.json['error'].lower()
