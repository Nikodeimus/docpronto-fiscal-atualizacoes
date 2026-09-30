"""Organization schema migration tested only against synthetic temporary databases."""
import sqlite3
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql
from app.db import _sqlite_organization_migration, _postgres_organization_migration, init_db


def legacy(path):
    with sqlite3.connect(path) as connection:
        connection.executescript('''
        PRAGMA foreign_keys=ON;
        CREATE TABLE users(id VARCHAR(32) PRIMARY KEY,email VARCHAR(250) UNIQUE NOT NULL,password TEXT NOT NULL,role VARCHAR(20) NOT NULL,created FLOAT NOT NULL);
        CREATE TABLE service_providers(id VARCHAR(32) PRIMARY KEY,name VARCHAR(200) NOT NULL,owner_id VARCHAR(32) NOT NULL REFERENCES users(id));
        CREATE TABLE companies(id VARCHAR(32) PRIMARY KEY,name VARCHAR(200) NOT NULL,document VARCHAR(14) UNIQUE NOT NULL);
        CREATE TABLE clients(id VARCHAR(32) PRIMARY KEY,provider_id VARCHAR(32) NOT NULL REFERENCES service_providers(id),name VARCHAR(200) NOT NULL);
        CREATE TABLE client_registrations(company_id VARCHAR(32) PRIMARY KEY REFERENCES companies(id),client_id VARCHAR(32) NOT NULL REFERENCES clients(id));
        CREATE TABLE members(user_id VARCHAR(32) REFERENCES users(id),company_id VARCHAR(32) REFERENCES companies(id),role VARCHAR(20) NOT NULL,PRIMARY KEY(user_id,company_id));
        CREATE TABLE preserved_documents(id VARCHAR(32) PRIMARY KEY,company_id VARCHAR(32) NOT NULL REFERENCES companies(id),xml TEXT NOT NULL,data TEXT NOT NULL);
        CREATE TABLE provider_tax_ids(document VARCHAR(14) PRIMARY KEY,provider_id VARCHAR(32) NOT NULL REFERENCES service_providers(id));
        CREATE INDEX ix_provider_tax_ids_provider_id ON provider_tax_ids(provider_id);
        INSERT INTO users VALUES('u1','synthetic@example.test','not-a-real-password','admin',1);
        INSERT INTO service_providers VALUES('org1','One','u1'),('org2','Two','u1');
        INSERT INTO companies VALUES('co1','Linked','00000000000001'),('legacy','Unlinked','00000000000002');
        INSERT INTO clients VALUES('client1','org1','Client one');
        INSERT INTO client_registrations VALUES('co1','client1');
        INSERT INTO members VALUES('u1','co1','admin'),('u1','legacy','admin');
        INSERT INTO preserved_documents VALUES('d1','co1','co1/d1/original.xml','{"preserve":true}');
        INSERT INTO provider_tax_ids VALUES('00000000000003','org1');
        ''')


def test_sqlite_backfills_and_preserves_ids_data_links(tmp_path):
    path=tmp_path/'legacy.sqlite'
    legacy(path)
    engine=create_engine('sqlite:///'+str(path))
    try:
        _sqlite_organization_migration(engine)
        with engine.connect() as connection:
            assert connection.execute(text('SELECT id,name,document,organization_id FROM companies ORDER BY id')).all()==[
                ('co1','Linked','00000000000001','org1'),('legacy','Unlinked','00000000000002',None)]
            assert connection.execute(text('SELECT * FROM preserved_documents')).one()==('d1','co1','co1/d1/original.xml','{"preserve":true}')
            assert connection.execute(text('SELECT * FROM client_registrations')).one()==('co1','client1')
            assert connection.execute(text('SELECT count(*) FROM members')).scalar()==2
            assert connection.exec_driver_sql('PRAGMA foreign_key_check').all()==[]
            assert connection.exec_driver_sql('PRAGMA foreign_keys').scalar()==1
        _sqlite_organization_migration(engine)
        with engine.connect() as connection:
            assert connection.execute(text('SELECT count(*) FROM companies')).scalar()==2
            assert connection.execute(text('SELECT organization_id FROM companies WHERE id=\'co1\'')).scalar()=='org1'
    finally:engine.dispose()


def test_duplicates_allowed_between_organizations_only(tmp_path):
    path=tmp_path/'legacy.sqlite';legacy(path)
    engine=create_engine('sqlite:///'+str(path))
    try:
        _sqlite_organization_migration(engine)
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO companies(id,name,document,organization_id) VALUES('co2','Same document other organization','00000000000001','org2')"))
            connection.execute(text("INSERT INTO provider_tax_ids(provider_id,document) VALUES('org2','00000000000003')"))
        from sqlalchemy.exc import IntegrityError
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("INSERT INTO companies(id,name,document,organization_id) VALUES('co3','Duplicate','00000000000001','org1')"))
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("INSERT INTO provider_tax_ids(provider_id,document) VALUES('org1','00000000000003')"))
        _sqlite_organization_migration(engine)
    finally:engine.dispose()


def test_failed_migration_rolls_back_without_losing_extra_columns(tmp_path):
    path=tmp_path/'legacy.sqlite';legacy(path)
    with sqlite3.connect(path) as connection:
        connection.execute('ALTER TABLE companies ADD COLUMN unexpected TEXT')
        connection.execute("UPDATE companies SET unexpected='must-preserve'")
    engine=create_engine('sqlite:///'+str(path))
    try:
        with pytest.raises(RuntimeError,match='colunas desconhecidas'):
            _sqlite_organization_migration(engine)
        with engine.connect() as connection:
            assert connection.execute(text('SELECT unexpected FROM companies WHERE id=\'co1\'')).scalar()=='must-preserve'
            assert connection.exec_driver_sql('PRAGMA foreign_keys').scalar()==1
            assert connection.exec_driver_sql('PRAGMA foreign_key_check').all()==[]
    finally:engine.dispose()


def test_init_db_migration_idempotent_and_team_models_registered(tmp_path):
    path=tmp_path/'legacy.sqlite';legacy(path)
    for _ in range(2):
        engine,session=init_db('sqlite:///'+str(path))
        try:
            with engine.connect() as connection:
                assert connection.execute(text("SELECT organization_id FROM companies WHERE id='co1'")).scalar()=='org1'
                assert connection.exec_driver_sql('PRAGMA foreign_key_check').all()==[]
            from app.teams import ProviderMember,TeamInvite
            from sqlalchemy import inspect
            assert inspect(engine).has_table(ProviderMember.__tablename__)
            assert inspect(engine).has_table(TeamInvite.__tablename__)
        finally:engine.dispose()


def test_postgresql_migration_sql_review(monkeypatch):
    """SQL generation review; deliberately no connection to a PostgreSQL server."""
    inspector=SimpleNamespace(get_columns=lambda table:[{'name':'id'},{'name':'name'},{'name':'document'}],
        get_unique_constraints=lambda table:[{'column_names':['document'],'name':'companies_document_key'}],
        get_indexes=lambda table:[],get_pk_constraint=lambda table:{'constrained_columns':['document'],'name':'provider_tax_ids_pkey'})
    monkeypatch.setattr('app.db.inspect',lambda connection:inspector)
    commands=[]
    connection=SimpleNamespace(dialect=postgresql.dialect(),execute=lambda statement:commands.append(str(statement)))
    _postgres_organization_migration(connection)
    assert any('ADD COLUMN organization_id VARCHAR(32) REFERENCES service_providers(id)' in sql for sql in commands)
    assert 'ALTER TABLE companies DROP CONSTRAINT companies_document_key' in commands
    assert any('PRIMARY KEY(provider_id,document)' in sql for sql in commands)
    assert any('WHERE companies.id=client_registrations.company_id AND companies.organization_id IS NULL' in sql for sql in commands)


def test_fresh_schema_orm_composite_identity(tmp_path):
    from app.db import User,ServiceProvider,Company,ProviderTaxId
    engine,Session=init_db('sqlite:///'+str(tmp_path/'fresh.sqlite'))
    try:
        with Session.begin() as session:
            session.add(User(id='u',email='synthetic@example.test',password='not-real'))
            session.flush()
            session.add_all([ServiceProvider(id='a',name='A',owner_id='u'),ServiceProvider(id='b',name='B',owner_id='u')])
            session.flush()
            session.add_all([Company(id='ca',name='A',document='00000000000001',organization_id='a'),Company(id='cb',name='B',document='00000000000001',organization_id='b')])
            session.add_all([ProviderTaxId(provider_id='a',document='00000000000001'),ProviderTaxId(provider_id='b',document='00000000000001')])
        with Session() as session:
            assert session.get(ProviderTaxId,('a','00000000000001')).provider_id=='a'
            assert session.get(ProviderTaxId,('b','00000000000001')).provider_id=='b'
            assert session.get(Company,'ca').organization_id=='a'
    finally:engine.dispose()
