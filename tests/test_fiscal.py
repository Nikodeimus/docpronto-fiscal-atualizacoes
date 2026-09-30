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
