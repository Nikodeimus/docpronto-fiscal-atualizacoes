import os,time,random,json,uuid,signal
from sqlalchemy import select,update,and_,or_
from .config import default_files
from .db import init_db,Job,Document,Company,Setting,log
from .providers import FsistProvider,DocProntoProvider,ProviderPending,ProviderFailure
from .storage import LocalStorageProvider
from .fiscal import parse_xml,FiscalError,build_reconstructed

class DocumentProcessingService:
    def __init__(self,Session,storage):self.Session=Session;self.storage=storage
    def claim(self):
        now=time.time();owner=uuid.uuid4().hex
        with self.Session.begin() as s:
            eligible=or_(and_(Job.state.in_(['pending','retrying']),Job.available<=now),and_(Job.state=='running',Job.lease_until<now))
            paused=select(Setting.key).where(Setting.value=='1')
            q=select(Job).join(Document,Job.document_id==Document.id).where(eligible,~(('pause:'+Document.company_id).in_(paused))).order_by(Job.available).limit(1)
            if s.bind.dialect.name=='postgresql':q=q.with_for_update(skip_locked=True,of=Job)
            job=s.scalar(q)
            if not job:return None
            result=s.execute(update(Job).where(Job.id==job.id,eligible).values(state='running',owner=owner,lease_until=now+180,attempts=Job.attempts+1))
            if result.rowcount!=1:return None
            return job.id,owner
    def once(self):
        claim=self.claim()
        if not claim:return False
        jid,owner=claim
        with self.Session() as s:
            job=s.get(Job,jid)
            if not job or job.owner!=owner or job.state!='running':return True
            doc=s.get(Document,job.document_id)
            if not doc:return True
            co=s.get(Company,doc.company_id)
            if not co:return True
            did=doc.id;cid=co.id;taxid=co.document;key=doc.key;pdf=doc.pdf;stage=job.stage
            if job.attempts>3:
                job.state='failed';job.owner=None;doc.status='erro';doc.error='Limite de tentativas após expiração de leases atingido.';s.commit();return True
            if doc.xml:
                job.state='completed';job.owner=None;s.commit();return True
        input_draft_path=None
        try:
            if not pdf:FsistProvider().get_document(key)
            if os.getenv('DOCPRONTO_ADAPTER_COMMAND'):
                raw=DocProntoProvider().convert(self.storage.resolve(pdf))
            else:
                from .pdf_engine import extract_invoice,validate_review
                with self.Session() as s:
                    current=s.get(Document,did)
                    input_draft_path=current.draft if current else None
                    draft=json.loads(self.storage.resolve(input_draft_path).read_text()) if input_draft_path else None
                draft=draft or extract_invoice(self.storage.resolve(pdf),key)
                with self.Session.begin() as s:
                    current=s.get(Document,did);active=s.get(Job,jid)
                    if not current or current.xml or not active or active.owner!=owner:return True
                    if current.draft!=input_draft_path:
                        active.state='pending';active.owner=None;active.available=time.time();active.attempts=max(0,active.attempts-1);return True
                    if not current.draft:current.draft=self.storage.save(cid,did,json.dumps(draft,ensure_ascii=False).encode(),'json')
                    input_draft_path=current.draft
                missing=validate_review(draft['invoice'])
                if draft.get('conflicts'):raise ProviderPending('A leitura detectou divergências de origem. Confira a nota antes de gerar o XML.')
                if missing:raise ProviderPending('Leitura local concluída. Confira e complete '+str(len(missing))+' campos no formulário da nota.')
                raw,_=build_reconstructed(draft['invoice'],taxid)
            data=parse_xml(raw,taxid)
            if data['key']!=key:raise FiscalError('Chave do XML convertido diverge da chave solicitada.')
            path=self.storage.save(cid,did,raw,'xml')
            with self.Session.begin() as s:
                job=s.get(Job,jid);doc=s.get(Document,did)
                if not job or job.owner!=owner or job.state!='running' or not doc or doc.xml:return True
                if not os.getenv('DOCPRONTO_ADAPTER_COMMAND') and doc.draft!=input_draft_path:
                    job.state='pending';job.owner=None;job.available=time.time();job.attempts=max(0,job.attempts-1);return True
                doc.xml=path;doc.data=json.dumps(data,ensure_ascii=False);doc.source='RECONSTRUCTED';doc.status='revisao';doc.error='XML do conversor recebido. Conferir e homologar antes da exportação.';doc.updated=time.time()
                job.state='completed';job.owner=None;job.lease_until=0;log(s,cid,None,'conversao_recebida',doc=did)
        except (ProviderPending,ProviderFailure,FiscalError) as ex:
            with self.Session.begin() as s:
                job=s.get(Job,jid);doc=s.get(Document,did)
                if not job or job.owner!=owner or job.state!='running' or not doc or doc.xml:return True
                if not os.getenv('DOCPRONTO_ADAPTER_COMMAND') and doc.draft!=input_draft_path:
                    job.state='pending';job.owner=None;job.available=time.time();job.attempts=max(0,job.attempts-1);return True
                if isinstance(ex,ProviderPending):job.state='manual';doc.status='revisao' if pdf else 'acao_manual'
                elif isinstance(ex,ProviderFailure) and job.attempts<3:
                    job.state='retrying';doc.status='aguardando';job.available=time.time()+min(300,15*2**job.attempts)+random.uniform(0,10)
                else:job.state='failed';doc.status='erro'
                job.owner=None;job.lease_until=0;doc.error=str(ex);doc.updated=time.time();log(s,cid,None,'processamento_'+job.state,str(ex),did)
        except Exception:
            # Lease makes unexpected crashes recoverable; no exception content may leak secrets.
            with self.Session.begin() as s:
                job=s.get(Job,jid)
                if job and job.owner==owner and job.state=='running':
                    job.state='failed';job.owner=None;job.lease_until=0
                    doc=s.get(Document,did)
                    if doc and not doc.xml:doc.status='erro';doc.error='Falha interna do worker. Consulte o administrador.'
            raise
        return True

def main():
    _,Session=init_db();storage=LocalStorageProvider(os.getenv('DATA_DIR',default_files()));service=DocumentProcessingService(Session,storage)
    running=True
    def stop(*args):
        nonlocal running;running=False
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    while running:
        try:
            if not service.once():time.sleep(2)
        except Exception:time.sleep(3)
if __name__=='__main__':main()
