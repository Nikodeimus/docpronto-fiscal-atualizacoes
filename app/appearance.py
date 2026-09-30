import base64,io,json,re
from flask import g,request,jsonify,send_file
from sqlalchemy import select
from PIL import Image,ImageOps
from .appearance_media import save_media,media_path
from .db import Setting,ServiceProvider,Client,ClientRegistration,Member
DEFAULT=dict(name='DocPronto',subtitle='GESTÃO FISCAL',primary='#881b38',background='#f5f7fa',logo='',size=44,scroll_style='rounded',frame_style='plain',frame_color='#881b38',theme='light',bg_zoom=100,bg_x=50,bg_y=50,bg_blur=0,bg_opacity=100)
def register_appearance(app):
    @app.route('/api/appearance/<pid>',methods=['GET','POST'])
    def appearance(pid):
        from .teams import provider_role
        provider=g.s.get(ServiceProvider,pid)
        team_role=provider_role(g.s,g.user,pid)
        accessible=g.s.scalar(select(Member.company_id).join(ClientRegistration,ClientRegistration.company_id==Member.company_id).join(Client,Client.id==ClientRegistration.client_id).where(Member.user_id==g.user.id,Client.provider_id==pid).limit(1))
        if not provider or (not team_role and not accessible):return jsonify(error='Prestadora indisponível.'),403
        key='appearance:'+pid
        row=g.s.get(Setting,key)
        if request.method=='GET':return jsonify(**({**DEFAULT,**json.loads(row.value)} if row else DEFAULT),can_edit=team_role=='admin')
        if team_role!='admin':return jsonify(error='Somente o responsável pode alterar a identidade.'),403
        d=request.get_json() or {}
        if not isinstance(d,dict):raise ValueError('Corpo JSON inválido.')
        if d.get('reset') is True:
            if row:g.s.delete(row)
            g.s.commit();return jsonify(**DEFAULT)
        out={}
        for field,limit in [('name',60),('subtitle',80)]:
            v=d.get(field)
            if not isinstance(v,str) or not v.strip() or len(v)>limit:raise ValueError('Nome ou subtítulo inválido.')
            out[field]=v.strip()
        for field in ['primary','background']:
            v=d.get(field)
            if not isinstance(v,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',v):raise ValueError('Cor inválida.')
            out[field]=v
        style=d.get('scroll_style','rounded')
        if style not in ('native','thin','rounded'):raise ValueError('Estilo inválido.')
        out['scroll_style']=style
        theme=d.get('theme','light')
        if theme not in ('light','dark','system'):raise ValueError('Tema inválido.')
        out['theme']=theme
        frame=d.get('frame_style','plain')
        if frame not in ('plain','metal','neon'):raise ValueError('Acabamento inválido.')
        color=d.get('frame_color',d.get('primary'))
        if not isinstance(color,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',color):raise ValueError('Cor inválida.')
        out.update(frame_style=frame,frame_color=color)
        for field,low,high,default in [('bg_zoom',100,250,100),('bg_x',0,100,50),('bg_y',0,100,50),('bg_blur',0,20,0),('bg_opacity',10,100,100)]:
            value=d.get(field,default)
            if type(value) is not int or not low<=value<=high:raise ValueError('Enquadramento inválido.')
            out[field]=value
        size=d.get('size')
        if type(size) is not int or not 32<=size<=100:raise ValueError('Tamanho inválido.')
        out['size']=size
        logo=d.get('logo','')
        if not isinstance(logo,str) or len(logo)>1500000:raise ValueError('Logo muito grande.')
        out['logo']=''
        if logo:
            if not re.match(r'^data:image/(png|jpeg|webp);base64,',logo):raise ValueError('Use PNG, JPEG ou WebP.')
            try:
                raw=base64.b64decode(logo.split(',',1)[1],validate=True)
                with Image.open(io.BytesIO(raw)) as img:
                    if img.width*img.height>16000000:raise ValueError()
                    img.thumbnail((600,600));buf=io.BytesIO();img.convert('RGBA').save(buf,format='PNG')
                out['logo']='data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()
            except Exception:raise ValueError('Imagem inválida ou grande demais.')
        if row:row.value=json.dumps(out)
        else:g.s.add(Setting(key=key,value=json.dumps(out)))
        g.s.commit();return jsonify(**out)

    @app.route('/api/profile',methods=['GET','POST'])
    def profile():
        key='profile:'+g.user.id
        row=g.s.get(Setting,key)
        if request.method=='GET':return jsonify(**(json.loads(row.value) if row else dict(name='',avatar='')))
        d=request.get_json() or {}
        if not isinstance(d,dict):raise ValueError('Corpo JSON inválido.')
        name=d.get('name','');avatar=d.get('avatar','')
        if not isinstance(name,str) or len(name)>80:raise ValueError('Nome inválido.')
        if not isinstance(avatar,str) or len(avatar)>1500000:raise ValueError('Imagem muito grande.')
        if avatar:
            if not re.match(r'^data:image/(png|jpeg|webp);base64,',avatar):raise ValueError('Use PNG, JPEG ou WebP.')
            try:
                raw=base64.b64decode(avatar.split(',',1)[1],validate=True)
                with Image.open(io.BytesIO(raw)) as img:
                    if img.width*img.height>16000000:raise ValueError()
                    img.thumbnail((256,256));buf=io.BytesIO();img.convert('RGBA').save(buf,format='PNG')
                avatar='data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()
            except Exception:raise ValueError('Imagem inválida.')
        out=dict(name=name.strip(),avatar=avatar)
        if row:row.value=json.dumps(out)
        else:g.s.add(Setting(key=key,value=json.dumps(out)))
        g.s.commit();return jsonify(**out)

    @app.route('/api/appearance-media/<pid>',methods=['GET','POST','DELETE'])
    def appearance_media(pid):
        from .teams import provider_role
        provider=g.s.get(ServiceProvider,pid)
        team_role=provider_role(g.s,g.user,pid)
        accessible=g.s.scalar(select(Member.company_id).join(ClientRegistration,ClientRegistration.company_id==Member.company_id).join(Client,Client.id==ClientRegistration.client_id).where(Member.user_id==g.user.id,Client.provider_id==pid).limit(1))
        if not provider or (not team_role and not accessible):return jsonify(error='Prestadora indisponível.'),403
        row=g.s.scalar(select(Setting).where(Setting.key=='appearance-media:'+pid).with_for_update())
        if request.method=='GET':
            if not row:return '',404
            data=json.loads(row.value)
            if 'data' in data:
                # Commit metadata only after a durable file exists. Legacy value
                # remains in the database if decoding/writing fails.
                name=save_media(app.storage,base64.b64decode(data['data'],validate=True))
                data=dict(kind=data['kind'],mime=data['mime'],file=name)
                row.value=json.dumps(data);g.s.commit()
            else:g.s.commit()
            path=media_path(app.storage,data.get('file'))
            if not path.is_file():return jsonify(error='Arquivo de fundo ausente. Reenvie a imagem ou restaure o backup.'),404
            if request.args.get('info'):return jsonify(kind=data['kind'])
            return send_file(path,mimetype=data['mime'],max_age=0,conditional=True)
        if team_role!='admin':return jsonify(error='Somente o responsável pode alterar o fundo.'),403
        if request.method=='DELETE':
            if row:g.s.delete(row)
            g.s.commit();return jsonify(ok=True)
        f=request.files.get('file')
        if not f:raise ValueError('Escolha uma imagem ou vídeo.')
        raw=f.read(12*1024*1024+1)
        if len(raw)>12*1024*1024:raise ValueError('Limite de 12 MB.')
        mime=f.mimetype
        if mime in ('image/png','image/jpeg','image/webp'):
            try:
                with Image.open(io.BytesIO(raw)) as img:
                    if img.width*img.height>24000000:raise ValueError()
                    img=ImageOps.exif_transpose(img);img.thumbnail((4096,4096),Image.Resampling.LANCZOS);buf=io.BytesIO();img.convert('RGB').save(buf,format='JPEG',quality=95,subsampling=0);raw=buf.getvalue()
                mime='image/jpeg'
            except Exception:raise ValueError('Imagem inválida.')
            kind='image'
        elif (mime=='video/mp4' and raw[4:8]==b'ftyp') or (mime=='video/webm' and raw[:4]==b'\x1aE\xdf\xa3'):
            kind='video'
        else:raise ValueError('Use PNG, JPEG, WebP, MP4 ou WebM válido.')
        name=save_media(app.storage,raw)
        data=json.dumps(dict(kind=kind,mime=mime,file=name))
        if row:row.value=data
        else:g.s.add(Setting(key='appearance-media:'+pid,value=data))
        g.s.commit();return jsonify(kind=kind)
