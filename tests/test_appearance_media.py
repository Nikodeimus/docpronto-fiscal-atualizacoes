import base64,json
from test_app import env
from app.db import Setting
from app.appearance_media import media_path
import pytest

def test_legacy_migration_ranges_and_access(env):
    app,c,h,cid=env
    pid=c.post('/api/providers',json={'name':'Fundo antigo'},headers=h).json['id']
    raw=b'0123456789abcdef'
    with app.session_factory() as s:
        s.add(Setting(key='appearance-media:'+pid,value=json.dumps(dict(kind='video',mime='video/mp4',data=base64.b64encode(raw).decode()))));s.commit()
    assert app.test_client().get('/api/appearance-media/'+pid).status_code==401
    assert c.get('/api/appearance-media/'+pid+'?info=1').json=={'kind':'video'}
    with app.session_factory() as s:
        meta=json.loads(s.get(Setting,'appearance-media:'+pid).value)
        assert 'data' not in meta
        path=media_path(app.storage,meta['file']);assert path.read_bytes()==raw
    result=c.get('/api/appearance-media/'+pid,headers={'Range':'bytes=2-5'})
    assert result.status_code==206
    assert result.data==raw[2:6]
    assert result.headers['Content-Range']=='bytes 2-5/16'
    assert c.delete('/api/appearance-media/'+pid,headers=h).status_code==200
    assert c.get('/api/appearance-media/'+pid).status_code==404
    assert path.exists()  # retained for concurrent reads and recovery

def test_migration_failure_preserves_legacy(env,monkeypatch):
    app,c,h,cid=env
    pid=c.post('/api/providers',json={'name':'Falha de disco'},headers=h).json['id']
    value=json.dumps(dict(kind='image',mime='image/jpeg',data=base64.b64encode(b'old').decode()))
    with app.session_factory() as s:s.add(Setting(key='appearance-media:'+pid,value=value));s.commit()
    def fail(*args):raise OSError('disk full')
    monkeypatch.setattr('app.appearance.save_media',fail)
    assert c.get('/api/appearance-media/'+pid).status_code==500
    with app.session_factory() as s:assert s.get(Setting,'appearance-media:'+pid).value==value
    with pytest.raises(ValueError):media_path(app.storage,'../../other')
