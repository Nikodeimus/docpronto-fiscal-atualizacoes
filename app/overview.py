"""Read-only system overview, restricted to explicit company memberships."""
import time,json
from flask import g,jsonify
from sqlalchemy import select,func
from .db import Company,Member,Document,Job,ClientRegistration,Client,Setting
from .distribution import CaptureBatch,DistributionTask,DistributionState
from .fiscal_history import FiscalArchive,canonical_note_ids
from .agent_bridge import CompanyCertificate,AgentDevice,device_allowed
from .client_capture import StoredA1

def register_overview(app):
    @app.get('/api/overview')
    def overview():
        ids=select(Member.company_id).where(Member.user_id==g.user.id)
        rows={}
        for co in g.s.scalars(select(Company).where(Company.id.in_(ids)).order_by(Company.name)):
            rows[co.id]=dict(id=co.id,name=co.name,document=co.document,client_id=None,provider_id=None,documents={},archive={},jobs={},batches={},last=None,next_allowed=0,certificate='Não selecionado',valid_until=None,online=False)
            rows[co.id].update(notes={'total':0,'complete':0,'pending':0},can_manage=False,capture_enabled=False,retry_at=0)
        for member in g.s.scalars(select(Member).where(Member.user_id==g.user.id)):
            if member.company_id in rows:rows[member.company_id]['can_manage']=member.role=='admin'
        for cid,kind,count in g.s.execute(select(FiscalArchive.company_id,FiscalArchive.kind,func.count()).where(FiscalArchive.id.in_(canonical_note_ids(ids))).group_by(FiscalArchive.company_id,FiscalArchive.kind)):
            rows[cid]['notes']['complete' if kind=='nfeProc' else 'pending']=count
            rows[cid]['notes']['total']+=count
        for setting in g.s.scalars(select(Setting).where(Setting.key.in_(['capture-registration:'+cid for cid in rows]))):
            rows[setting.key.removeprefix('capture-registration:')]['capture_enabled']=bool(json.loads(setting.value).get('enabled'))
        for batch in g.s.scalars(select(CaptureBatch).where(CaptureBatch.company_id.in_(ids),CaptureBatch.state=='active')):
            rows[batch.company_id]['retry_at']=max(rows[batch.company_id]['retry_at'],json.loads(batch.options).get('retry_at',0))
        for link,client in g.s.execute(select(ClientRegistration,Client).join(Client,Client.id==ClientRegistration.client_id).where(ClientRegistration.company_id.in_(ids))):
            rows[link.company_id].update(client_id=client.id,provider_id=client.provider_id)
        for model,col,key in [(Document,Document.status,'documents'),(FiscalArchive,FiscalArchive.kind,'archive'),(CaptureBatch,CaptureBatch.state,'batches')]:
            for cid,status,count in g.s.execute(select(model.company_id,col,func.count()).where(model.company_id.in_(ids)).group_by(model.company_id,col)):
                rows[cid][key][status]=count
        for cid,status,count in g.s.execute(select(Document.company_id,Job.state,func.count()).join(Job,Job.document_id==Document.id).where(Document.company_id.in_(ids)).group_by(Document.company_id,Job.state)):
            rows[cid]['jobs'][status]=count
        ranked=select(DistributionTask.id.label('id'),func.row_number().over(partition_by=DistributionTask.company_id,order_by=(DistributionTask.created.desc(),DistributionTask.id.desc())).label('rank')).where(DistributionTask.company_id.in_(ids)).subquery()
        now=time.time()
        for t in g.s.scalars(select(DistributionTask).join(ranked,ranked.c.id==DistributionTask.id).where(ranked.c.rank==1)):
            rows[t.company_id]['last']=dict(state='expired' if t.state in ('pending','running') and t.expires<now else t.state,message=t.message,created=t.created)
        for s in g.s.scalars(select(DistributionState).where(DistributionState.company_id.in_(ids))):
            rows[s.company_id]['next_allowed']=s.next_allowed
        for sel,dev in g.s.execute(select(CompanyCertificate,AgentDevice).join(AgentDevice,AgentDevice.id==CompanyCertificate.device_id).where(CompanyCertificate.company_id.in_(ids))):
            row=rows[sel.company_id]
            row['online']=bool(device_allowed(g.s,dev,sel.company_id) and dev.expires>now and dev.last_seen>now-45)
            cert=next((c for c in json.loads(dev.certificates) if c.get('thumbprint')==sel.thumbprint and c.get('store')==sel.store),None)
            row['certificate']='Selecionado' if cert else 'Indisponível no agente'
            row['valid_until']=cert.get('valid_until') if cert else None
        for saved in g.s.scalars(select(StoredA1).where(StoredA1.company_id.in_(ids))):
            setting=g.s.get(Setting,'capture-registration:'+saved.company_id)
            options=json.loads(setting.value) if setting else {}
            if options.get('certificate')=='installed':continue
            row=rows[saved.company_id]
            row['certificate']='A1 cadastrado'
            row['valid_until']=json.loads(saved.metadata_json).get('valid_until')
            if options.get('certificate')=='a1':
                dev=g.s.get(AgentDevice,options.get('device'))
                row['online']=bool(device_allowed(g.s,dev,saved.company_id) and dev.expires>now and dev.last_seen>now-45)
        from .attention import build_attention
        for row in rows.values():row['attention']=build_attention(row,now)
        return jsonify(updated=now,items=list(rows.values()))

    @app.get('/api/certificates/alerts')
    def certificate_alerts():
        from .agent_bridge import certificate_expired
        # Reuse overview's explicit membership boundary and effective certificate.
        data=overview().get_json()
        return jsonify(items=[{'company':row['id'],'name':row['name'],'valid_until':row['valid_until']}
            for row in data['items'] if certificate_expired({'valid_until':row['valid_until']})])
