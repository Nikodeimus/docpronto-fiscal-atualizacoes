import pytest
from test_app import env
from test_agent_bridge import pair

@pytest.mark.parametrize('code,expected', [('tls_failed','TLS'),('http_403','HTTP 403'),('dns_failed','DNS'),('timeout','tempo de espera'),('operation_failed','Falha na operação'),({'secret':'test'},'Consulte o agente'),('password=secret','Consulte o agente')])
def test_safe_distribution_diagnostic(env,code,expected):
    app,c,h,cid=env
    m,auth,device,_=pair(app,c,h,cid)
    cert={'thumbprint':'A'*40,'store':'CurrentUser','has_private_key':True}
    poll={'certificates':[cert],'capabilities':['distribution']}
    m.post('/api/agent/poll',json=poll,headers=auth)
    assert c.post('/api/distribution',json={'company':cid,'device':device,**cert,'uf':'35'},headers=h).status_code==200
    task=m.post('/api/agent/poll',json=poll,headers=auth).json['task']
    r=m.post('/api/agent/distribution-result',json={'id':task['id'],'ok':False,'error':code},headers=auth)
    assert r.status_code==200
    item=c.get('/api/distribution?company='+cid).json['items'][0]
    assert item['state']=='failed'
    assert expected in item['message']
    assert 'secret' not in item['message']

    state=c.get('/api/distribution?company='+cid).json
    assert state['next_allowed']==0



def test_late_failure_does_not_reopen_cancelled_batch(env):
    from app.distribution import CaptureBatch
    app,c,h,cid=env
    m,auth,device,_=pair(app,c,h,cid)
    cert={'thumbprint':'A'*40,'store':'CurrentUser','has_private_key':True}
    poll={'certificates':[cert],'capabilities':['distribution']}
    m.post('/api/agent/poll',json=poll,headers=auth)
    r=c.post('/api/distribution',json={'company':cid,'device':device,**cert,'uf':'35','batch':True,'continuous':True},headers=h)
    assert r.status_code==200,r.json
    bid=r.json['id'];task=m.post('/api/agent/poll',json=poll,headers=auth).json['task']
    assert task
    assert c.post('/api/capture/batches/'+bid+'/control',json={'company':cid,'action':'cancel'},headers=h).status_code==200
    assert m.post('/api/agent/distribution-result',json={'id':task['id'],'ok':False,'error':'timeout'},headers=auth).status_code==200
    with app.session_factory() as s:
        assert s.get(CaptureBatch,bid).state=='cancelled'
        assert s.get(CaptureBatch,bid).message.startswith('Cancelado')
    assert c.post('/api/capture/batches/'+bid+'/control',json={'company':cid,'action':'resume'},headers=h).status_code==400


def test_failed_query_shows_stage_and_safe_native_codes(env):
    app,c,h,cid=env;m,auth,device,_=pair(app,c,h,cid)
    cert={'thumbprint':'A'*40,'store':'CurrentUser','has_private_key':True}
    poll={'certificates':[cert],'capabilities':['distribution']}
    m.post('/api/agent/poll',json=poll,headers=auth)
    c.post('/api/distribution',json={'company':cid,'device':device,**cert,'uf':'35'},headers=h)
    task=m.post('/api/agent/poll',json=poll,headers=auth).json['task']
    result={'id':task['id'],'ok':False,'error':'http_transport_failed','stage':'https_send','diagnostics':[{'type':'System.IO.IOException','hresult':'0x80131620','native':'0x80090022','http_error':'Unknown','site':'System.Net.Security.SslStream.AuthenticateAsClientAsync','message':'password=SECRET','secret':'SECRET'},{'type':'SECRET','site':'https://host/?token=SECRET'}]}
    assert m.post('/api/agent/distribution-result',json=result,headers=auth).status_code==200
    item=c.get('/api/distribution?company='+cid).json['items'][0]
    assert 'comunicação HTTPS' in item['message']
    assert '0x80090022' in item['message']
    assert 'System.Net.Security.SslStream' in item['message']
    assert 'SECRET' not in item['message']
    assert c.get('/api/distribution?company='+cid).json['next_allowed']==0


def test_expired_connection_returns_auth_status_without_changing_valid_one(env):
    import time
    from app.agent_bridge import AgentDevice
    app,c,h,cid=env;m,auth,device,_=pair(app,c,h,cid)
    other,other_auth,other_device,_=pair(app,c,h,cid)
    with app.session_factory() as s:
        s.get(AgentDevice,device).expires=time.time()-1;s.commit()
    assert m.post('/api/agent/poll',json={'certificates':[]},headers=auth).status_code==401
    assert m.post('/api/agent/distribution-result',json={'id':'x'},headers=auth).status_code==401
    assert other.post('/api/agent/poll',json={'certificates':[]},headers=other_auth).status_code==200
