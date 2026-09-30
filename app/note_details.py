"""Evidence from preserved files; never substitutes a live SEFAZ check."""
import json,re,hashlib
from flask import g,request,jsonify
from sqlalchemy import select,or_,func
from lxml import etree
from .fiscal import validate_key


def search_notes(query,term):
 from .fiscal_history import FiscalArchive as A
 from .document_management import json_data
 if not isinstance(term,str) or len(term)>160:raise ValueError('Busca deve ter até 160 caracteres.')
 term=term.strip()
 if not term:return query
 fields=[A.key,func.substr(A.key,26,9),func.substr(A.key,7,14)]
 fields.extend(json_data(A.data)[name].as_string() for name in ('number','issuer_name','issuer_document','recipient_name','recipient_document'))
 return query.where(or_(*(field.icontains(term,autoescape=True) for field in fields)))


def register_note_details(app,company,storage):
 from .fiscal_history import FiscalArchive as A,note_query,history_period
 from .distribution import xml_root
 from .db import Setting
 ns={'n':'http://www.portalfiscal.inf.br/nfe'}
 @app.get('/api/history/notes/<key>')
 def details(key):
  cid=request.args.get('company');company(cid);validate_key(key)
  rows=list(g.s.scalars(select(A).where(A.company_id==cid,A.key==key).order_by(A.created,A.id)))
  if not rows:raise ValueError('Nota não encontrada nesta empresa.')
  versions=[];events=[];authorized=False;cancelled=False
  for row in rows:
   item={'id':row.id,'kind':row.kind,'created':row.created,'sha256':row.sha256,'warning':row.warning,'available':True,'origin':'Arquivo preservado no histórico','signature_validation':'not_verified'}
   origin=g.s.get(Setting,'history:origin:'+row.id)
   if origin:
    try:
     source=json.loads(origin.value)
     if isinstance(source,dict) and source.get('source')=='manual_import':item['origin']='Importação manual: '+str(source.get('filename','XML'))
    except (ValueError,TypeError):pass
   try:
    raw=storage.resolve(row.path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=row.sha256:raise ValueError('Integridade divergente')
    root=xml_root(raw)
    protocol=root.find('.//n:protNFe/n:infProt',ns)
    if protocol is not None and protocol.findtext('n:chNFe',namespaces=ns)==key:
     status=protocol.findtext('n:cStat',namespaces=ns);number=protocol.findtext('n:nProt',namespaces=ns)
     item['protocol_status']=status;item['protocol']=number
     if status in ('100','150') and number:authorized=True
    for ret in root.findall('.//n:retEvento/n:infEvento',ns):
     if ret.findtext('n:chNFe',namespaces=ns)!=key:continue
     kind=ret.findtext('n:tpEvento',namespaces=ns) or ''
     status=ret.findtext('n:cStat',namespaces=ns) or ''
     protocol=ret.findtext('n:nProt',namespaces=ns) or ''
     accepted=status=='135' and bool(protocol)
     events.append({'id':row.id,'type':kind,'label':{'110111':'Cancelamento','110110':'Carta de correção'}.get(kind,'Evento '+kind),'status':status,'accepted':accepted,'protocol':protocol,'registered_at':ret.findtext('n:dhRegEvento',namespaces=ns) or '', 'message':ret.findtext('n:xMotivo',namespaces=ns) or ''})
     if kind=='110111' and accepted:cancelled=True
   except (OSError,ValueError,etree.XMLSyntaxError):item['available']=False
   versions.append(item)
  chosen=g.s.scalar(note_query(cid).where(A.key==key))
  availability='pending_xml'
  if chosen and chosen.kind=='nfeProc':
   availability='complete' if next((v['available'] for v in versions if v['id']==chosen.id),False) else 'unavailable'
  return jsonify(key=key,data=json.loads(chosen.data) if chosen else {},availability=availability,fiscal_status='cancelled_in_file' if cancelled else 'authorized_in_file' if authorized else 'unknown',versions=versions,events=events,signature_validation='not_verified',message='Situação baseada somente nos protocolos dos arquivos preservados. Assinaturas não verificadas; não houve consulta atual à SEFAZ.')
 @app.post('/api/history/compare')
 def compare():
  body=request.get_json(silent=True)
  if not isinstance(body,dict):raise ValueError('Informe a lista de chaves.')
  cid=body.get('company');company(cid)
  supplied=body.get('keys')
  if isinstance(supplied,str):
   if len(supplied)>500000:raise ValueError('Compare até 10.000 chaves por vez.')
   supplied=[x for x in re.split(r'[\s;,]+',supplied.strip()) if x]
  if not isinstance(supplied,list) or not 1<=len(supplied)<=10000 or any(not isinstance(x,str) or len(x)>160 for x in supplied):raise ValueError('Informe de 1 a 10.000 chaves.')
  unique=set();invalid=[];duplicates=0
  for key in supplied:
   try:validate_key(key)
   except ValueError:invalid.append(key);continue
   if key in unique:duplicates+=1
   unique.add(key)
  base=note_query(cid)
  # Scope first, canonicalize before date filtering, then compare only supplied keys.
  period=history_period(base,body)
  result={'complete':[],'summaries':[],'missing':[],'outside_period':[],'invalid':invalid,'duplicates':duplicates,'coverage':'not_confirmed','message':'Comparação somente com a lista fornecida e os arquivos locais; não comprova todas as notas do período.'}
  ordered=sorted(unique)
  for offset in range(0,len(ordered),400):
   keys=ordered[offset:offset+400]
   found={r.key:r.kind for r in g.s.scalars(period.where(A.key.in_(keys)))}
   anywhere=set(g.s.scalars(select(A.key).where(A.company_id==cid,A.key.in_(keys),A.id.in_(base.with_only_columns(A.id)))))
   for key in keys:
    bucket='complete' if found.get(key)=='nfeProc' else 'summaries' if key in found else 'outside_period' if key in anywhere else 'missing'
    result[bucket].append(key)
  return jsonify(result)
