"""Adapters are intentionally explicit. No fabricated FSist or DocPronto endpoint."""
from pathlib import Path
import os,re,subprocess,tempfile,json,shlex
class ProviderPending(Exception):pass
class ProviderFailure(Exception):pass
class FsistProvider:
    def get_document(self,key):
        raise ProviderPending('Consulta manual necessária no FSist. Nenhuma API oficial de lote foi configurada. Abra o FSist e anexe o PDF ou XML obtido com autorização.')
class DocProntoProvider:
    def convert(self,pdf_path):
        # Administrator-managed local adapter command, never from an HTTP request.
        command=os.getenv('DOCPRONTO_ADAPTER_COMMAND')
        if not command:
            from .pdf_engine import extract_invoice
            from .fiscal import build_reconstructed
            result=extract_invoice(pdf_path)
            if result['missing'] or result['conflicts']:raise ProviderPending('Leitura local disponível. Complete e confira os campos fiscais no formulário.')
            return build_reconstructed(result['invoice'],None)[0]
        with tempfile.TemporaryDirectory() as d:
            output=Path(d)/'result.xml'
            try:r=subprocess.run(shlex.split(command)+['--input',str(pdf_path),'--output',str(output)],creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), capture_output=True,timeout=90,check=False)
            except (OSError,subprocess.TimeoutExpired) as ex:raise ProviderFailure('Adaptador local indisponível ou excedeu 90 segundos.') from ex
            if r.returncode or not output.is_file():raise ProviderFailure('Adaptador não produziu XML. Consulte seu log local, sem credenciais.')
            if output.stat().st_size>8*1024*1024:raise ProviderFailure('XML do adaptador excede 8 MB.')
            return output.read_bytes()

def extract_pdf(path):
    # Run text extraction in a bounded subprocess; PDFs may contain hostile streams.
    try:
        result=subprocess.run(['pdftotext','-layout','-nopgbrk',str(path),'-'],creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), capture_output=True,timeout=25)
    except FileNotFoundError:raise ProviderPending('Instale Poppler/pdftotext para extração local. PDF preservado.')
    except subprocess.TimeoutExpired:raise ProviderFailure('Extração do PDF excedeu o limite de 25 segundos.')
    if result.returncode:raise ProviderFailure('PDF inválido, protegido ou ilegível.')
    text=result.stdout.decode('utf-8',errors='replace')[:250000]
    if len(text.strip())<40:return {'text':text,'needs_ocr':True,'message':'PDF digitalizado: OCR não configurado. Informe os dados após conferir o documento.'}
    keys=list(dict.fromkeys(re.findall(r'(?<!\d)(?:\d{4}[ \t]*){11}(?!\d)',text)))
    return {'text':text,'candidate_keys':[''.join(re.findall(r'\d',k)) for k in keys],'needs_ocr':False,'message':'Texto extraído. Os campos fiscais exigem conferência antes de gerar XML.'}


def validate_pdf(raw):
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'document.pdf';path.write_bytes(raw)
        try:r=subprocess.run(['pdfinfo',str(path)],creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), capture_output=True,timeout=15)
        except FileNotFoundError:raise ProviderPending('Instale Poppler/pdfinfo para validar PDFs antes de anexar.')
        except subprocess.TimeoutExpired:raise ProviderFailure('Validação PDF excedeu 15 segundos.')
        if r.returncode:raise ProviderFailure('PDF inválido, protegido ou corrompido. Anexo recusado.')
        pages=re.search(rb'^Pages:\s+(\d+)',r.stdout,re.M)
        if not pages or int(pages.group(1))>500:raise ProviderFailure('PDF sem páginas ou acima de 500 páginas.')
