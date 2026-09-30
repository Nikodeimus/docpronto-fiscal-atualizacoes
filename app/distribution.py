from werkzeug.exceptions import Unauthorized
"""NF-e distribution via the certificate on a paired Windows device."""
import time,json,base64,gzip,io,hashlib,re,calendar
from datetime import date
from types import SimpleNamespace
from lxml import etree
from flask import request,jsonify,g
from sqlalchemy import select,update,func,insert
from sqlalchemy.orm import Mapped,mapped_column
from sqlalchemy import String,Text,Float,ForeignKey,Integer,UniqueConstraint
from .db import Base,uid,Company,Document,Setting,log
from .agent_bridge import AgentDevice,hash_value,device_allowed
from .fiscal import validate_key,FiscalError

class DistributionState(Base):
    __tablename__='distribution_states'
    company_id:Mapped[str]=mapped_column(ForeignKey('companies.id'),primary_key=True)
    nsu:Mapped[str]=mapped_column(String(15),default='000000000000000')
    next_allowed:Mapped[float]=mapped_column(Float,default=0)
class DistributionTask(Base):
    __tablename__='distribution_tasks'
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=uid)
    company_id:Mapped[str]=mapped_column(ForeignKey('companies.id'),index=True)
    device_id:Mapped[str]=mapped_column(ForeignKey('agent_devices.id'))
    payload:Mapped[str]=mapped_column(Text)
    state:Mapped[str]=mapped_column(String(20),default='pending')
    message:Mapped[str]=mapped_column(Text,default='Aguardando o computador')
    created:Mapped[float]=mapped_column(Float,default=time.time)
    expires:Mapped[float]=mapped_column(Float,default=lambda:time.time()+180)
    receipt:Mapped[str|None]=mapped_column(Text,nullable=True)

class CaptureBatch(Base):
    __tablename__='capture_batches'
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=uid)
    company_id:Mapped[str]=mapped_column(ForeignKey('companies.id'),index=True)
    device_id:Mapped[str]=mapped_column(ForeignKey('agent_devices.id'),index=True)
    options:Mapped[str]=mapped_column(Text)
    mode:Mapped[str]=mapped_column(String(16))
    state:Mapped[str]=mapped_column(String(16),default='active',index=True)
    total:Mapped[int]=mapped_column(Integer,default=0)
    xmls:Mapped[int]=mapped_column(Integer,default=0)
    summaries:Mapped[int]=mapped_column(Integer,default=0)
    batches:Mapped[int]=mapped_column(Integer,default=0)
    message:Mapped[str]=mapped_column(Text,default='Aguardando o conector.')
    coverage_from:Mapped[str]=mapped_column(String(10),default='')
    coverage_to:Mapped[str]=mapped_column(String(10),default='')
    created:Mapped[float]=mapped_column(Float,default=time.time)
class CaptureItem(Base):
    __tablename__='capture_items'
    __table_args__=(UniqueConstraint('batch_id','key'),)
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=uid)
    batch_id:Mapped[str]=mapped_column(ForeignKey('capture_batches.id'),index=True)
    key:Mapped[str]=mapped_column(String(44))
    state:Mapped[str]=mapped_column(String(16),default='pending',index=True)
    message:Mapped[str]=mapped_column(Text,default='Na fila')
class CaptureDispatch(Base):
    __tablename__='capture_dispatches'
    task_id:Mapped[str]=mapped_column(ForeignKey('distribution_tasks.id'),primary_key=True)
    batch_id:Mapped[str]=mapped_column(ForeignKey('capture_batches.id'),index=True)
    item_id:Mapped[str|None]=mapped_column(ForeignKey('capture_items.id'),nullable=True)

def requested_period(d):
    today=date.today();period=str(d.get('period','12'))
    if period not in ('6','12','all','custom','months'):raise ValueError('Período inválido.')
    if period=='months':
        def month_start(value):
            if not isinstance(value,str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}',value):
                raise ValueError('Informe o mês no formato AAAA-MM.')
            try:return date.fromisoformat(value+'-01')
            except ValueError:raise ValueError('Informe um mês válido no formato AAAA-MM.') from None
        start=month_start(d.get('month_from'))
        month_to=d.get('month_to')
        last_month=month_start(d.get('month_from') if month_to in (None,'') else month_to)
        current_month=today.replace(day=1)
        if start>last_month or last_month>current_month:
            raise ValueError('Confira a ordem dos meses; não selecione meses futuros.')
        end=min(today,last_month.replace(day=calendar.monthrange(last_month.year,last_month.month)[1]))
        return {'period':period,'date_from':start.isoformat(),'date_to':end.isoformat()}
    end=date.fromisoformat(d.get('date_to') or today.isoformat())
    if period=='custom':start=date.fromisoformat(d.get('date_from') or '')
    elif period=='all':start=None
    else:
        months=int(period);year=end.year+(end.month-1-months)//12;month=(end.month-1-months)%12+1
        start=date(year,month,min(end.day,calendar.monthrange(year,month)[1]))
    if end>today or (start and start>end):raise ValueError('Confira as datas do período.')
    return {'period':period,'date_from':start.isoformat() if start else '', 'date_to':end.isoformat()}

def xml_root(raw):
    if len(raw)>16*1024*1024 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('Resposta XML excede limite ou é insegura.')
    return etree.fromstring(raw,etree.XMLParser(resolve_entities=False,no_network=True))
def decode_response(raw):
    root=xml_root(raw);ns={'n':'http://www.portalfiscal.inf.br/nfe'}
    ret=root if root.tag=='{http://www.portalfiscal.inf.br/nfe}retDistDFeInt' else root.find('.//n:retDistDFeInt',ns)
    if ret is None:raise ValueError('SEFAZ não retornou retDistDFeInt.')
    def get(tag):return ret.findtext('n:'+tag,default='',namespaces=ns)
    status=get('cStat');last=get('ultNSU');maximum=get('maxNSU')
    if status not in ('137','138','656'):raise ValueError('SEFAZ '+status+': '+get('xMotivo')[:300])
    for n in (last,maximum):
        if n and (len(n)!=15 or not n.isdigit()):raise ValueError('NSU inválido.')
    # Rejections are not successful cursor updates. Preserve the fiscal reason
    # and let the caller apply the existing cooldown without advancing the NSU.
    if status=='656':return status,last,maximum,get('xMotivo'),[]
    if last and maximum and last>maximum:
        raise ValueError(f'SEFAZ {status}: {get("xMotivo")[:300]}. NSU retornado {last} excede máximo {maximum}; continuidade preservada.')
    files=[];total=0
    for item in ret.findall('.//n:docZip',ns):
        if len(files)>=50:raise ValueError('Resposta excede 50 documentos.')
        compressed=base64.b64decode(item.text or '',validate=True)
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as f:data=f.read(8*1024*1024+1)
        total+=len(data)
        if len(data)>8*1024*1024 or total>32*1024*1024:raise ValueError('Documento descompactado excede limite.')
        xml_root(data);files.append(data)
    return status,last,maximum,get('xMotivo'),files

def migrate_local_waits(session):
    """Release old pre-request reservations, never waits backed by a fiscal receipt."""
    marker='distribution:response-waits-v1'
    if session.get(Setting,marker):return
    for state in session.scalars(select(DistributionState).where(DistributionState.next_allowed>time.time())):
        session.execute(select(Company).where(Company.id==state.company_id).with_for_update()).scalar_one()
        task=session.scalar(select(DistributionTask).where(DistributionTask.company_id==state.company_id).order_by(DistributionTask.created.desc()).limit(1))
        if task and not task.receipt and task.state in ('pending','running','failed','cancelled'):
            state.next_allowed=0
            log(session,state.company_id,None,'espera_local_removida',task.id)
    session.add(Setting(key=marker,value='1'))


def register_distribution(app,company,ingest,storage):
    with app.session_factory() as session:
        # Serialize upgrade across server workers.
        if session.bind.dialect.name=='postgresql':
            from sqlalchemy import text
            session.execute(text('SELECT pg_advisory_xact_lock(602018)'))
        migrate_local_waits(session)
        session.commit()
    def dispatch_batch(dev):
        """Create the next certificate task for an active bulk capture, if any."""
        from .db import ClientRegistration,Client,ServiceProvider
        candidates=g.s.scalars(select(CaptureBatch).join(DistributionState,DistributionState.company_id==CaptureBatch.company_id)
            .outerjoin(ClientRegistration,ClientRegistration.company_id==CaptureBatch.company_id)
            .outerjoin(Client,Client.id==ClientRegistration.client_id).outerjoin(ServiceProvider,ServiceProvider.id==Client.provider_id)
            .join(Company,Company.id==CaptureBatch.company_id)
            .where(CaptureBatch.device_id==dev.id,CaptureBatch.state=='active',DistributionState.next_allowed<=time.time())
            .order_by(func.lower(ServiceProvider.name),func.lower(Client.name),Company.document,CaptureBatch.created))
        # A five-minute priority boost still lets waiting clients age ahead.
        candidates=sorted(candidates,key=lambda b:json.loads(b.options).get('last_dispatched',0)-300*json.loads(b.options).get('priority',0))
        for batch in candidates:
            options=json.loads(batch.options)
            if max(options.get('next_run',0),options.get('retry_at',0))>time.time():continue
            task=dispatch_candidate(dev,batch)
            if task:return task
        return None

    def dispatch_candidate(dev,batch):
        g.s.execute(select(Company).where(Company.id==batch.company_id).with_for_update()).scalar_one()
        if not device_allowed(g.s,dev,batch.company_id):
            batch.state='paused';batch.message='Vínculo com a empresa indisponível.';return None
        state=g.s.get(DistributionState,batch.company_id)
        if not state or state.next_allowed>time.time():return None
        active=g.s.scalar(select(DistributionTask).where(DistributionTask.company_id==batch.company_id,DistributionTask.state.in_(['pending','running']),DistributionTask.expires>time.time()))
        if active:return None
        for expired in g.s.scalars(select(DistributionTask).where(DistributionTask.company_id==batch.company_id,DistributionTask.state.in_(['pending','running']),DistributionTask.expires<=time.time())):
            expired.state='failed';expired.message='Prazo encerrado. Repita após conferir o conector.';finish_batch(expired)
        if batch.state!='active' or json.loads(batch.options).get('retry_at',0)>time.time():return None
        item=g.s.scalar(select(CaptureItem).where(
            CaptureItem.batch_id==batch.id,CaptureItem.state=='pending'
        ).order_by(CaptureItem.id).limit(1))
        options=json.loads(batch.options)
        if not options.get('a1'):
            from .agent_bridge import CompanyCertificate
            chosen=g.s.get(CompanyCertificate,batch.company_id)
            if chosen:
                options.update(thumbprint=chosen.thumbprint,store=chosen.store)
                batch.device_id=chosen.device_id;batch.options=json.dumps(options)
                if chosen.device_id!=dev.id:return None
            available=next((c for c in json.loads(dev.certificates) if c.get('thumbprint')==options['thumbprint'] and c.get('store')==options['store'] and c.get('has_private_key')),None)
            if not available:
                batch.state='paused';batch.message='Certificado associado indisponível. Conecte o token/cartão ou escolha outro certificado neste cadastro.';return None
        options['nsu']=state.nsu
        if options.get('a1'):
            caps=g.s.get(Setting,'agentcaps:'+dev.id)
            if not caps or 'stored_a1' not in json.loads(caps.value):
                batch.state='paused';batch.message='Atualize o agente para usar o A1 cadastrado.';return None
        key=item.key if item else ''
        if not item and batch.mode=='keys':
            batch.state='completed';batch.message='Todas as chaves foram processadas.'
            return None
        task=DistributionTask(
            company_id=batch.company_id,device_id=dev.id,
            payload=json.dumps({
                'kind':'distribution','thumbprint':options['thumbprint'],
                'store':options['store'],'document':options['document'],
                'uf':options['uf'],'nsu':options.get('nsu','000000000000000'),
                'key':key,'remaining':1,'archive':options.get('archive',False),'a1':options.get('a1',False),
            })
        )
        g.s.add(task);g.s.flush()
        g.s.add(CaptureDispatch(task_id=task.id,batch_id=batch.id,item_id=item.id if item else None))
        if item:item.state='running';item.message='Consulta enviada ao conector.'
        options['last_dispatched']=time.time();batch.options=json.dumps(options)
        batch.batches+=1;batch.message='Captura em andamento.'
        return task

    def finish_batch(task,status=None,xmls=0,summaries=0,files=None,last=None,maximum=None,transient=False):
        dispatch=g.s.get(CaptureDispatch,task.id)
        if not dispatch:return
        batch=g.s.get(CaptureBatch,dispatch.batch_id)
        item=g.s.get(CaptureItem,dispatch.item_id) if dispatch.item_id else None
        if not batch:return
        from .capture_progress import record_sample
        record_sample(g.s,batch,task,status)
        batch.xmls+=xmls;batch.summaries+=summaries
        if item:
            item.state='completed' if task.state=='completed' else 'failed'
            item.message=task.message
        # A result already in flight may be archived, but must not revive a cancelled batch.
        if batch.state=='cancelled':return
        if status=='656':
            if item:item.state='pending';item.message='Aguardando intervalo da SEFAZ.'
            batch.message='Aguardando liberação da SEFAZ.';return
        options=json.loads(batch.options)
        if task.state=='completed':
            options.pop('retry_count',None);options.pop('retry_at',None)
            batch.options=json.dumps(options)
        if task.state=='failed':
            count=options.get('retry_count',0)
            if transient and count<3:
                delay=(60,300,900)[count]
                options.update(retry_count=count+1,retry_at=time.time()+delay)
                batch.options=json.dumps(options)
                if item:item.state='pending';item.message='Aguardando nova tentativa autom\u00e1tica.'
                if batch.state=='active':batch.message=f'Falha tempor\u00e1ria. Nova tentativa autom\u00e1tica {count+1}/3 em {delay//60} minuto(s).'
            else:
                batch.state='paused';batch.message='Consulta falhou. Confira o conector e repita as falhas.'
        elif batch.mode=='history' and status in ('137','138'):
            options=json.loads(batch.options)
            options['nsu']=last or options.get('nsu','000000000000000')
            batch.options=json.dumps(options)
            if status=='137' or not last or not maximum or last>=maximum:
                if options.get('schedule')=='daily':
                    from .capture_schedule import first_daily_run,daily_target
                    options.update(next_run=first_daily_run(time.time(),options.get('schedule_hour',8)),last_daily_target=daily_target(time.time()))
                    batch.options=json.dumps(options)
                elif options.get('interval_hours',1)>1:
                    options['next_run']=time.time()+options['interval_hours']*3600
                    batch.options=json.dumps(options)
                if batch.state=='active':batch.state='active' if options.get('continuous') else 'completed'
                batch.message='Aguardando próxima busca automática.' if options.get('continuous') else 'Histórico disponível processado.'
        pending=g.s.scalar(select(func.count()).select_from(CaptureItem).where(
            CaptureItem.batch_id==batch.id,CaptureItem.state.in_(['pending','running'])
        ))
        if batch.mode=='keys' and not pending and batch.state=='active':
            batch.state='completed';batch.message='Lista de chaves processada.'

    def auth():
        value=request.headers.get('Authorization','')
        if not value.startswith('Bearer '):raise Unauthorized('Autenticação do agente ausente.')
        dev=g.s.scalar(select(AgentDevice).where(AgentDevice.token_hash==hash_value(value[7:]),AgentDevice.revoked=='0',AgentDevice.expires>time.time()))
        if not dev:raise Unauthorized('Agente não autorizado.')
        return dev
    @app.get('/api/distribution',endpoint='distribution_list')
    def listing():
        cid=request.args.get('company');company(cid,admin=True)
        state=g.s.get(DistributionState,cid)
        tasks=g.s.scalars(select(DistributionTask).where(DistributionTask.company_id==cid).order_by(DistributionTask.created.desc()).limit(30))
        return jsonify(nsu=state.nsu if state else '000000000000000',next_allowed=state.next_allowed if state else 0,items=[{'id':t.id,'state':'expired' if t.state in ('pending','running') and t.expires<time.time() else t.state,'message':t.message,'created':t.created} for t in tasks])
    @app.post('/api/distribution',endpoint='distribution_create')
    def create():
        d=request.get_json() or {};cid=d.get('company');co=company(cid,admin=True)
        period=requested_period(d)
        dev=g.s.get(AgentDevice,d.get('device'))
        if not dev or not device_allowed(g.s,dev,cid) or dev.revoked!='0' or dev.last_seen<time.time()-45:raise ValueError('Conecte o computador desta empresa.')
        cap=g.s.get(Setting,'agentcaps:'+dev.id)
        if not cap or 'distribution' not in json.loads(cap.value):raise ValueError('Atualize o aplicativo do agente para consultar a SEFAZ.')
        cert=next((c for c in json.loads(dev.certificates) if c['thumbprint']==d.get('thumbprint') and c['store']==d.get('store') and c['has_private_key']),None)
        if not cert:raise ValueError('Selecione um certificado disponível.')
        uf=str(d.get('uf',''))
        if uf not in '11 12 13 14 15 16 17 21 22 23 24 25 26 27 28 29 31 32 33 35 41 42 43 50 51 52 53'.split():raise ValueError('Selecione a UF da empresa.')
        # Lock company to serialize first state creation and simultaneous clients.
        g.s.execute(select(Company).where(Company.id==cid).with_for_update()).scalar_one()
        state=g.s.get(DistributionState,cid)
        if not state:state=DistributionState(company_id=cid,nsu='000000000000000',next_allowed=0);g.s.add(state)
        if state.next_allowed>time.time():raise ValueError('Aguarde o intervalo exigido pela SEFAZ; não reinicie o NSU.')
        active=g.s.scalar(select(DistributionTask).where(DistributionTask.company_id==cid,DistributionTask.state.in_(['pending','running']),DistributionTask.expires>time.time()))
        if active:raise ValueError('Uma consulta já está em andamento.')
        if g.s.scalar(select(CaptureBatch).where(CaptureBatch.company_id==cid,CaptureBatch.state.in_(['active','paused']))):
            raise ValueError('Conclua ou cancele o lote existente antes de iniciar outro.')
        if d.get('batch'):
            raw_keys=d.get('keys','')
            if not isinstance(raw_keys,str) or len(raw_keys)>5000000:raise ValueError('Envie até 100.000 chaves.')
            entries=[v for v in re.split(r'[\s,;]+',raw_keys.strip()) if v]
            if len(entries)>100000:raise ValueError('Envie até 100.000 chaves.')
            unique=list(dict.fromkeys(entries))
            for index,key in enumerate(unique):
                try:
                    validate_key(key)
                    if key[20:22]!='55':raise ValueError('Modelo não suportado na captura NF-e.')
                except ValueError:raise ValueError(f'Chave inválida na posição {index+1}. Envie somente NF-e modelo 55.')
            batch=CaptureBatch(company_id=cid,device_id=dev.id,mode='keys' if unique else 'history',total=len(unique),
                options=json.dumps({'thumbprint':cert['thumbprint'],'store':cert['store'],'document':co.document,'uf':uf,**period,'continuous':d.get('continuous') is True}))
            g.s.add(batch);g.s.flush()
            for start in range(0,len(unique),500):
                g.s.execute(insert(CaptureItem),[{'batch_id':batch.id,'key':key} for key in unique[start:start+500]])
            log(g.s,cid,g.user.id,'lote_captura_criado',str(len(unique)));g.s.commit()
            return jsonify(id=batch.id,total=len(unique),duplicates=len(entries)-len(unique))
        key=validate_key(d['key']) if d.get('key') else ''
        task=DistributionTask(company_id=cid,device_id=dev.id,payload=json.dumps({'kind':'distribution','thumbprint':cert['thumbprint'],'store':cert['store'],'document':co.document,'uf':uf,'nsu':state.nsu,'key':key,'remaining':20 if not key else 1,**period}))
        g.s.add(task)
        log(g.s,cid,g.user.id,'consulta_sefaz_solicitada');g.s.commit();return jsonify(id=task.id)
    @app.get('/api/capture/batches')
    def batches():
        cid=request.args.get('company');company(cid,admin=True)
        result=[]
        for b in g.s.scalars(select(CaptureBatch).where(CaptureBatch.company_id==cid).order_by(CaptureBatch.created.desc()).limit(30)):
            counts=dict(g.s.execute(select(CaptureItem.state,func.count()).where(CaptureItem.batch_id==b.id).group_by(CaptureItem.state)).all())
            state=g.s.get(DistributionState,cid)
            options=json.loads(b.options)
            from .capture_progress import capture_progress
            progress=capture_progress(g.s,b,counts,state,g.s.get(AgentDevice,b.device_id))
            result.append({'id':b.id,'state':b.state,'mode':b.mode,'total':b.total,'xmls':b.xmls,'summaries':b.summaries,'counts':counts,'message':b.message,'next_allowed':state.next_allowed if state else 0,'schedule':options.get('schedule','hourly'),'next_run':options.get('next_run',0),'retry_at':options.get('retry_at',0),'retry_count':options.get('retry_count',0),'period':options.get('period','all'),'date_from':options.get('date_from',''),'date_to':options.get('date_to',''),'progress':progress})
        return jsonify(items=result)

    @app.post('/api/capture/batches/<bid>/control')
    def control_batch(bid):
        d=request.get_json() or {};cid=d.get('company');company(cid,admin=True)
        g.s.execute(select(Company).where(Company.id==cid).with_for_update()).scalar_one()
        batch=g.s.get(CaptureBatch,bid)
        if not batch or batch.company_id!=cid:raise ValueError('Lote indisponível.')
        action=d.get('action')
        if action not in ('pause','resume','retry','cancel'):raise ValueError('Ação inválida.')
        if batch.state=='cancelled':raise ValueError('Lote cancelado não pode ser retomado.')
        if action in ('resume','retry'):
            other=g.s.scalar(select(CaptureBatch).where(CaptureBatch.company_id==cid,CaptureBatch.id!=bid,CaptureBatch.state.in_(['active','paused'])))
            if other:raise ValueError('Existe outro lote nesta empresa.')
        if action in ('retry','resume'):
            # Expired in-flight items must become failures before resetting the
            # retry queue, otherwise dispatch immediately pauses this batch again.
            expired=g.s.scalars(select(DistributionTask).join(CaptureDispatch,CaptureDispatch.task_id==DistributionTask.id).where(CaptureDispatch.batch_id==bid,DistributionTask.state.in_(['pending','running']),DistributionTask.expires<=time.time()))
            for task in expired:
                task.state='failed';task.message='Prazo encerrado. Nova tentativa solicitada.';finish_batch(task)
                if action=='resume':
                    dispatch=g.s.get(CaptureDispatch,task.id)
                    item=g.s.get(CaptureItem,dispatch.item_id) if dispatch and dispatch.item_id else None
                    if item:item.state='pending';item.message='Retomado após expiração durante a pausa.'
            g.s.flush()
            if action=='retry':
                options=json.loads(batch.options);options.pop('retry_count',None);options.pop('retry_at',None);batch.options=json.dumps(options)
            if action=='retry':g.s.execute(update(CaptureItem).where(CaptureItem.batch_id==bid,CaptureItem.state=='failed').values(state='pending',message='Aguardando nova tentativa.'))
        batch.state={'pause':'paused','resume':'active','retry':'active','cancel':'cancelled'}[action]
        batch.message={'pause':'Pausado. A consulta em andamento pode terminar.','resume':'Retomado; aguardando intervalo e conector.','retry':'Falhas reenviadas.','cancel':'Cancelado; a consulta em andamento pode terminar.'}[action]
        log(g.s,cid,g.user.id,'captura_'+action);g.s.commit();return jsonify(ok=True)

    @app.post('/api/distribution/cancel',endpoint='distribution_cancel')
    def cancel():
        cid=(request.get_json() or {}).get('company');company(cid,admin=True)
        # Keep running result importable, but stop subsequent batches.
        tasks=g.s.scalars(select(DistributionTask).where(DistributionTask.company_id==cid,DistributionTask.state.in_(['pending','running'])))
        for task in tasks:
            payload=json.loads(task.payload);payload['remaining']=1;task.payload=json.dumps(payload)
            if task.state=='pending':task.state='cancelled';task.message='Consulta cancelada pelo usuário.'
        log(g.s,cid,g.user.id,'consulta_sefaz_cancelada');g.s.commit();return jsonify(ok=True)
    def claim(dev):
        task=None
        queued=g.s.scalars(select(DistributionTask).where(DistributionTask.device_id==dev.id,DistributionTask.state=='pending',DistributionTask.expires>time.time(),DistributionTask.created<=time.time()).order_by(DistributionTask.created))
        for candidate in queued:
            candidate_state=g.s.get(DistributionState,candidate.company_id)
            if not candidate_state or candidate_state.next_allowed>time.time():continue
            candidate_dispatch=g.s.get(CaptureDispatch,candidate.id)
            if candidate_dispatch:
                candidate_batch=g.s.get(CaptureBatch,candidate_dispatch.batch_id)
                if not candidate_batch or candidate_batch.state!='active':continue
            task=candidate;break
        if not task:
            task=dispatch_batch(dev)
        if not task:return None
        g.s.execute(select(Company).where(Company.id==task.company_id).with_for_update()).scalar_one()
        g.s.refresh(task)
        if task.device_id!=dev.id or task.state!='pending':return None
        # A queued task is not an authorization grant. Links may have been revoked
        # after enqueueing, especially before decrypting an A1 for the connector.
        if not device_allowed(g.s,dev,task.company_id):
            task.state='cancelled';task.message='Vínculo com a empresa revogado antes do envio.';finish_batch(task)
            return None
        state=g.s.get(DistributionState,task.company_id)
        if not state or state.next_allowed>time.time():return None
        dispatch=g.s.get(CaptureDispatch,task.id)
        if dispatch:
            batch=g.s.get(CaptureBatch,dispatch.batch_id)
            if not batch or batch.state!='active':return None
        options=json.loads(task.payload)
        if not options.get('a1'):
            from .agent_bridge import CompanyCertificate
            chosen=g.s.get(CompanyCertificate,task.company_id)
            if chosen:
                options.update(thumbprint=chosen.thumbprint,store=chosen.store)
                task.device_id=chosen.device_id;task.payload=json.dumps(options)
                if task.device_id!=dev.id:return None
            available=any(c.get('thumbprint')==options.get('thumbprint') and c.get('store')==options.get('store') and c.get('has_private_key') for c in json.loads(dev.certificates))
            if not available:
                task.state='failed';task.message='Certificado associado indisponível. Confira a seleção deste cadastro.';finish_batch(task)
                return None
        else:
            caps=g.s.get(Setting,'agentcaps:'+dev.id)
            if not caps or 'stored_a1' not in json.loads(caps.value):
                task.state='failed';task.message='Atualize o agente para usar o A1 cadastrado.';finish_batch(task)
                return None
        if g.s.execute(update(DistributionTask).where(DistributionTask.id==task.id,DistributionTask.state=='pending').values(state='running')).rowcount!=1:return None
        output={'id':task.id,**options}
        if output.get('a1'):
            from .client_capture import StoredA1,cipher
            saved=g.s.get(StoredA1,task.company_id)
            if not saved:
                task.state='failed';task.message='A1 indisponível.';finish_batch(task);return None
            credentials=json.loads(cipher(storage).decrypt(saved.encrypted.encode()))
            output['pfx']=credentials['pfx'];output['pfx_password']=credentials['password']
        g.s.add(Setting(key='distribution:delivered:'+task.id,value=str(time.time())))
        return output
    app.extensions['distribution_claim']=claim
    @app.post('/api/agent/distribution-result',endpoint='distribution_result')
    def result():
        dev=auth();d=request.get_json() or {};task=g.s.get(DistributionTask,d.get('id'))
        if not task or task.device_id!=dev.id:raise ValueError('Consulta não pertence ao agente.')
        g.s.execute(select(Company).where(Company.id==task.company_id).with_for_update()).scalar_one()
        g.s.refresh(task)
        if not device_allowed(g.s,dev,task.company_id):raise ValueError('Vínculo com a empresa revogado.')
        acknowledgement='distribution:accepted:'+task.id
        accepted=g.s.get(Setting,acknowledgement)
        if accepted:return jsonify(ok=True,recovered=accepted.value=='recovered')
        if task.state=='completed':return jsonify(ok=True,recovered=False)
        delivered=g.s.get(Setting,'distribution:delivered:'+task.id)
        if task.state!='running' and not (task.state=='failed' and delivered):
            raise ValueError('Consulta não foi entregue ao conector ou foi cancelada.')
        state=g.s.get(DistributionState,task.company_id)
        if not state:raise ValueError('Estado de distribuição indisponível.')
        newer=g.s.scalar(select(DistributionTask.id).where(DistributionTask.company_id==task.company_id,DistributionTask.created>task.created).limit(1))
        recovered=task.state=='failed' or task.expires<time.time() or bool(newer)
        def acknowledge():
            g.s.add(Setting(key=acknowledgement,value='recovered' if recovered else 'accepted'))
            if recovered:log(g.s,task.company_id,None,'resposta_sefaz_recuperada',task.id)
            g.s.commit()
            return jsonify(ok=True,recovered=recovered)
        if d.get('ok') is not True:
            reasons = {
                'dns_failed': 'Não foi possível localizar o endereço da SEFAZ. Verifique DNS e internet.',
                'connection_failed': 'Não foi possível estabelecer conexão com a SEFAZ. Verifique rede, proxy e firewall.',
                'tls_failed': 'Falha na conexão segura (TLS) com a SEFAZ. Verifique certificado, cadeia de confiança e configuração de rede.',
                'certificate_key_failed': 'Não foi possível usar a chave do certificado. Verifique PIN, dispositivo e provedor do certificado.',
                'timeout': 'A consulta à SEFAZ excedeu o tempo de espera.',
                'http_transport_failed': 'Falha no transporte HTTP com a SEFAZ. Consulte o detalhe no agente.',
                'certificate_or_request_invalid': 'Certificado ou dados da consulta inválidos. Confira certificado, documento, UF e NSU.',
                'operation_failed': 'Falha na operação SEFAZ. Consulte o detalhe no agente.',
            }
            code = d.get('error')
            message = reasons.get(code) if isinstance(code,str) else None
            if isinstance(code,str) and len(code)==8 and code.startswith('http_') and code[5:].isdigit() and 100<=int(code[5:])<=599:
                message = 'SEFAZ respondeu HTTP '+code[5:]+'. Consulte o agente para diagnóstico.'
            stages={'certificate_selection':'seleção do certificado','private_key':'acesso à chave privada/PIN','request_preparation':'preparação da consulta','https_send':'comunicação HTTPS com a SEFAZ','http_response':'resposta HTTP da SEFAZ','response_body':'leitura da resposta da SEFAZ'}
            stage=d.get('stage')
            if isinstance(stage,str) and stage in stages:
                message=(message or 'Falha na consulta.')+' Etapa: '+stages[stage]+'.'
            facts=d.get('diagnostics')
            if isinstance(facts,list):
                details=[]
                for fact in facts[:5]:
                    if not isinstance(fact,dict):continue
                    fields=[]
                    for key in ('type','hresult','native','http_error','site'):
                        value=fact.get(key)
                        pattern=(r'0x[0-9A-Fa-f]{8}' if key in ('hresult','native') else r'(System\.[A-Za-z0-9_.+`]{1,150}|AgentError)' if key=='type' else r'System\.[A-Za-z0-9_.+<>` ,=-]{1,240}( > System\.[A-Za-z0-9_.+<>` ,=-]{1,240}){0,2}' if key=='site' else r'(Unknown|NameResolutionError|ConnectionError|SecureConnectionError|HttpProtocolError|ExtendedConnectNotSupported|VersionNegotiationError|UserAuthenticationError|ProxyTunnelError|InvalidResponse|ResponseEnded|ConfigurationLimitExceeded)')
                        if isinstance(value,str) and re.fullmatch(pattern,value):fields.append(key+'='+value)
                    if fields:details.append(' / '.join(fields))
                if details:message=(message or 'Falha na consulta.')+' Diagnóstico: '+' | '.join(details)+'.'
            # A local/transport error is not a SEFAZ response imposing a wait.
            # No reservation is created when dispatching; retain any genuine fiscal wait.
            if message:message += ' Nova tentativa disponível após corrigir a causa.'
            transient=code in ('dns_failed','connection_failed','timeout') or (isinstance(code,str) and re.fullmatch(r'http_5[0-9]{2}',code) is not None)
            task.state='failed';task.message=message or 'Falha no certificado ou na conexão com a SEFAZ. Consulte o agente.'
            if not recovered:finish_batch(task,transient=transient)
            return acknowledge()
        raw=base64.b64decode(d.get('response',''),validate=True)
        if len(raw)>16*1024*1024:raise ValueError('Resposta excede limite.')
        task.receipt=storage.save(task.company_id,task.id,raw,'xml')
        try:
            status,last,maximum,message,files=decode_response(raw);xmls=0;keys=0;events=0
            response_key='distribution:last-response:'+task.company_id
            response_info=json.dumps({'task_id':task.id,'received_at':time.time(),'status':status})
            response_setting=g.s.get(Setting,response_key)
            if not recovered or not response_setting:
                if response_setting:response_setting.value=response_info
                else:g.s.add(Setting(key=response_key,value=response_info))
            if status in ('137','138') and not json.loads(task.payload).get('key') and (not last or not maximum):raise ValueError('Resposta sem NSU; continuidade preservada.')
            # Only fiscal response rules impose a cooldown. More NSUs continue immediately.
            options=json.loads(task.payload)
            if status=='138' and not options.get('key') and last and maximum and last<maximum and int(last)<=int(options.get('nsu','0')):
                raise ValueError('Resposta sem avanço do NSU. Captura interrompida para evitar repetição; continuidade preservada.')
            wait = status=='656' or (not options.get('key') and (status=='137' or (last and maximum and last==maximum)))
            next_allowed=time.time()+3600 if wait else 0
            state.next_allowed=max(state.next_allowed,next_allowed) if recovered else next_allowed
            if options.get('key'):
                # SEFAZ permits 20 point queries per hour, not a fixed 181s gap.
                quota_key='distribution:key-responses:'+task.company_id
                quota=g.s.get(Setting,quota_key)
                now=time.time()
                recent=[v for v in json.loads(quota.value) if v>now-3600] if quota else []
                recent.append(now)
                if quota:quota.value=json.dumps(recent)
                else:g.s.add(Setting(key=quota_key,value=json.dumps(recent)))
                if len(recent)>=20:state.next_allowed=max(state.next_allowed,recent[-20]+3600)
            with g.s.begin_nested():
                warnings=0
                for file in files:
                    root=xml_root(file);name=etree.QName(root).localname
                    from .fiscal_history import archive_xml
                    co=g.s.get(Company,task.company_id)
                    archived=archive_xml(g.s,storage,task.company_id,file,co.document)
                    from .manifest_automation import record_summary
                    record_summary(g.s,task,archived,raw,co.document)
                    if archived.warning:warnings+=1
                    if json.loads(task.payload).get('archive'):
                        if name=='nfeProc':xmls+=1
                        elif name=='resNFe':keys+=1
                        else:events+=1
                        continue
                    if name=='nfeProc':
                        # Preserve the original even when fiscal interpretation fails.
                        # Such XMLs remain pending in history, never validated documents.
                        if not archived.warning:
                            try:
                                with g.s.begin_nested():
                                    ingest(dev,file,'sefaz.xml',target_company_id=task.company_id)
                            except FiscalError as ex:
                                archived.warning='XML original arquivado; importação em Documentos pendente: '+str(ex)[:300]
                                warnings+=1
                        xmls+=1
                    elif name=='resNFe':
                        ns={'n':'http://www.portalfiscal.inf.br/nfe'};key=validate_key(root.findtext('n:chNFe',namespaces=ns))
                        if not g.s.scalar(select(Document).where(Document.company_id==task.company_id,Document.key==key)):
                            doc=Document(company_id=task.company_id,key=key,status='acao_manual',source='RESUMO',error='Resumo recebido da SEFAZ; XML completo ainda não disponibilizado.');g.s.add(doc);keys+=1
                    else:events+=1
                if status in ('137','138') and last and not json.loads(task.payload).get('key'):
                    if int(last)<int(state.nsu) and not recovered:raise ValueError('NSU regressivo recusado.')
                    state.nsu=max(state.nsu,last)
                task.state='completed';task.message=f'SEFAZ {status}: {message}. XMLs: {xmls}; novas chaves: {keys}; eventos arquivados: {events}.'
                if warnings:task.message+=f' {warnings} arquivo(s) preservado(s) com aviso de leitura/importação; confira o Histórico fiscal.'
                options=json.loads(task.payload)
                if not recovered and status=='138' and last and maximum and last<maximum and not options.get('key') and options.get('remaining',1)>1 and not g.s.get(CaptureDispatch,task.id):
                    options['remaining']-=1;options['nsu']=last
                    next_device=task.device_id
                    if not options.get('a1'):
                        from .agent_bridge import CompanyCertificate
                        chosen=g.s.get(CompanyCertificate,task.company_id)
                        if chosen:
                            next_device=chosen.device_id;options.update(thumbprint=chosen.thumbprint,store=chosen.store)
                    g.s.add(DistributionTask(company_id=task.company_id,device_id=next_device,payload=json.dumps(options),created=time.time(),expires=time.time()+180))
        except (ValueError,etree.XMLSyntaxError,OSError) as ex:
            xmls=0;keys=0;events=0
            task.state='failed';task.message=str(ex)[:500]
        if not recovered:
            finish_batch(task,locals().get('status'),locals().get('xmls',0),locals().get('keys',0),locals().get('files',[]),locals().get('last'),locals().get('maximum'))
        else:
            # Archive the old receipt without changing current scheduling or a replacement task.
            dispatch=g.s.get(CaptureDispatch,task.id)
            if dispatch:
                batch=g.s.get(CaptureBatch,dispatch.batch_id)
                if batch:batch.xmls+=xmls;batch.summaries+=keys
                if dispatch.item_id:
                    replacement=g.s.scalar(select(CaptureDispatch.task_id).join(DistributionTask,DistributionTask.id==CaptureDispatch.task_id).where(CaptureDispatch.item_id==dispatch.item_id,DistributionTask.created>task.created).limit(1))
                    item=g.s.get(CaptureItem,dispatch.item_id)
                    if item and not replacement:
                        item.state='completed' if task.state=='completed' else 'failed';item.message=task.message
        dev.last_seen=time.time();log(g.s,task.company_id,None,'consulta_sefaz_'+task.state,task.message)
        return acknowledge()
