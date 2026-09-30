import datetime as dt
import json
import pytest
from sqlalchemy import select

from test_app import env
from app.db import Company, User
from app.fiscal_history import FiscalArchive
from app.agent_bridge import AgentDevice, certificate_expired, certificate_expiring_soon
from app.distribution import DistributionTask


def coverage(client,cid,**kwargs):
    return client.get('/api/history/coverage',query_string={'company':cid,**kwargs})


def test_month_counts_zeros_last_query_and_unconfirmed_coverage(env):
    app,client,headers,cid=env
    with app.session_factory() as session:
        session.add(Company(id='private',name='Private',document='12345678000195'))
        owner=session.scalar(select(User.id))
        session.add(AgentDevice(id='coverage-device',company_id=cid,created_by=owner))
        session.flush()
        records=[(cid,'nfeProc','2026-07-31T23:55:00-03:00',10),(cid,'resNFe','2026-07-02T10:00:00Z',20),(cid,'procEventoNFe','2026-09-01T00:00:00-03:00',30),(cid,'nfeProc','2026-09-03',40),('private','nfeProc','2026-07-03',50)]
        for index,(company,kind,issued,stamp) in enumerate(records):
            session.add(FiscalArchive(company_id=company,key='',kind=kind,sha256=str(index).zfill(64),path='synthetic-only',data=json.dumps({'issued_at':issued}),created=stamp))
        session.add(DistributionTask(company_id=cid,device_id='coverage-device',payload='{}',state='completed',message='Our query',created=5))
        session.add(DistributionTask(company_id='private',device_id='coverage-device',payload='{}',state='failed',message='Private query',created=99))
        session.commit()
    result=coverage(client,cid,month_from='2026-07',month_to='2026-09')
    assert result.status_code==200,result.json
    july,august,september=result.json['items']
    assert (july['xmls'],july['summaries'],july['events'],july['last_received'])==(1,1,0,20)
    assert (august['month'],august['xmls'],august['summaries'],august['events'],august['last_received'])==('2026-08',0,0,0,None)
    assert (september['xmls'],september['summaries'],september['events'],september['last_received'])==(1,0,1,40)
    assert all(item['coverage']=='not_confirmed' for item in result.json['items'])
    assert 'não pode ser confirmada' in result.json['message']
    assert result.json['last_query']['message']=='Our query'
    assert coverage(client,'private').status_code==400
    assert len(coverage(client,cid,month='2026-07').json['items'])==1


@pytest.mark.parametrize('month',[1,9,12])
def test_default_exactly_twelve_months_across_year_boundaries(env,monkeypatch,month):
    app,client,headers,cid=env
    class Frozen(dt.datetime):
        @classmethod
        def now(cls,tz=None):return cls(2026,month,15,tzinfo=dt.timezone.utc)
    monkeypatch.setattr(dt,'datetime',Frozen)
    result=coverage(client,cid)
    assert result.status_code==200,result.json
    items=result.json['items']
    assert len(items)==12
    assert items[-1]['month']==f'2026-{month:02d}'
    expected=2026*12+month-1-11
    assert items[0]['month']==f'{expected//12:04d}-{expected%12+1:02d}'
    assert all(item['xmls']==item['events']==item['summaries']==0 for item in items)


@pytest.mark.parametrize('params',[
    {'month_to':'2026-09'}, {'month_from':'2026-13'}, {'month_from':'2026-00'},
    {'month_from':'2026-9'}, {'month_from':'0000-01'},
    {'month_from':'2026-10','month_to':'2026-09'},
    {'month_from':'2000-01','month_to':'2010-01'},
])
def test_invalid_coverage_ranges(env,params):
    app,client,headers,cid=env
    assert coverage(client,cid,**params).status_code==400


def test_maximum_120_months_is_inclusive(env):
    app,client,headers,cid=env
    result=coverage(client,cid,month_from='2000-01',month_to='2009-12')
    assert result.status_code==200,result.json
    assert len(result.json['items'])==120


@pytest.mark.parametrize('days,expected',[(0,False),(-1,False),(1/86400,True),(30,True),(30+1/86400,False),(31,False)])
def test_certificate_soon_boundary_and_offset(monkeypatch,days,expected):
    import app.agent_bridge as bridge
    now=dt.datetime(2026,9,26,12,tzinfo=dt.timezone.utc)
    monkeypatch.setattr(bridge.time,'time',lambda:now.timestamp())
    expires=(now+dt.timedelta(days=days)).astimezone(dt.timezone(dt.timedelta(hours=-3)))
    certificate={'valid_until':expires.isoformat()}
    assert certificate_expiring_soon(certificate) is expected
    assert certificate_expired(certificate) is (days<=0)
    certificate['valid_until']=expires.astimezone(dt.timezone.utc).isoformat().replace('+00:00','Z')
    assert certificate_expiring_soon(certificate) is expected


@pytest.mark.parametrize('cert',[None,[],{}, {'valid_until':None},{'valid_until':'invalid'},{'valid_until':'2026-09-27T12:00:00'}, {'valid_until':'99999-01-01T00:00:00Z'}])
def test_unknown_certificate_expiry_is_not_classified(cert):
    assert certificate_expired(cert) is False
    assert certificate_expiring_soon(cert) is False
