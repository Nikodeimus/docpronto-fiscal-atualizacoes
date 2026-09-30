import io,json,zipfile
import pytest
from test_app import env
from app.db import Company
from app.fiscal_history import FiscalArchive


def seed(app,cid):
    entries=[('before','2025-12-31T23:59:59-03:00'),('first','2026-01-01T00:00:00-03:00'),('last','2026-02-28T23:59:59-03:00'),('after','2026-03-01T00:00:00-03:00'),('undated','')]
    result={}
    with app.session_factory() as s:
        s.add(Company(id='period-other',name='Other',document='12345678000195'));s.flush()
        for i,(hid,issued) in enumerate(entries+[('foreign','2026-01-15T12:00:00-03:00')]):
            company='period-other' if hid=='foreign' else cid
            raw=('<test id="'+hid+'"/>').encode()
            path=app.storage.save(company,'period-'+hid,raw,'xml')
            key=str(10**43+i)
            s.add(FiscalArchive(id=hid,company_id=company,key=key,kind='resNFe',sha256=hid,path=path,data=json.dumps({'issued_at':issued})))
            result[hid]=(key,raw)
        s.commit()
    return result


def check_exports(c,cid,query,expected,entries):
    params={'company':cid,**query}
    listing=c.get('/api/history',query_string=params)
    assert listing.status_code==200,listing.json
    assert listing.json['total']==len(expected)
    assert {row['id'] for row in listing.json['items']}==set(expected)
    txt=c.get('/api/history/keys.txt',query_string={**params,'page':2})
    assert txt.status_code==200
    assert txt.data==''.join(entries[hid][0]+'\r\n' for hid in sorted(expected,key=lambda hid:entries[hid][0])).encode()
    archive=c.get('/api/history/export',query_string={**params,'page':2})
    assert archive.status_code==200
    with zipfile.ZipFile(io.BytesIO(archive.data)) as z:
        assert set(z.namelist())=={entries[hid][0]+'-'+hid+'.xml' for hid in expected}
        for hid in expected:assert z.read(entries[hid][0]+'-'+hid+'.xml')==entries[hid][1]


def test_inclusive_period_consistent_list_zip_txt_and_delete_independent(env):
    app,c,h,cid=env;entries=seed(app,cid)
    check_exports(c,cid,{'month_from':'2026-01','month_to':'2026-02'},['first','last'],entries)
    check_exports(c,cid,{'month_from':'2026-02'},['last'],entries)
    check_exports(c,cid,{'month':'2026-01'},['first'],entries)
    check_exports(c,cid,{'month_from':'2025-12','month_to':'2026-01'},['before','first'],entries)
    check_exports(c,cid,{'month_from':'2027-01'},[],entries)
    check_exports(c,cid,{},['before','first','last','after','undated'],entries)
    assert c.get('/api/history',query_string={'company':cid,'month_from':'2026-01','month_to':'2026-02','page':2}).json['items']==[]
    for endpoint in ('/api/history','/api/history/export','/api/history/keys.txt'):
        assert c.get(endpoint,query_string={'company':'period-other','month_from':'2026-01'}).status_code==400
    preview=c.post('/api/history/delete-preview?month_from=2026-01&month_to=2026-02',json={'company':cid,'scope':'all'},headers=h)
    assert preview.json['count']==5


@pytest.mark.parametrize('query',[
    {'month_to':'2026-02'},
    {'month':'2026-01','month_to':'2026-02'},
    {'month_from':'2026-03','month_to':'2026-02'},
    {'month_from':'2026-2'},
    {'month_from':'2026-00'},
    {'month_from':'2026-13'},
    {'month_from':'0000-01'},
    {'month_from':'2026-01','month_to':'2026-02-01'},
    {'month':'2026-13'},
])
def test_invalid_period_same_validation_for_all_exports(env,query):
    app,c,h,cid=env
    for endpoint in ('/api/history','/api/history/export','/api/history/keys.txt'):
        result=c.get(endpoint,query_string={'company':cid,**query})
        assert result.status_code==400,(endpoint,result.json)
        assert 'error' in result.json
