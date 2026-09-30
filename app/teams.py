"""Organization membership and single-use invitations; no mail is sent."""
import hashlib
import re
import secrets
import time

from flask import g, jsonify, request
from sqlalchemy import Boolean, Float, ForeignKey, String, delete, func, or_, select, update
from sqlalchemy.orm import Mapped, mapped_column
from werkzeug.security import generate_password_hash

from .db import Base, Company, Login, Member, ServiceProvider, User, log, uid


ROLES=('admin','operator','viewer')


class ProviderMember(Base):
    __tablename__='provider_members'
    user_id:Mapped[str]=mapped_column(ForeignKey('users.id'),primary_key=True)
    provider_id:Mapped[str]=mapped_column(ForeignKey('service_providers.id'),primary_key=True)
    role:Mapped[str]=mapped_column(String(20))


class TeamInvite(Base):
    __tablename__='team_invites'
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=uid)
    provider_id:Mapped[str]=mapped_column(ForeignKey('service_providers.id'),index=True)
    email:Mapped[str]=mapped_column(String(250))
    role:Mapped[str]=mapped_column(String(20))
    token_hash:Mapped[str]=mapped_column(String(64),unique=True)
    expires:Mapped[float]=mapped_column(Float)
    created_by:Mapped[str]=mapped_column(ForeignKey('users.id'))
    used_at:Mapped[float|None]=mapped_column(Float,nullable=True)
    revoked:Mapped[bool]=mapped_column(Boolean,default=False)


def provider_role(session,user,pid):
    if not user or not isinstance(pid,str):return None
    provider=session.get(ServiceProvider,pid)
    if not provider:return None
    user_id=user.id if isinstance(user,User) else user
    if provider.owner_id==user_id:return 'admin'
    membership=session.get(ProviderMember,(user_id,pid))
    return membership.role if membership and membership.role in ROLES else None


def lock_provider(session,pid):
    """Serialize company creation and team grants on their shared organization.

    Acquire this before writing companies/members or reading their membership
    snapshot. PostgreSQL READ COMMITTED then sees the preceding transaction's
    new company or membership. no_autoflush avoids inserting pending FK rows
    before the organization lock is held.
    """
    if not isinstance(pid,str):raise ValueError('Organização indisponível.')
    with session.no_autoflush:
        provider=session.scalar(select(ServiceProvider).where(ServiceProvider.id==pid).with_for_update())
    if not provider:raise ValueError('Organização indisponível.')
    return provider


def grant_company_members(session,company_id,provider):
    """Grant the organization's users access only to an already-bound company."""
    provider=lock_provider(session,provider if isinstance(provider,str) else provider.id if provider else None)
    company=session.get(Company,company_id)
    if not provider or not company or company.organization_id!=provider.id:
        raise ValueError('Empresa não pertence a esta organização.')
    roles={member.user_id:member.role for member in session.scalars(select(ProviderMember).where(ProviderMember.provider_id==provider.id))}
    roles[provider.owner_id]='admin'
    strength={'viewer':0,'operator':1,'admin':2}
    for user_id,role in roles.items():
        if role not in ROLES:raise ValueError('Perfil da equipe inválido.')
        existing=session.get(Member,(user_id,company_id))
        if not existing:session.add(Member(user_id=user_id,company_id=company_id,role=role))
        elif strength.get(existing.role,-1)<strength[role]:existing.role=role


def _email(value):
    if not isinstance(value,str):raise ValueError('Informe um e-mail válido.')
    value=value.strip().lower()
    if len(value)>250 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',value):
        raise ValueError('Informe um e-mail válido.')
    return value


def _password(value):
    if not isinstance(value,str) or not 12<=len(value)<=1024:
        raise ValueError('Use uma senha de 12 a 1.024 caracteres.')
    return value


def _name(value):
    if not isinstance(value,str) or not 1<=len(value.strip())<=200:
        raise ValueError('Informe o nome da organização, com até 200 caracteres.')
    return value.strip()


def _payload():
    value=request.get_json(silent=True)
    if not isinstance(value,dict):raise ValueError('Corpo JSON inválido.')
    return value


def _current_session():
    token=request.cookies.get('docpronto_session','')
    login=g.s.get(Login,hashlib.sha256(token.encode()).hexdigest()) if token else None
    if not login or login.expires<time.time():return None,None
    return g.s.get(User,login.user_id),login


def register_teams(app,limited,secure):
    def fail(message,status=400):return jsonify(error=message),status

    def rate(action,limit):
        return limited('teams-'+action+':'+(request.remote_addr or 'unknown'),limit)

    def admin(pid):
        if provider_role(g.s,getattr(g,'user',None),pid)!='admin':
            raise ValueError('Organização indisponível ou perfil sem permissão.')
        return g.s.get(ServiceProvider,pid)

    def open_session(user,team,login=None):
        if login:
            g.s.commit()
            return jsonify(ok=True,csrf=login.csrf,team=team)
        token=secrets.token_urlsafe(48)
        csrf=secrets.token_urlsafe(32)
        g.s.add(Login(token_hash=hashlib.sha256(token.encode()).hexdigest(),user_id=user.id,expires=time.time()+8*3600,csrf=csrf))
        g.s.commit()
        response=jsonify(ok=True,csrf=csrf,team=team)
        response.set_cookie('docpronto_session',token,httponly=True,secure=secure,samesite='Strict',max_age=8*3600)
        return response

    def invitation(token,lock=False):
        if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,100}',token):
            raise ValueError('Convite inválido, expirado ou já utilizado.')
        query=select(TeamInvite).where(TeamInvite.token_hash==hashlib.sha256(token.encode()).hexdigest())
        if lock:query=query.with_for_update().execution_options(populate_existing=True)
        row=g.s.scalar(query)
        if not row or row.revoked or row.used_at is not None or row.expires<=time.time():
            raise ValueError('Convite inválido, expirado ou já utilizado.')
        if row.role not in ROLES:raise ValueError('Perfil do convite inválido.')
        return row

    @app.post('/api/signup')
    def team_signup():
        if not app.config.get('ALLOW_SIGNUP',True):return fail('Cadastro público desativado nesta instalação.',403)
        if rate('signup',5):return fail('Muitas tentativas. Aguarde cinco minutos.',429)
        if not g.s.scalar(select(func.count()).select_from(User)):
            return fail('Conclua a configuração inicial antes de cadastrar uma organização.',409)
        current,_=_current_session()
        if current:return fail('Você já está conectado. Crie a organização em Equipes.',409)
        data=_payload();email=_email(data.get('email'));password=_password(data.get('password'));name=_name(data.get('name'))
        if g.s.scalar(select(User.id).where(User.email==email)):
            return fail('Não foi possível criar esta conta. Se já possui cadastro, entre para continuar.',409)
        user=User(id=uid(),email=email,password=generate_password_hash(password),role='user')
        g.s.add(user);g.s.flush()
        provider=ServiceProvider(id=uid(),name=name,owner_id=user.id)
        g.s.add(provider);g.s.flush()
        log(g.s,None,user.id,'organizacao_cadastrada',provider.id)
        return open_session(user,{'id':provider.id,'name':provider.name,'role':'admin'})

    @app.get('/api/teams')
    def list_teams():
        memberships=select(ProviderMember.provider_id).where(ProviderMember.user_id==g.user.id)
        providers=g.s.scalars(select(ServiceProvider).where(or_(ServiceProvider.owner_id==g.user.id,ServiceProvider.id.in_(memberships))).order_by(ServiceProvider.name,ServiceProvider.id))
        return jsonify([{'id':p.id,'name':p.name,'role':provider_role(g.s,g.user,p.id),'owner_id':p.owner_id} for p in providers])

    @app.post('/api/teams')
    def create_team():
        provider=ServiceProvider(id=uid(),name=_name(_payload().get('name')),owner_id=g.user.id)
        g.s.add(provider);log(g.s,None,g.user.id,'organizacao_criada',provider.id);g.s.commit()
        return jsonify(id=provider.id,name=provider.name,role='admin')

    @app.get('/api/teams/<pid>/members')
    def team_members(pid):
        provider=admin(pid)
        owner=g.s.get(User,provider.owner_id)
        members=[{'id':owner.id,'email':owner.email,'role':'admin','owner':True}]
        rows=g.s.execute(select(User,ProviderMember.role).join(ProviderMember,ProviderMember.user_id==User.id).where(ProviderMember.provider_id==pid,User.id!=provider.owner_id).order_by(User.email))
        members.extend({'id':u.id,'email':u.email,'role':role,'owner':False} for u,role in rows)
        invites=g.s.scalars(select(TeamInvite).where(TeamInvite.provider_id==pid).order_by(TeamInvite.expires.desc()).limit(100))
        return jsonify(members=members,invites=[{'id':i.id,'email':i.email,'role':i.role,'expires':i.expires,'used_at':i.used_at,'revoked':i.revoked} for i in invites])

    def managed_member(pid,user_id):
        # Same organization-first lock as invite acceptance/company creation.
        # Authorize after acquiring it so a removed admin cannot mutate a team.
        provider=lock_provider(g.s,pid)
        admin(pid)
        if user_id==provider.owner_id:
            raise ValueError('O responsável pela organização não pode ser alterado ou removido.')
        member=g.s.get(ProviderMember,(user_id,pid),populate_existing=True)
        if not member:raise ValueError('Integrante indisponível nesta organização.')
        return member

    @app.patch('/api/teams/<pid>/members/<user_id>')
    def change_member_role(pid,user_id):
        role=_payload().get('role')
        if not isinstance(role,str) or role not in ROLES:
            raise ValueError('Escolha admin, operator ou viewer.')
        member=managed_member(pid,user_id)
        old_role=member.role
        member.role=role
        companies=select(Company.id).where(Company.organization_id==pid)
        g.s.execute(update(Member).where(Member.user_id==user_id,Member.company_id.in_(companies)).values(role=role))
        # Repair missing grants too; all company snapshots share the provider lock.
        g.s.flush()
        for company_id in g.s.scalars(companies):
            grant_company_members(g.s,company_id,pid)
        log(g.s,None,g.user.id,'integrante_perfil_alterado',pid+':'+user_id+':'+old_role+'->'+role)
        g.s.commit()
        return jsonify(ok=True,id=user_id,role=role)

    @app.delete('/api/teams/<pid>/members/<user_id>')
    def remove_member(pid,user_id):
        member=managed_member(pid,user_id)
        user=g.s.get(User,user_id)
        companies=select(Company.id).where(Company.organization_id==pid)
        g.s.execute(delete(Member).where(Member.user_id==user_id,Member.company_id.in_(companies)))
        g.s.delete(member)
        # An older unconsumed link must not restore membership after removal.
        g.s.execute(update(TeamInvite).where(TeamInvite.provider_id==pid,TeamInvite.email==user.email,TeamInvite.used_at.is_(None)).values(revoked=True))
        log(g.s,None,g.user.id,'integrante_removido',pid+':'+user_id)
        g.s.commit()
        return jsonify(ok=True,id=user_id)

    @app.post('/api/teams/<pid>/invites')
    def create_invite(pid):
        lock_provider(g.s,pid);admin(pid);data=_payload();email=_email(data.get('email'));role=data.get('role','viewer')
        if role not in ROLES:raise ValueError('Escolha admin, operator ou viewer.')
        existing=g.s.scalar(select(User).where(User.email==email))
        if existing and provider_role(g.s,existing,pid):raise ValueError('Esta pessoa já pertence à organização.')
        token=secrets.token_urlsafe(32)
        row=TeamInvite(id=uid(),provider_id=pid,email=email,role=role,token_hash=hashlib.sha256(token.encode()).hexdigest(),expires=time.time()+7*86400,created_by=g.user.id)
        g.s.add(row);log(g.s,None,g.user.id,'convite_criado',row.id);g.s.commit()
        return jsonify(id=row.id,token=token,link=request.host_url.rstrip('/')+'/#invite='+token,expires=row.expires,role=role)

    @app.get('/api/invites/<token>')
    def preview_invite(token):
        if rate('preview',60):return fail('Muitas consultas. Aguarde cinco minutos.',429)
        row=invitation(token)
        provider=g.s.get(ServiceProvider,row.provider_id)
        return jsonify(name=provider.name,role=row.role,expires=row.expires)

    @app.post('/api/invites/<token>/accept')
    def accept_invite(token):
        if rate('accept',15):return fail('Muitas tentativas. Aguarde cinco minutos.',429)
        data=_payload();user,login=_current_session()
        if login and not secrets.compare_digest(request.headers.get('X-CSRF-Token','').encode(),login.csrf.encode()):
            return fail('Proteção CSRF inválida. Entre novamente para continuar.',403)
        row=invitation(token)
        email=_email(data.get('email',user.email if user else None))
        if email!=row.email or (user and user.email!=row.email):
            return fail('O convite pertence a outro e-mail. Entre com a conta convidada.',403)
        # Same first lock as create_company, then refresh/lock the invitation.
        # Consistent order avoids missing concurrent companies and lock inversion.
        provider=lock_provider(g.s,row.provider_id)
        row=invitation(token,lock=True)
        if not user:
            if g.s.scalar(select(User.id).where(User.email==email)):
                return fail('Esta conta já existe. Entre com sua senha e aceite o convite novamente.',401)
            user=User(id=uid(),email=email,password=generate_password_hash(_password(data.get('password'))),role='user')
            g.s.add(user);g.s.flush()
        if provider_role(g.s,user,row.provider_id):raise ValueError('Esta pessoa já pertence à organização.')
        # Conditional consumption supplements row locks on SQLite and prevents
        # duplicate grants when acceptance and revocation race.
        claimed=g.s.execute(update(TeamInvite).where(TeamInvite.id==row.id,TeamInvite.used_at.is_(None),TeamInvite.revoked.is_(False),TeamInvite.expires>time.time()).values(used_at=time.time())).rowcount
        if claimed!=1:raise ValueError('Convite inválido, expirado ou já utilizado.')
        g.s.add(ProviderMember(user_id=user.id,provider_id=row.provider_id,role=row.role));g.s.flush()
        for cid in g.s.scalars(select(Company.id).where(Company.organization_id==provider.id)):
            grant_company_members(g.s,cid,provider)
        log(g.s,None,user.id,'convite_aceito',row.id)
        return open_session(user,{'id':provider.id,'name':provider.name,'role':row.role},login)

    @app.post('/api/teams/<pid>/invites/<iid>/revoke')
    def revoke_invite(pid,iid):
        lock_provider(g.s,pid);admin(pid)
        changed=g.s.execute(update(TeamInvite).where(TeamInvite.id==iid,TeamInvite.provider_id==pid,TeamInvite.used_at.is_(None)).values(revoked=True)).rowcount
        if changed!=1:raise ValueError('Convite indisponível ou já utilizado.')
        log(g.s,None,g.user.id,'convite_revogado',iid);g.s.commit()
        return jsonify(ok=True)
