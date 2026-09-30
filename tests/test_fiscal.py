from pathlib import Path
from copy import deepcopy
import pytest
from lxml import etree as E
from app.fiscal import *
RAW=(Path(__file__).parent/'fixtures/reference.xml').read_bytes()
KEY='35251211222333000181551010000269501925017728'
DEST='11444777000161';EMIT='11222333000181'
def mutated(path,value):
    r=E.fromstring(RAW);r.find(path,N).text=value;return E.tostring(r)
def test_reference_fields_and_reduction():
    d=parse_xml(RAW,DEST)
    assert d['flow']=='entrada' and d['number']=='26950' and d['series']=='101'
    assert d['totals']['vBC']=='30663.60' and d['totals']['vICMS']=='5519.45'
    assert d['items'][0]['icms']['pRedBC']=='77.7800'
    assert d['items'][0]['taxes']['PIS']['PISNT']['CST']=='06'
    assert d['schema_status']=='NAO_VERIFICADO' and d['signature_validation']=='NAO_VERIFICADA'
def test_identity_not_template():
    with pytest.raises(FiscalError):parse_xml(mutated('n:NFe/n:infNFe/n:emit/n:CNPJ','12345678000195'),DEST)
def test_company_operation():
    assert parse_xml(RAW,EMIT)['flow']=='saida'
    with pytest.raises(FiscalError,match='Operação'):parse_xml(RAW,DEST,'saida')
    with pytest.raises(FiscalError,match='não participa'):parse_xml(RAW,'28988409000187')
def test_key_dv_and_fields():
    with pytest.raises(FiscalError):validate_key(KEY[:-1]+'0')
    assert validate_key(' '.join(KEY[i:i+4] for i in range(0,44,4)))==KEY
    with pytest.raises(FiscalError,match='série'):parse_xml(mutated('n:NFe/n:infNFe/n:ide/n:serie','1'))
def test_incomplete():
    r=E.fromstring(RAW);i=r.find('n:NFe/n:infNFe',N);i.remove(i.find('n:emit',N))
    with pytest.raises(FiscalError,match='ide/emit/dest'):parse_xml(E.tostring(r))
def test_xxe_and_duplicate():
    with pytest.raises(FiscalError):parse_xml(b'<!DOCTYPE x [<!ENTITY a SYSTEM "file:///etc/passwd">]><x>&a;</x>')
    r=E.fromstring(RAW);r.find('n:NFe',N).append(deepcopy(r.find('n:NFe/n:infNFe',N)))
    with pytest.raises(FiscalError,match='exatamente um'):parse_xml(E.tostring(r))
def test_zero_preserved():
    d=parse_xml(RAW)
    assert d['totals']['vPIS']=='0.00'
    assert d['items'][0]['taxes']['IPI']['IPITrib']['vBC']=='0.00'
def test_sum_error():
    with pytest.raises(FiscalError,match='Soma'):parse_xml(mutated('n:NFe/n:infNFe/n:total/n:ICMSTot/n:vICMS','0.00'))
def test_rebuild_no_signature_protocol():
    inf=E.fromstring(RAW).find('n:NFe/n:infNFe',N);groups=flatten(inf)
    groups['det']['@nItem']='1'
    raw,data=build_reconstructed({'key':KEY,'groups':groups},DEST)
    assert b'<Signature' not in raw and b'<protNFe' not in raw
    assert data['issuer_document']==EMIT and data['totals']['vNF']=='138000.00'
def test_csv_evidence():
    r=reconciliation_csv((Path(__file__).parent/'fixtures/rejections.csv').read_bytes())
    assert len(r)==2 and all(x['category']=='ESTRUTURA_INCOMPLETA' for x in r)
def test_audit_total_nfe():
    with pytest.raises(FiscalError,match='vNF'):parse_xml(mutated('n:NFe/n:infNFe/n:total/n:ICMSTot/n:vNF','999999999.00'))
def test_audit_missing_icms():
    r=E.fromstring(RAW)
    for tax in r.findall('n:NFe/n:infNFe/n:det/n:imposto',N):tax.remove(tax.find('n:ICMS',N))
    with pytest.raises(FiscalError,match='ICMS ausente'):parse_xml(E.tostring(r))
def test_audit_invalid_contribution_family():
    r=E.fromstring(RAW);pis=r.find('n:NFe/n:infNFe/n:det/n:imposto/n:PIS',N)
    pis[0].tag='{'+NS+'}PISINVALIDO'
    with pytest.raises(FiscalError,match='PIS inválida'):parse_xml(E.tostring(r))
def test_audit_negative_contribution():
    r=E.fromstring(RAW);pis=r.find('n:NFe/n:infNFe/n:det/n:imposto/n:PIS',N);pis.clear()
    node=E.SubElement(pis,'{'+NS+'}PISAliq')
    for k,v in {'CST':'01','vBC':'-100','pPIS':'-5','vPIS':'-20'}.items():E.SubElement(node,'{'+NS+'}'+k).text=v
    with pytest.raises(FiscalError,match='numérico'):parse_xml(E.tostring(r))
def test_reconstructed_uses_schema_order_not_form_order():
    inf=E.fromstring(RAW).find('n:NFe/n:infNFe',N);groups=flatten(inf)
    groups['det']['@nItem']='1'
    def reverse(value):
        if isinstance(value,dict):return {k:reverse(v) for k,v in reversed(list(value.items()))}
        if isinstance(value,list):return [reverse(v) for v in value]
        return value
    raw,_=build_reconstructed({'key':KEY,'groups':reverse(groups)},DEST)
    root=E.fromstring(raw)
    emit=root.find('n:infNFe/n:emit',N);ide=root.find('n:infNFe/n:ide',N);tax=root.find('n:infNFe/n:det/n:imposto/n:ICMS/n:ICMS20',N)
    assert [E.QName(x).localname for x in emit][:3]==['CNPJ','xNome','xFant']
    assert [E.QName(x).localname for x in ide][:4]==['cUF','cNF','natOp','mod']
    assert [E.QName(x).localname for x in tax][:7]==['orig','CST','modBC','pRedBC','vBC','pICMS','vICMS']

def mono61_xml():
    r=E.fromstring(RAW)
    for group in r.findall('.//n:det/n:imposto/n:ICMS',N):
        for child in list(group):group.remove(child)
        node=E.SubElement(group,'{'+N['n']+'}ICMS61')
        for k,v in [('orig','0'),('CST','61'),('qBCMonoRet','10.0000'),('adRemICMSRet','1.3721'),('vICMSMonoRet','13.72')]:
            E.SubElement(node,'{'+N['n']+'}'+k).text=v
    for k in ['vBC','vICMS']:
        r.find('.//n:ICMSTot/n:'+k,N).text='0.00'
    return E.tostring(r)

def test_icms61_preserves_monophase_fields():
    data=parse_xml(mono61_xml(),DEST)
    assert data['items'][0]['taxes']['ICMS']['ICMS61']=={
        'orig':'0','CST':'61','qBCMonoRet':'10.0000','adRemICMSRet':'1.3721','vICMSMonoRet':'13.72'}
    assert data['totals']['vICMS']=='0.00'

@pytest.mark.parametrize('field,value',[('CST','60'),('adRemICMSRet','-1'),('adRemICMSRet','NaN'),('qBCMonoRet','-1')])
def test_icms61_rejects_invalid_values(field,value):
    r=E.fromstring(mono61_xml());r.find('.//n:ICMS61/n:'+field,N).text=value
    with pytest.raises(FiscalError):parse_xml(E.tostring(r),DEST)


def mono_xml(kind):
    r=E.fromstring(mono61_xml());node=r.find('.//n:ICMS61',N);node.tag='{'+NS+'}'+kind
    node.clear()
    fields={'orig':'0','CST':kind[-2:],'qBCMono':'10.0000','adRemICMS':'1.00','vICMSMono':'10.00'}
    if kind=='ICMS15':fields.update(qBCMonoReten='5.0000',adRemICMSReten='1.00',vICMSMonoReten='5.00')
    if kind=='ICMS53':fields.update(vICMSMonoOp='20.00',pDif='50.0000',vICMSMonoDif='10.00')
    for k,v in fields.items():E.SubElement(node,'{'+NS+'}'+k).text=v
    total=r.find('.//n:ICMSTot',N)
    for k in ('qBCMono','vICMSMono','qBCMonoReten','vICMSMonoReten'):
        if k in fields:E.SubElement(total,'{'+NS+'}'+k).text=fields[k]
    if kind=='ICMS15':total.find('n:vNF',N).text='138005.00'
    return E.tostring(r)

@pytest.mark.parametrize('kind',['ICMS02','ICMS15','ICMS53'])
def test_monophase_families_preserve_taxes_and_totals(kind):
    raw=mono_xml(kind);d=parse_xml(raw,DEST)
    assert d['items'][0]['icms']['CST']==kind[-2:]
    assert d['items'][0]['icms']['vICMSMono']=='10.00'
    assert d['totals']['vICMS']=='0.00'
    assert d['sha256']==hashlib.sha256(raw).hexdigest()

@pytest.mark.parametrize('kind',['ICMS02','ICMS15','ICMS53'])
def test_monophase_rejects_negative_ad_rem_and_wrong_cst(kind):
    r=E.fromstring(mono_xml(kind));r.find('.//n:'+kind+'/n:adRemICMS',N).text='-1'
    with pytest.raises(FiscalError):parse_xml(E.tostring(r))
    r=E.fromstring(mono_xml(kind));r.find('.//n:'+kind+'/n:CST',N).text='00'
    with pytest.raises(FiscalError):parse_xml(E.tostring(r))

def test_monophase_retention_is_in_invoice_total_and_reconciled():
    r=E.fromstring(mono_xml('ICMS15'));r.find('.//n:ICMSTot/n:vNF',N).text='138000.00'
    with pytest.raises(FiscalError,match='vNF'):parse_xml(E.tostring(r))
    r=E.fromstring(mono_xml('ICMS15'));r.find('.//n:ICMSTot/n:vICMSMonoReten',N).text='8.00'
    with pytest.raises(FiscalError,match='vICMSMonoReten'):parse_xml(E.tostring(r))


THIRD='28988409000187'

def service_xml(mixed=False):
    r=E.fromstring(RAW);inf=r.find('n:NFe/n:infNFe',N)
    item=inf.find('n:det',N)
    if mixed:
        item=deepcopy(item);item.set('nItem','2');inf.append(item)
    tax=item.find('n:imposto',N)
    for child in list(tax):tax.remove(child)
    iss=E.SubElement(tax,'{'+NS+'}ISSQN')
    for k,v in {'vBC':'100.00','vAliq':'5.00','vISSQN':'5.00','cMunFG':'3550308','cListServ':'01.01','indISS':'1','indIncentivo':'2'}.items():E.SubElement(iss,'{'+NS+'}'+k).text=v
    for family,rate,value in [('PIS','1.65','1.65'),('COFINS','7.60','7.60')]:
        group=E.SubElement(tax,'{'+NS+'}'+family);aliq=E.SubElement(group,'{'+NS+'}'+family+'Aliq')
        for k,v in {'CST':'01','vBC':'100.00','p'+family:rate,'v'+family:value}.items():E.SubElement(aliq,'{'+NS+'}'+k).text=v
    prod=item.find('n:prod',N)
    for k,v in {'qCom':'1','vUnCom':'100.00','vProd':'100.00','NCM':'00','CFOP':'5933'}.items():prod.find('n:'+k,N).text=v
    total=inf.find('n:total/n:ICMSTot',N)
    total.find('n:vNF',N).text='138100.00' if mixed else '100.00'
    if not mixed:
        for k in ('vProd','vBC','vICMS'):total.find('n:'+k,N).text='0.00'
    service=E.SubElement(inf.find('n:total',N),'{'+NS+'}ISSQNtot')
    for k,v in {'vServ':'100.00','vBC':'100.00','vISS':'5.00','vPIS':'1.65','vCOFINS':'7.60','dCompet':'2025-12-01'}.items():E.SubElement(service,'{'+NS+'}'+k).text=v
    return E.tostring(r)

@pytest.mark.parametrize('mixed',[False,True])
def test_issqn_services_and_goods_have_separate_totals(mixed):
    raw=service_xml(mixed);data=parse_xml(raw,DEST)
    assert data['service_totals']['vServ']=='100.00'
    assert data['service_totals']['vPIS']=='1.65'
    assert data['totals']['vPIS']=='0.00'
    assert data['items'][-1]['issqn']['vISSQN']=='5.00'
    assert data['items'][-1]['icms']=={}
    assert data['sha256']==hashlib.sha256(raw).hexdigest()

@pytest.mark.parametrize('path,value',[
 ('.//n:ISSQN/n:vBC','-1'),('.//n:ISSQN/n:vAliq','101'),
 ('.//n:ISSQN/n:indISS','9'),('.//n:ISSQNtot/n:vServ','101'),
 ('.//n:ISSQNtot/n:vPIS','0'),('.//n:ICMSTot/n:vNF','138000'),
 ('.//n:ISSQNtot/n:dCompet','2025-02-30')])
def test_issqn_invalid_totals_and_fields_rejected(path,value):
    r=E.fromstring(service_xml(True));r.find(path,N).text=value
    with pytest.raises(FiscalError):parse_xml(E.tostring(r))

def test_issqn_cannot_share_item_with_icms_or_omit_required_tax():
    r=E.fromstring(service_xml());tax=r.find('.//n:det/n:imposto',N)
    tax.append(deepcopy(E.fromstring(RAW).find('.//n:ICMS',N)))
    with pytest.raises(FiscalError,match='apenas ICMS ou ISSQN'):parse_xml(E.tostring(r))
    r=E.fromstring(service_xml());iss=r.find('.//n:ISSQN',N);iss.remove(iss.find('n:vISSQN',N))
    with pytest.raises(FiscalError,match='obrigatório'):parse_xml(E.tostring(r))

@pytest.mark.parametrize('role',['transportadora','autorizado_xml'])
def test_third_party_identity_is_not_purchase_or_sale(role):
    r=E.fromstring(RAW);inf=r.find('n:NFe/n:infNFe',N)
    if role=='transportadora':
        transp=inf.find('n:transp',N)
        if transp is None:transp=E.SubElement(inf,'{'+NS+'}transp')
        old=transp.find('n:transporta',N)
        if old is not None:transp.remove(old)
        node=E.SubElement(transp,'{'+NS+'}transporta')
    else:node=E.SubElement(inf,'{'+NS+'}autXML')
    E.SubElement(node,'{'+NS+'}CNPJ').text=THIRD
    raw=E.tostring(r);data=parse_xml(raw,THIRD)
    assert data['flow']=='terceiro' and data['participation']==[role]
    for operation in ('entrada','saida'):
        with pytest.raises(FiscalError,match='Operação'):parse_xml(raw,THIRD,operation)
    node.find('n:CNPJ',N).text=DEST
    with pytest.raises(FiscalError,match='não participa'):parse_xml(E.tostring(r),THIRD)

def test_third_party_identity_cannot_come_from_arbitrary_or_duplicate_groups():
    r=E.fromstring(RAW);inf=r.find('n:NFe/n:infNFe',N)
    extra=E.SubElement(inf,'{'+NS+'}infAdic');E.SubElement(extra,'{'+NS+'}CNPJ').text=THIRD
    with pytest.raises(FiscalError,match='não participa'):parse_xml(E.tostring(r),THIRD)
    auth=E.SubElement(inf,'{'+NS+'}autXML')
    E.SubElement(auth,'{'+NS+'}CNPJ').text=THIRD;E.SubElement(auth,'{'+NS+'}CPF').text='123'
    with pytest.raises(FiscalError,match='não participa'):parse_xml(E.tostring(r),THIRD)
