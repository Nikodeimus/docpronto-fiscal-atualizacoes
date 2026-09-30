from test_app import env, imp, KEY
from app.fiscal_history import archive_xml


def test_cancelled_note_is_visible_without_changing_processing_state(env):
    app, client, headers, cid = env
    imported = imp(client, headers, cid)
    did = imported.json['results'][0]['id']
    summary = ('<resNFe xmlns="http://www.portalfiscal.inf.br/nfe">'
               f'<chNFe>{KEY}</chNFe><cSitNFe>3</cSitNFe></resNFe>').encode()
    with app.session_factory.begin() as session:
        archive_xml(session, app.storage, cid, summary, '11444777000161')
    listed = client.get('/api/documents?company=' + cid).json['items'][0]
    assert listed['status'] == 'concluido'
    assert listed['fiscal_status'] == 'cancelled_in_file'
    assert client.get('/api/documents/' + did).json['fiscal_status'] == 'cancelled_in_file'
