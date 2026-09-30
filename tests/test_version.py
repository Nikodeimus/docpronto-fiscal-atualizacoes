from app.server import create_app
from app.version import VERSION, REVISION


def test_status_identifies_source_revision(tmp_path):
    app = create_app('sqlite:///' + str(tmp_path / 'db.sqlite'), str(tmp_path / 'files'), True)
    try:
        response = app.test_client().get('/api/status')
        assert response.status_code == 200
        assert response.json['version'] == VERSION
        assert response.json['revision'] == REVISION
    finally:
        app.engine.dispose()
