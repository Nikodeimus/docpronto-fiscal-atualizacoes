"""Explicit source coverage and original-file ingestion for historical NF-e."""
import hashlib,io,json,time,zipfile
from flask import g,request,jsonify
from lxml import etree
from sqlalchemy import select
from .db import Setting,log
from .fiscal_history import FiscalArchive,archive_xml
from .distribution import xml_root

STATES=dict(x.split(':') for x in '11:RO 12:AC 13:AM 14:RR 15:PA 16:AP 17:TO 21:MA 22:PI 23:CE 24:RN 25:PB 26:PE 27:AL 28:SE 29:BA 31:MG 32:ES 33:RJ 35:SP 41:PR 42:SC 43:RS 50:MS 51:MT 52:GO 53:DF'.split())
TYPES={
 'nfe':dict(name='NF-e · modelo 55',automatic=True,mode='Distribuição nacional pelo certificado e NSU',note='Disponível para as 27 UFs. Recebe o que o Ambiente Nacional disponibiliza ao titular; período escolhido não comprova cobertura integral.',url='https://www.nfe.fazenda.gov.br/portal/principal.aspx'),
 'nfce':dict(name='NFC-e · modelo 65',automatic=False,mode='Importação XML/ZIP e consulta pública',note='Importação de XML modelo 65 disponível para as 27 UFs. Consulte a cobertura por UF em Outros documentos fiscais. Links podem abrir o portal estadual ou uma consulta pública. Não há captura nacional automática integrada.',url='https://www.nfe.fazenda.gov.br/portal/webServices.aspx?tipoConteudo=OUC%2FYVNWZfo%3D'),
 'cte':dict(name='CT-e · modelo 57',automatic=True,mode='Distribuição própria pelo certificado e NSU',note='Canal dedicado de CT-e, ativado por empresa, com conector atualizado. Protocolo verificado em testes simulados; disponibilidade real depende da SEFAZ e do vínculo do certificado.',url='https://www.cte.fazenda.gov.br/portal/'),
 'nfse':dict(name='NFS-e · serviços',automatic=True,mode='Distribuição ADN por CNPJ e NSU',note='Captura pelo Ambiente Nacional com certificado e CNPJ. Depende dos documentos compartilhados no ADN; não cobre automaticamente todos os municípios. CPF não suportado nesta integração.',url='https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/documentacao-atual')}

def register_fiscal_sources(app,company,storage):
 @app.get('/api/fiscal/sources')
 def sources():
  cid=request.args.get('company');company(cid)
  return jsonify(states=[dict(code=k,uf=v,nfe='national',nfce='state_portal',cte='dedicated_distribution',nfse='municipal_or_national') for k,v in STATES.items()],types=TYPES,reviewed='2026-09-29',historical_note='Para períodos antigos, importe XMLs originais fornecidos pelo cliente, emitente ou sistema anterior. Certificado e chave não garantem que um arquivo antigo ainda esteja disponível na SEFAZ.')

 @app.post('/api/history/import')
 def import_history():
  cid=request.form.get('company');co=company(cid,admin=True)
  upload=request.files.get('file')
  if not upload:raise ValueError('Selecione um XML original ou ZIP de XMLs NF-e.')
  raw=upload.read(64*1024*1024+1)
  if len(raw)>64*1024*1024:raise ValueError('Arquivo excede 64 MB.')
  results=[]
  def one(name,content,defer=False):
   try:
    if len(content)>8*1024*1024:raise ValueError('XML excede 8 MB.')
    root=xml_root(content);kind=etree.QName(root).localname
    ns={'n':'http://www.portalfiscal.inf.br/nfe'}
    if kind not in ('nfeProc','resNFe','procEventoNFe','resEvento'):raise ValueError('Importe NF-e processada, resumo ou evento NF-e. Este tipo não está integrado.')
    if kind=='nfeProc':
     parties=[root.findtext('.//n:'+part+'/n:'+field,namespaces=ns) for part in ('emit','dest') for field in ('CNPJ','CPF')]
     from .fiscal import third_party_roles
     inf=root.find('n:NFe/n:infNFe',ns)
     if co.document not in parties and not (inf is not None and third_party_roles(inf,co.document)):raise ValueError('O CNPJ/CPF selecionado não participa desta nota.')
     if root.findtext('.//n:ide/n:mod',namespaces=ns)!='55':raise ValueError('Este importador é de NF-e modelo 55.')
    else:
     key=root.findtext('.//n:chNFe',namespaces=ns) or ''
     if not g.s.scalar(select(FiscalArchive.id).where(FiscalArchive.company_id==cid,FiscalArchive.key==key).limit(1)):
      if defer:return False
      raise ValueError('Importe primeiro a NF-e deste cadastro para vincular o resumo ou evento externo.')
    digest=hashlib.sha256(content).hexdigest()
    old=g.s.scalar(select(FiscalArchive).where(FiscalArchive.company_id==cid,FiscalArchive.sha256==digest))
    with g.s.begin_nested():
     row=archive_xml(g.s,storage,cid,content,co.document)
     if not old:g.s.add(Setting(key='history:origin:'+row.id,value=json.dumps(dict(source='manual_import',filename=str(name)[:240],imported_at=time.time()))))
    results.append(dict(file=str(name)[:240],id=row.id,key=row.key,duplicate=bool(old),warning=row.warning))
   except (ValueError,etree.XMLSyntaxError) as exc:results.append(dict(file=str(name)[:240],error=str(exc)[:400]))
  if raw[:2]==b'PK':
   try:
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
     entries=z.infolist()
     if len(entries)>5000 or sum(x.file_size for x in entries)>128*1024*1024:raise ValueError('Divida o ZIP: máximo de 5.000 entradas ou 128 MB descompactados.')
     # Originals before events even if event files sort first in the ZIP.
     selected=[x for x in entries if not x.is_dir() and x.filename.lower().endswith('.xml')]
     deferred=[]
     for x in selected:
      if x.file_size>8*1024*1024 or x.flag_bits&1 or x.compress_type not in (0,8) or x.file_size/max(1,x.compress_size)>200:
       results.append(dict(file=x.filename[:240],error='Entrada excessiva, cifrada ou compressão não suportada.'));continue
      try:
       if one(x.filename,z.read(x),defer=True) is False:deferred.append(x)
      except (zipfile.BadZipFile,RuntimeError,NotImplementedError):results.append(dict(file=x.filename[:240],error='Entrada ZIP inválida.'))
     for x in deferred:one(x.filename,z.read(x))
   except zipfile.BadZipFile:raise ValueError('ZIP inválido.') from None
  else:one(upload.filename or 'arquivo.xml',raw)
  if not results:raise ValueError('Nenhum XML encontrado.')
  added=sum('error' not in x and not x.get('duplicate') for x in results)
  duplicates=sum(x.get('duplicate',False) for x in results);failed=sum('error' in x for x in results)
  log(g.s,cid,g.user.id,'historico_importado',json.dumps(dict(added=added,duplicates=duplicates,failed=failed)))
  g.s.commit()
  return jsonify(added=added,duplicates=duplicates,failed=failed,items=results)
