"""Fiscal document parsing. Never manufacture identities, taxes or authorizations."""
import re, hashlib, json, os
from decimal import Decimal, InvalidOperation
from datetime import datetime
from lxml import etree as E
NS='http://www.portalfiscal.inf.br/nfe'
N={'n':NS}
class FiscalError(ValueError): pass

def digits(v): return re.sub(r'\D','',str(v or ''))
def validate_key(value):
    key=re.sub(r'[\s.\-/]','',str(value or ''))
    if not re.fullmatch(r'\d{44}',key): raise FiscalError('Chave deve conter 44 dígitos, sem letras.')
    total=sum(int(c)*(2+i%8) for i,c in enumerate(reversed(key[:43])))
    check=11-total%11; check=0 if check>=10 else check
    if int(key[-1])!=check: raise FiscalError('Dígito verificador da chave inválido.')
    if key[20:22] not in ('55','65'): raise FiscalError('Modelo não suportado por este parser: '+key[20:22]+'. NF-e/NFC-e não se confundem com NFS-e ou CT-e.')
    if key[:2] not in {'11','12','13','14','15','16','17','21','22','23','24','25','26','27','28','29','31','32','33','35','41','42','43','50','51','52','53'}: raise FiscalError('UF da chave inválida.')
    if not 1<=int(key[4:6])<=12: raise FiscalError('Mês da chave inválido.')
    return key

def validate_tax_id(v):
    v=digits(v)
    if len(set(v))<=1: return False
    if len(v)==14:
        for size,weights in [(12,[5,4,3,2,9,8,7,6,5,4,3,2]),(13,[6,5,4,3,2,9,8,7,6,5,4,3,2])]:
            rem=sum(int(a)*b for a,b in zip(v[:size],weights))%11
            if int(v[size])!=(0 if rem<2 else 11-rem):return False
        return True
    if len(v)==11:
        return all(int(v[n])==((sum(int(a)*b for a,b in zip(v[:n],range(n+1,1,-1)))*10)%11)%10 for n in (9,10))
    return False

def txt(e,path,default=None):
    v=e.find(path,N) if e is not None else None
    return v.text.strip() if v is not None and v.text else default

def dec(value):
    try:
        d=Decimal(str(value))
        if not d.is_finite() or d<0: raise ValueError()
        return d
    except (InvalidOperation,ValueError): raise FiscalError('Valor numérico inválido: '+str(value)[:30])

def flatten(e):
    if e is None:return {}
    out={}
    for child in e:
        k=E.QName(child).localname
        val=flatten(child) if len(child) else (child.text or '')
        if k in out:
            if not isinstance(out[k],list):out[k]=[out[k]]
            out[k].append(val)
        else:out[k]=val
    return out

def validate_taxes(tax,num):
    amounts={}
    for element in tax.iter():
        if len(element):continue
        name=E.QName(element).localname
        if re.fullmatch(r'[vpq][A-Z][A-Za-z0-9]*',name):
            value=dec(element.text)
            if name in {'pRedBC','pRedBCST','pICMS','pICMSST','pPIS','pCOFINS','pIPI','pFCP','pFCPST'} and value>100:raise FiscalError('Percentual acima de 100 no item '+num+': '+name)
    for family in ['PIS','COFINS']:
        node=tax.find('n:'+family,N)
        if node is None or len(node)!=1:raise FiscalError('Item '+num+': modalidade '+family+' ausente ou duplicada.')
        kind=E.QName(node[0]).localname;values=flatten(node[0])
        allowed={family+'Aliq':{'01','02'},family+'Qtde':{'03'},family+'NT':{'04','05','06','07','08','09'},family+'Outr':{'49',*[str(n) for n in range(50,100)]}}
        if kind not in allowed or values.get('CST') not in allowed.get(kind,set()):raise FiscalError('Modalidade/CST '+family+' inválida no item '+num)
        required=[]
        if kind.endswith('Aliq'):required=['vBC','p'+family,'v'+family]
        elif kind.endswith('Qtde'):required=['qBCProd','vAliqProd','v'+family]
        elif kind.endswith('Outr'):required=['v'+family]+(['qBCProd','vAliqProd'] if 'qBCProd' in values else ['vBC','p'+family])
        for field in required:
            if field not in values:raise FiscalError('Campo '+family+' ausente: '+field)
            dec(values[field])
        if kind.endswith('NT') and any(k.startswith(('v','p','q')) for k in values):raise FiscalError('Grupo não tributado '+family+' contém valores incompatíveis.')
        amounts['v'+family]=values.get('v'+family,'0')
    return amounts

def parse_xml(raw, company=None, operation='auto'):
    if len(raw)>8*1024*1024:raise FiscalError('XML excede 8 MB.')
    try:
        parser=E.XMLParser(resolve_entities=False,no_network=True,load_dtd=False,huge_tree=False)
        root=E.fromstring(raw,parser)
    except E.XMLSyntaxError as ex:raise FiscalError('XML malformado.') from ex
    if root.getroottree().docinfo.doctype or any(isinstance(x,E._Entity) for x in root.iter()):raise FiscalError('DTD e entidades não são permitidas.')
    if root.tag not in (f'{{{NS}}}nfeProc',f'{{{NS}}}NFe'):raise FiscalError('Modelo/namespace não suportado. Esperado NF-e 4.00, não converter NFS-e em NF-e.')
    infs=root.findall('.//n:infNFe',N)
    if len(infs)!=1:raise FiscalError('XML deve conter exatamente um infNFe.')
    inf=infs[0]
    expected=root.find('n:NFe/n:infNFe',N) if root.tag.endswith('nfeProc') else root.find('n:infNFe',N)
    if expected is not inf:raise FiscalError('Estrutura NF-e inesperada.')
    if inf.get('versao')!='4.00':raise FiscalError('Versão NF-e deve ser 4.00.')
    for group in ['ide','emit','dest','total']:
        if len(inf.findall('n:'+group,N))!=1:raise FiscalError('Arquivo NFe sem campos ide/emit/dest/total suficientes ou com grupos duplicados: '+group)
    ide=inf.find('n:ide',N);emit=inf.find('n:emit',N);dest=inf.find('n:dest',N)
    issuer=txt(emit,'n:CNPJ') or txt(emit,'n:CPF'); recipient=txt(dest,'n:CNPJ') or txt(dest,'n:CPF')
    for label,node,doc in [('emitente',emit,issuer),('destinatário',dest,recipient)]:
        if not txt(node,'n:xNome') or not doc:raise FiscalError('Nome e CNPJ/CPF do '+label+' são obrigatórios neste perfil de importação.')
        if not validate_tax_id(doc):raise FiscalError('CNPJ/CPF inválido do '+label+'.')
        if len(node.findall('n:CNPJ',N))+len(node.findall('n:CPF',N))!=1:raise FiscalError('Identidade duplicada do '+label+'.')
    key=validate_key(inf.get('Id','').removeprefix('NFe'))
    values={k:txt(ide,'n:'+k) for k in ['cUF','mod','serie','nNF','dhEmi','tpNF','tpEmis','cNF','cDV']}
    if not all(values.values()):raise FiscalError('Identificação incompleta: '+', '.join(k for k,v in values.items() if not v))
    try: issued=datetime.fromisoformat(values['dhEmi'])
    except ValueError:raise FiscalError('Data de emissão inválida.')
    if issued.tzinfo is None:raise FiscalError('Emissão precisa de fuso horário.')
    expected_parts=[(key[:2],values['cUF'],'UF'),(key[2:6],issued.strftime('%y%m'),'data'),(key[6:20],issuer.zfill(14),'emitente'),(key[20:22],values['mod'],'modelo'),(key[22:25],values['serie'].zfill(3),'série'),(key[25:34],values['nNF'].zfill(9),'número'),(key[34],values['tpEmis'],'tipo de emissão'),(key[35:43],values['cNF'],'código numérico'),(key[-1],values['cDV'],'DV')]
    for a,b,label in expected_parts:
        if a!=b:raise FiscalError('Chave diverge do campo '+label+'. Não substituir os dados da nota.')
    if values['tpNF'] not in ('0','1'):raise FiscalError('tpNF inválido.')
    if operation not in ('auto','entrada','saida'):raise FiscalError('Operação inválida.')
    flow=None
    if company:
        company=digits(company)
        if company==issuer:flow='saida' if values['tpNF']=='1' else 'entrada'
        elif company==recipient:flow='entrada' if values['tpNF']=='1' else 'saida'
        else:raise FiscalError('CNPJ/CPF da empresa não participa como emitente ou destinatário. Não alterar o XML para forçar importação.')
        if operation!='auto' and operation!=flow:raise FiscalError('Operação incorreta para a empresa: esperado '+flow+'.')
    total=inf.find('n:total/n:ICMSTot',N)
    if total is None:raise FiscalError('Grupo total/ICMSTot ausente.')
    totals=flatten(total)
    for k in ['vNF','vProd','vBC','vICMS']:
        if k not in totals:raise FiscalError('Total obrigatório ausente: '+k)
        dec(totals[k])
    items=[]
    dets=inf.findall('n:det',N)
    if not dets:raise FiscalError('Nota sem itens det/prod/imposto.')
    seen=set()
    for det in dets:
        num=det.get('nItem')
        if not num or num in seen:raise FiscalError('nItem ausente ou duplicado.')
        seen.add(num); prod=det.find('n:prod',N); tax=det.find('n:imposto',N)
        if prod is None or tax is None:raise FiscalError('Produto ou imposto ausente.')
        p=flatten(prod)
        for k in ['cProd','xProd','NCM','CFOP','uCom','qCom','vUnCom','vProd']:
            if k not in p or p[k]=='':raise FiscalError('Item '+num+': campo ausente '+k)
        for k in ['qCom','vUnCom','vProd']:dec(p[k])
        if abs(dec(p['qCom'])*dec(p['vUnCom'])-dec(p['vProd']))>Decimal('0.02'):raise FiscalError('Valor do produto diverge de quantidade × valor unitário no item '+num)
        icms=tax.find('n:ICMS',N)
        if icms is None or len(icms)!=1:raise FiscalError('Item '+num+': modalidade ICMS ausente ou duplicada.')
        kind=E.QName(icms[0]).localname
        supported={'ICMS00','ICMS10','ICMS20','ICMS30','ICMS40','ICMS51','ICMS60','ICMS61','ICMS70','ICMS90','ICMSPart','ICMSST','ICMSSN101','ICMSSN102','ICMSSN201','ICMSSN202','ICMSSN500','ICMSSN900'}
        if kind not in supported:raise FiscalError('Modalidade ICMS requer parser específico: '+kind)
        icms_data=flatten(icms[0])
        cst_map={'ICMS00':['00'],'ICMS10':['10'],'ICMS20':['20'],'ICMS30':['30'],'ICMS40':['40','41','50'],'ICMS51':['51'],'ICMS60':['60'],'ICMS61':['61'],'ICMS70':['70'],'ICMS90':['90'],'ICMSPart':['10','90'],'ICMSST':['41','60'],'ICMSSN101':['101'],'ICMSSN102':['102','103','300','400'],'ICMSSN201':['201'],'ICMSSN202':['202','203'],'ICMSSN500':['500'],'ICMSSN900':['900']}
        if icms_data.get('CSOSN' if kind.startswith('ICMSSN') else 'CST') not in cst_map[kind]:raise FiscalError('CST/CSOSN incompatível com a modalidade ICMS.')
        if kind=='ICMS61':
            for field in ('qBCMonoRet','adRemICMSRet','vICMSMonoRet'):
                if field in icms_data:dec(icms_data[field])
        contributions=validate_taxes(tax,num)
        if not ('CST' in icms_data or 'CSOSN' in icms_data):raise FiscalError('CST/CSOSN ausente no item '+num)
        if kind in {'ICMS00','ICMS10','ICMS20','ICMS70','ICMSPart'}:
            for field in ['vBC','pICMS','vICMS']:
                if field not in icms_data:raise FiscalError('Campo ICMS obrigatório ausente: '+field)
        for field in ['vBC','vICMS','vICMSDeson','vST','vFCPST','pRedBC','pICMS']:
            if field in icms_data:dec(icms_data[field])
        if kind in {'ICMS20','ICMS70'} and 'pRedBC' not in icms_data:raise FiscalError('Percentual de redução ausente no item '+num)
        items.append({'number':num,'product':p,'taxes':flatten(tax),'icms':icms_data,'additional':txt(det,'n:infAdProd'),'contributions':contributions})
    product_sum=sum((dec(i['product']['vProd']) for i in items if i['product'].get('indTot','1')=='1'),Decimal(0))
    if abs(product_sum-dec(totals['vProd']))>Decimal('0.02'):raise FiscalError('Soma dos produtos diverge de vProd total.')
    for field in ['vBC','vICMS']:
        available=[i['icms'].get(field,'0') for i in items]
        if abs(sum((dec(v) for v in available),Decimal(0))-dec(totals[field]))>Decimal('0.02'):raise FiscalError('Soma dos itens diverge de '+field+' total.')
    for family in ['PIS','COFINS']:
        field='v'+family
        if field not in totals:raise FiscalError('Total obrigatório ausente: '+field)
        if abs(sum((dec(i['contributions'][field]) for i in items),Decimal(0))-dec(totals[field]))>Decimal('0.02'):raise FiscalError('Soma dos itens diverge de '+field+' total.')
    # Standard NF-e total profile. Specialized layouts must get a separate tested parser.
    additions=['vST','vFCPST','vFrete','vSeg','vOutro','vII','vIPI','vIPIDevol']
    deductions=sum((dec(i['icms'].get('vICMSDeson','0')) for i in items if i['icms'].get('indDeduzDeson','1')!='0'),Decimal(0))
    calculated=dec(totals['vProd'])-dec(totals.get('vDesc','0'))-deductions+sum((dec(totals.get(f,'0')) for f in additions),Decimal(0))
    if abs(calculated-dec(totals['vNF']))>Decimal('0.02'):raise FiscalError('Total vNF diverge dos componentes da nota neste perfil. Revisar exceções fiscais; não ajustar valores automaticamente.')
    protocol=root.find('n:protNFe/n:infProt',N)
    if protocol is not None and txt(protocol,'n:chNFe')!=key:raise FiscalError('Chave do protocolo diverge da NF-e.')
    schema_status='NAO_VERIFICADO'
    schema=os.getenv('NFE_XSD_PATH')
    if schema:
        try:
            schema_doc=E.parse(schema,E.XMLParser(resolve_entities=False,no_network=True))
            xsd=E.XMLSchema(schema_doc); xsd.assertValid(root);schema_status='VALIDADO'
        except (E.XMLSchemaError,E.DocumentInvalid,OSError):raise FiscalError('XML reprovado pelo pacote XSD configurado; conferir versão e raiz do schema.')
    return {'key':key,'number':values['nNF'],'series':values['serie'],'issued_at':values['dhEmi'],'movement_at':txt(ide,'n:dhSaiEnt'), 'model':values['mod'],'tpNF':values['tpNF'],'flow':flow,'issuer':flatten(emit),'recipient':flatten(dest),'issuer_document':issuer,'recipient_document':recipient,'issuer_name':txt(emit,'n:xNome'),'recipient_name':txt(dest,'n:xNome'),'totals':totals,'items':items,'additional':flatten(inf.find('n:infAdic',N)),'protocol':flatten(protocol),'signature_present':bool(root.findall('.//{http://www.w3.org/2000/09/xmldsig#}Signature')),'signature_validation':'NAO_VERIFICADA','schema_status':schema_status,'sha256':hashlib.sha256(raw).hexdigest(),'warnings':['A validação estrutural não comprova autorização na SEFAZ nem verifica assinatura digital.','TARE, crédito apropriável e ajustes do importador dependem da regra do sistema de destino.']}

# Canonical child order for the supported NF-e reconstruction profile.
# Dictionary/editing order must never determine XML element order.
FIELD_ORDER={
'ide':'cUF cNF natOp mod serie nNF dhEmi dhSaiEnt tpNF idDest cMunFG tpImp tpEmis cDV tpAmb finNFe indFinal indPres indIntermed procEmi verProc dhCont xJust NFref',
'emit':'CNPJ CPF xNome xFant enderEmit IE IEST IM CNAE CRT',
'dest':'CNPJ CPF idEstrangeiro xNome enderDest indIEDest IE ISUF IM email',
'enderEmit':'xLgr nro xCpl xBairro cMun xMun UF CEP cPais xPais fone',
'enderDest':'xLgr nro xCpl xBairro cMun xMun UF CEP cPais xPais fone',
'det':'prod imposto impostoDevol infAdProd obsItem',
'prod':'cProd cEAN cBarra xProd NCM NVE CEST indEscala CNPJFab cBenef EXTIPI CFOP uCom qCom vUnCom vProd cEANTrib cBarraTrib uTrib qTrib vUnTrib vFrete vSeg vDesc vOutro indTot DI detExport xPed nItemPed nFCI rastro infProdNFF infProdEmb',
'imposto':'vTotTrib ICMS IPI II ISSQN PIS PISST COFINS COFINSST ICMSUFDest',
'ICMSTot':'vBC vICMS vICMSDeson vFCPUFDest vICMSUFDest vICMSUFRemet vFCP vBCST vST vFCPST vFCPSTRet qBCMono vICMSMono qBCMonoReten vICMSMonoReten qBCMonoRet vICMSMonoRet vProd vFrete vSeg vDesc vII vIPI vIPIDevol vPIS vCOFINS vOutro vNF vTotTrib',
'ICMS61':'orig CST qBCMonoRet adRemICMSRet vICMSMonoRet',
'ICMS20':'orig CST modBC pRedBC vBC pICMS vICMS vBCFCP pFCP vFCP vICMSDeson motDesICMS indDeduzDeson',
'ICMS00':'orig CST modBC vBC pICMS vICMS pFCP vFCP',
'ICMS40':'orig CST vICMSDeson motDesICMS indDeduzDeson',
'ICMS10':'orig CST modBC vBC pICMS vICMS vBCFCP pFCP vFCP modBCST pMVAST pRedBCST vBCST pICMSST vICMSST vBCFCPST pFCPST vFCPST vICMSSTDeson motDesICMSST',
'IPI':'clEnq CNPJProd cSelo qSelo cEnq IPITrib IPINT',
'IPITrib':'CST vBC pIPI qUnid vUnid vIPI',
'IPINT':'CST',
'PISAliq':'CST vBC pPIS vPIS','PISQtde':'CST qBCProd vAliqProd vPIS','PISNT':'CST','PISOutr':'CST vBC pPIS qBCProd vAliqProd vPIS',
'COFINSAliq':'CST vBC pCOFINS vCOFINS','COFINSQtde':'CST qBCProd vAliqProd vCOFINS','COFINSNT':'CST','COFINSOutr':'CST vBC pCOFINS qBCProd vAliqProd vCOFINS',
'ICMSSN101':'orig CSOSN pCredSN vCredICMSSN','ICMSSN102':'orig CSOSN',
'ICMSSN201':'orig CSOSN modBCST pMVAST pRedBCST vBCST pICMSST vICMSST vBCFCPST pFCPST vFCPST pCredSN vCredICMSSN',
'ICMSSN202':'orig CSOSN modBCST pMVAST pRedBCST vBCST pICMSST vICMSST vBCFCPST pFCPST vFCPST',
'total':'ICMSTot ISSQNtot retTrib','transp':'modFrete transporta retTransp veicTransp reboque vagao balsa vol','transporta':'CNPJ CPF xNome IE xEnder xMun UF','veicTransp':'placa UF RNTC',
'pag':'detPag vTroco','detPag':'indPag tPag xPag vPag card','infAdic':'infAdFisco infCpl obsCont obsFisco procRef',
}
GENERIC_ICMS_ORDER='orig CST CSOSN modBC pRedBC vBC pICMS vICMSOp pDif vICMSDif vICMS vBCFCP pFCP vFCP modBCST pMVAST pRedBCST vBCST pICMSST vICMSST vBCFCPST pFCPST vFCPST vBCSTRet pST vICMSSubstituto vICMSSTRet vBCFCPSTRet pFCPSTRet vFCPSTRet pRedBCEfet vBCEfet pICMSEfet vICMSEfet vICMSDeson motDesICMS pCredSN vCredICMSSN indDeduzDeson'

def ordered_fields(name,value):
    order=FIELD_ORDER.get(name,GENERIC_ICMS_ORDER if name.startswith('ICMS') else '').split()
    indexes={key:i for i,key in enumerate(order)}
    return sorted(value.items(),key=lambda item:(-1 if item[0].startswith('@') else indexes.get(item[0],len(order))))

def build_reconstructed(data,company,operation='auto'):
    """JSON of original NF-e groups, preserving insertion order. No example invoice data."""
    if not isinstance(data,dict):raise FiscalError('Dados precisam ser objeto JSON.')
    groups=data.get('groups')
    if not isinstance(groups,dict):raise FiscalError('Informe groups com os campos reais extraídos da nota.')
    if any(k in groups for k in ['Signature','protNFe','infProt']):raise FiscalError('Não inserir assinatura ou protocolo copiado de outro XML.')
    key=validate_key(data.get('key'))
    root=E.Element(E.QName(NS,'NFe'),nsmap={None:NS})
    inf=E.SubElement(root,E.QName(NS,'infNFe'),Id='NFe'+key,versao='4.00')
    allowed=['ide','emit','avulsa','dest','retirada','entrega','autXML','det','total','transp','cobr','pag','infIntermed','infAdic','exporta','compra','cana','infRespTec','infSolicNFF','agropecuario']
    if set(groups)-set(allowed):raise FiscalError('Grupo desconhecido: '+', '.join(set(groups)-set(allowed)))
    def add(parent,name,value):
        if not re.fullmatch('[A-Za-z][A-Za-z0-9]*',name):raise FiscalError('Nome de campo inválido.')
        if isinstance(value,list):
            for v in value:add(parent,name,v)
            return
        node=E.SubElement(parent,E.QName(NS,name))
        if isinstance(value,dict):
            for k,v in ordered_fields(name,value):
                if k=='@nItem':node.set('nItem',str(v))
                elif k.startswith('@'):raise FiscalError('Atributo não permitido.')
                else:add(node,k,v)
        elif value is not None:node.text=str(value)
        else:raise FiscalError('Campo sem origem confirmada: '+name)
    for k in allowed:
        if k in groups:add(inf,k,groups[k])
    raw=E.tostring(root,xml_declaration=True,encoding='UTF-8')
    result=parse_xml(raw,company,operation)
    return raw,result

def reconciliation_csv(raw):
    import csv,io
    try:t=raw.decode('utf-8-sig')
    except UnicodeDecodeError:t=raw.decode('cp1252')
    rows=list(csv.DictReader(io.StringIO(t),delimiter=';'))
    if len(rows)>100000:raise FiscalError('CSV excede 100.000 linhas.')
    results=[]
    for row in rows:
        error=row.get('Descrição do Erro','');name=row.get('Nome do Arquivo','');key=row.get('Chave de Acesso','')
        if 'ide/emit/dest' in error:category='ESTRUTURA_INCOMPLETA';action='Obter PDF/XML real e preencher ide, emit e dest. Não usar CNPJ genérico.'
        elif 'CNPJ/CPF' in error:category='VINCULO_EMPRESA';action='Conferir empresa selecionada, emitente, destinatário e entrada/saída; preservar a identidade da nota.'
        else:category='REVISAR';action='Comparar o XML recusado com o documento original e a especificação do importador.'
        results.append({'file':name,'key':key,'error':error,'category':category,'action':action})
    return results
