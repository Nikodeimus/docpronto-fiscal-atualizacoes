"""Read-only next steps based on observed state, never live SEFAZ claims."""
from .diagnostics import validity

def build_attention(row,now):
    items=[]
    def add(code,title,detail,page,label,level='warning',admin=False,pending=False):
        items.append(dict(code=code,title=title,detail=detail,page=page,label=label,level=level,admin=admin,pending=pending))
    cert=row.get('certificate','Não selecionado')
    if cert=='Não selecionado':
        add('certificate_missing','Certificado não configurado','Escolha o certificado que será usado neste cadastro.','certificates','Configurar certificado',admin=True)
    else:
        status=validity(row.get('valid_until'),now)
        if status=='expired':add('certificate_expired','Certificado vencido','Selecione um certificado válido para novas consultas.','certificates','Ver certificado','error',True)
        elif status=='unknown':add('certificate_unknown','Validade não confirmada','O conector não informou uma validade verificável.','certificates','Conferir certificado',admin=True)
        if not row.get('online'):add('connector_offline','Conector sem comunicação recente','Confira se o computador está ligado e conectado ao DocPronto.','integrations','Ver conexão',admin=True)
    if row.get('retry_at',0)>now:
        add('automatic_retry','Nova tentativa programada','O motor aguarda o prazo da próxima tentativa automática.','capture','Acompanhar tentativa','info',True)
    elif (row.get('last') or {}).get('state') in ('failed','expired'):
        add('query_failed','Última consulta não terminou','Confira o motivo registrado antes de repetir a consulta.','capture','Ver consulta','error',True)
    if row.get('batches',{}).get('paused',0):add('capture_paused','Captura pausada','A consulta depende de retomada ou correção do motivo da pausa.','capture','Ver lotes pausados',admin=True)
    pending=row.get('notes',{}).get('pending',0)
    if pending:
        detail='O resumo foi recebido, mas o XML completo ainda não está no histórico. A causa de liberação não foi confirmada.'
        if not row.get('capture_enabled'):detail+=' A rotina automática não está ativada neste cadastro.'
        add('pending_xml',f'{pending} nota(s) aguardando XML',detail,'history','Ver XMLs pendentes','info',pending=True)
    if row.get('documents',{}).get('erro',0):add('document_errors','Documentos com erro','Existem documentos que precisam de análise.','documents','Ver documentos','error')
    if row.get('documents',{}).get('revisao',0):add('document_review','Documentos para conferência','Confira os dados antes de finalizar o processamento.','documents','Conferir documentos')
    return sorted(items,key=lambda x:{'error':0,'warning':1,'info':2}[x['level']])
