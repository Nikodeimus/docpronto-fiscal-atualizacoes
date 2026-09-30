"""NFC-e originals and verified public consultation routing; never scrape portals."""
import hashlib, io, zipfile
from flask import g, request, jsonify
from lxml import etree
from .fiscal import validate_key, validate_tax_id, NS
from .distribution import xml_root

STATES=dict(x.split(':') for x in '11:RO 12:AC 13:AM 14:RR 15:PA 16:AP 17:TO 21:MA 22:PI 23:CE 24:RN 25:PB 26:PE 27:AL 28:SE 29:BA 31:MG 32:ES 33:RJ 35:SP 41:PR 42:SC 43:RS 50:MS 51:MT 52:GO 53:DF'.split())
REVIEWED='2026-09-29'
PORTALS={
 'PA':'https://app.sefa.pa.gov.br/pservicos/',
 'MA':'http://www.nfce.sefaz.ma.gov.br/portal/consultaNFe.do',
 'AC':'https://sefaznet.ac.gov.br/nfce/consulta.xhtml',
 'AL':'https://nfce.sefaz.al.gov.br/consultanfce.htm',
 'AM':'https://www.sefaz.am.gov.br/nfce/formConsulta.do',
 'AP':'https://sfzvirtualprd.sefaz.ap.gov.br/',
 'BA':'https://www.sefaz.ba.gov.br/inspetoria-eletronica/icms/',
 'CE':'https://cfe.sefaz.ce.gov.br/',
 'DF':'https://www.receita.fazenda.df.gov.br/aplicacoes/CartaServicos/servico.cfm?codServico=826&codSubCategoria=232&codTipoPessoa=6',
 'ES':'https://sefaz.es.gov.br/consulta-nfce',
 'GO':'https://goias.gov.br/economia/consultas-e-servicos-2/',
 'MS':'https://www.dfe.ms.gov.br/nfce/consulta/',
 'MT':'https://www.sefaz.mt.gov.br/portal/nfce/',
 'PB':'https://www.sefaz.pb.gov.br/info/notas-fiscais/nfc-e',
 'PE':'https://www.sefaz.pe.gov.br/Noticias/Paginas/Aviso-aos-contribuintes-emissores-de-NFC-e.aspx',
 'PI':'https://www.piauidigital.pi.gov.br/home/sefaz-eageat/',
 'RJ':'https://portal.fazenda.rj.gov.br/dfe/',
 'RN':'https://www.sefaz.rn.gov.br/postagem/nfce/',
 'RO':'https://nfce.sefin.ro.gov.br/home.jsp',
 'RR':'https://portalapp.sefaz.rr.gov.br/nfce/servlet/wp_consulta_nfce',
 'SC':'https://sat.sef.sc.gov.br/nfce/consulta',
 'SE':'https://www.sefaz.se.gov.br/SitePages/default.aspx',
 'TO':'https://portal.sefaz.to.gov.br/busca',

 'SP':'https://www.nfce.fazenda.sp.gov.br/consulta',
 'MG':'https://portalsped.fazenda.mg.gov.br/portalnfce',
 'PR':'https://www.fazenda.pr.gov.br/servicos/Empresa/Documentos-fiscais-eletronicos/Consultar-NFC-e-nota-fiscal-do-consumidor-eletronica-gzNEE1NO',
 'RS':'https://www.sefaz.rs.gov.br/NFE/NFE-NFC.ASPX',
}
SOURCES={**PORTALS,'PA':'https://agenciapara.com.br/noticia/150/download-de-arquivos-da-sefa-agora-em-dispositivos-moveis','MG':'https://portalsped.fazenda.mg.gov.br/spedmg/nfce/web-services/','SC':'https://www.sef.sc.gov.br/api-portal/Documento/ver/1398','RJ':'https://portal.fazenda.rj.gov.br/dfe/wp-content/uploads/sites/17/2023/01/DF-e_NF-e.pdf'}
PORTAL_NAVIGATION={'PA','AP','BA','CE','DF','ES','GO','MT','PB','PE','PI','PR','RJ','RN','SE','TO'}

def resolve_key(value,uf=None):
 key=validate_key(value)
 if key[20:22]!='65':raise ValueError('Informe uma chave NFC-e modelo 65.')
 state=STATES[key[:2]]
 if uf and uf!=state:raise ValueError('UF informada diverge da chave NFC-e.')
 return dict(key=key,uf=state,url=PORTALS.get(state),source=SOURCES.get(state),reviewed=REVIEWED if state in PORTALS else None,verified=state in PORTALS,automatic=False,mode=('official_navigation' if state in PORTAL_NAVIGATION else 'public_portal') if state in PORTALS else 'not_verified',availability='not_tested',transport_warning='Portal oficial acessível apenas por HTTP nesta revisão; não há conexão criptografada.' if state=='MA' else '',message='Portal de serviços indicado pela comunicação oficial da SEFA-PA. A disponibilidade atual não foi confirmada; pode exigir certificado ou login. Não é uma consulta pública automática por chave.' if state=='PA' else 'Abra o portal oficial e escolha a consulta NFC-e de produção. Pode ser necessário certificado, login ou verificação humana. Acesso não garante XML completo.' if state in PORTAL_NAVIGATION else 'Abra a consulta oficial e informe a chave. Consulta pública não garante download do XML.' if state in PORTALS else 'Endereço de consulta desta UF ainda não verificado. Importe o XML original fornecido pelo emitente.')

def validate_original(raw,taxid):
 if len(raw)>8*1024*1024:raise ValueError('XML excede 8 MB.')
 root=xml_root(raw);ns={'n':NS}
 if root.getroottree().docinfo.doctype or any(isinstance(x,etree._Entity) for x in root.iter()):raise ValueError('DTD e entidades não são permitidas.')
 if root.tag!='{'+NS+'}nfeProc':raise ValueError('Importe NFC-e processada (nfeProc), com protocolo.')
 infs=root.findall('n:NFe/n:infNFe',ns)
 if len(infs)!=1 or len(root.findall('.//n:infNFe',ns))!=1:raise ValueError('Estrutura NFC-e ambígua ou ausente.')
 inf=infs[0]
 if not inf.get('Id','').startswith('NFe'):raise ValueError('Identificador NFC-e inválido.')
 key=resolve_key(inf.get('Id','').removeprefix('NFe'))['key']
 def one(path,required=True):
  found=inf.findall(path,ns)
  if len(found)>1 or (required and len(found)!=1):raise ValueError('Campo NFC-e ausente ou duplicado: '+path)
  return (found[0].text or '').strip() if found else ''
 if one('n:ide/n:mod')!='65' or one('n:ide/n:cUF')!=key[:2]:raise ValueError('Modelo/UF divergem da chave.')
 issuer=one('n:emit/n:CNPJ')
 if not validate_tax_id(issuer) or issuer!=key[6:20]:raise ValueError('Emitente inválido ou divergente da chave.')
 dests=inf.findall('n:dest',ns)
 if len(dests)>1:raise ValueError('Destinatário duplicado.')
 identities=inf.findall('n:dest/n:CNPJ',ns)+inf.findall('n:dest/n:CPF',ns)
 if len(identities)>1:raise ValueError('Identidade do destinatário duplicada.')
 recipient=(identities[0].text or '').strip() if identities else ''
 if recipient and not validate_tax_id(recipient):raise ValueError('Documento do destinatário inválido.')
 if taxid not in (issuer,recipient):raise ValueError('A empresa não participa como emitente ou destinatário identificado. NFC-e sem destinatário só pode ser importada pelo emitente.')
 protocols=root.findall('n:protNFe/n:infProt',ns)
 if len(protocols)!=1 or len(protocols[0].findall('n:chNFe',ns))!=1 or len(protocols[0].findall('n:cStat',ns))!=1 or protocols[0].findtext('n:chNFe',namespaces=ns)!=key:raise ValueError('Protocolo ausente, duplicado ou divergente.')
 if protocols[0].findtext('n:cStat',namespaces=ns) not in ('100','150'):raise ValueError('Protocolo não informa autorização de uso.')
 return dict(key=key,issuer=issuer,recipient=recipient,signature='NAO_VERIFICADA')

def register_nfce(app,company,storage):
 @app.get('/api/nfce/coverage')
 def coverage():
  company(request.args.get('company'))
  return jsonify(items=[dict(uf=v,code=k,url=PORTALS.get(v),source=SOURCES.get(v),verified=v in PORTALS,reviewed=REVIEWED if v in PORTALS else None,import_xml=True,automatic=False,availability='not_tested',mode=('official_navigation' if v in PORTAL_NAVIGATION else 'public_portal') if v in PORTALS else 'not_verified') for k,v in STATES.items()])
 @app.get('/api/nfce/resolve')
 def resolve():
  company(request.args.get('company'))
  return jsonify(resolve_key(request.args.get('key',''),request.args.get('uf')))
 @app.post('/api/nfce/import')
 def ingest():
  from .fiscal_channels import store_file,FiscalFile
  from sqlalchemy import select
  from .db import log
  cid=request.form.get('company');co=company(cid,admin=True)
  upload=request.files.get('file')
  if not upload:raise ValueError('Selecione um XML NFC-e ou ZIP.')
  raw=upload.read(64*1024*1024+1)
  if len(raw)>64*1024*1024:raise ValueError('Arquivo excede 64 MB.')
  results=[]
  def one(name,data):
   try:
    meta=validate_original(data,co.document)
    digest=hashlib.sha256(data).hexdigest()
    old=g.s.scalar(select(FiscalFile.id).where(FiscalFile.company_id==cid,FiscalFile.service=='nfce',FiscalFile.sha256==digest))
    with g.s.begin_nested():row=store_file(g.s,storage,cid,'nfce',data)
    results.append(dict(file=name[:240],id=row.id,key=meta['key'],duplicate=bool(old),signature=meta['signature']))
   except (ValueError,etree.XMLSyntaxError) as ex:results.append(dict(file=name[:240],error=str(ex)[:400]))
  if raw[:2]==b'PK':
   try:
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
     entries=z.infolist()
     if len(entries)>5000 or sum(x.file_size for x in entries)>128*1024*1024:raise ValueError('ZIP excede 5.000 entradas ou 128 MB descompactados.')
     for x in entries:
      if x.is_dir() or not x.filename.lower().endswith('.xml'):continue
      if x.file_size>8*1024*1024 or x.flag_bits&1 or x.compress_type not in (0,8) or x.file_size/max(1,x.compress_size)>200:
       results.append(dict(file=x.filename[:240],error='Entrada excessiva, cifrada ou compressão não suportada.'));continue
      one(x.filename,z.read(x))
   except (zipfile.BadZipFile,RuntimeError,NotImplementedError):raise ValueError('ZIP inválido.') from None
  else:one(upload.filename or 'nota.xml',raw)
  if not results:raise ValueError('Nenhum XML encontrado.')
  added=sum('error' not in x and not x.get('duplicate') for x in results)
  failed=sum('error' in x for x in results)
  log(g.s,cid,g.user.id,'nfce_importada',str(added)+' importados; '+str(failed)+' falhas')
  g.s.commit()
  return jsonify(added=added,failed=failed,duplicates=sum(bool(x.get('duplicate')) for x in results),items=results)
