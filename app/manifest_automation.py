"""Automatic science only for summaries traceable to accepted national distribution."""
import hashlib,json,time
from datetime import datetime,timezone
from sqlalchemy import String,Text,Float,ForeignKey,select
from sqlalchemy.orm import Mapped,mapped_column
from .db import Base,Setting
from .fiscal_history import FiscalArchive

class ScienceCandidate(Base):
    __tablename__='science_candidates'
    company_id:Mapped[str]=mapped_column(ForeignKey('companies.id'),primary_key=True)
    key:Mapped[str]=mapped_column(String(44),primary_key=True)
    archive_id:Mapped[str]=mapped_column(String(32))
    proof:Mapped[str]=mapped_column(Text)
    state:Mapped[str]=mapped_column(String(24),default='pending')
    message:Mapped[str]=mapped_column(Text,default='Aguardando autorização de Ciência automática.')
    created:Mapped[float]=mapped_column(Float,default=time.time)


def record_summary(session,task,archived,receipt,taxid):
    if archived.kind=='nfeProc':
        candidate=session.get(ScienceCandidate,(task.company_id,archived.key))
        if candidate:candidate.state='complete';candidate.message='XML completo recebido no histórico.'
        return
    if archived.kind!='resNFe' or json.loads(task.payload).get('document')!=taxid:return
    row=session.get(ScienceCandidate,(task.company_id,archived.key))
    if row and row.state not in ('pending','ineligible'):return
    proof=json.dumps({'task':task.id,'receipt_sha256':hashlib.sha256(receipt).hexdigest(),'document':taxid,'summary_sha256':archived.sha256})
    if row:row.archive_id=archived.id;row.proof=proof;row.state='pending'
    else:session.add(ScienceCandidate(company_id=task.company_id,key=archived.key,archive_id=archived.id,proof=proof))


def proven_summary(app,session,co,key):
    from .distribution import DistributionTask,decode_response
    from .fiscal_channels import safe_xml
    row=session.get(ScienceCandidate,(co.id,key))
    if not row:raise ValueError('Resumo sem origem comprovada em consulta oficial desta empresa.')
    proof=json.loads(row.proof);task=session.get(DistributionTask,proof['task']);note=session.get(FiscalArchive,row.archive_id)
    if not task or task.company_id!=co.id or task.state!='completed' or not task.receipt or not note or note.company_id!=co.id or note.key!=key or note.kind!='resNFe':raise ValueError('Consulta ou resumo de origem indisponível.')
    if proof['document']!=co.document or json.loads(task.payload).get('document')!=co.document:raise ValueError('Documento consultado não corresponde à empresa.')
    receipt=app.storage.resolve(task.receipt).read_bytes();raw=app.storage.resolve(note.path).read_bytes()
    if hashlib.sha256(receipt).hexdigest()!=proof['receipt_sha256'] or hashlib.sha256(raw).hexdigest()!=proof['summary_sha256'] or note.sha256!=proof['summary_sha256']:raise ValueError('Origem fiscal com integridade divergente.')
    status,_,_,_,files=decode_response(receipt)
    if status!='138' or raw not in files:raise ValueError('Resumo não consta no recibo oficial preservado.')
    root=safe_xml(raw);ns='{http://www.portalfiscal.inf.br/nfe}'
    if root.tag!=ns+'resNFe' or root.findtext(ns+'chNFe')!=key or root.findtext(ns+'cSitNFe')!='1':raise ValueError('Resumo não indica NF-e autorizada.')
    issued=datetime.fromisoformat(root.findtext(ns+'dhEmi','').replace('Z','+00:00'))
    if issued.tzinfo is None or not -300<=time.time()-issued.timestamp()<=7*86400:raise ValueError('Ciência automática limitada a resumos emitidos nos últimos 7 dias.')
    if session.scalar(select(FiscalArchive.id).where(FiscalArchive.company_id==co.id,FiscalArchive.key==key,FiscalArchive.kind=='nfeProc')):raise ValueError('XML completo já recebido; Ciência automática dispensada.')
    for event in session.scalars(select(FiscalArchive).where(FiscalArchive.company_id==co.id,FiscalArchive.key==key,FiscalArchive.kind.in_(('procEventoNFe','resEvento')))):
        eventraw=app.storage.resolve(event.path).read_bytes()
        if hashlib.sha256(eventraw).hexdigest()!=event.sha256:raise ValueError('Evento com integridade divergente; confira a nota.')
        r=safe_xml(eventraw)
        if any(code in ('110111','210200','210210','210220','210240') for code in r.xpath('//*[local-name()="tpEvento"]/text()')):raise ValueError('Evento anterior exige conferência; não enviar Ciência automaticamente.')
    return row


def schedule_automatic(app,session,dev,queue):
    from .fiscal_channels import FiscalChannel,FiscalTask
    from .agent_bridge import device_allowed
    from .db import Company
    active=[]
    for ch in session.scalars(select(FiscalChannel).where(FiscalChannel.service=='manifest',FiscalChannel.enabled=='1',FiscalChannel.next_allowed<=time.time())):
        flag=session.get(Setting,'manifest:auto:'+ch.company_id)
        if flag and flag.value=='1' and ch.consent and device_allowed(session,dev,ch.company_id):active.append(ch.company_id)
    if not active:return
    for row in session.scalars(select(ScienceCandidate).where(ScienceCandidate.company_id.in_(active),ScienceCandidate.state=='pending').order_by(ScienceCandidate.created).limit(200)):
        enabled=session.get(Setting,'manifest:auto:'+row.company_id);channel=session.get(FiscalChannel,(row.company_id,'manifest'))
        if not enabled or enabled.value!='1' or not channel or channel.enabled!='1' or not channel.consent or channel.next_allowed>time.time() or not device_allowed(session,dev,row.company_id):continue
        existing=session.scalar(select(FiscalTask).where(FiscalTask.company_id==row.company_id,FiscalTask.service=='manifest',FiscalTask.state.in_(('pending','running'))))
        if existing:continue
        co=session.get(Company,row.company_id)
        try:
            proven_summary(app,session,co,row.key)
            task=queue(co,'manifest',row.key)
            options=json.loads(task.payload);options['automatic']=True;task.payload=json.dumps(options)
            proof=json.loads(row.proof);proof['manifest_task']=task.id;row.proof=json.dumps(proof)
            row.state='queued';row.message='Ciência agendada: '+task.id
        except (ValueError,OSError) as ex:
            row.state='ineligible';row.message=str(ex)[:300]


def automation_status(session,cid):
    flag=session.get(Setting,'manifest:auto:'+cid)
    rows=list(session.scalars(select(ScienceCandidate).where(ScienceCandidate.company_id==cid).order_by(ScienceCandidate.created.desc()).limit(20)))
    from .fiscal_channels import FiscalTask
    items=[]
    for r in rows:
        state,message=r.state,r.message
        taskid=json.loads(r.proof).get('manifest_task')
        task=session.get(FiscalTask,taskid) if taskid else None
        if r.state=='queued' and task and task.state in ('failed','completed','cancelled'):
            state='review';message=task.message+' Confira o resultado; nenhum reenvio automático será feito.'
        items.append({'key':r.key,'state':state,'message':message})
    return {'automatic':bool(flag and flag.value=='1'),'candidates':items}

def schedule_xml_after_science(session,task,status):
    """One delayed key capture through existing NF-e quota/pause machinery."""
    from .distribution import CaptureBatch,CaptureItem
    if status!='135':return
    key=json.loads(task.payload).get('key','');candidate=session.get(ScienceCandidate,(task.company_id,key))
    if not candidate or candidate.state!='queued':return
    flag=session.get(Setting,'manifest:auto:'+task.company_id)
    from .fiscal_channels import FiscalChannel
    channel=session.get(FiscalChannel,(task.company_id,'manifest'))
    if not flag or flag.value!='1' or not channel or channel.enabled!='1':return
    options=json.loads(task.payload)
    if options.get('uf') not in '11 12 13 14 15 16 17 21 22 23 24 25 26 27 28 29 31 32 33 35 41 42 43 50 51 52 53'.split():
        candidate.state='action_required';candidate.message='Ciência registrada. Configure a UF na captura antes de buscar o XML.';return
    options={k:options[k] for k in ('thumbprint','store','a1','uf') if k in options}
    from .db import Company
    options.update(document=session.get(Company,task.company_id).document,period='all',next_run=time.time()+300,continuous=False,archive=False)
    batch=CaptureBatch(company_id=task.company_id,device_id=task.device_id,options=json.dumps(options),mode='keys',total=1,message='XML após Ciência: aguardando disponibilidade e intervalo da SEFAZ.')
    session.add(batch);session.flush();session.add(CaptureItem(batch_id=batch.id,key=key))
    candidate.state='xml_queued';candidate.message='Ciência registrada. Consulta do XML agendada em Buscar notas, respeitando a espera da SEFAZ.'

def backfill_proofs(app,session,co):
    """Recover provenance only from preserved successful receipts; never restore XMLs."""
    from .distribution import DistributionTask,decode_response
    notes={r.sha256:r for r in session.scalars(select(FiscalArchive).where(FiscalArchive.company_id==co.id,FiscalArchive.kind=='resNFe').order_by(FiscalArchive.created.desc()).limit(1000)) if not session.get(ScienceCandidate,(co.id,r.key))}
    if not notes:return
    total=0
    for task in session.scalars(select(DistributionTask).where(DistributionTask.company_id==co.id,DistributionTask.state=='completed',DistributionTask.receipt.is_not(None)).order_by(DistributionTask.created.desc()).limit(100)):
        if json.loads(task.payload).get('document')!=co.document:continue
        try:
            raw=app.storage.resolve(task.receipt).read_bytes();total+=len(raw)
            if total>32*1024*1024:break
            status,_,_,_,files=decode_response(raw)
            if status!='138':continue
            for file in files:
                note=notes.get(hashlib.sha256(file).hexdigest())
                if note:
                    record_summary(session,task,note,raw,co.document);notes.pop(note.sha256,None)
        except (ValueError,OSError):continue
        if not notes:break
