from test_app import env
def test_appearance_persistence_and_validation(env):
    app,c,h,cid=env
    pid=c.post('/api/providers',json={'name':'Prestadora'},headers=h).json['id']
    d=c.get('/api/appearance/'+pid).json
    d.update(name='Minha marca',primary='#123456',logo='',size=50)
    assert c.post('/api/appearance/'+pid,json=d,headers=h).status_code==200
    assert c.get('/api/appearance/'+pid).json['name']=='Minha marca'
    d['logo']='data:image/svg+xml;base64,AAAA'
    assert c.post('/api/appearance/'+pid,json=d,headers=h).status_code==400
    assert c.get('/api/appearance/inexistente').status_code==403
    assert app.test_client().get('/api/appearance/'+pid).status_code==401
    assert c.post('/api/appearance/'+pid,json={'reset':True},headers=h).status_code==200
    assert c.get('/api/appearance/'+pid).json['name']=='DocPronto'

def test_profile_frames_and_backdrop(env):
    import io
    from PIL import Image
    app,c,h,cid=env
    pid=c.post('/api/providers',json={'name':'Visual'},headers=h).json['id']
    d=c.get('/api/appearance/'+pid).json
    d.update(frame_style='neon',frame_color='#00ffee',size=100,theme='dark')
    assert c.post('/api/appearance/'+pid,json=d,headers=h).status_code==200
    assert c.get('/api/appearance/'+pid).json['frame_style']=='neon'
    assert c.get('/api/appearance/'+pid).json['theme']=='dark'
    d['frame_style']='invalid'
    assert c.post('/api/appearance/'+pid,json=d,headers=h).status_code==400
    assert c.post('/api/profile',json={'name':'Meu perfil','avatar':''},headers=h).status_code==200
    assert c.get('/api/profile').json['name']=='Meu perfil'
    assert app.test_client().get('/api/profile').status_code==401
    buf=io.BytesIO();Image.new('RGB',(10,10),'blue').save(buf,format='PNG');buf.seek(0)
    assert c.post('/api/appearance-media/'+pid,data={'file':(buf,'fundo.png')},headers=h).status_code==200
    assert c.get('/api/appearance-media/'+pid+'?info=1').json['kind']=='image'
    assert c.get('/api/appearance-media/'+pid).mimetype=='image/jpeg'
    assert app.test_client().get('/api/appearance-media/'+pid).status_code==401
    assert c.post('/api/appearance-media/'+pid,data={'file':(io.BytesIO(b'invalid'),'fundo.mp4')},headers=h).status_code==400
    assert c.delete('/api/appearance-media/'+pid,headers=h).status_code==200
    assert c.get('/api/appearance-media/'+pid).status_code==404

def test_background_position(env):
    app,c,h,cid=env
    pid=c.post('/api/providers',json={'name':'Fundo'},headers=h).json['id']
    d=c.get('/api/appearance/'+pid).json
    assert (d['bg_zoom'],d['bg_x'],d['bg_y'])==(100,50,50)
    d.update(bg_zoom=180,bg_x=0,bg_y=100,bg_blur=5,bg_opacity=85)
    assert c.post('/api/appearance/'+pid,json=d,headers=h).status_code==200
    saved=c.get('/api/appearance/'+pid).json
    assert (saved['bg_blur'],saved['bg_opacity'])==(5,85)
    assert (saved['bg_zoom'],saved['bg_x'],saved['bg_y'])==(180,0,100)
    for k,v in [('bg_zoom',99),('bg_x',101),('bg_y','50'),('bg_blur',21),('bg_opacity',101)]:
        assert c.post('/api/appearance/'+pid,json={**d,k:v},headers=h).status_code==400


def test_media_policy_and_malformed_settings(env):
    app,c,h,cid=env
    policy=c.get('/').headers['Content-Security-Policy']
    assert "img-src 'self' data: blob:" in policy
    assert "media-src 'self' blob:" in policy
    assert "script-src 'self'" in policy
    assert "object-src 'none'" in policy
    pid=c.post('/api/providers',json={'name':'Validação'},headers=h).json['id']
    assert c.post('/api/appearance/'+pid,json=['invalid'],headers=h).status_code==400
    assert c.post('/api/profile',json=['invalid'],headers=h).status_code==400
    assert c.get('/api/status').json['version']=='1.8.23'
