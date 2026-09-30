import pytest
from test_app import env

@pytest.mark.parametrize('path',[
    '/api/distribution','/api/distribution/cancel','/api/history/archive-workspace',
    '/api/registrations/{cid}/capture','/api/agent/poll','/api/agent/distribution-result',
    '/api/profile','/api/login',
])
def test_nonobject_json_is_client_error(env,path):
    app,c,h,cid=env
    r=c.post(path.format(cid=cid),json=['unexpected'],headers=h)
    assert r.status_code==400 and r.json['error']=='Corpo JSON inválido.'
