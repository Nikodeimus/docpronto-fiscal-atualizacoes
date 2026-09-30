import io,hashlib,zipfile
import pytest
from test_app import env
from app.nfce import resolve_key,validate_original,STATES

def key(uf='35',model='65'):
 base=uf+'2609'+'11222333000181'+model+'001'+'000000001'+'1'+'12345678'
 dv=11-sum(int(c)*(2+i%8) for i,c in enumerate(reversed(base)))%11
 return base+str(0 if dv>=10 else dv)

def xml(dest='11444777000161'):
 k=key()
 return f'<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><NFe><infNFe Id="NFe{k}"><ide><cUF>35</cUF><mod>65</mod></ide><emit><CNPJ>11222333000181</CNPJ></emit>{("<dest><CNPJ>"+dest+"</CNPJ></dest>") if dest else ""}</infNFe></NFe><protNFe><infProt><chNFe>{k}</chNFe><cStat>100</cStat></infProt></protNFe></nfeProc>'.encode()

def test_resolver_all_ufs_and_validation():
 for code,uf in STATES.items():assert resolve_key(key(code))['uf']==uf
 assert resolve_key(key())['verified']
 assert resolve_key(key('15'))['mode']=='official_navigation'
 assert len(__import__('app.nfce',fromlist=['PORTALS']).PORTALS)==27
 for bad in ('https://evil.test',key()[:-1]+'9',key(model='55')):
  with pytest.raises(ValueError):resolve_key(bad)
 with pytest.raises(ValueError):resolve_key(key(),'RJ')

def test_import_participation_and_protocol():
 assert validate_original(xml(),'11444777000161')['signature']=='NAO_VERIFICADA'
 assert validate_original(xml(''),'11222333000181')['key']==key()
 with pytest.raises(ValueError):validate_original(xml(''),'11444777000161')
 with pytest.raises(ValueError):validate_original(xml().replace(b'<cStat>100',b'<cStat>999'),'11444777000161')
 with pytest.raises(ValueError):validate_original(xml().replace(b'<mod>65',b'<mod>55'),'11444777000161')
 with pytest.raises(ValueError):validate_original(b'<!DOCTYPE a [<!ENTITY x SYSTEM "file:///secret">]>'+xml(),'11444777000161')

def test_nfce_api_batch_original_permissions(env):
 app,c,h,cid=env
 blob=io.BytesIO()
 with zipfile.ZipFile(blob,'w') as z:
  z.writestr('../../original.xml',xml());z.writestr('duplicate.xml',xml());z.writestr('bad.xml',b'<invalid')
 r=c.post('/api/nfce/import',data={'company':cid,'file':(io.BytesIO(blob.getvalue()),'batch.zip')},headers=h)
 assert r.status_code==200,r.json
 assert (r.json['added'],r.json['duplicates'],r.json['failed'])==(1,1,1)
 from app.fiscal_channels import FiscalFile
 with app.session_factory() as s:
  row=s.get(FiscalFile,r.json['items'][0]['id'])
  assert row.sha256==hashlib.sha256(xml()).hexdigest()
  assert app.storage.resolve(row.path).read_bytes()==xml()
 assert app.test_client().get('/api/nfce/coverage?company='+cid).status_code==401
 assert c.get('/api/nfce/resolve?company=missing&key='+key()).status_code in (400,403,404)
 assert len(c.get('/api/nfce/coverage?company='+cid).json['items'])==27
 assert c.post('/api/nfce/import',data={'company':cid,'file':(io.BytesIO(xml()),'a.xml')}).status_code==403


@pytest.mark.parametrize('transform', [
 lambda raw: raw.replace(b'Id="NFe', b'Id="'),
 lambda raw: raw.replace(b'<cStat>100</cStat>', b'<cStat>100</cStat><cStat>999</cStat>'),
 lambda raw: raw.replace(b'<mod>65</mod>', b'<mod>65</mod><mod>65</mod>'),
 lambda raw: ('<!DOCTYPE x [<!ENTITY x SYSTEM "file:///secret">]>'+raw.decode()).encode('utf-16'),
])
def test_reject_ambiguous_or_entity_xml(transform):
 with pytest.raises(ValueError):validate_original(transform(xml()),'11444777000161')

def test_viewer_cannot_import_or_access_other_company(env):
 app,c,h,cid=env
 c.post('/api/users',json={'company':cid,'email':'viewer@example.test','password':'long-viewer-password','role':'viewer'},headers=h)
 viewer=app.test_client();vh={'X-CSRF-Token':viewer.post('/api/login',json={'email':'viewer@example.test','password':'long-viewer-password'}).json['csrf']}
 assert viewer.post('/api/nfce/import',data={'company':cid,'file':(io.BytesIO(xml()),'nota.xml')},headers=vh).status_code==400
 assert viewer.get('/api/nfce/coverage?company='+cid).status_code==200
 other=c.post('/api/companies',json={'name':'Emitente','document':'11222333000181'},headers=h).json['id']
 assert viewer.get('/api/nfce/coverage?company='+other).status_code==400


def test_verified_portals_are_static_official_https():
 from app.nfce import PORTALS,SOURCES
 from urllib.parse import urlparse
 assert set(PORTALS)<=set(STATES.values())
 for code,state in STATES.items():
  result=resolve_key(key(code))
  if state in PORTALS:
   assert result['verified'] and result['source']==SOURCES[state]
   assert urlparse(result['url']).scheme==('http' if state=='MA' else 'https')
   if state=='MA':assert result['transport_warning']
   assert urlparse(result['url']).hostname.endswith('.gov.br')
   assert not result['automatic'] and result['availability']=='not_tested'
  else:assert not result['verified'] and result['url'] is None
