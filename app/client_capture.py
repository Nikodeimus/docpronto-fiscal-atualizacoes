"""Per-registration capture settings and encrypted A1 storage."""
import base64,json,os,time
from pathlib import Path
from flask import g,request,jsonify
from sqlalchemy import select,String,Text,ForeignKey
from sqlalchemy.orm import Mapped,mapped_column
from cryptography.fernet import Fernet
from cryptography import x509
from cryptography.hazmat.primitives.serialization import pkcs12
from .db import Base,Setting,Company,ClientRegistration,Client,ServiceProvider,log
from .certificates import inspect_a1
from .agent_bridge import AgentDevice,CompanyCertificate,device_allowed

class StoredA1(Base):
    __tablename__='stored_a1'
    company_id:Mapped[str]=mapped_column(ForeignKey('companies.id'),primary_key=True)
    encrypted:Mapped[str]=mapped_column(Text)
    metadata_json:Mapped[str]=mapped_column(Text)

def cipher(storage):
    path=storage.root/'certificate-vault.key'
    if not path.exists():
        import tempfile
        fd,temp=tempfile.mkstemp(dir=storage.root,prefix='vault-key-')
        try:
            with os.fdopen(fd,'wb') as f:f.write(Fernet.generate_key());f.flush();os.fsync(f.fileno())
            try:os.link(temp,path)
            except FileExistsError:pass
        finally:os.unlink(temp)
    return Fernet(path.read_bytes())

def certificate_document(cert):
    try:san=cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:raise ValueError('Certificado sem identificação fiscal ICP-Brasil.')
    for oid,size,start in [('2.16.76.1.3.3',14,0),('2.16.76.1.3.1',11,8)]:
        for item in san:
            if isinstance(item,x509.OtherName) and item.type_id.dotted_string==oid:
                raw=item.value
                if len(raw)<2 or raw[0] not in (4,12,19,22):continue
                length=raw[1];offset=2
                if length&128:
                    count=length&127
                    if count>2:continue
                    length=int.from_bytes(raw[2:2+count],'big');offset+=count
                value=raw[offset:offset+length].decode('ascii',errors='ignore')
                doc=value[start:start+size]
                if len(doc)==size and doc.isdigit():return doc
    raise ValueError('Não foi possível identificar CNPJ/CPF do certificado.')

def register_client_capture(app,company,storage):
    @app.get('/api/capture/monitor')
    def monitor():
        from .db import Member
        from .distribution import CaptureBatch,DistributionState,DistributionTask
        rows=[]
        registrations=g.s.scalars(select(Company).join(Member,Member.company_id==Company.id).where(Member.user_id==g.user.id,Member.role=='admin'))
        for co in registrations:
            setting=g.s.get(Setting,'capture-registration:'+co.id)
            if not setting:continue
            options=json.loads(setting.value);link=g.s.get(ClientRegistration,co.id)
            client=g.s.get(Client,link.client_id) if link else None
            provider=g.s.get(ServiceProvider,client.provider_id) if client else None
            state=g.s.get(DistributionState,co.id)
            batch=g.s.scalar(select(CaptureBatch).where(CaptureBatch.company_id==co.id).order_by(CaptureBatch.created.desc()).limit(1))
            task=g.s.scalar(select(DistributionTask).where(DistributionTask.company_id==co.id).order_by(DistributionTask.created.desc()).limit(1))
            dev=g.s.get(AgentDevice,options['device'])
            batch_options=json.loads(batch.options) if batch else {}
            rows.append({'company':co.id,'document':co.document,'name':co.name,'provider':provider.name if provider else 'Sem prestadora','client':client.name if client else co.name,'schedule':options.get('schedule','hourly'),'schedule_hour':options.get('schedule_hour',8),'interval_hours':options.get('interval_hours',1),'priority':options.get('priority',0),'overdue_since':batch_options.get('next_run',0) if batch_options.get('next_run',0) and batch_options['next_run']<time.time() else None,'next_run':batch_options.get('next_run',0),'retry_at':batch_options.get('retry_at',0),'retry_count':batch_options.get('retry_count',0),'last_daily_target':batch_options.get('last_daily_target',''),'enabled':options.get('enabled'), 'state':batch.state if batch else '', 'message':task.message if task else (batch.message if batch else ''),'next_allowed':state.next_allowed if state else 0,'online':bool(dev and dev.revoked=='0' and dev.expires>time.time() and dev.last_seen>time.time()-45)})
        rows.sort(key=lambda r:(r['provider'].casefold(),r['client'].casefold(),r['document']))
        return jsonify(items=rows)
    @app.post('/api/registrations/<cid>/a1')
    def save_a1(cid):
        co=company(cid,admin=True)
        f=request.files.get('file')
        if not f:raise ValueError('Selecione o arquivo A1.')
        raw=f.read(256001);password=request.form.get('password','');info=inspect_a1(raw,password)
        _,cert,_=pkcs12.load_key_and_certificates(raw,password.encode() if password else None)
        document=certificate_document(cert)
        matches=(len(document)==14 and len(co.document)==14 and document[:8]==co.document[:8]) or document==co.document
        if not matches:raise ValueError('O titular do certificado não corresponde ao CNPJ/CPF deste cadastro.')
        row=g.s.get(StoredA1,cid)
        if not row:row=StoredA1(company_id=cid);g.s.add(row)
        row.encrypted=cipher(storage).encrypt(json.dumps({'pfx':base64.b64encode(raw).decode(),'password':password}).encode()).decode()
        row.metadata_json=json.dumps({'subject':info['subject'],'valid_until':info['valid_until'],'document':document})
        log(g.s,cid,g.user.id,'a1_cadastrado');g.s.commit();return jsonify(ok=True)
    @app.get('/api/registrations/<cid>/capture')
    def settings(cid):
        company(cid,admin=True);a1=g.s.get(StoredA1,cid);setting=g.s.get(Setting,'capture-registration:'+cid)
        return jsonify(a1=json.loads(a1.metadata_json) if a1 else None,settings=json.loads(setting.value) if setting else {})
    @app.post('/api/registrations/<cid>/capture')
    def save(cid):
        co=company(cid,admin=True);d=request.get_json() or {};dev=g.s.get(AgentDevice,d.get('device'))
        if not device_allowed(g.s,dev,cid):raise ValueError('Vincule o computador a este CNPJ/CPF em Certificados.')
        uf=str(d.get('uf',''))
        if uf not in '11 12 13 14 15 16 17 21 22 23 24 25 26 27 28 29 31 32 33 35 41 42 43 50 51 52 53'.split():raise ValueError('Selecione uma UF válida.')
        schedule=d.get('schedule','hourly')
        if schedule not in ('hourly','daily'):raise ValueError('Frequência de captura inválida.')
        preferences={name:d.get(name,default) for name,default in [('schedule_hour',8),('interval_hours',1),('priority',0)]}
        for name,low,high in [('schedule_hour',0,23),('interval_hours',1,24),('priority',0,1)]:
            if type(preferences[name]) is not int or not low<=preferences[name]<=high:raise ValueError('Horário, intervalo ou prioridade inválidos.')
        mode=d.get('certificate','installed')
        chosen=g.s.get(CompanyCertificate,cid)
        if mode=='a1':
            if not g.s.get(StoredA1,cid):raise ValueError('Cadastre o certificado A1 primeiro.')
        elif mode!='installed' or not chosen or chosen.device_id!=dev.id:raise ValueError('Vincule o certificado deste cadastro em Certificados.')
        from .distribution import CaptureBatch,DistributionState
        g.s.execute(select(Company).where(Company.id==cid).with_for_update()).scalar_one()
        setting=g.s.get(Setting,'capture-registration:'+cid)
        if not setting:setting=Setting(key='capture-registration:'+cid);g.s.add(setting)
        setting.value=json.dumps({'device':dev.id,'uf':uf,'certificate':mode,'enabled':d.get('enabled') is True,'schedule':schedule,**preferences})
        batches=list(g.s.scalars(select(CaptureBatch).where(CaptureBatch.company_id==cid,CaptureBatch.state.in_(['active','paused']))))
        for old in batches:old.state='cancelled';old.message='Substituído pela configuração do cadastro.'
        if d.get('enabled') is True:
            if not g.s.get(DistributionState,cid):g.s.add(DistributionState(company_id=cid))
            options={'thumbprint':chosen.thumbprint if mode=='installed' else '', 'store':chosen.store if mode=='installed' else 'CurrentUser','document':co.document,'uf':uf,'continuous':True,'archive':True,'a1':mode=='a1',**preferences}
            if schedule=='daily':
                from .capture_schedule import first_daily_run
                options.update(schedule='daily',next_run=first_daily_run(time.time(),preferences['schedule_hour']))
            g.s.add(CaptureBatch(company_id=cid,device_id=dev.id,mode='history',options=json.dumps(options)))
        log(g.s,cid,g.user.id,'captura_configurada');g.s.commit();return jsonify(ok=True)
