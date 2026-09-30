"""Independent distribution channels. Never mix CT-e/NFS-e cursors with NF-e."""

import base64,gzip,hashlib,io,json,re,time

from datetime import datetime,timezone

from flask import g,request,jsonify,send_file

from sqlalchemy import select,update,String,Text,Float,ForeignKey,UniqueConstraint

from sqlalchemy.orm import Mapped,mapped_column

from werkzeug.exceptions import Unauthorized

from lxml import etree

from .db import Base,uid,Company,Setting,log

from .agent_bridge import AgentDevice,CompanyCertificate,hash_value,device_allowed

from .distribution import xml_root

def safe_xml(raw):
    root=xml_root(raw)
    if root.getroottree().docinfo.doctype or any(isinstance(x,etree._Entity) for x in root.iter()):raise ValueError('DTD e entidades XML nao permitidos.')
    return root

SERVICES=('cte','nfse','manifest')

CAPABILITIES={'cte':'cte_distribution','nfse':'nfse_distribution','manifest':'manifest_science'}

class FiscalChannel(Base):

    __tablename__='fiscal_channels'

    company_id:Mapped[str]=mapped_column(ForeignKey('companies.id'),primary_key=True)

    service:Mapped[str]=mapped_column(String(16),primary_key=True)

    enabled:Mapped[str]=mapped_column(String(1),default='0')

    consent:Mapped[str]=mapped_column(Text,default='')

    nsu:Mapped[str]=mapped_column(String(20),default='000000000000000')

    next_allowed:Mapped[float]=mapped_column(Float,default=0)

    message:Mapped[str]=mapped_column(Text,default='Não ativado.')

class FiscalTask(Base):

    __tablename__='fiscal_tasks'

    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=uid)

    company_id:Mapped[str]=mapped_column(ForeignKey('companies.id'),index=True)

    device_id:Mapped[str]=mapped_column(ForeignKey('agent_devices.id'))

    service:Mapped[str]=mapped_column(String(16))

    payload:Mapped[str]=mapped_column(Text,default='{}')

    state:Mapped[str]=mapped_column(String(20),default='pending')

    message:Mapped[str]=mapped_column(Text,default='Na fila.')

    created:Mapped[float]=mapped_column(Float,default=time.time)

    delivered:Mapped[float]=mapped_column(Float,default=0)

    expires:Mapped[float]=mapped_column(Float,default=0)

    receipt:Mapped[str]=mapped_column(Text,default='')

class FiscalFile(Base):

    __tablename__='fiscal_files'

    __table_args__=(UniqueConstraint('company_id','service','sha256'),)

    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=uid)

    company_id:Mapped[str]=mapped_column(ForeignKey('companies.id'),index=True)

    service:Mapped[str]=mapped_column(String(16))

    kind:Mapped[str]=mapped_column(String(60))

    key:Mapped[str]=mapped_column(String(64),default='')

    sha256:Mapped[str]=mapped_column(String(64))

    path:Mapped[str]=mapped_column(Text)

    created:Mapped[float]=mapped_column(Float,default=time.time)

def store_file(session,storage,cid,service,raw):

    root=safe_xml(raw);digest=hashlib.sha256(raw).hexdigest()

    old=session.scalar(select(FiscalFile).where(FiscalFile.company_id==cid,FiscalFile.service==service,FiscalFile.sha256==digest))

    if old:return old

    keys=root.xpath('//*[local-name()="chCTe" or local-name()="chNFe" or local-name()="chNFSe"]/text()')

    ids=root.xpath('//@Id')

    key=str(keys[0] if keys else (ids[0] if ids else ''))

    key=re.sub(r'^(NFe|CTe|NFS)', '',key)[:64]

    row=FiscalFile(id=uid(),company_id=cid,service=service,kind=etree.QName(root).localname,key=key,sha256=digest,path='')

    row.path=storage.save(cid,'fiscal-'+row.id,raw,'xml');session.add(row);session.flush();return row

def unzip(value):

    compressed=base64.b64decode(value,validate=True)

    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:data=stream.read(8*1024*1024+1)

    if len(data)>8*1024*1024:raise ValueError('XML descompactado excede limite.')

    safe_xml(data);return data

def decode_nfse_response(raw):
    # Official ADN contributors OpenAPI, retrieved using mTLS from docs only.
    data=json.loads(raw)
    if not isinstance(data,dict) or data.get('TipoAmbiente')!='PRODUCAO':raise ValueError('Resposta ADN fora do ambiente de producao.')
    status=data.get('StatusProcessamento');entries=data.get('LoteDFe') or []
    if not isinstance(entries,list) or len(entries)>1000:raise ValueError('Lote ADN excede limite de seguranca de 1000 documentos.')
    if status=='NENHUM_DOCUMENTO_LOCALIZADO':
        if entries:raise ValueError('Resposta ADN contraditoria.')
        return '137','',[]
    if status!='DOCUMENTOS_LOCALIZADOS':
        errors=data.get('Erros') or []
        reason='; '.join(str(x.get('Codigo',''))+' '+str(x.get('Descricao','')) for x in errors[:5] if isinstance(x,dict)) if isinstance(errors,list) else ''
        raise ValueError('ADN rejeitou a consulta: '+reason[:400])
    if not entries:raise ValueError('ADN informou documentos mas retornou lote vazio.')
    files=[];last=0;total=0
    for entry in entries:
        if not isinstance(entry,dict):raise ValueError('Documento ADN invalido.')
        nsu=entry.get('NSU')
        if type(nsu) is not int or nsu<=last or nsu>9223372036854775807:raise ValueError('NSU ADN invalido ou fora de ordem.')
        encoded=entry.get('ArquivoXml')
        if not isinstance(encoded,str):raise ValueError('XML ADN ausente.')
        rawfile=unzip(encoded);total+=len(rawfile)
        if total>32*1024*1024:raise ValueError('Lote ADN descompactado excede limite.')
        root=safe_xml(rawfile)
        if etree.QName(root).namespace!='http://www.sped.fazenda.gov.br/nfse':raise ValueError('XML ADN com namespace inesperado.')
        last=nsu;files.append(rawfile)
    return '138',str(last).zfill(15),files

def decode_fiscal_response(service,raw):

    if len(raw)>16*1024*1024:raise ValueError('Resposta excede limite.')

    if service=='nfse':return decode_nfse_response(raw)

    root=safe_xml(raw)

    if service=='cte':

        nodes=root.xpath('//*[local-name()="retDistDFeInt"]')

        if len(nodes)!=1:raise ValueError('Resposta CT-e inválida.')

        ret=nodes[0]

        def get(name):return ret.findtext('{*}'+name,'')

        status=get('cStat');last=get('ultNSU')

        if status not in ('137','138','656'):raise ValueError('CT-e '+status+': '+get('xMotivo')[:180])

        maximum=get('maxNSU')
        if status in ('137','138') and (len(last)!=15 or not last.isdigit() or len(maximum)!=15 or not maximum.isdigit() or last>maximum):raise ValueError('NSU CT-e invalido.')

        items=ret.findall('.//{*}docZip')

        if len(items)>50:raise ValueError('Lote CT-e excede limite.')

        files=[] if status=='656' else [unzip(item.text or '') for item in items]

        if sum(map(len,files))>32*1024*1024:raise ValueError('Lote excede limite.')

        return status,last,files

    events=root.xpath('//*[local-name()="retEvento"]/*[local-name()="infEvento"]')

    if len(events)!=1:raise ValueError('Recibo de manifestação inválido.')

    status=events[0].findtext('{*}cStat','')

    if status not in ('135','136','573'):raise ValueError('Manifestação rejeitada: '+status)

    if events[0].findtext('{*}tpEvento','')!='210210':raise ValueError('Evento inesperado.')

    return status,'',[raw]

def register_fiscal_channels(app,company):

    def channel(cid,service):

        if service not in SERVICES:raise ValueError('Canal inválido.')

        row=g.s.get(FiscalChannel,(cid,service))

        if not row:row=FiscalChannel(company_id=cid,service=service);g.s.add(row);g.s.flush()

        return row

    def auth():

        token=request.headers.get('Authorization','')

        dev=g.s.scalar(select(AgentDevice).where(AgentDevice.token_hash==hash_value(token[7:]),AgentDevice.revoked=='0',AgentDevice.expires>time.time())) if token.startswith('Bearer ') else None

        if not dev:raise Unauthorized('Conector sem autorização.')

        return dev

    @app.get('/api/fiscal/channels')

    def fiscal_channels():

        cid=request.args.get('company');company(cid)

        rows={x.service:x for x in g.s.scalars(select(FiscalChannel).where(FiscalChannel.company_id==cid))}

        items=[]

        for service in SERVICES:

            row=rows.get(service)

            tasks=g.s.scalars(select(FiscalTask).where(FiscalTask.company_id==cid,FiscalTask.service==service).order_by(FiscalTask.created.desc()).limit(10))

            items.append({'service':service,'supported':True,'reason':'Consulta documentos compartilhados no ADN nacional; a disponibilidade depende do compartilhamento municipal.' if service=='nfse' else '', 'capability':CAPABILITIES[service],'enabled':bool(row and row.enabled=='1'),'consent':bool(row and row.consent),'nsu':row.nsu if row else '000000000000000','next_allowed':row.next_allowed if row else 0,'message':row.message if row else 'Não ativado.','tasks':[{'id':t.id,'state':t.state,'message':('Aguardando recuperacao do resultado pelo conector; nao reenviado para evitar duplicidade.' if t.state=='running' and t.expires<time.time() else t.message),'created':t.created} for t in tasks]})

        from .manifest_automation import automation_status
        for item in items:
            if item['service']=='manifest':item.update(automation_status(g.s,cid))
        return jsonify(items=items)

    @app.put('/api/fiscal/channels/<service>')

    def fiscal_settings(service):

        d=request.get_json() or {};co=company(d.get('company'),admin=True);row=channel(co.id,service)

        enabled=d.get('enabled') is True

        if service=='nfse' and enabled and len(co.document)!=14:raise ValueError('A consulta ADN desta versao exige CNPJ.')

        if service=='manifest' and enabled and d.get('consent') is not True:raise ValueError('Confirme autorização para Ciência da Emissão; ela não substitui manifestação conclusiva.')

        if service=='manifest':
            automatic=d.get('automatic') is True
            if automatic and not (enabled and d.get('consent') is True and d.get('automatic_consent') is True):raise ValueError('Confirme a autorizacao especifica da Ciencia automatica.')
            setting=g.s.get(Setting,'manifest:auto:'+co.id)
            if not setting:setting=Setting(key='manifest:auto:'+co.id,value='0');g.s.add(setting)
            setting.value='1' if automatic else '0'
            if automatic:
                from .manifest_automation import backfill_proofs
                backfill_proofs(app,g.s,co)
        row.enabled='1' if enabled else '0'

        row.consent=json.dumps({'user':g.user.id,'at':time.time(),'event':'210210','automatic':d.get('automatic') is True,'policy':'official-summary-7-days-v1'}) if service=='manifest' and enabled else ''

        row.message='Ativado.' if enabled else 'Pausado.'

        log(g.s,co.id,g.user.id,'canal_fiscal_configurado',service);g.s.commit();return jsonify(ok=True)

    def queue(co,service,key=''):

        row=channel(co.id,service)

        if service=='nfse' and len(co.document)!=14:raise ValueError('A consulta ADN desta versao exige CNPJ.')

        if row.enabled!='1':raise ValueError('Ative o canal primeiro.')

        existing=g.s.scalar(select(FiscalTask).where(FiscalTask.company_id==co.id,FiscalTask.service==service,FiscalTask.state.in_(('pending','running'))))

        if existing:
            if service=='manifest' and json.loads(existing.payload).get('key')!=key:raise ValueError('Aguarde a consulta atual antes de enviar outra chave.')

            if existing.state=='running' and existing.expires<time.time():existing.message='Aguardando recuperação do resultado pelo conector; não reenviado para evitar duplicidade.'

            return existing

        chosen=g.s.get(CompanyCertificate,co.id)

        setting=g.s.get(Setting,'capture-registration:'+co.id)

        config=json.loads(setting.value) if setting else {}

        use_a1=config.get('certificate')=='a1'
        uf=str(config.get('uf',''))
        if service=='cte' and uf not in '11 12 13 14 15 16 17 21 22 23 24 25 26 27 28 29 31 32 33 35 41 42 43 50 51 52 53'.split():raise ValueError('Configure a UF no cadastro de captura antes de consultar CT-e.')

        if not chosen and not use_a1:raise ValueError('Selecione um certificado para esta empresa.')

        dev=g.s.get(AgentDevice,config.get('device')) if use_a1 else g.s.get(AgentDevice,chosen.device_id)

        if not device_allowed(g.s,dev,co.id):raise ValueError('Conector sem vínculo com a empresa.')

        if service=='manifest':

            if not row.consent:raise ValueError('Ciência não autorizada.')

            prior=g.s.scalars(select(FiscalTask).where(FiscalTask.company_id==co.id,FiscalTask.service=='manifest')).all()
            if any(json.loads(t.payload).get('key')==key and not(t.state=='cancelled' and not t.delivered) for t in prior):raise ValueError('Ja existe envio para esta chave; confira o resultado antes de repetir.')
            from .fiscal_history import FiscalArchive

            from .fiscal import validate_key

            key=validate_key(key)

            note=g.s.scalar(select(FiscalArchive).where(FiscalArchive.company_id==co.id,FiscalArchive.key==key,FiscalArchive.kind=='nfeProc'))

            if not note:
                from .manifest_automation import proven_summary
                proven_summary(app,g.s,co,key)
            else:

                archived=app.storage.resolve(note.path).read_bytes()
                if hashlib.sha256(archived).hexdigest()!=note.sha256:raise ValueError('XML divergente; restaure uma copia integra antes de manifestar.')
                root=safe_xml(archived)
                ns={'n':'http://www.portalfiscal.inf.br/nfe'}
                infos=root.findall('n:NFe/n:infNFe',ns)
                if root.tag!='{http://www.portalfiscal.inf.br/nfe}nfeProc' or len(infos)!=1 or infos[0].get('Id')!='NFe'+key:raise ValueError('Estrutura da NF-e invalida para manifestacao.')

                dest=infos[0].xpath('./n:dest/n:CNPJ/text() | ./n:dest/n:CPF/text()',namespaces=ns)

                if dest!=[co.document]:raise ValueError('A empresa não é destinatária desta nota.')

        task=FiscalTask(company_id=co.id,device_id=dev.id,service=service,payload=json.dumps({'key':key,'uf':uf,'a1':use_a1,'thumbprint':chosen.thumbprint if chosen else '', 'store':chosen.store if chosen else '', 'consent':service=='manifest','event_time':datetime.now(timezone.utc).isoformat(timespec='seconds')}))

        g.s.add(task);g.s.flush();return task

    @app.post('/api/fiscal/channels/<service>/queue')

    def fiscal_queue(service):

        d=request.get_json() or {};co=company(d.get('company'),admin=True);task=queue(co,service,d.get('key',''))

        log(g.s,co.id,g.user.id,'canal_fiscal_consulta',service);g.s.commit();return jsonify(id=task.id,state=task.state)

    @app.post('/api/fiscal/channels/<service>/<action>')

    def fiscal_pause(service,action):

        if action not in ('pause','resume'):raise ValueError('Ação inválida.')

        d=request.get_json() or {};co=company(d.get('company'),admin=True);row=channel(co.id,service)

        if service=='nfse' and len(co.document)!=14:raise ValueError('A consulta ADN desta versao exige CNPJ.')

        if action=='resume' and service=='manifest' and not row.consent:raise ValueError('Autorize a Ciência primeiro.')

        row.enabled='0' if action=='pause' else '1';row.message='Pausado.' if action=='pause' else 'Retomado; espera fiscal preservada.'

        g.s.commit();return jsonify(ok=True,next_allowed=row.next_allowed)

    def claim(dev,capabilities):
        if 'manifest_science' in capabilities:
            from .manifest_automation import schedule_automatic
            schedule_automatic(app,g.s,dev,queue)
        supported=[service for service in ('cte','nfse') if CAPABILITIES[service] in capabilities]
        if supported:
            for active in g.s.scalars(select(FiscalChannel).where(FiscalChannel.service.in_(supported),FiscalChannel.enabled=='1',FiscalChannel.next_allowed<=time.time())):
                if not device_allowed(g.s,dev,active.company_id):continue
                try:queue(g.s.get(Company,active.company_id),active.service)
                except ValueError:pass

        for task in g.s.scalars(select(FiscalTask).where(FiscalTask.device_id==dev.id,FiscalTask.state=='pending').order_by(FiscalTask.created).limit(100)):

            if CAPABILITIES[task.service] not in capabilities or not device_allowed(g.s,dev,task.company_id):continue

            row=channel(task.company_id,task.service)

            if row.enabled!='1' or row.next_allowed>time.time():continue

            if task.service=='manifest' and not row.consent:continue

            chosen=g.s.get(CompanyCertificate,task.company_id)

            options=json.loads(task.payload)
            if task.service=='manifest' and options.get('automatic'):
                flag=g.s.get(Setting,'manifest:auto:'+task.company_id)
                if not flag or flag.value!='1':
                    task.state='cancelled';task.message='Ciencia automatica desativada antes do envio.'
                    from .manifest_automation import ScienceCandidate
                    candidate=g.s.get(ScienceCandidate,(task.company_id,options.get('key')))
                    if candidate:candidate.state='pending';candidate.message=task.message
                    continue
            options['nsu']=row.nsu
            task.payload=json.dumps(options)

            if options.get('a1'):

                from .client_capture import StoredA1,cipher

                saved=g.s.get(StoredA1,task.company_id)

                if not saved or 'stored_a1' not in capabilities:continue

                credentials=json.loads(cipher(app.storage).decrypt(saved.encrypted.encode()))

                options.update(pfx=credentials['pfx'],pfx_password=credentials['password'])

            else:

                if not chosen or chosen.device_id!=dev.id:continue

                options.update(thumbprint=chosen.thumbprint,store=chosen.store)

            if g.s.execute(update(FiscalTask).where(FiscalTask.id==task.id,FiscalTask.state=='pending').values(state='running',delivered=time.time(),expires=time.time()+180)).rowcount!=1:continue

            co=g.s.get(Company,task.company_id)

            return {'id':task.id,'kind':'fiscal_query','service':task.service,'document':co.document,'nsu':row.nsu,'event_code':'210210',**options}

        return None

    app.extensions['fiscal_claim']=claim

    @app.post('/api/agent/fiscal-result')

    def fiscal_result():

        dev=auth();d=request.get_json() or {};task=g.s.get(FiscalTask,d.get('id'))

        if not task or task.device_id!=dev.id or not device_allowed(g.s,dev,task.company_id):raise ValueError('Consulta inválida.')

        if d.get('service')!=task.service:raise ValueError('Canal não corresponde à consulta.')

        if task.state in ('completed','failed'):return jsonify(ok=True,replayed=True)

        if task.state!='running' or not task.delivered:raise ValueError('Consulta não executada.')

        row=channel(task.company_id,task.service)

        if d.get('ok') is not True:

            task.state='failed';task.message=str(d.get('message') or d.get('error') or 'Falha no conector.')[:500];row.message=task.message;row.next_allowed=max(row.next_allowed,time.time()+60)

        else:

            encoded=d.get('response','')
            try:
                if not isinstance(encoded,str) or len(encoded)>24*1024*1024:raise ValueError('Resposta codificada excede limite.')
                raw=base64.b64decode(encoded,validate=True)
            except (ValueError,TypeError):
                evidence=json.dumps({'error':'Base64 invalido ou excessivo','response':str(encoded)[:4096]}).encode('utf8')
                task.receipt=app.storage.save(task.company_id,'fiscal-receipt-'+task.id,evidence,'json')
                task.state='failed';task.message='Resposta codificada invalida; evidencia limitada preservada.';row.message=task.message;row.next_allowed=max(row.next_allowed,time.time()+3600)
                g.s.commit();return jsonify(ok=True,rejected=True)

            try:
                status,last,files=decode_fiscal_response(task.service,raw)
                if task.service=='nfse' and d.get('http_status') not in (None,200,404):raise ValueError('ADN retornou erro HTTP.')
                if task.service=='nfse' and d.get('http_status')==404 and status!='137':raise ValueError('ADN retornou status HTTP contraditorio.')

            except (ValueError,etree.XMLSyntaxError,OSError) as ex:

                task.receipt=app.storage.save(task.company_id,'fiscal-receipt-'+task.id,raw,'json' if task.service=='nfse' else 'xml')

                task.state='failed';task.message='Resposta preservada para diagnóstico: '+str(ex)[:250]

                row.message=task.message;row.next_allowed=max(row.next_allowed,time.time()+3600)

                g.s.commit();return jsonify(ok=True,rejected=True)

            if task.service=='manifest':

                root=xml_root(raw);keys=root.xpath('//*[local-name()="chNFe"]/text()')

                co=g.s.get(Company,task.company_id)
                event=root.xpath('//*[local-name()="retEvento"]/*[local-name()="infEvento"]')[0]
                author=event.findtext('{*}CNPJDest') or event.findtext('{*}CPFDest') or event.findtext('{*}CNPJ') or event.findtext('{*}CPF')
                if keys!=[json.loads(task.payload)['key']] or (author and author!=co.document) or event.findtext('{*}nSeqEvento')!='1' or event.findtext('{*}tpAmb')!='1':
                    task.receipt=app.storage.save(task.company_id,'fiscal-receipt-'+task.id,raw,'xml');task.state='failed';task.message='Recibo de manifestacao divergente; preservado para conferencia.';row.message=task.message
                    g.s.commit();return jsonify(ok=True,rejected=True)

            if task.service=='manifest':
                from .manifest_automation import schedule_xml_after_science
                schedule_xml_after_science(g.s,task,status)
            for rawfile in files:store_file(g.s,app.storage,task.company_id,task.service,rawfile)

            task.receipt=app.storage.save(task.company_id,'fiscal-receipt-'+task.id,raw,'json' if task.service=='nfse' else 'xml')

            if last and status!='656':row.nsu=str(max(int(last),int(row.nsu))).zfill(15)

            drained=task.service=='cte' and last and xml_root(raw).xpath('string(//*[local-name()="maxNSU"])')==last
            row.next_allowed=max(row.next_allowed,time.time()+3600) if status in ('137','656') or drained else max(row.next_allowed,time.time()+60)

            no_advance=task.service=='nfse' and status=='138' and int(last)<=int(json.loads(task.payload).get('nsu','0'))
            if no_advance:row.enabled='0'
            task.state='completed';task.message=('Evento ja registrado anteriormente; confira o recibo.' if status=='573' else f'Resposta {status}: {len(files)} arquivos preservados.');row.message=task.message
            if no_advance:task.message=row.message='ADN repetiu o NSU; arquivos preservados e canal pausado para evitar consultas repetidas.'

        g.s.commit();return jsonify(ok=True)

    @app.get('/api/fiscal/files')

    def fiscal_files():

        cid=request.args.get('company');company(cid);service=request.args.get('service','cte')

        rows=g.s.scalars(select(FiscalFile).where(FiscalFile.company_id==cid,FiscalFile.service==service).order_by(FiscalFile.created.desc()).limit(200))

        return jsonify(items=[{'id':r.id,'service':r.service,'kind':r.kind,'key':r.key,'created':r.created} for r in rows])

    @app.get('/api/fiscal/files/<fid>/download')

    def fiscal_download(fid):

        row=g.s.get(FiscalFile,fid)

        if not row:raise ValueError('Arquivo não encontrado.')

        company(row.company_id)

        raw=app.storage.resolve(row.path).read_bytes()

        if hashlib.sha256(raw).hexdigest()!=row.sha256:raise ValueError('Arquivo divergente; verifique o backup.')

        return send_file(io.BytesIO(raw),as_attachment=True,download_name=(row.key or row.id)+'.xml',mimetype='application/xml')
