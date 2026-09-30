from test_fiscal_channels import ready
from test_app import env
from app.fiscal_channels import decode_fiscal_response
import base64,gzip,json,pytest

def payload(entries=None,status='DOCUMENTOS_LOCALIZADOS',environment='PRODUCAO'):
    xml=b'<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse"><infNFSe Id="NFS123"/></NFSe>'
    return json.dumps({'StatusProcessamento':status,'TipoAmbiente':environment,'LoteDFe':entries if entries is not None else [{'NSU':9223372036854775807,'ArquivoXml':base64.b64encode(gzip.compress(xml)).decode(),'TipoDocumento':'NFSE','ChaveAcesso':'123'}]}).encode()

def test_nfse_documented_contract_and_int64():
    status,last,files=decode_fiscal_response('nfse',payload())
    assert status=='138' and last=='9223372036854775807' and len(files)==1
    status,last,files=decode_fiscal_response('nfse',payload([],status='NENHUM_DOCUMENTO_LOCALIZADO'))
    assert status=='137' and last=='' and files==[]

def test_nfse_rejects_wrong_environment_and_rejection():
    for raw in [payload(environment='HOMOLOGACAO'),payload([],status='REJEICAO'),payload([],status='DOCUMENTOS_LOCALIZADOS')]:
        with pytest.raises(ValueError):decode_fiscal_response('nfse',raw)

def nfse_poll(machine,auth):
    return machine.post('/api/agent/poll',headers=auth,json={'certificates':[],'capabilities':['nfse_distribution']}).json['task']

def test_nfse_queue_result_replay_empty_and_independent_cursor(env):
    from app.fiscal_channels import FiscalChannel,FiscalTask
    app,c,h,cid=env;m,auth,_=ready(env,'nfse')
    assert c.post('/api/fiscal/channels/nfse/queue',json={'company':cid},headers=h).status_code==200
    task=nfse_poll(m,auth);assert task['service']=='nfse'
    body={'id':task['id'],'service':'nfse','ok':True,'http_status':200,'response':base64.b64encode(payload()).decode()}
    assert m.post('/api/agent/fiscal-result',headers=auth,json=body).status_code==200
    assert m.post('/api/agent/fiscal-result',headers=auth,json=body).json['replayed']
    with app.session_factory.begin() as s:
        state=s.get(FiscalChannel,(cid,'nfse'));assert state.nsu=='9223372036854775807';state.next_allowed=0
        assert s.get(FiscalChannel,(cid,'cte')) is None
    task=nfse_poll(m,auth)
    body.update(id=task['id'],http_status=404,response=base64.b64encode(payload([],status='NENHUM_DOCUMENTO_LOCALIZADO')).decode())
    assert m.post('/api/agent/fiscal-result',headers=auth,json=body).status_code==200
    with app.session_factory() as s:assert s.get(FiscalChannel,(cid,'nfse')).nsu=='9223372036854775807'
    assert len(c.get('/api/fiscal/files?company='+cid+'&service=nfse').json['items'])==1

def test_nfse_no_progress_pauses_without_skipping_nsus(env):
    from app.fiscal_channels import FiscalChannel
    app,c,h,cid=env;m,auth,_=ready(env,'nfse')
    with app.session_factory.begin() as s:s.get(FiscalChannel,(cid,'nfse')).nsu='9223372036854775807'
    task=nfse_poll(m,auth)
    body={'id':task['id'],'service':'nfse','ok':True,'http_status':200,'response':base64.b64encode(payload()).decode()}
    assert m.post('/api/agent/fiscal-result',headers=auth,json=body).status_code==200
    with app.session_factory() as s:
        state=s.get(FiscalChannel,(cid,'nfse'));assert state.nsu=='9223372036854775807' and state.enabled=='0'

def test_nfse_rejects_duplicates_bool_and_missing_xml():
    data=json.loads(payload());entry=data['LoteDFe'][0]
    for entries in [[dict(entry,NSU=True)],[entry,entry],[dict(entry,ArquivoXml=None)]]:
        with pytest.raises(ValueError):decode_fiscal_response('nfse',payload(entries))
