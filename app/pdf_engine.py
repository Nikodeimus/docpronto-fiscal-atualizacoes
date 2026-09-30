"""Local, evidence-bearing DANFE extraction. Missing data is never guessed.

Profiles: labelled fields (LABEL: value) and pdftotext -layout DANFE cells.
Scanned documents use local OCR when installed. Reconstruction is a reviewable
import artifact, not a replacement for an authorized XML.
"""
import os
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path
from .fiscal import FiscalError, validate_key, digits

ENGINE_VERSION = 'danfe-local-1.0'
MAX_PAGES = 30
MAX_BYTES = 32 * 1024 * 1024
MAX_TEXT = 2 * 1024 * 1024


def _norm(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c)).upper().strip()


def _money(value):
    value = value.strip().replace('R$', '').replace(' ', '')
    if ',' in value:
        value = value.replace('.', '').replace(',', '.')
    try:
        number = Decimal(value)
        if not number.is_finite() or number < 0:
            return None
        return format(number, 'f')
    except InvalidOperation:
        return None


def review_spec():
    """Machine-readable review form: paths use groups-relative dot notation.

    '*' applies to every det; taxes require selecting the real ICMS/PIS/COFINS
    subgroup. No default zero, CST, origin, payment or address is provided.
    """
    result = []
    def fields(prefix, names, label):
        for name in names.split():
            result.append({'path': prefix + name, 'label': label + ' / ' + name,
                           'required': True, 'source_required': True})
    fields('ide.', 'cUF cNF natOp mod serie nNF dhEmi tpNF idDest cMunFG tpImp tpEmis cDV tpAmb finNFe indFinal indPres procEmi verProc', 'Identificação')
    fields('emit.', 'xNome IE CRT', 'Emitente')
    fields('dest.', 'xNome indIEDest', 'Destinatário')
    for group, address in [('emit', 'enderEmit'), ('dest', 'enderDest')]:
        result.append({'path': group + '.CNPJ|CPF', 'label': group + ' / documento', 'required': True, 'source_required': True})
        fields(group + '.' + address + '.', 'xLgr nro xBairro cMun xMun UF', group + ' / endereço')
    fields('det.*.prod.', 'cProd cEAN xProd NCM CFOP uCom qCom vUnCom vProd cEANTrib uTrib qTrib vUnTrib indTot', 'Produto')
    fields('total.ICMSTot.', 'vBC vICMS vICMSDeson vFCP vBCST vST vFCPST vFCPSTRet vProd vFrete vSeg vDesc vII vIPI vIPIDevol vPIS vCOFINS vOutro vNF', 'Totais')
    fields('transp.', 'modFrete', 'Transporte')
    fields('pag.detPag.*.', 'tPag vPag', 'Pagamento')
    result.extend([
        {'path': 'det.*.@nItem', 'label': 'Número do item', 'required': True, 'source_required': True},
        {'path': 'det.*.imposto.ICMS', 'label': 'ICMS: modalidade e campos reais', 'required': True, 'source_required': True},
        {'path': 'det.*.imposto.PIS', 'label': 'PIS: modalidade e campos reais', 'required': True, 'source_required': True},
        {'path': 'det.*.imposto.COFINS', 'label': 'COFINS: modalidade e campos reais', 'required': True, 'source_required': True},
    ])
    return result


def _empty(v):
    return v is None or v == '' or v == {} or v == []


def validate_review(invoice):
    """Return missing paths; fiscal.build_reconstructed performs value checks.

    This is a completeness gate for a domestic NF-e 4.00 profile, not full XSD
    validation. Specialized/export layouts must use original authorized XML.
    """
    if not isinstance(invoice, dict) or not isinstance(invoice.get("groups", {}), dict):
        raise FiscalError("Revisão precisa de objeto invoice com groups.")
    missing = []
    if _empty(invoice.get('key')):
        missing.append('key')
    groups = invoice.get('groups', {})
    def walk(node, parts, prefix):
        if not parts:
            if _empty(node): missing.append(prefix)
            return
        part, rest = parts[0], parts[1:]
        if part == '*':
            entries = node if isinstance(node, list) else [node] if isinstance(node, dict) and node else []
            if not entries:
                missing.append(prefix + '.*')
            for i, item in enumerate(entries): walk(item, rest, prefix + '.' + str(i))
            return
        if '|' in part:
            if not isinstance(node, dict) or not any(not _empty(node.get(k)) for k in part.split('|')):
                missing.append(prefix + '.' + part)
            return
        value = node.get(part) if isinstance(node, dict) else None
        walk(value, rest, (prefix + '.' + part).strip('.'))
    for spec in review_spec(): walk(groups, spec['path'].split('.'), 'groups')
    dets = groups.get('det', [])
    if isinstance(dets, dict): dets = [dets]
    if not isinstance(dets, list): dets = []
    for index, det in enumerate(dets):
        if not isinstance(det, dict): continue
        tax = det.get('imposto', {})
        if not isinstance(tax, dict): continue
        for family in ['ICMS', 'PIS', 'COFINS']:
            choice = tax.get(family, {})
            if not isinstance(choice, dict) or len(choice) != 1:
                missing.append(f'groups.det.{index}.imposto.{family}.modalidade'); continue
            kind, values = next(iter(choice.items()))
            if not isinstance(values, dict):
                missing.append(f'groups.det.{index}.imposto.{family}.{kind}'); continue
            required = ['orig', 'CSOSN' if kind.startswith('ICMSSN') else 'CST'] if family == 'ICMS' else ['CST']
            if family == 'ICMS':
                if kind in ('ICMS00', 'ICMS10', 'ICMS20', 'ICMS51', 'ICMS70', 'ICMS90', 'ICMSSN900'):
                    required += ['modBC', 'vBC', 'pICMS', 'vICMS']
                if kind in ('ICMS20', 'ICMS70'): required += ['pRedBC']
                if kind in ('ICMS10', 'ICMS30', 'ICMS70', 'ICMSSN201', 'ICMSSN202'):
                    required += ['modBCST', 'vBCST', 'pICMSST', 'vICMSST']
                if kind in ('ICMSSN101', 'ICMSSN201'): required += ['pCredSN', 'vCredICMSSN']
            elif kind.endswith('Aliq'): required += ['vBC', 'p' + family, 'v' + family]
            elif kind.endswith('Qtde'): required += ['qBCProd', 'vAliqProd', 'v' + family]
            elif kind.endswith('Outr'):
                required += ['v' + family]
                required += ['qBCProd', 'vAliqProd'] if 'qBCProd' in values else ['vBC', 'p' + family]
            for field in required:
                if _empty(values.get(field)): missing.append(f'groups.det.{index}.imposto.{family}.{kind}.{field}')
    dest=groups.get('dest') if isinstance(groups.get('dest'),dict) else {}
    if str(dest.get('indIEDest', '')) == '1' and _empty(dest.get('IE')):
        missing.append('groups.dest.IE')
    return list(dict.fromkeys(missing))


# These are exact semantic labels, never arbitrary nearby numbers.
LABELS = {
    'NATUREZA DA OPERACAO': ('ide.natOp', 'text'),
    'DATA DE EMISSAO': ('ide.dhEmi', 'datetime'),
    'DATA DA EMISSAO': ('ide.dhEmi', 'datetime'),
    'DATA E HORA DE EMISSAO': ('ide.dhEmi', 'datetime'),
    'VALOR TOTAL DOS PRODUTOS': ('total.ICMSTot.vProd', 'money'),
    'VALOR TOTAL DA NOTA': ('total.ICMSTot.vNF', 'money'),
    'BASE DE CALCULO DO ICMS': ('total.ICMSTot.vBC', 'money'),
    'VALOR DO ICMS': ('total.ICMSTot.vICMS', 'money'),
    'BASE DE CALCULO DO ICMS ST': ('total.ICMSTot.vBCST', 'money'),
    'VALOR DO ICMS ST': ('total.ICMSTot.vST', 'money'),
    'VALOR DO FRETE': ('total.ICMSTot.vFrete', 'money'),
    'VALOR DO SEGURO': ('total.ICMSTot.vSeg', 'money'),
    'DESCONTO': ('total.ICMSTot.vDesc', 'money'),
    'OUTRAS DESPESAS ACESSORIAS': ('total.ICMSTot.vOutro', 'money'),
    'VALOR TOTAL DO IPI': ('total.ICMSTot.vIPI', 'money'),
}


def extract_text(text, expected_key=None, source='pdf_text'):
    """Public pure parser, also used by deterministic labelled-text tests."""
    if len(text.encode('utf-8')) > MAX_TEXT: raise FiscalError('Texto PDF excede limite de extração.')
    lines = text.splitlines()
    invoice = {'key': None, 'groups': {}}
    evidence, conflicts = {}, []
    def put(path, value, line, method='label', confidence=0.96):
        if value is None or value == '': return
        node = invoice['groups']
        parts = path.split('.')
        for part in parts[:-1]: node = node.setdefault(part, {})
        if parts[-1] in node and node[parts[-1]] != value:
            conflicts.append({'path': 'groups.' + path, 'previous': node[parts[-1]], 'found': value, 'line': line + 1})
            return
        node[parts[-1]] = value
        evidence['groups.' + path] = {'source': source if method != 'access_key' else 'access_key', 'line': line + 1, 'method': method, 'confidence': confidence, 'excerpt': lines[line][:300] if lines and line >= 0 else ''}
    candidates = []
    for i, line in enumerate(lines):
        for match in re.finditer(r'(?<!\d)(?:\d{44}|\d{4}(?:[ .]+\d{4}){10})(?!\d)', line):
            try: key = validate_key(digits(match.group()))
            except FiscalError: continue
            candidates.append((key, i))
    keys = set(k for k, _ in candidates)
    if len(keys) > 1: raise FiscalError('PDF contém múltiplas chaves válidas; separar as notas antes da conversão.')
    expected = validate_key(expected_key) if expected_key else None
    if keys and expected and expected not in keys: raise FiscalError('A chave encontrada no PDF diverge da chave solicitada.')
    key = next(iter(keys)) if keys else expected
    if key:
        invoice['key'] = key
        line = candidates[0][1] if candidates else -1
        evidence['key'] = {'source': source if keys else 'user_input', 'line': line + 1, 'confidence': 1.0, 'method': 'validated_access_key'}
        # Encoded fields only: key does not supply names, dates, taxes or amounts.
        for field, value in {'cUF':key[:2], 'mod':key[20:22], 'serie':str(int(key[22:25])), 'nNF':str(int(key[25:34])), 'tpEmis':key[34], 'cNF':key[35:43], 'cDV':key[-1]}.items():
            put('ide.' + field, value, line, 'access_key', 1.0)
    section = None
    section_labels = {
        'CNPJ': 'CNPJ', 'CPF': 'CPF', 'CNPJ/CPF': 'document', 'CPF/CNPJ':'document',
        'NOME / RAZAO SOCIAL': 'xNome', 'NOME/RAZAO SOCIAL':'xNome', 'RAZAO SOCIAL': 'xNome',
        'INSCRICAO ESTADUAL': 'IE', 'BAIRRO/DISTRITO': 'address.xBairro', 'BAIRRO / DISTRITO':'address.xBairro',
        'MUNICIPIO':'address.xMun', 'UF':'address.UF', 'CEP':'address.CEP',
        'LOGRADOURO':'address.xLgr', 'NUMERO DO ENDERECO':'address.nro', 'CODIGO DO MUNICIPIO':'address.cMun',
    }
    for i, line in enumerate(lines):
        normalized = _norm(line)
        if 'DESTINATARIO' in normalized and ('REMETENTE' in normalized or normalized.rstrip(':') == 'DESTINATARIO'): section = 'dest'
        elif normalized.rstrip(':') in ('EMITENTE', 'IDENTIFICACAO DO EMITENTE'): section = 'emit'
        elif any(normalized.startswith(v) for v in ['CALCULO DO IMPOSTO','DADOS DOS PRODUTOS','TRANSPORTADOR','FATURA','DADOS ADICIONAIS']): section = None
        # One or more labelled cells split only on layout gaps, not normal spaces.
        cells = list(re.finditer(r'\S(?:.*?\S)?(?= {2,}|$)', line))
        for ci, cell in enumerate(cells):
            chunk = cell.group().strip()
            label, sep, inline = chunk.partition(':')
            label = _norm(label).rstrip('.')
            target = LABELS.get(label)
            if section and label in section_labels:
                field = section_labels[label]
                if field.startswith('address.'):
                    field = ('enderEmit.' if section == 'emit' else 'enderDest.') + field.split('.', 1)[1]
                target = (section + '.' + field, 'document' if field in ('CNPJ', 'CPF', 'document') else 'text')
            if target is None: continue
            value = inline.strip() if sep else ''
            if not value and i + 1 < len(lines):
                end = cells[ci + 1].start() if ci + 1 < len(cells) else len(lines[i+1])
                value = lines[i + 1][cell.start():end].strip()
            path, typ = target
            if typ == 'money': value = _money(value)
            elif typ == 'document':
                value = digits(value)
                if len(value) not in (11, 14): continue
                if path.endswith('.document'): path = path.rsplit('.', 1)[0] + ('.CPF' if len(value)==11 else '.CNPJ')
            elif typ == 'datetime':
                # Date-only DANFEs cannot provide the required time and UTC offset.
                if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:[+-]\d\d:\d\d|Z)', value):
                    evidence['unresolved.' + path] = {'source': source, 'line':i+1, 'excerpt':value[:100], 'reason':'Data sem hora/fuso ISO completo; informar conforme documento original.'}
                    continue
            if value and _norm(value) not in LABELS and _norm(value) not in section_labels:
                put(path, value, i, confidence=0.80 if source=='ocr' else 0.96)
    # Detect single-line product column headings in pipe or spatial layout tables.
    # Multiline/wrapped descriptions are left for review. No inferred tax modality.
    header_aliases = {'CODIGO':'cProd','COD. PROD.':'cProd','DESCRICAO':'xProd','DESCRICAO DO PRODUTO':'xProd','NCM':'NCM','NCM/SH':'NCM','CFOP':'CFOP','UN':'uCom','UNID':'uCom','QTD':'qCom','QUANTIDADE':'qCom','V.UNIT':'vUnCom','VALOR UNITARIO':'vUnCom','V.TOTAL':'vProd','VALOR TOTAL':'vProd','EAN':'cEAN','CODIGO PRODUTO':'cProd','COD. PRODUTO':'cProd','DESCRICAO DO PRODUTO / SERVICO':'xProd','DESCRICAO DO PRODUTO/SERVICO':'xProd','QUANT.':'qCom','VLR. UNIT.':'vUnCom','VLR. TOTAL':'vProd','VL. UNITARIO':'vUnCom','VL. TOTAL':'vProd'}
    for index, line in enumerate(lines):
        pipe = '|' in line
        columns = list(re.finditer(r'\S(?:.*?\S)?(?= {2,}|$)', line)) if not pipe else []
        labels = line.strip().strip('|').split('|') if pipe else [c.group() for c in columns]
        headers = [header_aliases.get(_norm(c)) for c in labels]
        if not all(x in headers for x in ('cProd','xProd','NCM','CFOP','qCom','vUnCom','vProd','uCom')): continue
        items = []
        for row_index in range(index+1, len(lines)):
            row = lines[row_index]
            if pipe:
                if '|' not in row: break
                cells = [v.strip() for v in row.strip().strip('|').split('|')]
            else:
                if not row.strip(): break
                cells = [row[c.start():columns[j+1].start() if j+1<len(columns) else len(row)].strip() for j,c in enumerate(columns)]
            if len(cells) != len(headers): break
            prod = {}
            for name, value in zip(headers, cells):
                if name:
                    value = _money(value) if name in ('qCom','vUnCom','vProd') else value
                    if value is not None: prod[name] = value
            if not re.fullmatch(r'\d{8}', prod.get('NCM','')) or not re.fullmatch(r'\d{4}', prod.get('CFOP','')): break
            item_index = len(items)
            items.append({'@nItem':str(item_index+1), 'prod':prod, 'imposto':{}})
            for name in prod:
                evidence[f'groups.det.{item_index}.prod.{name}'] = {'source':source,'line':row_index+1,'method':'labelled_table','confidence':0.75 if source=='ocr' else 0.95,'excerpt':row[:300]}
        if items:
            if invoice['groups'].get('det'): conflicts.append({'path':'groups.det', 'reason':'Múltiplas tabelas detectadas; revisar concatenação.'})
            else: invoice['groups']['det'] = items
    missing = validate_review(invoice)
    if expected and not keys:
        conflicts.append({'path':'key', 'reason':'Chave fornecida não foi localizada no texto do PDF; confirmar vínculo do documento.'})
    return {'text':text, 'invoice':invoice, 'missing':missing, 'evidence':evidence, 'conflicts':conflicts,
            'needs_ocr':not bool(text.strip()), 'engine_version':ENGINE_VERSION,
            'profile':'DANFE_LABELLED_V1' if any(v.get('method') in ('label', 'labelled_table') for v in evidence.values()) else 'UNRECOGNIZED',
            'warnings':['Revisar todos os campos extraídos. PDF pode omitir campos obrigatórios do XML; dados ausentes precisam de origem confirmada.', 'XML reconstruído não recebe assinatura ou protocolo fictícios.']}


def _run(args, timeout=45):
    try:
        result = subprocess.run(args, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FiscalError('Extração local indisponível ou excedeu tempo: ' + Path(args[0]).name) from exc
    if result.returncode: raise FiscalError('Falha na extração PDF local: ' + Path(args[0]).name)
    if len(result.stdout)>MAX_TEXT: raise FiscalError('Saída da extração excede limite.')
    return result.stdout


def extract_invoice(path, expected_key=None):
    deadline = time.monotonic() + 90
    def run(args, timeout=45):
        remaining = deadline - time.monotonic()
        if remaining <= 0: raise FiscalError("Extração excedeu limite total de 90 segundos.")
        return _run(args, min(timeout, remaining))
    path = Path(path).resolve()
    if not path.is_file() or path.stat().st_size>MAX_BYTES: raise FiscalError('PDF ausente ou excede 32 MB.')
    with path.open('rb') as stream:
        if stream.read(5)!=b'%PDF-': raise FiscalError('Arquivo não é PDF.')
    if not shutil.which('pdftotext') or not shutil.which('pdfinfo'):
        raise FiscalError('Instale poppler-utils (pdftotext e pdfinfo) no servidor.')
    info = run(['pdfinfo', str(path)]).decode('utf-8','replace')
    pages_match = re.search(r'^Pages:\s+(\d+)', info, re.M)
    if not pages_match or not 1<=int(pages_match.group(1))<=MAX_PAGES:
        raise FiscalError('PDF deve conter de 1 a 30 páginas; divida documentos maiores.')
    text = run(['pdftotext','-layout','-enc','UTF-8',str(path),'-']).decode('utf-8','replace')
    page_texts=text.split('\f')
    if page_texts and not page_texts[-1].strip(): page_texts.pop()
    needs_ocr = not page_texts or any(len(re.sub(r'\s','',page))<80 for page in page_texts)
    source='pdf_text'
    if needs_ocr and shutil.which('tesseract') and shutil.which('pdftoppm'):
        if int(pages_match.group(1))>8: raise FiscalError('OCR limitado a 8 páginas por documento; divida o PDF.')
        with tempfile.TemporaryDirectory(prefix='docpronto-ocr-') as temp:
            parts=[]
            for page in range(1,int(pages_match.group(1))+1):
                prefix=str(Path(temp)/'page')
                run(['pdftoppm','-f',str(page),'-l',str(page),'-scale-to','2400','-singlefile','-png',str(path),prefix],30)
                parts.append(run(['tesseract',prefix+'.png','stdout','--psm','3'],30).decode('utf-8','replace'))
                if sum(len(t) for t in parts)>MAX_TEXT: raise FiscalError('OCR excede limite de texto.')
            text='\n\f\n'.join(parts)
            source='ocr'
    result=extract_text(text,expected_key,source)
    result['needs_ocr']=needs_ocr and source!='ocr'
    result['ocr_used']=source=='ocr'
    result['pages']=int(pages_match.group(1))
    if result['needs_ocr']: result['warnings'].append('PDF digitalizado: instale tesseract-ocr e poppler-utils para extração local.')
    return result
