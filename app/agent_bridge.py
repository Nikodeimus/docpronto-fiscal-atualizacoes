from werkzeug.exceptions import Unauthorized
"""Outbound-only Windows agent pairing. No private key/PIN crosses the bridge."""
import base64,hashlib,json,secrets,time
from datetime import datetime,timezone
from flask import request,jsonify,g
from sqlalchemy import select,update,delete,or_
from sqlalchemy.orm import Mapped,mapped_column
from sqlalchemy import String,Text,Float,ForeignKey
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa,ec,padding
from .db import Base,uid,Company,Member,Setting,log

class AgentDevice(Base):
    __tablename__='agent_devices'
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=uid)
    company_id:Mapped[str]=mapped_column(ForeignKey('companies.id'),index=True)
    created_by:Mapped[str]=mapped_column(String(32))
    name:Mapped[str]=mapped_column(String(150),default='Agente Windows')
    code_hash:Mapped[str|None]=mapped_column(String(64),unique=True,nullable=True)
    code_expires:Mapped[float]=mapped_column(Float,default=0)
    token_hash:Mapped[str|None]=mapped_column(String(64),unique=True,nullable=True)
    expires:Mapped[float]=mapped_column(Float,default=0)
    last_seen:Mapped[float]=mapped_column(Float,default=0)
    certificates:Mapped[str]=mapped_column(Text,default='[]')
    revoked:Mapped[str]=mapped_column(String(1),default='0')
class AgentCompanyLink(Base):
    __tablename__='agent_company_links'
    device_id:Mapped[str]=mapped_column(ForeignKey('agent_devices.id'),primary_key=True)
    company_id:Mapped[str]=mapped_column(ForeignKey('companies.id'),primary_key=True)
    created_by:Mapped[str]=mapped_column(String(32))
    created:Mapped[float]=mapped_column(Float,default=time.time)

class CompanyCertificate(Base):
    __tablename__='company_certificates'
    company_id:Mapped[str]=mapped_column(ForeignKey('companies.id'),primary_key=True)
    device_id:Mapped[str]=mapped_column(ForeignKey('agent_devices.id'))
    thumbprint:Mapped[str]=mapped_column(String(40))
    store:Mapped[str]=mapped_column(String(30))
    updated_by:Mapped[str]=mapped_column(String(32))


def device_allowed(session,device,company_id):
    """An explicit company link is required; certificate inventory grants no access."""
    return bool(device and device.revoked=='0' and (device.company_id==company_id or
        session.get(AgentCompanyLink,(device.id,company_id)) is not None))

class CertificateTask(Base):
    __tablename__='certificate_tasks'
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=uid)
    device_id:Mapped[str]=mapped_column(ForeignKey('agent_devices.id'),index=True)
    thumbprint:Mapped[str]=mapped_column(String(40))
    store:Mapped[str]=mapped_column(String(30))
    challenge:Mapped[str]=mapped_column(String(80))
    state:Mapped[str]=mapped_column(String(20),default='pending')
    expires:Mapped[float]=mapped_column(Float)
    result:Mapped[str]=mapped_column(Text,default='')
    created:Mapped[float]=mapped_column(Float,default=time.time)

def certificate_expired(cert):
    if not isinstance(cert,dict):return False
    try:
        value=datetime.fromisoformat(str(cert.get('valid_until','')).replace('Z','+00:00'))
        if value.tzinfo is None:return False
        return value.timestamp()<=time.time()
    except (ValueError,TypeError,OverflowError):return False


def certificate_expiring_soon(cert):
    if not isinstance(cert,dict):return False
    try:
        value=datetime.fromisoformat(str(cert.get('valid_until','')).replace('Z','+00:00'))
        if value.tzinfo is None:return False
        return 0<value.timestamp()-time.time()<=30*86400
    except (ValueError,TypeError,OverflowError):return False


def hidden_certificate_key(cid,device_id,cert):
    identity=json.dumps([cid,device_id,cert.get('thumbprint'),cert.get('store')])
    return 'cert-hidden:'+hashlib.sha256(identity.encode()).hexdigest()


def hash_value(v):return hashlib.sha256(v.encode()).hexdigest()
def register_agent_bridge(app,company,ingest_callback,limited):
    def payload():
        d=request.get_json(silent=True)
        if not isinstance(d,dict):raise ValueError('JSON inválido.')
        return d
    def device_auth():
        auth=request.headers.get('Authorization','')
        if not auth.startswith('Bearer '):raise Unauthorized('Agente sem autenticação.')
        dev=g.s.scalar(select(AgentDevice).where(AgentDevice.token_hash==hash_value(auth[7:]),AgentDevice.revoked=='0',AgentDevice.expires>time.time()))
        if not dev:raise Unauthorized('Agente expirado ou revogado. Pareie novamente.')
        return dev
    @app.post('/api/certificates/agents')
    def pair_code():
        d=payload();co=company(d.get('company'),admin=True)
        if limited('pair:'+g.user.id,10):return jsonify(error='Aguarde antes de criar outro pareamento.'),429
        code=secrets.token_hex(8).upper();dev=AgentDevice(company_id=co.id,created_by=g.user.id,code_hash=hash_value(code),code_expires=time.time()+300)
        g.s.add(dev);log(g.s,co.id,g.user.id,'agente_pareamento_criado');g.s.commit()
        return jsonify(id=dev.id,code=code,expires_in=300)
    @app.get('/api/certificates/agents')
    def devices():
        cid=request.args.get('company');company(cid,admin=True)
        devices=g.s.scalars(select(AgentDevice).where(or_(AgentDevice.company_id==cid,AgentDevice.id.in_(select(AgentCompanyLink.device_id).where(AgentCompanyLink.company_id==cid))),AgentDevice.revoked=='0'))
        result=[]
        for dev in devices:
            tasks=g.s.scalars(select(CertificateTask).where(CertificateTask.device_id==dev.id).order_by(CertificateTask.created.desc()).limit(10))
            result.append({'id':dev.id,'name':dev.name,'online':dev.last_seen>time.time()-45 and dev.expires>time.time(),'paired':bool(dev.token_hash),'last_seen':dev.last_seen,'certificates':[{**c,'expired':certificate_expired(c),'expiring_soon':certificate_expiring_soon(c)} for c in json.loads(dev.certificates) if not g.s.get(Setting,hidden_certificate_key(cid,dev.id,c))],'tasks':[{'id':t.id,'thumbprint':t.thumbprint,'state':'expired' if t.state in ('pending','running') and t.expires<time.time() else t.state,'result':t.result,'created':t.created} for t in tasks]})
        return jsonify(items=result)
    @app.post('/api/certificates/agents/<device_id>/certificates/remove')
    def remove_expired_certificate(device_id):
        d=payload();cid=d.get('company');company(cid,admin=True)
        dev=g.s.get(AgentDevice,device_id)
        if not device_allowed(g.s,dev,cid):raise ValueError('Computador não vinculado a esta empresa.')
        cert=next((c for c in json.loads(dev.certificates) if c.get('thumbprint')==d.get('thumbprint') and c.get('store')==d.get('store')),None)
        if not cert or not certificate_expired(cert):raise ValueError('Só é possível excluir da lista certificados vencidos com validade conhecida.')
        key=hidden_certificate_key(cid,dev.id,cert)
        if not g.s.get(Setting,key):g.s.add(Setting(key=key,value='1'))
        # This is a company-scoped list dismissal, never a Windows key deletion.
        log(g.s,cid,g.user.id,'certificado_vencido_ocultado',cert['thumbprint'][-8:])
        g.s.commit();return jsonify(ok=True)
    @app.post('/api/certificates/agents/<device_id>/certificates/remove-expired')
    def remove_all_expired_certificates(device_id):
        d=payload();cid=d.get('company');company(cid,admin=True)
        dev=g.s.get(AgentDevice,device_id)
        if not device_allowed(g.s,dev,cid):raise ValueError('Computador não vinculado a esta empresa.')
        keys={hidden_certificate_key(cid,dev.id,c) for c in json.loads(dev.certificates) if certificate_expired(c)}
        keys=[key for key in keys if not g.s.get(Setting,key)]
        if keys and (type(d.get('count')) is not int or d['count']!=len(keys)):
            raise ValueError('A lista de vencidos mudou. Atualize a lista e confirme novamente.')
        for key in keys:g.s.add(Setting(key=key,value='1'))
        if keys:log(g.s,cid,g.user.id,'certificados_vencidos_ocultados',json.dumps({'device':dev.id,'count':len(keys)}))
        g.s.commit();return jsonify(ok=True,removed=len(keys))
    @app.get('/api/certificates/agents/reusable')
    def reusable():
        cid=request.args.get('company');company(cid,admin=True)
        candidates=g.s.scalars(select(AgentDevice).where(AgentDevice.created_by==g.user.id,AgentDevice.revoked=='0',AgentDevice.token_hash.is_not(None),AgentDevice.expires>time.time()))
        return jsonify(items=[{'id':dev.id,'name':dev.name,'online':dev.last_seen>time.time()-45,
            'linked':device_allowed(g.s,dev,cid)} for dev in candidates])
    @app.post('/api/certificates/agents/<device_id>/link')
    def link(device_id):
        d=payload();cid=d.get('company');company(cid,admin=True);dev=g.s.get(AgentDevice,device_id)
        if not dev or dev.revoked!='0' or dev.created_by!=g.user.id or not dev.token_hash or dev.expires<=time.time():
            raise ValueError('Computador não disponível para reutilização por este usuário.')
        company(dev.company_id,admin=True)
        if not device_allowed(g.s,dev,cid):g.s.add(AgentCompanyLink(device_id=dev.id,company_id=cid,created_by=g.user.id))
        log(g.s,cid,g.user.id,'computador_vinculado');g.s.commit();return jsonify(ok=True)
    @app.get('/api/certificates/selection')
    def get_selection():
        cid=request.args.get('company');company(cid,admin=True);chosen=g.s.get(CompanyCertificate,cid)
        if not chosen:return jsonify(selected=None)
        dev=g.s.get(AgentDevice,chosen.device_id)
        cert=next((c for c in json.loads(dev.certificates) if c.get('thumbprint')==chosen.thumbprint and c.get('store')==chosen.store),None) if dev else None
        return jsonify(selected={'device_id':chosen.device_id,'thumbprint':chosen.thumbprint,'store':chosen.store,
            'available':bool(device_allowed(g.s,dev,cid) and dev.expires>time.time() and dev.last_seen>time.time()-45 and cert and cert.get('has_private_key'))})
    @app.post('/api/certificates/selection')
    def select_certificate():
        d=payload();cid=d.get('company');company(cid,admin=True);dev=g.s.get(AgentDevice,d.get('device_id'))
        if not device_allowed(g.s,dev,cid):raise ValueError('Computador não vinculado a esta empresa.')
        cert=next((c for c in json.loads(dev.certificates) if c.get('thumbprint')==d.get('thumbprint') and c.get('store')==d.get('store') and c.get('has_private_key')),None)
        if not cert:raise ValueError('Certificado com chave privada não disponível neste computador.')
        g.s.execute(select(Company).where(Company.id==cid).with_for_update()).scalar_one()
        chosen=g.s.get(CompanyCertificate,cid)
        if not chosen:chosen=CompanyCertificate(company_id=cid);g.s.add(chosen)
        chosen.device_id=dev.id;chosen.thumbprint=cert['thumbprint'];chosen.store=cert['store'];chosen.updated_by=g.user.id
        # Future work follows this company's selection. Never rewrite work in flight.
        from .distribution import CaptureBatch,DistributionTask
        for batch in g.s.scalars(select(CaptureBatch).where(CaptureBatch.company_id==cid,CaptureBatch.state.in_(['active','paused']))):
            options=json.loads(batch.options)
            options.update(thumbprint=chosen.thumbprint,store=chosen.store,a1=False)
            batch.device_id=dev.id;batch.options=json.dumps(options)
        for task in g.s.scalars(select(DistributionTask).where(DistributionTask.company_id==cid,DistributionTask.state=='pending')):
            options=json.loads(task.payload)
            options.update(thumbprint=chosen.thumbprint,store=chosen.store,a1=False)
            task.device_id=dev.id;task.payload=json.dumps(options)
        setting=g.s.get(Setting,'capture-registration:'+cid)
        if setting:
            options=json.loads(setting.value);options.update(device=dev.id,certificate='installed');setting.value=json.dumps(options)
        in_flight=g.s.scalar(select(DistributionTask.id).where(DistributionTask.company_id==cid,DistributionTask.state=='running',DistributionTask.expires>time.time()).limit(1))
        log(g.s,cid,g.user.id,'certificado_selecionado',chosen.thumbprint[-8:]);g.s.commit();return jsonify(ok=True,in_flight=bool(in_flight))
    @app.post('/api/certificates/agents/<device_id>/revoke')
    def revoke(device_id):
        dev=g.s.get(AgentDevice,device_id)
        if not dev:raise ValueError('Agente não encontrado.')
        cid=(request.get_json(silent=True) or {}).get('company',dev.company_id)
        company(cid,admin=True)
        if not device_allowed(g.s,dev,cid):raise ValueError('Computador não vinculado a esta empresa.')
        if cid!=dev.company_id:
            g.s.execute(delete(AgentCompanyLink).where(AgentCompanyLink.device_id==dev.id,AgentCompanyLink.company_id==cid))
            g.s.execute(delete(CompanyCertificate).where(CompanyCertificate.company_id==cid,CompanyCertificate.device_id==dev.id))
            log(g.s,cid,g.user.id,'computador_desvinculado');g.s.commit();return jsonify(ok=True)
        dev.revoked='1';dev.token_hash=None;dev.code_hash=None
        g.s.execute(delete(CompanyCertificate).where(CompanyCertificate.device_id==dev.id))
        log(g.s,dev.company_id,g.user.id,'agente_revogado');g.s.commit();return jsonify(ok=True)
    @app.post('/api/certificates/agents/<device_id>/test')
    def request_test(device_id):
        d=payload();dev=g.s.get(AgentDevice,device_id)
        if not dev or dev.revoked=='1':raise ValueError('Agente indisponível.')
        cid=d.get('company',dev.company_id);company(cid,admin=True)
        if not device_allowed(g.s,dev,cid):raise ValueError('Computador não vinculado a esta empresa.')
        cert=next((c for c in json.loads(dev.certificates) if c.get('thumbprint')==d.get('thumbprint') and c.get('store')==d.get('store')),None)
        if not cert or not cert.get('has_private_key'):raise ValueError('Certificado não disponível no agente.')
        if dev.last_seen<time.time()-45:raise ValueError('Agente está desconectado.')
        if limited('certtest:'+dev.id,10):return jsonify(error='Limite de testes atingido; aguarde 5 minutos.'),429
        task=CertificateTask(device_id=dev.id,thumbprint=cert['thumbprint'],store=cert['store'],challenge=base64.b64encode(secrets.token_bytes(32)).decode(),expires=time.time()+120)
        g.s.add(task);log(g.s,dev.company_id,g.user.id,'certificado_teste_solicitado');g.s.commit();return jsonify(id=task.id,ok=True)
    @app.post('/api/agent/pair')
    def pair():
        d=payload()
        if limited('agentpair:'+request.remote_addr,10):return jsonify(error='Aguarde 5 minutos.'),429
        code=str(d.get('code','')).replace(' ','').upper()
        dev=g.s.scalar(select(AgentDevice).where(AgentDevice.code_hash==hash_value(code),AgentDevice.code_expires>time.time(),AgentDevice.revoked=='0'))
        if not dev:raise ValueError('Código inválido ou expirado.')
        token=secrets.token_urlsafe(48)
        updated=g.s.execute(update(AgentDevice).where(AgentDevice.id==dev.id,AgentDevice.token_hash.is_(None),AgentDevice.code_hash==hash_value(code)).values(token_hash=hash_value(token),code_hash=None,name=str(d.get('name','Agente Windows'))[:150],expires=time.time()+30*86400,last_seen=time.time()))
        if updated.rowcount!=1:raise ValueError('Código já utilizado.')
        co=g.s.get(Company,dev.company_id);log(g.s,co.id,None,'agente_pareado');g.s.commit()
        return jsonify(token=token,company_id=co.id,company_name=co.name)
    @app.post('/api/agent/poll')
    def poll():
        dev=device_auth();d=payload();certs=d.get('certificates',[])
        cap=g.s.get(Setting,'agentcaps:'+dev.id)
        value=json.dumps(d.get('capabilities',[]) if isinstance(d.get('capabilities',[]),list) else [])
        if len(value)>1000:raise ValueError('Capacidades inválidas.')
        if cap:cap.value=value
        else:g.s.add(Setting(key='agentcaps:'+dev.id,value=value))
        # Same inventory bound as the Windows agent; do not reject offices with >100 certificates.
        if not isinstance(certs,list) or len(certs)>1000:raise ValueError('Lista de certificados inválida (máximo de 1000).')
        normalized=[]
        for c in certs:
            if not isinstance(c,dict):continue
            thumb=str(c.get('thumbprint','')).replace(' ','').upper()
            if len(thumb)!=40 or any(ch not in '0123456789ABCDEF' for ch in thumb):continue
            normalized.append({'thumbprint':thumb,'subject':str(c.get('subject',''))[:1000],'valid_until':str(c.get('valid_until',''))[:50],'has_private_key':c.get('has_private_key') is True,'store':c.get('store','CurrentUser')})
        dev.certificates=json.dumps(normalized);dev.last_seen=time.time();dev.expires=time.time()+30*86400
        g.s.execute(delete(CertificateTask).where(CertificateTask.expires<time.time()-86400))
        task=g.s.scalar(select(CertificateTask).where(CertificateTask.device_id==dev.id,CertificateTask.state=='pending',CertificateTask.expires>time.time()).order_by(CertificateTask.created).limit(1))
        output=None
        if task:
            claimed=g.s.execute(update(CertificateTask).where(CertificateTask.id==task.id,CertificateTask.state=='pending').values(state='running')).rowcount
            if claimed:output={'id':task.id,'kind':'self_test','thumbprint':task.thumbprint,'store':task.store,'challenge':task.challenge}
        # Alternate persisted channel turns, keeping both fiscal queues live.
        turn=g.s.get(Setting,'agent-turn:'+dev.id)
        fiscal_first=not turn or turn.value!='fiscal'
        claimers=['fiscal','distribution'] if fiscal_first else ['distribution','fiscal']
        for which in claimers:
            if output is not None:break
            if which=='fiscal' and 'fiscal_claim' in app.extensions:output=app.extensions['fiscal_claim'](dev,json.loads(value))
            elif which=='distribution' and 'distribution' in json.loads(value) and 'distribution_claim' in app.extensions:output=app.extensions['distribution_claim'](dev)
            if output is not None:
                if turn:turn.value=which
                else:g.s.add(Setting(key='agent-turn:'+dev.id,value=which))
        bindings=[]
        for chosen,co in g.s.execute(select(CompanyCertificate,Company).join(Company,Company.id==CompanyCertificate.company_id).where(CompanyCertificate.device_id==dev.id)):
            if device_allowed(g.s,dev,co.id):bindings.append({'company_id':co.id,'company_name':co.name,'document':co.document,'thumbprint':chosen.thumbprint,'store':chosen.store})
        g.s.commit();return jsonify(task=output,company_id=dev.company_id,bindings=bindings)
    @app.post('/api/agent/result')
    def result():
        dev=device_auth();d=payload();task=g.s.get(CertificateTask,d.get('id'))
        if not task or task.device_id!=dev.id or task.state!='running' or task.expires<time.time():raise ValueError('Teste expirado, concluído ou não pertence ao agente.')
        success=False;message='Falha no teste local; confira o token, PIN e driver.'
        if d.get('ok') is True:
            try:
                der=base64.b64decode(d.get('certificate_der',''),validate=True);sig=base64.b64decode(d.get('signature',''),validate=True)
                if len(der)>32000 or len(sig)>1024:raise ValueError()
                cert=x509.load_der_x509_certificate(der)
                if cert.fingerprint(hashes.SHA1()).hex().upper()!=task.thumbprint:raise ValueError()
                now=datetime.now(timezone.utc)
                if not cert.not_valid_before_utc<=now<=cert.not_valid_after_utc:raise ValueError()
                public=cert.public_key();challenge=base64.b64decode(task.challenge)
                if isinstance(public,rsa.RSAPublicKey) and d.get('algorithm')=='RSA-SHA256':public.verify(sig,challenge,padding.PKCS1v15(),hashes.SHA256())
                elif isinstance(public,ec.EllipticCurvePublicKey) and d.get('algorithm')=='ECDSA-SHA256':public.verify(sig,challenge,ec.ECDSA(hashes.SHA256()))
                else:raise ValueError()
                success=True;message='Posse da chave comprovada pelo agente. Cadeia ICP-Brasil, revogação e autorização fiscal não verificadas.'
            except Exception:message='Assinatura, identidade ou validade do certificado não conferem com o desafio.'
        task.state='completed' if success else 'failed';task.result=message;dev.last_seen=time.time();log(g.s,dev.company_id,None,'certificado_teste_'+task.state);g.s.commit()
        return jsonify(ok=success,message=message)
    @app.post('/api/agent/import')
    def agent_import():
        dev=device_auth()
        if limited('agentimport:'+dev.id,300):return jsonify(error='Limite temporário; tentar depois.'),429
        file=request.files.get('file')
        if not file:raise ValueError('Arquivo ausente.')
        raw=file.read(20*1024*1024+1)
        if len(raw)>20*1024*1024:raise ValueError('Limite de 20 MB por arquivo.')
        result=ingest_callback(dev,raw,file.filename or '')
        dev.last_seen=time.time();log(g.s,dev.company_id,None,'agente_arquivo_importado');g.s.commit();return jsonify(ok=True,**result)
