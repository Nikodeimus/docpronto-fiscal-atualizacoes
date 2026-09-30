"""Readable fiscal mirror from original XML; explicitly not an issuer's PDF."""
import io
from xml.sax.saxutils import escape
from lxml import etree
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,LongTable,TableStyle

def render_pdf(raw,source='IMPORTED'):
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('XML inseguro.')
    root=etree.fromstring(raw,etree.XMLParser(resolve_entities=False,no_network=True))
    ns={'n':'http://www.portalfiscal.inf.br/nfe'};inf=root.find('.//n:infNFe',ns)
    if inf is None:raise ValueError('XML sem NF-e completa.')
    styles=getSampleStyleSheet();story=[]
    styles['BodyText'].fontSize=9;styles['BodyText'].leading=12
    labels={'nNF':'Número','serie':'Série','dhEmi':'Emissão','dhSaiEnt':'Saída/entrada','natOp':'Natureza da operação','xNome':'Nome','xFant':'Nome fantasia','xLgr':'Logradouro','nro':'Número','xCpl':'Complemento','xBairro':'Bairro','xMun':'Município','fone':'Telefone','vNF':'Valor total da nota','vProd':'Produtos','vBC':'Base ICMS','vICMS':'ICMS','vFrete':'Frete','vDesc':'Desconto','vIPI':'IPI','vPIS':'PIS','vCOFINS':'COFINS','vOutro':'Outras despesas','infCpl':'Informações complementares','nProt':'Protocolo','dhRecbto':'Recebimento','xMotivo':'Motivo'}
    def para(text,style='BodyText'):return Paragraph(escape(str(text)),styles[style])
    def get(node,path):return node.findtext(path,default='',namespaces=ns)
    def block(title,node):
        if node is None:return
        story.append(para(title,'Heading2'))
        values=[(labels.get(etree.QName(e).localname,etree.QName(e).localname),e.text) for e in node.iter() if len(e)==0 and e.text]
        rows=[[para(label),para(value)] for label,value in values]
        if rows:
            table=LongTable(rows,colWidths=[130,365]);table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('ROWBACKGROUNDS',(0,0),(-1,-1),[colors.HexColor('#f6f7f9'),colors.white]),('LEFTPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),3)]));story.append(table)
    story.append(para('DocPronto | Espelho da NF-e','Title'))
    story.append(para('PDF gerado a partir do XML. Não é o PDF original do emitente nem substitui o DANFE.'))
    if source=='RECONSTRUCTED':story.append(para('Origem: XML reconstruído de PDF e conferido pelo usuário.'))
    story.append(para('Chave: '+inf.get('Id','').removeprefix('NFe')))
    for title,tag in [('Identificação','ide'),('Emitente','emit'),('Destinatário','dest')]:block(title,inf.find('n:'+tag,ns))
    story.append(para('Produtos e serviços','Heading2'))
    rows=[[para(x) for x in ['Item / produto','CFOP / NCM','Quantidade','Valor']]]
    for det in inf.findall('n:det',ns):
        prod=det.find('n:prod',ns)
        if prod is None:continue
        rows.append([para(det.get('nItem','')+' - '+get(prod,'n:xProd')),para(get(prod,'n:CFOP')+' / '+get(prod,'n:NCM')),para(get(prod,'n:qCom')+' '+get(prod,'n:uCom')),para(get(prod,'n:vProd'))])
    table=LongTable(rows,colWidths=[240,90,80,85],repeatRows=1)
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#f3e7ec')),('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),.3,colors.lightgrey)]));story.append(table)
    for det in inf.findall('n:det',ns):
        block('Tributos do item '+det.get('nItem',''),det.find('n:imposto',ns))
        block('Informações adicionais do item',det.find('n:infAdProd',ns))
    for title,tag in [('Totais','total'),('Transporte','transp'),('Cobrança','cobr'),('Pagamento','pag'),('Informações adicionais','infAdic')]:block(title,inf.find('n:'+tag,ns))
    block('Protocolo presente no arquivo (não verificado)',root.find('.//n:infProt',ns))
    out=io.BytesIO()
    def footer(canvas,doc):
        canvas.setFont('Helvetica',8);canvas.drawString(36,22,'Espelho fiscal gerado pelo DocPronto');canvas.drawRightString(A4[0]-36,22,str(doc.page))
    SimpleDocTemplate(out,pagesize=A4,rightMargin=36,leftMargin=36,topMargin=36,bottomMargin=40).build(story,onFirstPage=footer,onLaterPages=footer)
    return out.getvalue()
