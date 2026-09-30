import json
from test_app import env
from test_agent_bridge import pair
from app.distribution import DistributionTask,CaptureBatch
from app.db import Company


def configured(env):
    app,c,h,cid=env;m,auth,device,_=pair(app,c,h,cid)
    certs=[{'thumbprint':v*40,'store':'CurrentUser','has_private_key':True,'subject':'CN='+v} for v in ['A','B']]
    poll={'certificates':certs,'capabilities':['distribution']}
    m.post('/api/agent/poll',json=poll,headers=auth)
    return m,auth,device,certs,poll


def choose(c,h,cid,device,cert):
    return c.post('/api/certificates/selection',json={'company':cid,'device_id':device,**cert},headers=h)


def test_switch_updates_paused_batch_and_pending_work(env):
    app,c,h,cid=env;m,auth,device,certs,poll=configured(env)
    assert choose(c,h,cid,device,certs[0]).status_code==200
    created=c.post('/api/distribution',json={'company':cid,'device':device,**certs[0],'uf':'35','batch':True},headers=h)
    assert created.status_code==200
    bid=created.json['id']
    assert c.post('/api/capture/batches/'+bid+'/control',json={'company':cid,'action':'pause'},headers=h).status_code==200
    assert choose(c,h,cid,device,certs[1]).json['in_flight'] is False
    with app.session_factory() as s:
        batch=s.get(CaptureBatch,bid)
        assert batch.state=='paused'
        assert json.loads(batch.options)['thumbprint']==certs[1]['thumbprint']
    assert c.post('/api/capture/batches/'+bid+'/control',json={'company':cid,'action':'resume'},headers=h).status_code==200
    reply=m.post('/api/agent/poll',json=poll,headers=auth).json
    assert reply['task']['thumbprint']==certs[1]['thumbprint']
    assert reply['bindings'][0]['thumbprint']==certs[1]['thumbprint']
    taskid=reply['task']['id']
    assert choose(c,h,cid,device,certs[0]).json['in_flight'] is True
    with app.session_factory() as s:
        assert json.loads(s.get(DistributionTask,taskid).payload)['thumbprint']==certs[1]['thumbprint']
        assert json.loads(s.get(CaptureBatch,bid).options)['thumbprint']==certs[0]['thumbprint']


def test_switch_pending_task_without_repairing_agent(env):
    app,c,h,cid=env;m,auth,device,certs,poll=configured(env)
    assert c.post('/api/distribution',json={'company':cid,'device':device,**certs[0],'uf':'35'},headers=h).status_code==200
    assert choose(c,h,cid,device,certs[1]).status_code==200
    reply=m.post('/api/agent/poll',json=poll,headers=auth).json
    assert reply['task']['thumbprint']==certs[1]['thumbprint']
    assert c.get('/api/certificates/selection?company='+cid).json['selected']['thumbprint']==certs[1]['thumbprint']


def test_bindings_are_separate_per_company(env):
    from app.agent_bridge import AgentCompanyLink
    app,c,h,cid=env;m,auth,device,certs,poll=configured(env)
    other=c.post('/api/companies',json={'name':'Outra','document':'11222333000181'},headers=h)
    assert other.status_code==200,other.json
    otherid=other.json['id']
    with app.session_factory() as s:
        # Same administrator explicitly linked this computer to the other company.
        from app.agent_bridge import AgentDevice
        dev=s.get(AgentDevice,device)
        s.add(AgentCompanyLink(device_id=device,company_id=otherid,created_by=dev.created_by));s.commit()
    assert choose(c,h,cid,device,certs[0]).status_code==200
    assert choose(c,h,otherid,device,certs[1]).status_code==200
    assert c.get('/api/certificates/selection?company='+cid).json['selected']['thumbprint']==certs[0]['thumbprint']
    assert c.get('/api/certificates/selection?company='+otherid).json['selected']['thumbprint']==certs[1]['thumbprint']
    bindings=m.post('/api/agent/poll',json=poll,headers=auth).json['bindings']
    assert {b['company_id']:b['thumbprint'] for b in bindings}=={cid:certs[0]['thumbprint'],otherid:certs[1]['thumbprint']}


def test_existing_batch_uses_current_binding_and_never_falls_back(env):
    app,c,h,cid=env;m,auth,device,certs,poll=configured(env)
    assert choose(c,h,cid,device,certs[1]).status_code==200
    # Simulate a batch saved by an older version with a stale certificate snapshot.
    r=c.post('/api/distribution',json={'company':cid,'device':device,**certs[0],'uf':'35','batch':True},headers=h)
    assert r.status_code==200
    reply=m.post('/api/agent/poll',json=poll,headers=auth).json
    assert reply['task']['thumbprint']==certs[1]['thumbprint']
    assert m.post('/api/agent/distribution-result',json={'id':reply['task']['id'],'ok':False,'error':'certificate_key_failed'},headers=auth).status_code==200
    assert c.post('/api/capture/batches/'+r.json['id']+'/control',json={'company':cid,'action':'retry'},headers=h).status_code==200
    poll['certificates']=[certs[0]]
    reply=m.post('/api/agent/poll',json=poll,headers=auth).json
    assert reply['task'] is None
    with app.session_factory() as s:
        batch=s.get(CaptureBatch,r.json['id'])
        assert batch.state=='paused'
        assert 'indisponível' in batch.message
