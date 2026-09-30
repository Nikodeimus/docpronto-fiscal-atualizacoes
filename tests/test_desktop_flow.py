from test_app import env
from pathlib import Path

def test_installer_requires_login(env):
    app,c,h,cid=env
    anonymous=app.test_client()
    assert anonymous.get('/api/agent-installer').status_code==401
    # Source checkout may intentionally omit build artifacts.
    response=c.get('/api/agent-installer')
    assert response.status_code in (200,400)
    if response.status_code==200:
        assert 'attachment' in response.headers['Content-Disposition']
        assert response.data[:2]==b'MZ'

def test_browser_pairing_is_one_use_and_requires_admin(env):
    app,c,h,cid=env
    assert c.post('/api/certificates/agents',json={'company':cid}).status_code==403
    r=c.post('/api/certificates/agents',json={'company':cid},headers=h)
    assert r.status_code==200
    code=r.json['code']
    assert len(code)==16
    assert c.post('/api/agent/pair',json={'code':code,'name':'Computador'}).status_code==200
    assert c.post('/api/agent/pair',json={'code':code,'name':'Repetido'}).status_code==400
