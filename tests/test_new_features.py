import io,zipfile,json,base64,gzip
import pytest
from test_app import env,RAW
from test_agent_bridge import pair
from app.distribution import decode_response

def test_export_all_and_pdf(env):
    app,c,h,cid=env
    r=c.post('/api/import',data={'company':cid,'file':(io.BytesIO(RAW),'nota.xml')},headers=h)
    did=r.json['results'][0]['id']
    r=c.post('/api/export/all',json={'company':cid},headers=h)
    assert r.status_code==200
    with zipfile.ZipFile(io.BytesIO(r.data)) as z:
        xml=next(n for n in z.namelist() if n.endswith('.xml'));assert z.read(xml)==RAW
        assert len(z.read('manifesto.jsonl').splitlines())==1
    r=c.get('/api/documents/'+did+'/download/pdf');assert r.status_code==200 and r.data.startswith(b'%PDF')
    assert c.post('/api/export/all',json={'company':'other'},headers=h).status_code==400

def test_delete_all_requires_exact_confirmed_snapshot(env):
    app,c,h,cid=env
    for _ in range(2):
        assert c.post('/api/import',data={'company':cid,'file':(io.BytesIO(RAW),'nota.xml')},headers=h).status_code==200
    preview=c.post('/api/documents/delete-preview',json={'company':cid,'scope':'all'},headers=h)
    assert preview.status_code==200,preview.json
    assert c.post('/api/my-data/deletion-password',json={'login_password':'long-password-test','new_password':'A1b2'},headers=h).status_code==200
    body={'company':cid,'token':preview.json['token'],'deletion_password':'wrong','confirm_count':preview.json['count']}
    assert c.post('/api/documents/delete-batch',json=body,headers=h).status_code==400
    body['deletion_password']='A1b2'
    removed=c.post('/api/documents/delete-batch',json=body,headers=h)
    assert removed.status_code==200 and removed.json['deleted']==1,removed.json
    assert c.get('/api/documents?company='+cid).json['total']==0
    assert c.post('/api/documents/delete-batch',json=body,headers=h).status_code==400

def response(status='138',files=None,last='000000000000001',maximum='000000000000001'):
    docs=''.join('<docZip NSU="000000000000001" schema="procNFe_v4.00.xsd">'+base64.b64encode(gzip.compress(x)).decode()+'</docZip>' for x in files or [])
    return f'<retDistDFeInt xmlns="http://www.portalfiscal.inf.br/nfe"><cStat>{status}</cStat><xMotivo>Teste</xMotivo><ultNSU>{last}</ultNSU><maxNSU>{maximum}</maxNSU><loteDistDFeInt>{docs}</loteDistDFeInt></retDistDFeInt>'.encode()

def test_distribution_import_and_cooldown(env):
    app,c,h,cid=env;m,auth,id,_=pair(app,c,h,cid)
    cert={'thumbprint':'A'*40,'store':'CurrentUser','has_private_key':True}
    m.post('/api/agent/poll',json={'certificates':[cert],'capabilities':['distribution']},headers=auth)
    req={'company':cid,'device':id,**cert,'uf':'35'}
    r=c.post('/api/distribution',json=req,headers=h);assert r.status_code==200,r.json
    assert c.post('/api/distribution',json=req,headers=h).status_code==400
    task=m.post('/api/agent/poll',json={'certificates':[cert],'capabilities':['distribution']},headers=auth).json['task'];assert task['kind']=='distribution'
    raw=response(files=[RAW]);body={'id':task['id'],'ok':True,'response':base64.b64encode(raw).decode()}
    r=m.post('/api/agent/distribution-result',json=body,headers=auth);assert r.status_code==200,r.json
    assert c.get('/api/distribution?company='+cid).json['items'][0]['state']=='completed'
    assert c.get('/api/documents?company='+cid).json['total']==1
    assert m.post('/api/agent/distribution-result',json=body,headers=auth).status_code==200
    assert c.post('/api/distribution',json=req,headers=h).status_code==400

def test_distribution_rejects_xxe_and_oversized():
    with pytest.raises(ValueError):decode_response(b'<!DOCTYPE x><x/>')
    with pytest.raises(ValueError):decode_response(response(files=[b'x'*(8*1024*1024+1)]))
    assert decode_response(response(status='656'))[0]=='656'

def test_export_crosses_500_and_excludes_unreviewed(env):
    from app.db import Document
    app,c,h,cid=env
    with app.session_factory() as s:
        path=app.storage.save(cid,'shared',RAW,'xml')
        for i in range(501):s.add(Document(company_id=cid,key=str(i).zfill(44),xml=path,source='IMPORTED',status='concluido'))
        s.add(Document(company_id=cid,key='9'*44,xml=path,source='RECONSTRUCTED',status='revisao'))
        s.commit()
    r=c.post('/api/export/all',json={'company':cid,'include_reconstructed':True},headers=h)
    assert r.status_code==200
    with zipfile.ZipFile(io.BytesIO(r.data)) as z:assert sum(n.endswith('.xml') for n in z.namelist())==501

def test_summary_auto_continue_cancel_and_old_agent(env):
    from app.db import Document
    from app.distribution import DistributionTask
    from sqlalchemy import select
    app,c,h,cid=env;m,auth,id,_=pair(app,c,h,cid)
    cert={'thumbprint':'B'*40,'store':'CurrentUser','has_private_key':True}
    req={'company':cid,'device':id,**cert,'uf':'35'}
    assert m.post('/api/agent/poll',json={'certificates':[cert],'capabilities':None},headers=auth).status_code==200
    assert c.post('/api/distribution',json=req,headers=h).status_code==400
    m.post('/api/agent/poll',json={'certificates':[cert],'capabilities':['distribution']},headers=auth)
    assert c.post('/api/distribution',json=req,headers=h).status_code==200
    task=m.post('/api/agent/poll',json={'certificates':[cert],'capabilities':['distribution']},headers=auth).json['task']
    from lxml import etree
    key=etree.fromstring(RAW).find('.//{http://www.portalfiscal.inf.br/nfe}infNFe').get('Id')[3:]
    summary=f'<resNFe xmlns="http://www.portalfiscal.inf.br/nfe"><chNFe>{key}</chNFe></resNFe>'.encode()
    raw=response(files=[summary],maximum='000000000000002')
    assert m.post('/api/agent/distribution-result',json={'id':task['id'],'ok':True,'response':base64.b64encode(raw).decode()},headers=auth).status_code==200
    with app.session_factory() as s:
        doc=s.scalar(select(Document));assert doc.source=='RESUMO' and doc.xml is None
        assert len(list(s.scalars(select(DistributionTask))))==2
    assert c.post('/api/distribution/cancel',json={'company':cid},headers=h).status_code==200
    assert c.get('/api/distribution?company='+cid).json['items'][0]['state']=='cancelled'

@pytest.mark.parametrize('last,maximum',[('2','3'),('000000000000003','000000000000002')])
def test_distribution_bad_nsu(last,maximum):
    with pytest.raises(ValueError):decode_response(response(last=last,maximum=maximum))

def test_bulk_capture_thousand_pause_cooldown_and_isolation(env):
    from app.distribution import DistributionState
    app,c,h,cid=env;m,auth,device,_=pair(app,c,h,cid)
    cert={'thumbprint':'A'*40,'store':'CurrentUser','has_private_key':True}
    poll={'certificates':[cert],'capabilities':['distribution']}
    m.post('/api/agent/poll',json=poll,headers=auth)
    from lxml import etree
    original=etree.fromstring(RAW).find('.//{http://www.portalfiscal.inf.br/nfe}infNFe').get('Id')[3:]
    keys=[]
    for i in range(1001):
        stem=original[:25]+str(i).zfill(9)+original[34:43]
        total=sum(int(n)*w for n,w in zip(reversed(stem),([2,3,4,5,6,7,8,9]*6)))
        dv=11-total%11
        keys.append(stem+str(0 if dv>=10 else dv))
    req={'company':cid,'device':device,**cert,'uf':'35','batch':True,'keys':'\n'.join(keys+[keys[0]])}
    r=c.post('/api/distribution',json=req,headers=h)
    assert r.status_code==200,r.json
    assert r.json['total']==1001 and r.json['duplicates']==1
    bid=r.json['id'];url='/api/capture/batches/'+bid+'/control'
    assert c.post(url,json={'company':'other','action':'pause'},headers=h).status_code==400
    assert c.post(url,json={'company':cid,'action':'pause'},headers=h).status_code==200
    assert not m.post('/api/agent/poll',json=poll,headers=auth).json.get('task')
    c.post(url,json={'company':cid,'action':'resume'},headers=h)
    task=m.post('/api/agent/poll',json=poll,headers=auth).json['task']
    assert task['key'] in keys
    assert not m.post('/api/agent/poll',json=poll,headers=auth).json.get('task')
    result={'id':task['id'],'ok':True,'response':base64.b64encode(response(status='656',last='000000000000003',maximum='000000000000000')).decode()}
    assert m.post('/api/agent/distribution-result',json=result,headers=auth).status_code==200
    listing=c.get('/api/capture/batches?company='+cid).json['items'][0]
    assert listing['counts']['pending']==1001
    assert not m.post('/api/agent/poll',json=poll,headers=auth).json.get('task')
    c.post(url,json={'company':cid,'action':'cancel'},headers=h)
    assert c.post(url,json={'company':cid,'action':'resume'},headers=h).status_code==400

def test_icms61_import_keeps_original_xml(env):
    from test_fiscal import mono61_xml
    app,c,h,cid=env
    raw=mono61_xml()
    r=c.post('/api/import',data={'company':cid,'file':(io.BytesIO(raw),'combustivel.xml')},headers=h)
    assert r.status_code==200,r.json
    did=r.json['results'][0]['id']
    assert c.get('/api/documents/'+did+'/download/xml').data==raw


def test_rejection_preserves_reason_with_inverted_nsu():
    result=decode_response(response(status='656',last='000000000000003',maximum='000000000000000'))
    assert result==('656','000000000000003','000000000000000','Teste',[])

def test_success_inverted_nsu_has_diagnostic():
    with pytest.raises(ValueError,match='SEFAZ 138: Teste.*000000000000003.*000000000000000'):
        decode_response(response(last='000000000000003',maximum='000000000000000'))

@pytest.mark.parametrize('archive_only',[False,True])
def test_distribution_preserves_icms_warning_and_continues(env,archive_only):
    from lxml import etree
    from app.distribution import DistributionTask,DistributionState
    app,c,h,cid=env;m,auth,device,_=pair(app,c,h,cid)
    root=etree.fromstring(RAW)
    icms=root.find('.//{http://www.portalfiscal.inf.br/nfe}ICMS')
    icms.getparent().remove(icms)
    problematic=etree.tostring(root)
    cert={'thumbprint':'A'*40,'store':'CurrentUser','has_private_key':True}
    m.post('/api/agent/poll',json={'certificates':[cert],'capabilities':['distribution']},headers=auth)
    r=c.post('/api/distribution',json={'company':cid,'device':device,**cert,'uf':'35'},headers=h)
    assert r.status_code==200
    task=m.post('/api/agent/poll',json={'certificates':[cert],'capabilities':['distribution']},headers=auth).json['task']
    if archive_only:
        with app.session_factory() as s:
            row=s.get(DistributionTask,task['id']);payload=json.loads(row.payload);payload['archive']=True;row.payload=json.dumps(payload);s.commit()
    body={'id':task['id'],'ok':True,'response':base64.b64encode(response(files=[problematic,RAW])).decode()}
    assert m.post('/api/agent/distribution-result',json=body,headers=auth).status_code==200
    result=c.get('/api/distribution?company='+cid).json
    assert result['items'][0]['state']=='completed'
    assert '1 arquivo(s) preservado(s) com aviso' in result['items'][0]['message']
    assert result['nsu']=='000000000000001'
    rows=c.get('/api/history?company='+cid).json['items']
    assert len(rows)==2
    warned=next(x for x in rows if x['warning'])
    assert 'modalidade ICMS' in warned['warning']
    assert c.get('/api/history/'+warned['id']+'/xml').data==problematic
    assert c.get('/api/history/keys.txt?company='+cid).data==(warned['key']+'\r\n').encode()
    docs=c.get('/api/documents?company='+cid).json
    assert docs['total']==(0 if archive_only else 1)
    assert m.post('/api/agent/distribution-result',json=body,headers=auth).status_code==200
    assert c.get('/api/history?company='+cid).json['total']==2
