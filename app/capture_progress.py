"""Observed capture progress; never changes dispatch or fiscal cooldowns."""
import json
import math
import time
from statistics import median

from .db import Setting
from .agent_bridge import device_allowed


def record_sample(session,batch,task,status,now=None):
    now=time.time() if now is None else now
    if batch.mode!='keys' or task.state!='completed' or status not in ('137','138'):return
    duration=now-task.created
    if duration<=0:return
    key='capture-progress:'+batch.id
    saved=session.get(Setting,key)
    samples=json.loads(saved.value) if saved else []
    if any(item['task']==task.id for item in samples):return
    samples=(samples+[{'task':task.id,'seconds':duration,'at':now}])[-20:]
    if saved:saved.value=json.dumps(samples)
    else:session.add(Setting(key=key,value=json.dumps(samples)))


def build_progress(batch,counts,device,state,samples=(),recent_responses=(),last_task=None,linked=True,now=None):
    now=time.time() if now is None else now
    online=bool(linked and device and device.revoked=='0' and device.token_hash and device.expires>now and device.last_seen>now-45)
    succeeded=counts.get('completed',0);failed=counts.get('failed',0)
    total=batch.total if batch.mode=='keys' and batch.total>0 else None
    done=succeeded+failed if total is not None else None
    wait=max(0,math.ceil((state.next_allowed if state else 0)-now))
    status=batch.state
    expired=bool(last_task and last_task.state in ('pending','running') and last_task.expires<=now)
    if batch.state=='active':
        status='waiting_sefaz' if wait else 'offline' if not online else 'needs_attention' if expired else 'running' if counts.get('running') or (last_task and last_task.state=='running') else 'queued'
    sample_seconds=[s['seconds'] for s in samples if isinstance(s.get('seconds'),(float,int)) and s['seconds']>0 and s.get('at',0)>now-3600]
    eta=None;reason='unknown_total' if total is None else 'insufficient_samples'
    remaining=max(0,total-done) if total is not None else None
    if total is not None:
        if remaining==0:eta=0;reason='finished'
        elif batch.state!='active':reason='not_active'
        elif wait:reason='waiting_sefaz'
        elif not online:reason='offline'
        elif expired:reason='needs_attention'
        elif len(sample_seconds)>=3:
            used=len([at for at in recent_responses if at>now-3600])
            if remaining>max(0,20-used):reason='future_sefaz_limit'
            else:eta=math.ceil(median(sample_seconds)*remaining);reason='recent_samples'
    return {'determinate':total is not None,'total':total,'done':done,'succeeded':succeeded,'failed':failed,
            'pending':counts.get('pending',0),'running':counts.get('running',0),
            'percent':min(100,round(done*100/total,1)) if total else None,
            'elapsed_seconds':max(0,int(now-batch.created)), 'eta_seconds':eta,'eta_reason':reason,
            'sample_size':len(sample_seconds),'wait_seconds':wait,
            # Earliest allowed poll, not a promise that the connector will poll.
            'next_poll_at':max(now,state.next_allowed if state else 0) if batch.state=='active' and online and not expired else None,
            'online':online,'status':status}


def capture_progress(session,batch,counts,state,device):
    from sqlalchemy import select
    from .distribution import DistributionTask,CaptureDispatch
    saved=session.get(Setting,'capture-progress:'+batch.id)
    quota=session.get(Setting,'distribution:key-responses:'+batch.company_id)
    last=session.scalar(select(DistributionTask).join(CaptureDispatch,CaptureDispatch.task_id==DistributionTask.id).where(CaptureDispatch.batch_id==batch.id,DistributionTask.company_id==batch.company_id).order_by(DistributionTask.created.desc()).limit(1))
    return build_progress(batch,counts,device,state,json.loads(saved.value) if saved else [],json.loads(quota.value) if quota else [],last,device_allowed(session,device,batch.company_id))
