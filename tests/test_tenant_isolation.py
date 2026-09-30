import io
from test_app import env, RAW


def signup(app, label):
    client=app.test_client()
    response=client.post('/api/signup',json={'name':'Organização '+label,'email':label+'@example.test','password':'tenant-password-123'})
    assert response.status_code==200,response.json
    return client,{'X-CSRF-Token':response.json['csrf']},response.json['team']['id']


def create_company(client,headers,organization_id,tax='11444777000161'):
    response=client.post('/api/companies',json={'name':'Cadastro da equipe','document':tax,'organization_id':organization_id},headers=headers)
    assert response.status_code==200,response.json
    return response.json['id']


def test_same_taxid_separate_organizations_never_share_documents(env):
    app,root,headers,legacy=env
    a,ah,ao=signup(app,'alpha');b,bh,bo=signup(app,'beta')
    ac=create_company(a,ah,ao);bc=create_company(b,bh,bo)
    assert ac!=bc and ac!=legacy
    response=a.post('/api/import',data={'company':ac,'file':(io.BytesIO(RAW),'reference.xml')},headers=ah)
    did=response.json['results'][0]['id']
    assert a.get('/api/dashboard?company='+ac).json['total']==1
    assert b.get('/api/dashboard?company='+bc).json['total']==0
    assert b.get('/api/documents/'+did).status_code==400
    assert b.get('/api/documents/'+did+'/download/xml').status_code==400
    assert b.post('/api/export',json={'company':bc,'ids':[did]},headers=bh).status_code==400
    assert [item['id'] for item in a.get('/api/companies').json]==[ac]
    assert {item['id'] for item in root.get('/api/companies').json}=={legacy}
    assert a.post('/api/companies',json={'name':'Duplicado','document':'11444777000161','organization_id':ao},headers=ah).status_code==400


def test_org_owner_cannot_create_or_move_company_to_other_org(env):
    app,*_=env
    a,ah,ao=signup(app,'alpha');b,bh,bo=signup(app,'beta')
    ac=create_company(a,ah,ao)
    foreign_client=b.post('/api/clients',json={'name':'Cliente B','provider_id':bo},headers=bh).json['id']
    assert a.post('/api/companies',json={'name':'Tentativa','document':'11222333000181','client_id':foreign_client},headers=ah).status_code==400
    assert a.post('/api/companies',json={'name':'Sem organização','document':'11222333000181'},headers=ah).status_code==403
    assert a.post('/api/updates/check',json={},headers=ah).status_code==403
    # An owner of both organizations still cannot silently move fiscal records.
    extra=a.post('/api/teams',json={'name':'Outra organização'},headers=ah).json['id']
    extra_client=a.post('/api/clients',json={'name':'Outro cliente','provider_id':extra},headers=ah).json['id']
    old_client=a.get('/api/companies').json[0]['client_id']
    response=a.patch('/api/registrations/'+ac+'/client',json={'client_id':extra_client,'expected_client_id':old_client,'confirm_document':'11444777000161'},headers=ah)
    assert response.status_code==400
    assert a.get('/api/companies').json[0]['provider_id']==ao


def test_team_member_inherits_new_company_and_viewer_cannot_write(env):
    app,*_=env
    owner,headers,organization=signup(app,'owner')
    invite=owner.post('/api/teams/'+organization+'/invites',json={'email':'viewer@example.test','role':'viewer'},headers=headers)
    assert invite.status_code==200,invite.json
    viewer=app.test_client()
    accepted=viewer.post('/api/invites/'+invite.json['token']+'/accept',json={'email':'viewer@example.test','password':'viewer-password-123'})
    assert accepted.status_code==200,accepted.json
    vh={'X-CSRF-Token':accepted.json['csrf']}
    cid=create_company(owner,headers,organization)
    assert viewer.get('/api/dashboard?company='+cid).status_code==200
    assert viewer.get('/api/companies').json[0]['role']=='viewer'
    assert viewer.post('/api/keys',json={'company':cid,'keys':'123'},headers=vh).status_code==400
    assert viewer.post('/api/companies',json={'name':'Indevido','document':'11222333000181','organization_id':organization},headers=vh).status_code==400
    assert viewer.post('/api/teams/'+organization+'/invites',json={'email':'other@example.test','role':'admin'},headers=vh).status_code in (400,403)
