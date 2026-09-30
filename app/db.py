import os, uuid, time
from pathlib import Path
from .config import load_env,default_database
from sqlalchemy import text, create_engine, String, Text, Integer, Float, UniqueConstraint, ForeignKey, event, inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

def uid():return uuid.uuid4().hex
class Base(DeclarativeBase):pass
class User(Base):
    __tablename__='users'
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=uid)
    email:Mapped[str]=mapped_column(String(250),unique=True)
    password:Mapped[str]=mapped_column(Text)
    role:Mapped[str]=mapped_column(String(20),default='admin')
    created:Mapped[float]=mapped_column(Float,default=time.time)
class Company(Base):
    __tablename__='companies'
    __table_args__=(UniqueConstraint('organization_id','document',name='uq_company_organization_document'),)
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=uid)
    name:Mapped[str]=mapped_column(String(200))
    document:Mapped[str]=mapped_column(String(14))
    organization_id:Mapped[str|None]=mapped_column(ForeignKey('service_providers.id'),nullable=True,index=True)
class ServiceProvider(Base):
    __tablename__='service_providers'
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=uid)
    name:Mapped[str]=mapped_column(String(200))
    owner_id:Mapped[str]=mapped_column(ForeignKey('users.id'))
class ProviderTaxId(Base):
    __tablename__='provider_tax_ids'
    provider_id:Mapped[str]=mapped_column(ForeignKey('service_providers.id'),primary_key=True,index=True)
    document:Mapped[str]=mapped_column(String(14),primary_key=True)
class Client(Base):
    __tablename__='clients'
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=uid)
    provider_id:Mapped[str]=mapped_column(ForeignKey('service_providers.id'),index=True)
    name:Mapped[str]=mapped_column(String(200))
class ClientRegistration(Base):
    __tablename__='client_registrations'
    company_id:Mapped[str]=mapped_column(ForeignKey('companies.id'),primary_key=True)
    client_id:Mapped[str]=mapped_column(ForeignKey('clients.id'),index=True)
class Member(Base):
    __tablename__='members'
    user_id:Mapped[str]=mapped_column(ForeignKey('users.id'),primary_key=True)
    company_id:Mapped[str]=mapped_column(ForeignKey('companies.id'),primary_key=True)
    role:Mapped[str]=mapped_column(String(20),default='admin')
class Login(Base):
    __tablename__='sessions'
    token_hash:Mapped[str]=mapped_column(String(64),primary_key=True)
    user_id:Mapped[str]=mapped_column(ForeignKey('users.id'))
    expires:Mapped[float]=mapped_column(Float)
    csrf:Mapped[str]=mapped_column(String(80))
class Document(Base):
    __tablename__='documents'
    __table_args__=(UniqueConstraint('company_id','key'),)
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=uid)
    version:Mapped[int]=mapped_column(Integer,default=1)
    __mapper_args__={'version_id_col':version}
    company_id:Mapped[str]=mapped_column(ForeignKey('companies.id'),index=True)
    key:Mapped[str]=mapped_column(String(44))
    status:Mapped[str]=mapped_column(String(30),default='aguardando',index=True)
    source:Mapped[str]=mapped_column(String(20),default='PENDENTE')
    data:Mapped[str]=mapped_column(Text,default='{}')
    error:Mapped[str]=mapped_column(Text,default='')
    pdf:Mapped[str|None]=mapped_column(Text,nullable=True)
    xml:Mapped[str|None]=mapped_column(Text,nullable=True)
    draft:Mapped[str|None]=mapped_column(Text,nullable=True)
    created:Mapped[float]=mapped_column(Float,default=time.time)
    updated:Mapped[float]=mapped_column(Float,default=time.time)
class Job(Base):
    __tablename__='jobs'
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=uid)
    document_id:Mapped[str]=mapped_column(ForeignKey('documents.id'),unique=True)
    state:Mapped[str]=mapped_column(String(20),default='pending',index=True)
    stage:Mapped[str]=mapped_column(String(20),default='lookup')
    attempts:Mapped[int]=mapped_column(Integer,default=0)
    available:Mapped[float]=mapped_column(Float,default=time.time,index=True)
    lease_until:Mapped[float]=mapped_column(Float,default=0)
    owner:Mapped[str|None]=mapped_column(String(32),nullable=True)
class Audit(Base):
    __tablename__='audit'
    id:Mapped[str]=mapped_column(String(32),primary_key=True,default=uid)
    company_id:Mapped[str|None]=mapped_column(String(32),nullable=True,index=True)
    user_id:Mapped[str|None]=mapped_column(String(32),nullable=True)
    document_id:Mapped[str|None]=mapped_column(String(32),nullable=True)
    action:Mapped[str]=mapped_column(String(80))
    details:Mapped[str]=mapped_column(Text,default='')
    created:Mapped[float]=mapped_column(Float,default=time.time)
class Setting(Base):
    __tablename__='settings'
    key:Mapped[str]=mapped_column(String(80),primary_key=True)
    value:Mapped[str]=mapped_column(Text)
class RateBucket(Base):
    __tablename__='rate_buckets'
    key:Mapped[str]=mapped_column(String(100),primary_key=True)
    count:Mapped[int]=mapped_column(Integer,default=1)
    expires:Mapped[float]=mapped_column(Float)

def _sqlite_organization_migration(engine):
    """Rebuild SQLite's inline UNIQUE/PK without changing IDs or child references."""
    raw=engine.raw_connection()
    try:
        cursor=raw.cursor()
        cursor.execute('PRAGMA foreign_keys=OFF')
        cursor.execute('BEGIN IMMEDIATE')
        def columns(table):return {row[1] for row in cursor.execute('PRAGMA table_info('+table+')')}
        def global_document_unique(table):
            indexes=list(cursor.execute('PRAGMA index_list('+table+')'))
            return any(row[2] and [col[2] for col in cursor.execute('PRAGMA index_info("'+row[1].replace('"','""')+'")')]==['document'] for row in indexes)
        def extras(table):
            return list(cursor.execute("SELECT type,name,sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL",(table,)))
        existing=columns('companies')
        if 'organization_id' not in existing or global_document_unique('companies'):
            if existing-{'id','name','document','organization_id'}:
                raise RuntimeError('Migração de empresas encontrou colunas desconhecidas; banco preservado.')
            preserved=extras('companies')
            removed_indexes=set()
            for row in list(cursor.execute('PRAGMA index_list(companies)')):
                if row[2] and [col[2] for col in cursor.execute('PRAGMA index_info("'+row[1].replace('"','""')+'")')]==['document']:
                    removed_indexes.add(row[1])
            cursor.execute('CREATE TABLE companies_org_new (id VARCHAR(32) NOT NULL PRIMARY KEY, name VARCHAR(200) NOT NULL, document VARCHAR(14) NOT NULL, organization_id VARCHAR(32) REFERENCES service_providers(id), CONSTRAINT uq_company_organization_document UNIQUE(organization_id,document))')
            org='organization_id' if 'organization_id' in existing else 'NULL'
            cursor.execute('INSERT INTO companies_org_new(id,name,document,organization_id) SELECT id,name,document,'+org+' FROM companies')
            cursor.execute('DROP TABLE companies')
            cursor.execute('ALTER TABLE companies_org_new RENAME TO companies')
            for kind,name,sql in preserved:
                if name not in removed_indexes:cursor.execute(sql)
        cursor.execute('CREATE INDEX IF NOT EXISTS ix_companies_organization_id ON companies(organization_id)')
        cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS uq_company_organization_document_idx ON companies(organization_id,document)')
        cursor.execute('UPDATE companies SET organization_id=(SELECT clients.provider_id FROM client_registrations JOIN clients ON clients.id=client_registrations.client_id WHERE client_registrations.company_id=companies.id) WHERE organization_id IS NULL AND EXISTS(SELECT 1 FROM client_registrations WHERE client_registrations.company_id=companies.id)')
        pk=[row[1] for row in sorted(cursor.execute('PRAGMA table_info(provider_tax_ids)'),key=lambda row:row[5]) if row[5]]
        if pk!=['provider_id','document']:
            if columns('provider_tax_ids')!={'provider_id','document'}:
                raise RuntimeError('Migração dos CNPJs da organização encontrou colunas desconhecidas; banco preservado.')
            preserved=extras('provider_tax_ids')
            cursor.execute('CREATE TABLE provider_tax_ids_org_new (provider_id VARCHAR(32) NOT NULL REFERENCES service_providers(id), document VARCHAR(14) NOT NULL, PRIMARY KEY(provider_id,document))')
            cursor.execute('INSERT INTO provider_tax_ids_org_new(provider_id,document) SELECT provider_id,document FROM provider_tax_ids')
            cursor.execute('DROP TABLE provider_tax_ids')
            cursor.execute('ALTER TABLE provider_tax_ids_org_new RENAME TO provider_tax_ids')
            for kind,name,sql in preserved:cursor.execute(sql)
        cursor.execute('CREATE INDEX IF NOT EXISTS ix_provider_tax_ids_provider_id ON provider_tax_ids(provider_id)')
        if cursor.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeError('Migração encontrou referências inconsistentes; alterações revertidas.')
        cursor.execute('COMMIT')
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.cursor().execute('PRAGMA foreign_keys=ON')
        raw.close()


def _postgres_organization_migration(connection):
    """Called under the same transaction/advisory lock as schema initialization."""
    inspector=inspect(connection)
    quote=connection.dialect.identifier_preparer.quote
    if 'organization_id' not in {col['name'] for col in inspector.get_columns('companies')}:
        connection.execute(text('ALTER TABLE companies ADD COLUMN organization_id VARCHAR(32) REFERENCES service_providers(id)'))
    for constraint in inspector.get_unique_constraints('companies'):
        if constraint['column_names']==['document']:
            connection.execute(text('ALTER TABLE companies DROP CONSTRAINT '+quote(constraint['name'])))
    for index in inspector.get_indexes('companies'):
        if index.get('unique') and index['column_names']==['document'] and not index.get('duplicates_constraint'):
            connection.execute(text('DROP INDEX '+quote(index['name'])))
    connection.execute(text('CREATE INDEX IF NOT EXISTS ix_companies_organization_id ON companies(organization_id)'))
    connection.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS uq_company_organization_document_idx ON companies(organization_id,document)'))
    connection.execute(text('UPDATE companies SET organization_id=clients.provider_id FROM client_registrations JOIN clients ON clients.id=client_registrations.client_id WHERE companies.id=client_registrations.company_id AND companies.organization_id IS NULL'))
    pk=inspector.get_pk_constraint('provider_tax_ids')
    if pk['constrained_columns']!=['provider_id','document']:
        connection.execute(text('ALTER TABLE provider_tax_ids DROP CONSTRAINT '+quote(pk['name'])))
        connection.execute(text('ALTER TABLE provider_tax_ids ADD CONSTRAINT provider_tax_ids_pkey PRIMARY KEY(provider_id,document)'))


def init_db(url=None):
    load_env()
    url=url or os.getenv('DATABASE_URL',default_database())
    if url.startswith('sqlite:///'):
        Path(url.removeprefix('sqlite:///')).parent.mkdir(parents=True,exist_ok=True)
    engine=create_engine(url,pool_pre_ping=True,connect_args={'check_same_thread':False,'timeout':30} if url.startswith('sqlite') else {})
    from . import teams  # Register organization/team tables before create_all.
    if url.startswith('sqlite'):
        @event.listens_for(engine,'connect')
        def pragmas(conn,record):
            conn.isolation_level=None
            conn.execute('PRAGMA foreign_keys=ON');conn.execute('PRAGMA journal_mode=WAL')
        @event.listens_for(engine,'begin')
        def begin(conn):conn.exec_driver_sql('BEGIN')
    with engine.begin() as connection:
        if engine.dialect.name=='postgresql':connection.execute(text('SELECT pg_advisory_xact_lock(602017)'))
        Base.metadata.create_all(connection)
        if engine.dialect.name=='postgresql':_postgres_organization_migration(connection)
    if engine.dialect.name=='sqlite':_sqlite_organization_migration(engine)
    return engine,sessionmaker(engine,expire_on_commit=False)

def log(s,company,user,action,details='',doc=None):s.add(Audit(company_id=company,user_id=user,action=action,details=details,document_id=doc))
