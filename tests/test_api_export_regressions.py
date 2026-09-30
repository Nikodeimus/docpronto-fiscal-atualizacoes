import pytest
from test_app import env, imp
from app.db import Document

@pytest.mark.parametrize('ids', [[{}], [None], [1], [''], ['x' * 1000]])
def test_export_invalid_ids_are_client_errors(env, ids):
    app, client, headers, cid = env
    result = client.post('/api/export', json={'company': cid, 'ids': ids}, headers=headers)
    assert result.status_code == 400

@pytest.mark.parametrize('route', ['/api/export', '/api/export/all'])
def test_export_missing_original_explains_restore(env, route):
    app, client, headers, cid = env
    did = imp(client, headers, cid).json['results'][0]['id']
    with app.session_factory() as session:
        app.storage.resolve(session.get(Document, did).xml).unlink()
    result = client.post(route, json={'company': cid, 'ids': [did]}, headers=headers)
    assert result.status_code == 400
    assert 'ausente' in result.json['error'].lower()

def test_export_requires_boolean_reconstruction_confirmation(env):
    app, client, headers, cid = env
    did = imp(client, headers, cid).json['results'][0]['id']
    with app.session_factory.begin() as session:
        doc = session.get(Document, did)
        doc.source = 'RECONSTRUCTED'
        doc.status = 'concluido'
    result = client.post('/api/export', json={'company': cid, 'ids': [did], 'include_reconstructed': 'false'}, headers=headers)
    assert result.status_code == 400

@pytest.mark.parametrize('company', [{}, [], 123])
def test_company_object_is_rejected_before_database_access(env, company):
    app, client, headers, cid = env
    result = client.post('/api/export/all', json={'company': company}, headers=headers)
    assert result.status_code == 400
