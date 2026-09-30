"""Read-only local capture prerequisites. No SEFAZ request or fiscal event."""
import json
import time
from flask import g,jsonify,request
from sqlalchemy import select
from .agent_bridge import AgentDevice,device_allowed
from .db import Setting
from .diagnostics import validity
from .distribution import DistributionState,DistributionTask

UF_CODES=set('11 12 13 14 15 16 17 21 22 23 24 25 26 27 28 29 31 32 33 35 41 42 43 50 51 52 53'.split())


def _list(value):
    try:
        data=json.loads(value)
        return data if isinstance(data,list) else []
    except (ValueError,TypeError):return []


def inspect_capture(session,company_id,options,now=None):
    now=time.time() if now is None else now
    blockers=[];warnings=[]
    def block(code,message):blockers.append({'code':code,'message':message})
    device=session.get(AgentDevice,options.get('device')) if options.get('device') else None
    linked=device_allowed(session,device,company_id)
    if not linked:
        block('connector_unlinked','Selecione um computador vinculado a esta empresa.')
        device=None
    if device:
        if not device.token_hash or device.expires<=now or device.last_seen<=now-45:
            block('connector_offline','Abra o conector e aguarde sua conexão antes de consultar.')
        caps=session.get(Setting,'agentcaps:'+device.id)
        if 'distribution' not in _list(caps.value if caps else ''):
            block('connector_unsupported','Atualize o conector para consultar notas.')
        inventory=_list(device.certificates)
        cert=next((item for item in inventory if isinstance(item,dict) and
            item.get('thumbprint')==options.get('thumbprint') and item.get('store')==options.get('store')),None)
        if not cert or not options.get('thumbprint') or not options.get('store'):
            block('certificate_unavailable','Selecione um certificado disponível neste computador.')
        else:
            if cert.get('has_private_key') is not True:
                block('certificate_private_key','O certificado não informou chave privada disponível.')
            state=validity(cert.get('valid_until'),now)
            if state=='expired':block('certificate_expired','O certificado venceu. Selecione um certificado válido.')
            elif state=='unknown':warnings.append({'code':'certificate_validity_unknown','message':'Validade não informada; o conector deverá validar o certificado ao consultar.'})
    if str(options.get('uf','')) not in UF_CODES:
        block('uf_invalid','Selecione a UF da empresa.')
    fiscal=session.get(DistributionState,company_id)
    next_allowed=fiscal.next_allowed if fiscal else 0
    if next_allowed>now:
        block('sefaz_cooldown','Aguarde o intervalo da SEFAZ antes de iniciar outra consulta.')
    running=session.scalar(select(DistributionTask.id).where(DistributionTask.company_id==company_id,
        DistributionTask.state.in_(('pending','running')),DistributionTask.expires>now).limit(1))
    if running:block('query_in_progress','Já há uma consulta em andamento para esta empresa.')
    warnings.append({'code':'network_not_tested','message':'DNS, internet e SEFAZ não foram testados ao vivo. A consulta confirma a comunicação.'})
    return {'company':company_id,'ready':not blockers,'blockers':blockers,'warnings':warnings,
            'checked_at':now,'next_allowed':next_allowed,'live_test':False}


def register_capture_preflight(app,company):
    @app.get('/api/capture/preflight')
    def capture_preflight():
        cid=request.args.get('company');company(cid,admin=True)
        return jsonify(inspect_capture(g.s,cid,request.args))
