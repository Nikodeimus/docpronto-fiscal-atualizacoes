"""Independent fiscal archive: working-document deletion never removes this store."""
import io,json,time,hashlib,zipfile,secrets
from flask import g,request,jsonify,send_file
from sqlalchemy import select,func,delete,case
from sqlalchemy.orm import Mapped,mapped_column
from sqlalchemy import String,Text,Float,ForeignKey,UniqueConstraint
from .db import Base,uid,Document,ClientRegistration,Setting,log
from .fiscal import parse_xml,validate_key
from lxml import etree
from .history_trash import retain_deleted_history,register_history_trash

class FiscalArchive(Base):
    __tablename__='fiscal_archive'
    __table_args__=(UniqueConstraint('company_id','sha256'),)
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=uid)
    company_id:Mapped[str]=mapped_column(ForeignKey('companies.id'),index=True)
    key:Mapped[str]=mapped_column(String(44),index=True)
    kind:Mapped[str]=mapped_column(String(40))
    sha256:Mapped[str]=mapped_column(String(64))
    path:Mapped[str]=mapped_column(Text)
    data:Mapped[str]=mapped_column(Text,default='{}')
    warning:Mapped[str]=mapped_column(Text,default='')
    created:Mapped[float]=mapped_column(Float,default=time.time)

def archive_xml(session,storage,cid,raw,taxid):
    from .distribution import xml_root
    root=xml_root(raw);kind=etree.QName(root).localname
    digest=hashlib.sha256(raw).hexdigest()
    old=session.scalar(select(FiscalArchive).where(FiscalArchive.company_id==cid,FiscalArchive.sha256==digest))
    if old:return old
    ns={'n':'http://www.portalfiscal.inf.br/nfe'}
    key=root.findtext('.//n:chNFe',namespaces=ns) or ''
    inf=root.find('.//n:infNFe',ns)
    if inf is not None:key=inf.get('Id','').removeprefix('NFe')
    key=validate_key(key) if key else ''
    issued=root.findtext('.//n:dhEmi',namespaces=ns) or root.findtext('.//n:dhEvento',namespaces=ns) or ''
    data={'issued_at':issued};warning=''
    if kind=='nfeProc':
        try:data=parse_xml(raw,taxid,'auto')
        except ValueError as ex:warning='XML original arquivado; leitura pendente: '+str(ex)[:300]
    else:
        issued=root.findtext('.//n:dhEmi',namespaces=ns) or root.findtext('.//n:dhEvento',namespaces=ns) or ''
        data={'issued_at':issued,'issuer_name':root.findtext('.//n:xNome',namespaces=ns) or '', 'issuer_document':root.findtext('.//n:CNPJ',namespaces=ns) or root.findtext('.//n:CPF',namespaces=ns) or ''}
    row=FiscalArchive(id=uid(),company_id=cid,key=key,kind=kind,sha256=digest,data=json.dumps(data),warning=warning,path='')
    row.path=storage.save(cid,'history-'+row.id,raw,'xml');session.add(row);session.flush();return row

def fiscal_statuses(session,storage,cid,keys):
    """Read verified local evidence across all dates, scoped to this company."""
    from .distribution import xml_root
    states={key:'unknown' for key in keys}
    if not states:return states
    ns={'n':'http://www.portalfiscal.inf.br/nfe'}
    query=select(FiscalArchive).where(FiscalArchive.company_id==cid,FiscalArchive.key.in_(states))
    for row in session.scalars(query):
        try:
            path=storage.resolve(row.path)
            if path.stat().st_size>8*1024*1024:continue
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=row.sha256:continue
            root=xml_root(raw);state='unknown'
            if root.tag=='{'+ns['n']+'}resNFe' and root.findtext('n:chNFe',namespaces=ns)==row.key:
                state={'1':'authorized_in_file','3':'cancelled_in_file'}.get(root.findtext('n:cSitNFe',namespaces=ns),'unknown')
            if root.tag=='{'+ns['n']+'}resEvento' and root.findtext('n:chNFe',namespaces=ns)==row.key:
                if root.findtext('n:tpEvento',namespaces=ns)=='110111' and root.findtext('n:nProt',namespaces=ns):state='cancelled_in_file'
            for ret in root.findall('.//n:retEvento/n:infEvento',ns):
                if ret.findtext('n:chNFe',namespaces=ns)==row.key and ret.findtext('n:tpEvento',namespaces=ns)=='110111' and ret.findtext('n:cStat',namespaces=ns)=='135' and ret.findtext('n:nProt',namespaces=ns):state='cancelled_in_file'
            prot=root.find('n:protNFe/n:infProt',ns)
            if state=='unknown' and prot is not None and prot.findtext('n:chNFe',namespaces=ns)==row.key and prot.findtext('n:cStat',namespaces=ns) in ('100','150') and prot.findtext('n:nProt',namespaces=ns):state='authorized_in_file'
            if state=='cancelled_in_file' or states[row.key]=='unknown':states[row.key]=state
        except (OSError,ValueError,etree.XMLSyntaxError):continue
    return states


def canonical_note_ids(company_ids):
    # Full XML wins even when a later summary/event arrives. Group before
    # applying dates so the same note cannot appear in two emission months.
    ranked=select(FiscalArchive.id.label('id'),func.row_number().over(
        partition_by=(FiscalArchive.company_id,FiscalArchive.key),
        order_by=(case((FiscalArchive.kind=='nfeProc',0),else_=1),FiscalArchive.created.desc(),FiscalArchive.id.desc())
    ).label('rank')).where(FiscalArchive.company_id.in_(company_ids),FiscalArchive.kind.in_(('nfeProc','resNFe')),func.length(FiscalArchive.key)==44).subquery()
    return select(ranked.c.id).where(ranked.c.rank==1)

def note_query(cid):
    return select(FiscalArchive).where(FiscalArchive.company_id==cid,FiscalArchive.id.in_(canonical_note_ids([cid])))

def history_period(query,values):
    """Apply inclusive issue-date or issue-month ranges consistently to all reads."""
    import re
    from datetime import date
    from .document_management import json_data
    daily_start=values.get('date_from','');daily_end=values.get('date_to','')
    if daily_start or daily_end:
        if any(values.get(k) for k in ('month','month_from','month_to')):raise ValueError('Escolha dias ou meses, sem combinar os dois filtros.')
        if not daily_start:raise ValueError('Informe o dia inicial do período.')
        daily_end=daily_end or daily_start
        for value in (daily_start,daily_end):
            if not isinstance(value,str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',value):raise ValueError('Data inválida. Use AAAA-MM-DD.')
            try:date.fromisoformat(value)
            except ValueError:raise ValueError('Data inválida. Informe um dia válido.') from None
        if daily_start>daily_end:raise ValueError('O dia inicial deve ser anterior ou igual ao dia final.')
        issued=func.substr(json_data(FiscalArchive.data)['issued_at'].as_string(),1,10)
        return query.where(issued>=daily_start,issued<=daily_end)
    start=values.get('month_from','')
    end=values.get('month_to','')
    if end and not start:raise ValueError('Informe o mês inicial do período.')
    if not start:start=values.get('month','')
    if not start:return query
    end=end or start
    for value in (start,end):
        if not isinstance(value,str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}',value):
            raise ValueError('Mês inválido. Use AAAA-MM.')
        try:date.fromisoformat(value+'-01')
        except ValueError:raise ValueError('Mês inválido. Use um mês e ano válidos.') from None
    if start>end:raise ValueError('O mês inicial deve ser anterior ou igual ao mês final.')
    issued=func.substr(json_data(FiscalArchive.data)['issued_at'].as_string(),1,7)
    return query.where(issued>=start,issued<=end)

def register_history(app,company,storage,limited):
    from .note_details import register_note_details,search_notes
    register_note_details(app,company,storage)
    register_history_trash(app,company,storage)
    def scope():
        cid=request.args.get('company');company(cid);return cid
    @app.get('/api/history')
    def listing():
        cid=scope();q=select(FiscalArchive).where(FiscalArchive.company_id==cid)
        view=request.args.get('view','files')
        if view not in ('files','notes'):raise ValueError('Visualização inválida.')
        if view=='notes':q=search_notes(note_query(cid),request.args.get('q',''))
        q=history_period(q,request.args)
        meta={}
        if view=='notes':
            complete=g.s.scalar(select(func.count()).select_from(q.where(FiscalArchive.kind=='nfeProc').subquery()))
            pending=g.s.scalar(select(func.count()).select_from(q.where(FiscalArchive.kind=='resNFe').subquery()))
            meta=dict(notes_view=True,complete_xml=complete,pending_xml=pending)
            if request.args.get('pending')=='1':q=q.where(FiscalArchive.kind=='resNFe')
        page=max(1,int(request.args.get('page','1')))
        total=g.s.scalar(select(func.count()).select_from(q.subquery()))
        rows=list(g.s.scalars(q.order_by(FiscalArchive.created.desc(),FiscalArchive.id.desc()).offset((page-1)*50).limit(50)))
        states=fiscal_statuses(g.s,storage,cid,{r.key for r in rows})
        return jsonify(total=total,page=page,**meta,items=[{'id':r.id,'key':r.key,'kind':r.kind,'data':json.loads(r.data),'warning':r.warning,'created':r.created,'fiscal_status':states.get(r.key,'unknown'),'status':'complete' if r.kind=='nfeProc' else 'pending_xml' if r.kind=='resNFe' else 'event'} for r in rows])
    @app.get('/api/history/coverage')
    def monthly_coverage():
        from datetime import datetime,timezone
        from .document_management import json_data
        from .distribution import DistributionTask
        cid=scope()
        now=datetime.now(timezone.utc)
        if request.args.get('month_to') and not (request.args.get('month_from') or request.args.get('month')):
            raise ValueError('Informe o mês inicial do período.')
        start=request.args.get('month_from') or request.args.get('month') or f'{now.year-1 if now.month<12 else now.year:04d}-{now.month+1 if now.month<12 else 1:02d}'
        end=request.args.get('month_to') or (start if request.args.get('month_from') or request.args.get('month') else now.strftime('%Y-%m'))
        period={'month_from':start,'month_to':end}
        if request.args.get('date_from') or request.args.get('date_to'):
            period=request.args
            history_period(select(FiscalArchive.id),period) # Validate before slicing.
            start=request.args['date_from'][:7];end=(request.args.get('date_to') or request.args['date_from'])[:7]
        query=history_period(select(FiscalArchive.id).where(FiscalArchive.company_id==cid),period)
        first=int(start[:4])*12+int(start[5:])-1;last=int(end[:4])*12+int(end[5:])-1
        if last-first>=120:raise ValueError('Escolha até 120 meses para o resumo mensal.')
        issued=func.substr(json_data(FiscalArchive.data)['issued_at'].as_string(),1,7)
        rows=g.s.execute(select(issued,FiscalArchive.kind,func.count(),func.max(FiscalArchive.created)).where(FiscalArchive.id.in_(query)).group_by(issued,FiscalArchive.kind)).all()
        groups={}
        for month,kind,count,received in rows:groups.setdefault(month,[]).append((kind,count,received))
        canonical=history_period(note_query(cid),period).subquery()
        note_groups={}
        for month,kind,count in g.s.execute(select(issued,FiscalArchive.kind,func.count()).where(FiscalArchive.id.in_(select(canonical.c.id))).group_by(issued,FiscalArchive.kind)):
            note_groups.setdefault(month,{})[kind]=count
        items=[]
        for index in range(first,last+1):
            month=f'{index//12:04d}-{index%12+1:02d}';parts=groups.get(month,[])
            items.append({'month':month,'xmls':sum(n for kind,n,_ in parts if kind=='nfeProc'),'summaries':sum(n for kind,n,_ in parts if kind=='resNFe'),'events':sum(n for kind,n,_ in parts if kind not in ('nfeProc','resNFe')),'last_received':max((stamp for _,_,stamp in parts),default=None),'coverage':'not_confirmed'})
            note_counts=note_groups.get(month,{})
            items[-1].update(notes=sum(note_counts.values()),complete_notes=note_counts.get('nfeProc',0),pending_notes=note_counts.get('resNFe',0))
        last_query=g.s.scalar(select(DistributionTask).where(DistributionTask.company_id==cid).order_by(DistributionTask.created.desc()).limit(1))
        return jsonify(items=items,last_query={'created':last_query.created,'state':last_query.state,'message':last_query.message} if last_query else None,message='Os números mostram arquivos recebidos. A SEFAZ não informa o total esperado por mês; a cobertura completa não pode ser confirmada.')
    @app.get('/api/history/<hid>/xml')
    def history_download(hid):
        row=g.s.get(FiscalArchive,hid)
        if not row:raise ValueError('Arquivo não encontrado.')
        company(row.company_id);return send_file(storage.resolve(row.path),as_attachment=True,download_name=(row.key or row.id)+'-'+row.kind+'.xml')
    @app.post('/api/history/archive-workspace')
    def preserve():
        cid=(request.get_json() or {}).get('company');co=company(cid,admin=True);count=0
        for doc in g.s.scalars(select(Document).where(Document.company_id==cid,Document.xml.is_not(None))):
            archive_xml(g.s,storage,cid,storage.resolve(doc.xml).read_bytes(),co.document);count+=1
        log(g.s,cid,g.user.id,'historico_arquivado',str(count));g.s.commit();return jsonify(count=count)
    @app.get('/api/history/export')
    def history_export():
        cid=scope()
        import tempfile,re,shutil
        from datetime import date
        organized=request.args.get('organized')=='1'
        co=company(cid)
        document=re.sub(r'[^0-9]','',co.document) or 'sem-documento'
        query=history_period(select(FiscalArchive).where(FiscalArchive.company_id==cid),request.args)
        output=tempfile.SpooledTemporaryFile(max_size=8*1024*1024)
        try:
            with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z, tempfile.SpooledTemporaryFile(max_size=1024*1024) as manifest:
                manifest.write(b'{"coverage":"not_confirmed","signature_validation":"not_verified","files":[')
                first=True
                status_cache={}
                for row in g.s.scalars(query.execution_options(yield_per=100)):
                    name=(row.key or row.id)+'-'+row.id+'.xml'
                    if organized:
                        issued=json.loads(row.data).get('issued_at','');month=str(issued)[:7]
                        try:date.fromisoformat(month+'-01')
                        except ValueError:month='sem-data'
                        kind=re.sub(r'[^A-Za-z0-9_-]','_',row.kind) or 'outros'
                        name=document+'/'+month+'/'+kind+'/'+name
                    try:
                        if organized:
                            digest=hashlib.sha256()
                            with storage.resolve(row.path).open('rb') as source,z.open(name,'w') as target:
                                for block in iter(lambda:source.read(1024*1024),b''):
                                    digest.update(block);target.write(block)
                            if digest.hexdigest()!=row.sha256:raise ValueError('A integridade de um arquivo diverge do registro. Confira o backup; nenhum ZIP parcial foi entregue.')
                        else:z.write(storage.resolve(row.path),name)
                    except OSError as ex:raise ValueError('Um arquivo do histórico está indisponível. Restaure o arquivo antes de exportar; nenhum ZIP parcial foi entregue.') from ex
                    if organized:
                        origin=g.s.get(Setting,'history:origin:'+row.id)
                        try:provenance=json.loads(origin.value) if origin else {'source':'not_recorded'}
                        except ValueError:provenance={'source':'not_recorded'}
                        entry={'path':name,'integrity':'verified','key':row.key,'kind':row.kind,'sha256':row.sha256,'received_at':row.created,'origin':provenance,'availability':'complete' if row.kind=='nfeProc' else 'summary' if row.kind=='resNFe' else 'event'}
                        if row.key not in status_cache:status_cache.update(fiscal_statuses(g.s,storage,cid,[row.key]))
                        entry['fiscal_status']=status_cache.get(row.key,'unknown')
                        if not first:manifest.write(b',')
                        first=False;manifest.write(json.dumps(entry,ensure_ascii=False).encode('utf-8'))
                if organized:
                    manifest.write(b']}');manifest.seek(0)
                    with z.open('manifesto.json','w') as target:shutil.copyfileobj(manifest,target)
            output.seek(0)
            response=send_file(output,as_attachment=True,download_name='historico-fiscal.zip',mimetype='application/zip')
            response.call_on_close(output.close)
            return response
        except Exception:
            output.close()
            raise
    @app.get('/api/history/keys.txt')
    def history_keys():
        cid=scope()
        import re,tempfile
        keys=history_period(select(FiscalArchive.key).where(FiscalArchive.company_id==cid),request.args).distinct().order_by(FiscalArchive.key)
        output=tempfile.SpooledTemporaryFile(max_size=8*1024*1024)
        try:
            for key in g.s.scalars(keys.execution_options(yield_per=1000)):
                if re.fullmatch(r'[0-9]{44}',key or ''):
                    output.write((key+'\r\n').encode('ascii'))
            output.seek(0)
            response=send_file(output,as_attachment=True,download_name='historico-chaves.txt',mimetype='text/plain')
            response.headers['Cache-Control']='private, no-store'
            response.call_on_close(output.close)
            return response
        except Exception:
            output.close()
            raise

    def deletion_selection(cid,mode,ids):
        query=select(FiscalArchive.id,FiscalArchive.sha256).where(FiscalArchive.company_id==cid)
        if mode=='selected':
            if not isinstance(ids,list) or not 1<=len(ids)<=1000 or any(not isinstance(x,str) for x in ids):
                raise ValueError('Selecione de 1 a 1.000 arquivos.')
            query=query.where(FiscalArchive.id.in_(set(ids)))
        elif mode!='all':raise ValueError('Seleção inválida.')
        return query.order_by(FiscalArchive.id)

    @app.post('/api/history/delete-preview')
    def history_delete_preview():
        from .document_management import snapshot
        d=request.get_json(silent=True)
        if not isinstance(d,dict):raise ValueError('Corpo JSON inválido.')
        cid=d.get('company');co=company(cid,admin=True)
        mode=d.get('scope');ids=d.get('ids',[])
        rows=g.s.execute(deletion_selection(cid,mode,ids)).all()
        if mode=='selected' and len(rows)!=len(set(ids)):raise ValueError('Seleção contém arquivo indisponível.')
        if not rows:raise ValueError('Nenhum arquivo para excluir.')
        token=secrets.token_urlsafe(24)
        saved={'company':cid,'user':g.user.id,'scope':mode,'ids':ids if mode=='selected' else [],'count':len(rows),'hash':snapshot(rows),'expires':time.time()+600}
        g.s.add(Setting(key='history_delete:'+token,value=json.dumps(saved)));g.s.commit()
        return jsonify(token=token,count=len(rows),company_name=co.name,company_document=co.document)

    @app.post('/api/history/delete-batch')
    def history_delete_batch():
        from .document_management import snapshot,verify_deletion_password
        d=request.get_json(silent=True)
        if not isinstance(d,dict):raise ValueError('Corpo JSON inválido.')
        cid=d.get('company');company(cid,admin=True)
        verify_deletion_password(d,limited)
        token=d.get('token','')
        if not isinstance(token,str) or len(token)>60:raise ValueError('Confirmação inválida.')
        entry=g.s.scalar(select(Setting).where(Setting.key=='history_delete:'+token).with_for_update())
        if not entry:raise ValueError('Solicite uma nova confirmação de exclusão.')
        saved=json.loads(entry.value)
        if saved['company']!=cid or saved['user']!=g.user.id or saved['expires']<time.time():raise ValueError('Confirmação expirada ou de outra empresa/usuário.')
        if type(d.get('confirm_count')) is not int or d['confirm_count']!=saved['count']:raise ValueError('Confirme a quantidade exata de arquivos.')
        rows=g.s.execute(deletion_selection(cid,saved['scope'],saved['ids']).with_for_update()).all()
        if len(rows)!=saved['count'] or snapshot(rows)!=saved['hash']:raise ValueError('O histórico mudou. Confira uma nova prévia antes de excluir.')
        ids=[row[0] for row in rows]
        trash_batch=retain_deleted_history(g.s,cid,g.user.id,ids)
        for start in range(0,len(ids),500):
            g.s.execute(delete(FiscalArchive).where(FiscalArchive.company_id==cid,FiscalArchive.id.in_(ids[start:start+500])))
        # Match document deletion: remove records, retain physical files under the installation policy.
        # DistributionState and working Documents must remain intact.
        log(g.s,cid,g.user.id,'historico_excluido',json.dumps({'count':len(ids),'scope':saved['scope'],'snapshot':saved['hash']}))
        g.s.delete(entry);g.s.commit()
        return jsonify(ok=True,deleted=len(ids),trash_batch=trash_batch)
