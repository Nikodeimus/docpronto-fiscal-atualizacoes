import base64,json,hashlib,time
from datetime import datetime,timezone
from sqlalchemy import select
from test_app import env,KEY,RAW
from test_receipt_recovery import prepare
from test_new_features import response
from app.db import Company,Setting
from app.fiscal_history import FiscalArchive,archive_xml
from app.manifest_automation import ScienceCandidate,proven_summary
import pytest

def summary():
 return ('<resNFe xmlns="http://www.portalfiscal.inf.br/nfe"><chNFe>'+KEY+'</chNFe><CNPJ>11222333000181</CNPJ><dhEmi>'+datetime.now(timezone.utc).isoformat()+'</dhEmi><cSitNFe>1</cSitNFe></resNFe>').encode()

def received(env):
 app,c,h,cid=env;m,auth,dev,poll,bid,task,body=prepare(env)
 body['response']=base64.b64encode(response(files=[summary()])).decode()
 r=m.post('/api/agent/distribution-result',json=body,headers=auth);assert r.status_code==200,r.json
 return m,auth,dev,poll,task

def test_official_summary_proof_and_tamper(env):
 app,c,h,cid=env;received(env)
 with app.session_factory() as s:
  co=s.get(Company,cid);candidate=proven_summary(app,s,co,KEY);assert candidate.state=='pending'
  note=s.get(FiscalArchive,candidate.archive_id);app.storage.resolve(note.path).write_bytes(b'<tampered/>')
  with pytest.raises(ValueError,match='integridade'):proven_summary(app,s,co,KEY)

def test_manual_summary_never_proves_recipient(env):
 app,c,h,cid=env
 with app.session_factory.begin() as s:
  archive_xml(s,app.storage,cid,summary(),'11444777000161')
  with pytest.raises(ValueError,match='origem'):proven_summary(app,s,s.get(Company,cid),KEY)

def test_default_disabled_then_opt_in_queues_once(env):
 app,c,h,cid=env;m,auth,dev,poll,task=received(env)
 from app.agent_bridge import CompanyCertificate
 with app.session_factory.begin() as s:
  chosen=s.get(CompanyCertificate,cid)
  if not chosen:s.add(CompanyCertificate(company_id=cid,device_id=dev,thumbprint='A'*40,store='CurrentUser',updated_by='test'))
  s.add(Setting(key='capture-registration:'+cid,value=json.dumps({'uf':'35','device':dev,'certificate':'installed'})))
 manifestpoll={'certificates':poll['certificates'],'capabilities':['manifest_science']}
 assert m.post('/api/agent/poll',headers=auth,json=manifestpoll).json['task'] is None
 r=c.put('/api/fiscal/channels/manifest',headers=h,json={'company':cid,'enabled':True,'consent':True,'automatic':True})
 assert r.status_code==400
 r=c.put('/api/fiscal/channels/manifest',headers=h,json={'company':cid,'enabled':True,'consent':True,'automatic':True,'automatic_consent':True});assert r.status_code==200,r.json
 sent=m.post('/api/agent/poll',headers=auth,json=manifestpoll).json['task'];assert sent and sent['service']=='manifest' and sent['key']==KEY
 assert m.post('/api/agent/poll',headers=auth,json=manifestpoll).json['task'] is None
 c.post('/api/fiscal/channels/manifest/pause',headers=h,json={'company':cid})
 with app.session_factory() as s:assert s.get(ScienceCandidate,(cid,KEY)).state=='queued'

def test_receipt_tamper_or_other_company_rejected(env):
 app,c,h,cid=env;received(env)
 from app.distribution import DistributionTask
 with app.session_factory.begin() as s:
  row=s.get(ScienceCandidate,(cid,KEY));proof=json.loads(row.proof);t=s.get(DistributionTask,proof['task']);app.storage.resolve(t.receipt).write_bytes(b'<changed/>')
  with pytest.raises(ValueError,match='integridade'):proven_summary(app,s,s.get(Company,cid),KEY)

def test_old_cancelled_and_complete_notes_are_not_automatic(env):
 app,c,h,cid=env;received(env)
 with app.session_factory.begin() as s:
  archive_xml(s,app.storage,cid,RAW,'11444777000161')
  with pytest.raises(ValueError,match='completo'):proven_summary(app,s,s.get(Company,cid),KEY)

@pytest.mark.parametrize('old,cancelled',[(True,False),(False,True)])
def test_expired_or_cancelled_summary_not_eligible(env,old,cancelled):
 app,c,h,cid=env;m,auth,dev,poll,bid,task,body=prepare(env)
 raw=summary()
 if old:
  from datetime import timedelta
  raw=raw.replace(datetime.now(timezone.utc).date().isoformat().encode(),(datetime.now(timezone.utc)-timedelta(days=30)).date().isoformat().encode())
 if cancelled:raw=raw.replace(b'<cSitNFe>1',b'<cSitNFe>3')
 body['response']=base64.b64encode(response(files=[raw])).decode();assert m.post('/api/agent/distribution-result',json=body,headers=auth).status_code==200
 with app.session_factory() as s:
  with pytest.raises(ValueError):proven_summary(app,s,s.get(Company,cid),KEY)

def test_success_schedules_one_xml_capture_and_replay_does_not_duplicate(env):
 test_default_disabled_then_opt_in_queues_once(env)
 app,c,h,cid=env
 from app.fiscal_channels import FiscalTask
 from app.agent_bridge import AgentDevice
 from app.distribution import CaptureBatch,CaptureItem
 from app.manifest_automation import schedule_xml_after_science
 # Already tested authenticated receipt endpoint separately; exercise scheduling atomically.
 c.post('/api/fiscal/channels/manifest/resume',headers=h,json={'company':cid})
 with app.session_factory.begin() as s:
  task=s.scalar(select(FiscalTask).where(FiscalTask.service=='manifest'))
  schedule_xml_after_science(s,task,'135');schedule_xml_after_science(s,task,'135')
  row=s.get(ScienceCandidate,(cid,KEY));assert row.state=='xml_queued'
  items=list(s.scalars(select(CaptureItem).where(CaptureItem.key==KEY)));assert len(items)==1
  batch=s.get(CaptureBatch,items[0].batch_id);assert batch.mode=='keys' and json.loads(batch.options)['next_run']>=time.time()+290
  assert 'pfx' not in batch.options
  archive_xml(s,app.storage,cid,RAW,'11444777000161')


def test_xml_followup_requires_uf_and_never_schedules_after_failed_science(env):
 test_default_disabled_then_opt_in_queues_once(env)
 app,c,h,cid=env
 from app.fiscal_channels import FiscalTask
 from app.manifest_automation import schedule_xml_after_science
 from app.distribution import CaptureItem
 c.post('/api/fiscal/channels/manifest/resume',headers=h,json={'company':cid})
 with app.session_factory.begin() as s:
  task=s.scalar(select(FiscalTask).where(FiscalTask.service=='manifest'))
  schedule_xml_after_science(s,task,'573');assert not s.scalar(select(CaptureItem))
  opts=json.loads(task.payload);opts['uf']='';task.payload=json.dumps(opts)
  schedule_xml_after_science(s,task,'135');assert not s.scalar(select(CaptureItem));assert s.get(ScienceCandidate,(cid,KEY)).state=='action_required'

def test_revoked_automation_never_dispatches_pending_event(env):
 app,c,h,cid=env;m,auth,dev,poll,original=received(env)
 from app.fiscal_channels import FiscalTask
 c.put('/api/fiscal/channels/manifest',headers=h,json={'company':cid,'enabled':True,'consent':True,'automatic':True,'automatic_consent':True})
 with app.session_factory.begin() as s:
  task=FiscalTask(company_id=cid,device_id=dev,service='manifest',payload=json.dumps({'key':KEY,'automatic':True,'consent':True}));s.add(task);s.flush();taskid=task.id
  s.get(ScienceCandidate,(cid,KEY)).state='queued'
 c.put('/api/fiscal/channels/manifest',headers=h,json={'company':cid,'enabled':True,'consent':True,'automatic':False})
 result=m.post('/api/agent/poll',headers=auth,json={'certificates':poll['certificates'],'capabilities':['manifest_science']})
 assert result.status_code==200 and result.json['task'] is None
 with app.session_factory() as s:assert s.get(FiscalTask,taskid).state=='cancelled'

def test_backfill_only_preserved_summaries_never_restores_deleted_files(env):
 app,c,h,cid=env;received(env)
 from app.manifest_automation import backfill_proofs
 with app.session_factory.begin() as s:
  candidate=s.get(ScienceCandidate,(cid,KEY));s.delete(candidate);s.flush()
  backfill_proofs(app,s,s.get(Company,cid));s.flush();assert proven_summary(app,s,s.get(Company,cid),KEY)
  s.delete(s.get(ScienceCandidate,(cid,KEY)));s.delete(s.scalar(select(FiscalArchive).where(FiscalArchive.key==KEY)));s.flush()
  backfill_proofs(app,s,s.get(Company,cid));assert s.get(ScienceCandidate,(cid,KEY)) is None
  assert not s.scalar(select(FiscalArchive).where(FiscalArchive.key==KEY))
