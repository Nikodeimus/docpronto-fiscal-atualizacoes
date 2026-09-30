import json
import os
import pytest
from flask import Flask
import local_runtime
from app import local_network as network


@pytest.mark.parametrize('host',['192.168.1.10','10.0.0.8','172.16.1.3','172.31.255.254','CENTRAL-01','central.local','fiscal.empresa.lan'])
def test_safe_hosts(host):assert network.validate_hosts([host])==[host.lower()]


@pytest.mark.parametrize('host',['8.8.8.8','172.32.0.1','169.254.0.1','0.0.0.0','192.168.1.4:8080','http://192.168.1.4','central/a','*.local','a..local','public.example.com','user@central','192.168.999.1'])
def test_unsafe_hosts(host):
    with pytest.raises(ValueError):network.validate_hosts([host])


def test_config_default_loopback_and_explicit_lan_preserves_token(tmp_path,monkeypatch):
    state=tmp_path/'DocProntoLocal';state.mkdir();token='a'*40
    (state/'local.json').write_text(json.dumps({'setup_token':token,'unrelated':'preserve'}))
    # Runtime.configure mutates only process environment and this temporary directory.
    for key in ('DOCPRONTO_HOME','DATA_DIR','DATABASE_URL','SETUP_TOKEN','BIND_HOST','PORT','ALLOWED_HOSTS','COOKIE_SECURE','DOCPRONTO_LAN_ENABLED','DOCPRONTO_ADAPTER_COMMAND','NFE_XSD_PATH','PATH','TESSDATA_PREFIX'):
        monkeypatch.setenv(key,os.environ.get(key,''))
    local_runtime.configure(state)
    assert os.environ['BIND_HOST']=='127.0.0.1'
    assert os.environ['DOCPRONTO_LAN_ENABLED']=='0'
    network.save_settings(state,True,['192.168.1.10','CENTRAL'])
    saved=json.loads((state/'local.json').read_text())
    assert saved['setup_token']==token and saved['unrelated']=='preserve'
    assert os.environ['BIND_HOST']=='127.0.0.1' # Saving never restarts/rebinds the site.
    local_runtime.configure(state)
    assert os.environ['BIND_HOST']=='0.0.0.0'
    assert os.environ['ALLOWED_HOSTS']=='localhost,127.0.0.1,192.168.1.10,central'
    assert local_runtime.URL=='http://127.0.0.1:8080'
    network.save_settings(state,False,[]);local_runtime.configure(state)
    assert os.environ['BIND_HOST']=='127.0.0.1'


def test_enabled_requires_central_host(tmp_path):
    with pytest.raises(ValueError):network.save_settings(tmp_path,True,[])


@pytest.mark.parametrize('address,allowed',[('127.0.0.1',True),('::1',True),('192.168.2.30',True),('10.1.1.1',True),('8.8.8.8',False),('100.64.0.1',False),('2001:4860:4860::8888',False)])
def test_remote_public_addresses_denied(address,allowed,monkeypatch):
    monkeypatch.setenv('DOCPRONTO_LAN_ENABLED','1')
    app=Flask(__name__);network.register_local_network(app)
    app.add_url_rule('/probe',view_func=lambda:'ok')
    result=app.test_client().get('/probe',environ_overrides={'REMOTE_ADDR':address})
    assert result.status_code==(200 if allowed else 403)


def test_network_api_superadmin_only_and_no_automatic_restart(tmp_path,monkeypatch):
    from flask import g
    from types import SimpleNamespace
    state=tmp_path/'DocProntoLocal';state.mkdir();(state/'local.json').write_text(json.dumps({'setup_token':'x'*40}))
    monkeypatch.setattr(network.local_runtime,'state_root',lambda:state)
    monkeypatch.setattr(network,'installed_here',lambda *args:True)
    monkeypatch.setenv('DOCPRONTO_LAN_ENABLED','0')
    app=Flask(__name__);role=['superadmin']
    @app.before_request
    def user():g.user=SimpleNamespace(role=role[0])
    network.register_local_network(app)
    client=app.test_client()
    result=client.post('/api/network/settings',json={'enabled':True,'hosts':['192.168.1.10']})
    assert result.status_code==200 and result.json['restart_required']
    status=client.get('/api/network').json
    assert status['enabled'] and not status['active_enabled'] and status['restart_required']
    role[0]='admin'
    assert client.get('/api/network').status_code==403
    assert client.post('/api/network/settings',json={'enabled':False,'hosts':[]}).status_code==403
