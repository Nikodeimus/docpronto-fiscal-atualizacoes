import io
from test_app import env, RAW
from app.db import User, Member
from werkzeug.security import generate_password_hash

def test_hierarchy_preserves_documents_and_rejects_reassignment(env):
    app,c,h,cid=env
    did=c.post('/api/import',data={'company':cid,'file':(io.BytesIO(RAW),'n.xml')},headers=h).json['results'][0]['id']
    provider=c.post('/api/providers',json={'name':'Prestadora'},headers=h).json['id']
    assert c.post('/api/providers/'+provider+'/documents',json={'document':'11444777000161'},headers=h).status_code==200
    clients=[c.post('/api/clients',json={'provider_id':provider,'name':name},headers=h).json['id'] for name in ['Cliente A','Cliente B']]
    url='/api/clients/'+clients[0]+'/registrations'
    assert c.post(url,json={'company_id':cid},headers=h).status_code==200
    assert c.post(url,json={'company_id':cid},headers=h).status_code==200
    assert c.post('/api/clients/'+clients[1]+'/registrations',json={'company_id':cid},headers=h).status_code==400
    assert c.get('/api/documents/'+did+'/download/xml').data==RAW
    row=c.get('/api/companies').json[0]
    assert row['client_id']==clients[0] and row['provider_id']==provider
    org=c.get('/api/organization').json
    assert len(org['clients'])==2 and org['providers'][0]['documents']==['11444777000161']

def test_organization_does_not_grant_cross_user_access(env):
    app,c,h,cid=env
    provider=c.post('/api/providers',json={'name':'Prestadora privada'},headers=h).json['id']
    client=c.post('/api/clients',json={'provider_id':provider,'name':'Cliente privado'},headers=h).json['id']
    c.post('/api/clients/'+client+'/registrations',json={'company_id':cid},headers=h)
    with app.session_factory() as s:
        u=User(email='outsider@test.com',password=generate_password_hash('long-password-test'),role='superadmin')
        s.add(u);s.commit()
    outsider=app.test_client()
    token=outsider.post('/api/login',json={'email':'outsider@test.com','password':'long-password-test'}).json['csrf']
    headers={'X-CSRF-Token':token}
    assert outsider.get('/api/organization').json=={'clients':[],'providers':[]}
    assert outsider.post('/api/clients',json={'provider_id':provider,'name':'Intruso'},headers=headers).status_code==400
    assert outsider.post('/api/clients/'+client+'/registrations',json={'company_id':cid},headers=headers).status_code==400

def test_edit_delete_and_correct_link_preserve_records(env):
    app,c,h,cid=env
    p=c.post('/api/providers',json={'name':'Nova prestadora'},headers=h).json['id']
    a,b=[c.post('/api/clients',json={'provider_id':p,'name':n},headers=h).json['id'] for n in ['Primeiro','Segundo']]
    assert any(x['id']==p for x in c.get('/api/organization').json['providers'])
    assert c.patch('/api/providers/'+p,json={'name':'Renomeada'},headers=h).status_code==200
    assert c.patch('/api/clients/'+a,json={'name':'Cliente certo'},headers=h).status_code==200
    c.post('/api/clients/'+a+'/registrations',json={'company_id':cid},headers=h)
    did=c.post('/api/import',data={'company':cid,'file':(io.BytesIO(RAW),'n.xml')},headers=h).json['results'][0]['id']
    tax=c.get('/api/companies').json[0]['document']
    url='/api/registrations/'+cid+'/client'
    body={'client_id':b,'expected_client_id':a,'confirm_document':tax}
    assert c.patch(url,json={**body,'confirm_document':'wrong'},headers=h).status_code==400
    assert c.patch(url,json=body,headers=h).status_code==200
    assert c.patch(url,json=body,headers=h).status_code==400
    assert c.get('/api/documents/'+did+'/download/xml').data==RAW
    assert c.delete('/api/clients/'+b,headers=h).status_code==400
    assert c.delete('/api/registrations/'+cid,json={'confirm_document':tax,'clear_operations':True},headers=h).status_code==400
    assert c.delete('/api/clients/'+a,headers=h).status_code==200
    assert c.delete('/api/providers/'+p,headers=h).status_code==400
    assert c.patch(url,json={'client_id':None,'expected_client_id':b,'confirm_document':tax},headers=h).status_code==200
    assert c.delete('/api/clients/'+b,headers=h).status_code==200
    assert c.delete('/api/providers/'+p,headers=h).status_code==400
    row=c.get('/api/companies').json[0]
    assert row['provider_id']==p and row['client_id'] is None
    assert c.get('/api/documents/'+did+'/download/xml').data==RAW

def test_empty_registration_delete_and_edit(env):
    app,c,h,cid=env
    row=c.get('/api/companies').json[0]
    assert c.patch('/api/registrations/'+cid,json={'name':'Novo nome','document':row['document']},headers=h).status_code==200
    assert c.delete('/api/registrations/'+cid,json={'confirm_document':'wrong'},headers=h).status_code==400
    assert c.delete('/api/registrations/'+cid,json={'confirm_document':row['document'],'clear_operations':True},headers=h).status_code==200
    assert c.get('/api/companies').json==[]


def test_delete_registration_clears_settings_and_preserves_shared_agent(env):
    from app.db import Setting,Company,Member
    from app.agent_bridge import AgentDevice,AgentCompanyLink,CompanyCertificate
    from app.distribution import DistributionState
    app,c,h,cid=env
    tax=c.get('/api/companies').json[0]['document']
    with app.session_factory() as s:
        owner=s.query(Member).filter_by(company_id=cid).first().user_id
        other=Company(name='Outro',document='11222333000181');s.add(other);s.flush();otherid=other.id
        dev=AgentDevice(company_id=cid,created_by=owner);s.add(dev);s.flush();devid=dev.id
        s.add(AgentCompanyLink(device_id=dev.id,company_id=other.id,created_by=owner))
        s.add(CompanyCertificate(company_id=cid,device_id=dev.id,thumbprint='a'*40,store='CurrentUser',updated_by=owner))
        s.add(DistributionState(company_id=cid))
        s.add(Setting(key='capture-registration:'+cid,value='{}'));s.commit()
    r=c.delete('/api/registrations/'+cid,json={'confirm_document':tax,'clear_operations':True},headers=h)
    assert r.status_code==200,r.json
    with app.session_factory() as s:
        assert s.get(AgentDevice,devid).company_id==otherid
        assert s.get(Company,otherid)
        assert s.get(Company,cid) is None
        assert s.get(Setting,'capture-registration:'+cid) is None
