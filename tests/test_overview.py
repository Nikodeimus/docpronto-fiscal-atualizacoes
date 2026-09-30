from test_app import env,imp
from app.db import Company,Document

def test_overview_membership_and_counts(env):
    app,c,h,cid=env
    imp(c,h,cid)
    with app.session_factory() as s:
        hidden=Company(name='CONFIDENCIAL',document='12345678000199')
        s.add(hidden);s.flush()
        s.add(Document(company_id=hidden.id,key='9'*44,status='erro'))
        s.commit()
    r=c.get('/api/overview')
    assert r.status_code==200
    assert len(r.json['items'])==1
    row=r.json['items'][0]
    assert row['id']==cid
    assert sum(row['documents'].values())==1
    assert row['last'] is None
    assert row['archive']=={}
    assert 'CONFIDENCIAL' not in r.text
    assert app.test_client().get('/api/overview').status_code==401


def test_overview_a1_uses_configured_agent_not_installed_selection(env):
    import json
    from test_agent_bridge import pair
    from app.db import Setting
    from app.client_capture import StoredA1
    from app.agent_bridge import AgentDevice
    app,c,h,cid=env
    _,_,device,_=pair(app,c,h,cid)
    with app.session_factory.begin() as s:
        s.add(StoredA1(company_id=cid,encrypted='fixture-not-used',metadata_json=json.dumps({'valid_until':'2030-01-01'})))
        s.add(Setting(key='capture-registration:'+cid,value=json.dumps({'certificate':'a1','device':device,'enabled':True})))
    row=c.get('/api/overview').json['items'][0]
    assert row['online'] is True and row['certificate']=='A1 cadastrado'
    with app.session_factory.begin() as s:s.get(AgentDevice,device).revoked='1'
    assert c.get('/api/overview').json['items'][0]['online'] is False
    with app.session_factory.begin() as s:
        s.get(Setting,'capture-registration:'+cid).value=json.dumps({'certificate':'installed','device':device})
    assert c.get('/api/overview').json['items'][0]['certificate']!='A1 cadastrado'
