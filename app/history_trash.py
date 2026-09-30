"""Opt-in recovery for deletions recorded after this feature was installed."""
import hashlib,json,time
from flask import g,request,jsonify
from sqlalchemy import select,func,String,Text,Float,ForeignKey
from sqlalchemy.orm import Mapped,mapped_column
from .db import Base,uid,log

class HistoryTrashBatch(Base):
 __tablename__='history_trash_batches'
 id:Mapped[str]=mapped_column(String(32),primary_key=True,default=uid)
 company_id:Mapped[str]=mapped_column(ForeignKey('companies.id'),index=True)
 user_id:Mapped[str]=mapped_column(String(32))
 created:Mapped[float]=mapped_column(Float,default=time.time)

class HistoryTrashItem(Base):
 __tablename__='history_trash_items'
 id:Mapped[str]=mapped_column(String(32),primary_key=True,default=uid)
 batch_id:Mapped[str]=mapped_column(ForeignKey('history_trash_batches.id'),index=True)
 archive_id:Mapped[str]=mapped_column(String(32))
 snapshot:Mapped[str]=mapped_column(Text)
 state:Mapped[str]=mapped_column(String(24),default='pending')


def retain_deleted_history(session,cid,user_id,ids):
 from .fiscal_history import FiscalArchive
 batch=HistoryTrashBatch(company_id=cid,user_id=user_id);session.add(batch);session.flush()
 for offset in range(0,len(ids),400):
  for row in session.scalars(select(FiscalArchive).where(FiscalArchive.company_id==cid,FiscalArchive.id.in_(ids[offset:offset+400]))):
   snapshot={field:getattr(row,field) for field in ('key','kind','sha256','path','data','warning','created')}
   session.add(HistoryTrashItem(batch_id=batch.id,archive_id=row.id,snapshot=json.dumps(snapshot,ensure_ascii=False)))
 session.flush()
 return batch.id


def register_history_trash(app,company,storage):
 from .fiscal_history import FiscalArchive
 @app.get('/api/history/trash')
 def list_trash():
  cid=request.args.get('company');company(cid,admin=True)
  page=max(1,int(request.args.get('page','1')))
  total=g.s.scalar(select(func.count()).select_from(HistoryTrashBatch).where(HistoryTrashBatch.company_id==cid))
  batches=list(g.s.scalars(select(HistoryTrashBatch).where(HistoryTrashBatch.company_id==cid).order_by(HistoryTrashBatch.created.desc(),HistoryTrashBatch.id.desc()).offset((page-1)*20).limit(20)))
  groups={}
  for bid,state,count in g.s.execute(select(HistoryTrashItem.batch_id,HistoryTrashItem.state,func.count()).where(HistoryTrashItem.batch_id.in_([x.id for x in batches])).group_by(HistoryTrashItem.batch_id,HistoryTrashItem.state)):
   groups.setdefault(bid,{})[state]=count
  return jsonify(page=page,total=total,items=[{'id':b.id,'created':b.created,'total':sum(groups.get(b.id,{}).values()),'pending':groups.get(b.id,{}).get('pending',0),'restored':groups.get(b.id,{}).get('restored',0),'duplicates':groups.get(b.id,{}).get('duplicate',0)} for b in batches],message='Somente exclusões feitas após a ativação da lixeira aparecem aqui. A restauração depende dos arquivos originais preservados.')
 @app.post('/api/history/trash/<batch_id>/restore')
 def restore_trash(batch_id):
  body=request.get_json(silent=True)
  if not isinstance(body,dict):raise ValueError('Informe a empresa para restaurar.')
  cid=body.get('company');company(cid,admin=True)
  batch=g.s.scalar(select(HistoryTrashBatch).where(HistoryTrashBatch.id==batch_id,HistoryTrashBatch.company_id==cid).with_for_update())
  if not batch:raise ValueError('Lote de exclusão não encontrado nesta empresa.')
  changed=False
  result={'restored':0,'already_restored':0,'duplicates':0,'unavailable':0,'conflicts':0,'issues':[]}
  for item in g.s.scalars(select(HistoryTrashItem).where(HistoryTrashItem.batch_id==batch.id).order_by(HistoryTrashItem.id).with_for_update()):
   if item.state=='restored':result['already_restored']+=1;continue
   if item.state=='duplicate':result['duplicates']+=1;continue
   data=json.loads(item.snapshot)
   existing=g.s.get(FiscalArchive,item.archive_id)
   duplicate=g.s.scalar(select(FiscalArchive).where(FiscalArchive.company_id==cid,FiscalArchive.sha256==data['sha256']))
   if duplicate:
    item.state='duplicate';result['duplicates']+=1;changed=True;continue
   if existing:
    result['conflicts']+=1
    if len(result['issues'])<100:result['issues'].append({'key':data['key'],'reason':'Identificador já utilizado; registro preservado.'})
    continue
   try:
    digest=hashlib.sha256()
    with storage.resolve(data['path']).open('rb') as source:
     for chunk in iter(lambda:source.read(1024*1024),b''):digest.update(chunk)
    if digest.hexdigest()!=data['sha256']:raise ValueError('Hash divergente')
   except (OSError,ValueError):
    result['unavailable']+=1
    if len(result['issues'])<100:result['issues'].append({'key':data['key'],'reason':'Arquivo ausente ou integridade divergente.'})
    continue
   g.s.add(FiscalArchive(id=item.archive_id,company_id=cid,**data));g.s.flush()
   item.state='restored';result['restored']+=1;changed=True
  if changed:
   log(g.s,cid,g.user.id,'historico_restaurado',json.dumps({'batch':batch.id,**{k:v for k,v in result.items() if k!='issues'}}))
  g.s.commit()
  return jsonify(**result)
