import unittest
import tempfile
from pathlib import Path
import shutil
from unittest.mock import patch
from app.pdf_engine import extract_text, extract_invoice, validate_review, review_spec
from app.fiscal import FiscalError


def make_key(number='000026950'):
    stem='3525121122233300018155101'+number+'1'+'92501772'
    check=11-sum(int(c)*(2+i%8) for i,c in enumerate(reversed(stem)))%11
    return stem+str(0 if check>=10 else check)


class PDFEngineTests(unittest.TestCase):
    def test_labelled_document_preserves_zero_and_real_identities(self):
        raw=f'''CHAVE DE ACESSO: {make_key()}
EMITENTE
RAZAO SOCIAL: Empresa Original Ltda
CNPJ: 88.305.859/0004-00
DESTINATARIO / REMETENTE
NOME / RAZAO SOCIAL: Cliente Real Ltda
CNPJ/CPF: 28.988.409/0001-87
CALCULO DO IMPOSTO
BASE DE CALCULO DO ICMS: 0,00
VALOR DO ICMS: 0,00
VALOR TOTAL DOS PRODUTOS: 1.234,56
VALOR TOTAL DA NOTA: 1.234,56
'''
        result=extract_text(raw)
        groups=result['invoice']['groups']
        self.assertEqual(groups['emit']['CNPJ'],'11222333000181')
        self.assertEqual(groups['dest']['CNPJ'],'28988409000187')
        self.assertEqual(groups['total']['ICMSTot']['vICMS'],'0.00')
        self.assertEqual(groups['total']['ICMSTot']['vNF'],'1234.56')
        self.assertIn('groups.ide.dhEmi',result['missing'])
        self.assertIn('groups.det.*',result['missing'])
        self.assertEqual(result['evidence']['groups.ide.serie']['source'],'access_key')
        self.assertNotIn('Signature',groups)

    def test_expected_key_not_used_to_manufacture_issuer(self):
        result=extract_text('Documento sem campos fiscais',make_key())
        self.assertNotIn('emit',result['invoice']['groups'])
        self.assertIn('groups.emit.CNPJ|CPF',result['missing'])
        self.assertEqual(result['evidence']['key']['source'],'user_input')

    def test_key_mismatch_rejected(self):
        with self.assertRaisesRegex(FiscalError,'diverge'):
            extract_text(make_key(),make_key('000026951'))

    def test_multiple_invoices_rejected(self):
        with self.assertRaisesRegex(FiscalError,'múltiplas'):
            extract_text(make_key()+'\n'+make_key('000026951'))

    def test_date_only_requires_review(self):
        result=extract_text('DATA DE EMISSAO: 01/12/2025')
        self.assertNotIn('ide',result['invoice']['groups'])
        self.assertIn('unresolved.ide.dhEmi',result['evidence'])

    def test_complete_iso_date(self):
        result=extract_text('DATA E HORA DE EMISSAO: 2025-12-01T14:32:00-03:00')
        self.assertEqual(result['invoice']['groups']['ide']['dhEmi'],'2025-12-01T14:32:00-03:00')

    def test_layout_cells(self):
        result=extract_text('VALOR DO ICMS       VALOR TOTAL DA NOTA\n0,00                45,50')
        self.assertEqual(result['invoice']['groups']['total']['ICMSTot']['vICMS'],'0.00')
        self.assertEqual(result['invoice']['groups']['total']['ICMSTot']['vNF'],'45.50')

    def test_labelled_item_table(self):
        result=extract_text('CODIGO | DESCRICAO | NCM | CFOP | UN | QTD | V.UNIT | V.TOTAL\nA001 | Produto teste | 12345678 | 5102 | UN | 2,000 | 0,00 | 0,00\n')
        item=result['invoice']['groups']['det'][0]
        self.assertEqual(item['prod']['vProd'],'0.00')
        self.assertEqual(item['prod']['qCom'],'2.000')
        self.assertEqual(item['imposto'],{})
        self.assertIn('groups.det.0.imposto.ICMS',result['missing'])
        self.assertNotIn('cEANTrib',item['prod'])

    def test_conflicting_totals_reported(self):
        result=extract_text('VALOR TOTAL DA NOTA: 12,00\nVALOR TOTAL DA NOTA: 13,00')
        self.assertEqual(result['conflicts'][0]['path'],'groups.total.ICMSTot.vNF')

    def test_review_zero_is_present_and_tax_fields_required(self):
        invoice={'key':make_key(),'groups':{'total':{'ICMSTot':{'vICMS':0}},'det':[{'imposto':{'ICMS':{'ICMS20':{'CST':'20','orig':'0','vBC':'0','pICMS':'0','vICMS':'0','modBC':'3'}}}}]}}
        missing=validate_review(invoice)
        self.assertNotIn('groups.total.ICMSTot.vICMS',missing)
        self.assertIn('groups.det.0.imposto.ICMS.ICMS20.pRedBC',missing)

    def test_non_pdf_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'fake.pdf';path.write_text('Not a PDF')
            with self.assertRaisesRegex(FiscalError,'não é PDF'):extract_invoice(path)

    def test_reference_xml_review_completeness(self):
        from app.fiscal import flatten, N
        from lxml import etree as E
        root=E.parse(str(Path(__file__).parent/'fixtures'/'reference.xml'))
        node=root.find('.//n:infNFe',N)
        invoice={'key':node.get('Id')[3:],'groups':flatten(node)}
        items=invoice['groups']['det']
        items=[items] if isinstance(items,dict) else items
        for item,element in zip(items,node.findall('n:det',N)):
            item['@nItem']=element.get('nItem')
        self.assertEqual(validate_review(invoice),[])

    def test_real_pdf_toolchain(self):
        if not shutil.which('pdftotext') or not shutil.which('pdfinfo'):
            self.skipTest('Poppler not installed')
        try:
            from reportlab.pdfgen import canvas
        except ImportError:
            self.skipTest('Reportlab unavailable for synthetic PDF fixture')
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'danfe.pdf'
            pdf=canvas.Canvas(str(path))
            lines=[f'CHAVE DE ACESSO: {make_key()}', 'EMITENTE',
                   'RAZAO SOCIAL: Companhia de Teste Real', 'CNPJ: 88.305.859/0004-00',
                   'CALCULO DO IMPOSTO', 'VALOR DO ICMS: 0,00', 'VALOR TOTAL DA NOTA: 123,45']
            for i,line in enumerate(lines): pdf.drawString(25,800-i*20,line)
            pdf.save()
            result=extract_invoice(path,make_key())
            self.assertEqual(result['invoice']['groups']['total']['ICMSTot']['vNF'],'123.45')
            self.assertFalse(result['ocr_used'])
            self.assertTrue(result['missing'])

    def test_review_spec_no_invented_defaults(self):
        self.assertTrue(review_spec())
        self.assertTrue(all('default' not in field for field in review_spec()))

if __name__=='__main__': unittest.main()
