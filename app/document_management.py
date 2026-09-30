"""Company-scoped document queries, summaries and confirmed bulk removal."""
import hashlib,json,secrets,time,re
from datetime import date
from decimal import Decimal,InvalidOperation
from flask import g,request,jsonify
from sqlalchemy import select,func,JSON,delete
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy.ext.compiler import compiles
from .db import Document,Job,Setting,log
from .fiscal import FiscalError


class json_data(FunctionElement):
    """Expose JSON stored as Text without SQLite's destructive CAST AS JSON."""
    type=JSON()
    inherit_cache=True

@compiles(json_data)
def _json_data_default(element,compiler,**kw):
    return 'CAST('+compiler.process(list(element.clauses)[0],**kw)+' AS JSON)'

@compiles(json_data,'sqlite')
def _json_data_sqlite(element,compiler,**kw):
    return compiler.process(list(element.clauses)[0],**kw)

def filtered(query,values):
    data=json_data(Document.data)
    for field,operator in [('date_from','ge'),('date_to','le')]:
        value=values.get(field)
        if value:
            date.fromisoformat(value)
            issued=func.substr(data['issued_at'].as_string(),1,10)
            query=query.where(issued>=value if operator=='ge' else issued<=value)
    if values.get('date_from') and values.get('date_to') and values['date_from']>values['date_to']:
        raise FiscalError('Data inicial deve ser anterior à data final.')
    if values.get('model'):
        model=str(values['model'])
        if model not in ('55','65','57','67','59','62','NFS-e'):raise FiscalError('Modelo inválido.')
        query=query.where(func.coalesce(data['model'].as_string(),func.substr(Document.key,21,2))==model)
    direction=values.get('direction') or values.get('flow')
    if direction:
        if direction not in ('entrada','saida'):raise FiscalError('Direção inválida.')
        query=query.where(data['flow'].as_string()==direction)
    return query


def monthly_summary(session,cid):
    months={};undated=0
    stmt=select(Document.data).where(Document.company_id==cid).execution_options(yield_per=500)
    for raw in session.scalars(stmt):
        data=json.loads(raw);issued=data.get('issued_at','');flow=data.get('flow')
        if len(issued)<7 or flow not in ('entrada','saida'):
            undated+=1;continue
        month=issued[:7]
        item=months.setdefault(month,{'month':month,'received_count':0,'issued_count':0,'received_total':Decimal(0),'issued_total':Decimal(0)})
        prefix='received' if flow=='entrada' else 'issued'
        item[prefix+'_count']+=1
        try:value=Decimal(str(data.get('totals',{}).get('vNF','0')))
        except InvalidOperation:value=Decimal(0)
        if value.is_finite():item[prefix+'_total']+=value
    result=[]
    for key in sorted(months):
        item=months[key]
        for field in ('received_total','issued_total'):item[field]=format(item[field],'.2f')
        result.append(item)
    return {'months':result,'unclassified':undated,'coverage_note':'Histórico dos documentos armazenados; não comprova cobertura completa da fonte.'}


def snapshot(rows):
    digest=hashlib.sha256()
    for did,version in rows:digest.update(f'{did}:{version}\n'.encode())
    return digest.hexdigest()


def verify_deletion_password(data,limited):
    """All document removal paths share the same credential and attempt budget."""
    from werkzeug.security import check_password_hash
    if limited('delete-password-check:'+g.user.id,10):
        raise FiscalError('Muitas tentativas. Aguarde cinco minutos.')
    credential=g.s.get(Setting,'delete_password:'+g.user.id)
    if not credential:raise FiscalError('Cadastre sua senha de exclusão em Meus dados.')
    password=data.get('deletion_password','')
    if not isinstance(password,str) or not re.fullmatch(r'[A-Za-z0-9]{4}',password) or not check_password_hash(credential.value,password):
        raise FiscalError('Senha de exclusão incorreta.')


def register_management(app,company,payload,limited):
    @app.get('/api/documents/summary')
    def summary():
        cid=request.args.get('company');company(cid)
        return jsonify(monthly_summary(g.s,cid))

    def selection(cid,scope,ids):
        query=select(Document.id,Document.version).where(Document.company_id==cid)
        if scope=='selected':
            if not isinstance(ids,list) or not 1<=len(ids)<=1000 or any(not isinstance(x,str) for x in ids):
                raise FiscalError('Selecione de 1 a 1.000 registros ou use excluir todos.')
            query=query.where(Document.id.in_(set(ids)))
        elif scope!='all':raise FiscalError('Seleção inválida.')
        return query.order_by(Document.id)

    @app.post('/api/documents/delete-preview')
    def preview():
        d=payload();cid=d.get('company');co=company(cid,admin=True)
        scope=d.get('scope','selected');ids=d.get('ids',[])
        query=selection(cid,scope,ids);rows=g.s.execute(query).all()
        if scope=='selected' and len(rows)!=len(set(ids)):raise FiscalError('Seleção contém documento indisponível.')
        if not rows:raise FiscalError('Nenhum lançamento para excluir.')
        target=query.with_only_columns(Document.id).order_by(None)
        running=g.s.scalar(select(func.count()).select_from(Job).where(Job.document_id.in_(target),Job.state=='running'))
        token=secrets.token_urlsafe(24)
        entry={'company':cid,'user':g.user.id,'scope':scope,'ids':ids if scope=='selected' else [],'count':len(rows),'hash':snapshot(rows),'expires':time.time()+600}
        g.s.add(Setting(key='delete_preview:'+token,value=json.dumps(entry)));g.s.commit()
        return jsonify(token=token,count=len(rows),company_name=co.name,company_document=co.document,blocked_running=running,expires=entry['expires'])

    @app.get('/api/my-data')
    def my_data():
        return jsonify(email=g.user.email,deletion_password_set=g.s.get(Setting,'delete_password:'+g.user.id) is not None)

    @app.post('/api/my-data/deletion-password')
    def save_deletion_password():
        from werkzeug.security import check_password_hash,generate_password_hash
        d=payload()
        if limited('delete-password-settings:'+g.user.id,5):raise FiscalError('Muitas tentativas. Aguarde cinco minutos.')
        current=d.get('login_password','')
        password=d.get('new_password','')
        if not isinstance(current,str) or len(current)>256 or not check_password_hash(g.user.password,current):
            raise FiscalError('Senha de acesso incorreta.')
        if not isinstance(password,str) or not re.fullmatch(r'[A-Za-z0-9]{4}',password):
            raise FiscalError('Use exatamente 4 letras ou números (A–Z, a–z, 0–9).')
        key='delete_password:'+g.user.id
        row=g.s.get(Setting,key)
        hashed=generate_password_hash(password)
        if row:row.value=hashed
        else:g.s.add(Setting(key=key,value=hashed))
        log(g.s,None,g.user.id,'senha_exclusao_alterada','')
        g.s.commit()
        return jsonify(ok=True)

    @app.post('/api/documents/delete-batch')
    def remove():
        d=payload();cid=d.get('company');co=company(cid,admin=True)
        verify_deletion_password(d,limited)
        token=d.get('token','')
        if not isinstance(token,str) or len(token)>60:raise FiscalError('Confirmação inválida.')
        entry=g.s.scalar(select(Setting).where(Setting.key=='delete_preview:'+token).with_for_update())
        if not entry:raise FiscalError('Solicite uma nova confirmação de exclusão.')
        saved=json.loads(entry.value)
        if saved['company']!=cid or saved['user']!=g.user.id or saved['expires']<time.time():raise FiscalError('Confirmação expirada ou de outra empresa/usuário.')
        if type(d.get('confirm_count')) is not int or d['confirm_count']!=saved['count']:
            raise FiscalError('Confirme a quantidade exata de lançamentos.')
        query=selection(cid,saved['scope'],saved['ids'])
        target=query.with_only_columns(Document.id).order_by(None)
        # Serialize against claim(): acquire job locks before inspecting their state.
        jobs=g.s.scalars(select(Job).where(Job.document_id.in_(target)).order_by(Job.id).with_for_update()).all()
        if any(job.state=='running' for job in jobs):raise FiscalError('Há processamento em andamento. Aguarde sua conclusão antes de excluir.')
        rows=g.s.execute(query.with_for_update()).all()
        if len(rows)!=saved['count'] or snapshot(rows)!=saved['hash']:raise FiscalError('Os lançamentos mudaram. Confira uma nova prévia antes de excluir.')
        # Delete only the confirmed snapshot. New imports remain intact.
        ids=[row[0] for row in rows]
        for start in range(0,len(ids),500):
            batch=ids[start:start+500]
            g.s.execute(delete(Job).where(Job.document_id.in_(batch)))
            g.s.execute(delete(Document).where(Document.company_id==cid,Document.id.in_(batch)))
        log(g.s,cid,g.user.id,'lancamentos_excluidos',json.dumps({'count':len(ids),'scope':saved['scope'],'snapshot':saved['hash']}))
        g.s.delete(entry);g.s.commit()
        return jsonify(ok=True,deleted=len(ids),message='Lançamentos excluídos. Arquivos preservados conforme a política de retenção.')
