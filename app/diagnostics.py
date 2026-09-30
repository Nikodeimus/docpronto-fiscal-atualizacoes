"""Company-scoped observations; opening diagnostics never contacts SEFAZ."""
import json
import math
import time
from datetime import datetime

from flask import g, jsonify, request
from sqlalchemy import or_, select

from .db import Setting
from .agent_bridge import AgentDevice,AgentCompanyLink,CompanyCertificate,CertificateTask,device_allowed
from .client_capture import StoredA1
from .distribution import DistributionState,DistributionTask


def validity(value,now):
    try:
        date=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if date.tzinfo is None:return 'unknown'
        return 'expired' if date.timestamp()<=now else 'valid'
    except (ValueError,TypeError,OverflowError):return 'unknown'


def register_diagnostics(app,company):
    @app.get('/api/diagnostics')
    def diagnostics():
        cid=request.args.get('company');company(cid,admin=True);now=time.time()
        setting=g.s.get(Setting,'capture-registration:'+cid)
        options=json.loads(setting.value) if setting else {}
        chosen=g.s.get(CompanyCertificate,cid)
        device_id=options.get('device') or (chosen.device_id if chosen else None)
        device=g.s.get(AgentDevice,device_id) if device_id else None
        if not device_id:
            linked=select(AgentCompanyLink.device_id).where(AgentCompanyLink.company_id==cid)
            device=g.s.scalar(select(AgentDevice).where(or_(AgentDevice.company_id==cid,AgentDevice.id.in_(linked)),AgentDevice.revoked=='0').order_by(AgentDevice.last_seen.desc()).limit(1))
        allowed=device_allowed(g.s,device,cid)
        if not allowed:device=None
        online=bool(device and device.token_hash and device.expires>now and device.last_seen>now-45)
        capabilities=g.s.get(Setting,'agentcaps:'+device.id) if device else None
        caps=json.loads(capabilities.value) if capabilities else []
        connector={'id':device.id if device else None,'name':device.name if device else None,
                   'configured':bool(device_id or device),'linked':bool(allowed),'online':online,
                   'last_seen':device.last_seen if device else None,
                   'session_expires_at':device.expires if device else None,'distribution_capable':'distribution' in caps}
        mode=options.get('certificate','installed')
        certificate={'mode':mode,'selected':False,'available':False,'validity':'unknown','valid_until':None,
                     'has_private_key':None,'thumbprint':None,'store':None,'last_test':None}
        if mode=='a1':
            saved=g.s.get(StoredA1,cid)
            if saved:
                metadata=json.loads(saved.metadata_json)
                certificate.update(selected=True,valid_until=metadata.get('valid_until'),has_private_key=True)
                certificate['validity']=validity(certificate['valid_until'],now)
                certificate['available']=bool(online and 'stored_a1' in caps and certificate['validity']=='valid')
        elif chosen and device and chosen.device_id==device.id:
            cert=next((v for v in json.loads(device.certificates) if v.get('thumbprint')==chosen.thumbprint and v.get('store')==chosen.store),None)
            certificate.update(selected=True,thumbprint=chosen.thumbprint,store=chosen.store)
            if cert:
                certificate.update(has_private_key=cert.get('has_private_key') is True,valid_until=cert.get('valid_until'))
                certificate['validity']=validity(certificate['valid_until'],now)
                certificate['available']=bool(online and certificate['has_private_key'] and certificate['validity']=='valid')
            test=g.s.scalar(select(CertificateTask).where(CertificateTask.device_id==device.id,CertificateTask.thumbprint==chosen.thumbprint,CertificateTask.store==chosen.store).order_by(CertificateTask.created.desc()).limit(1))
            if test:certificate['last_test']={'state':'expired' if test.state in ('pending','running') and test.expires<=now else test.state,'requested_at':test.created}
        state=g.s.get(DistributionState,cid)
        task=g.s.scalar(select(DistributionTask).where(DistributionTask.company_id==cid).order_by(DistributionTask.created.desc()).limit(1))
        saved_response=g.s.get(Setting,'distribution:last-response:'+cid)
        response=json.loads(saved_response.value) if saved_response else None
        wait=max(0,math.ceil((state.next_allowed if state else 0)-now))
        task_state=('expired' if task and task.state in ('pending','running') and task.expires<=now else task.state) if task else None
        status='waiting' if wait else 'last_attempt_failed' if task_state in ('failed','expired') else 'query_pending' if task_state in ('pending','running') else 'last_response' if response else 'unknown'
        sefaz={'status':status,'live_test':False,'last_response':response,
               'last_attempt':{'id':task.id,'state':task_state,'requested_at':task.created,'message':task.message} if task else None,
               'wait_seconds':wait,'next_allowed':state.next_allowed if state else 0,
               'note':'Estado observado no conector e nas consultas anteriores. Nenhuma conexão nova com a SEFAZ foi testada.'}
        actions=[]
        if not online:actions.append('Abra o conector vinculado e confira o pareamento e a internet.')
        elif not connector['distribution_capable']:actions.append('Atualize o conector para a versão com consulta de distribuição.')
        if not certificate['selected']:actions.append('Selecione o certificado deste cadastro em Certificados.')
        elif certificate['validity']=='expired':actions.append('Troque o certificado vencido antes de consultar.')
        elif certificate['validity']=='unknown':actions.append('Confira a validade do certificado; o conector não informou uma data válida.')
        if certificate['selected'] and certificate['has_private_key'] is False:actions.append('Confira o dispositivo, PIN e acesso à chave privada.')
        if wait:actions.append('Aguarde o intervalo informado pela SEFAZ; não reinicie o NSU.')
        if status=='last_attempt_failed':actions.append('Confira a mensagem da última tentativa antes de repetir a consulta.')
        return jsonify(company=cid,checked_at=now,connector=connector,certificate=certificate,sefaz=sefaz,actions=actions)
