"""Monthly selection is metadata; distribution still follows the persisted NSU."""
import base64
import json
from datetime import date
import pytest
from test_app import env, RAW
from test_capture_audit_regressions import setup_capture
from test_new_features import response
from app.distribution import requested_period, DistributionState, CaptureBatch


class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 9, 25)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr('app.distribution.date', FixedDate)


@pytest.mark.parametrize('body,start,end', [
    ({'month_from': '2024-02'}, '2024-02-01', '2024-02-29'),
    ({'month_from': '2025-02'}, '2025-02-01', '2025-02-28'),
    ({'month_from': '2025-12', 'month_to': '2026-02'}, '2025-12-01', '2026-02-28'),
    ({'month_from': '2026-09'}, '2026-09-01', '2026-09-25'),
    ({'month_from': '2026-08', 'month_to': '2026-09'}, '2026-08-01', '2026-09-25'),
    ({'month_from': '2026-01', 'month_to': ''}, '2026-01-01', '2026-01-31'),
])
def test_month_boundaries(body, start, end):
    assert requested_period({'period': 'months', **body}) == {'period': 'months', 'date_from': start, 'date_to': end}


@pytest.mark.parametrize('body', [
    {}, {'month_from': ''}, {'month_from': 202601}, {'month_from': []},
    {'month_from': '2026-1'}, {'month_from': '26-01'}, {'month_from': '2026-01-01'},
    {'month_from': '2026-00'}, {'month_from': '2026-13'}, {'month_from': '0000-01'},
    {'month_from': ' 2026-01'}, {'month_from': '2026-10'},
    {'month_from': '2026-03', 'month_to': '2026-02'},
    {'month_from': '2026-01', 'month_to': '2026-10'},
    {'month_from': '2026-01', 'month_to': []},
])
def test_invalid_months(body):
    with pytest.raises(ValueError):
        requested_period({'period': 'months', **body})


def test_month_selection_persisted_listed_and_does_not_reset_nsu(env):
    app, client, headers, company = env
    machine, auth, device, certs, poll, req = setup_capture(env)
    with app.session_factory.begin() as session:
        session.add(DistributionState(company_id=company, nsu='000000000000041', next_allowed=0))
    result = client.post('/api/distribution', json={**req, 'batch': True, 'period': 'months', 'month_from': '2026-01', 'month_to': '2026-02'}, headers=headers)
    assert result.status_code == 200, result.json
    batch_id = result.json['id']
    listed = client.get('/api/capture/batches?company=' + company).json['items'][0]
    assert (listed['period'], listed['date_from'], listed['date_to']) == ('months', '2026-01-01', '2026-02-28')
    with app.session_factory() as session:
        options = json.loads(session.get(CaptureBatch, batch_id).options)
        assert (options['date_from'], options['date_to']) == ('2026-01-01', '2026-02-28')
        assert session.get(DistributionState, company).nsu == '000000000000041'
    task = machine.post('/api/agent/poll', json=poll, headers=auth).json['task']
    assert task['nsu'] == '000000000000041'
    assert 'month_from' not in task and 'month_to' not in task
    # Fixture invoice is from 2025, outside the selected Jan/Feb 2026 range.
    raw = response(files=[RAW], last='000000000000042', maximum='000000000000042')
    reply = machine.post('/api/agent/distribution-result', json={'id': task['id'], 'ok': True, 'response': base64.b64encode(raw).decode()}, headers=auth)
    assert reply.status_code == 200, reply.json
    history = client.get('/api/history?company=' + company).json
    assert history['total'] == 1
    assert client.get('/api/history/' + history['items'][0]['id'] + '/xml').data == RAW
    with app.session_factory() as session:
        assert session.get(DistributionState, company).nsu == '000000000000042'


def test_invalid_month_request_does_not_create_capture(env):
    app, client, headers, company = env
    machine, auth, device, certs, poll, req = setup_capture(env)
    result = client.post('/api/distribution', json={**req, 'batch': True, 'period': 'months', 'month_from': '2026-10'}, headers=headers)
    assert result.status_code == 400
    assert client.get('/api/capture/batches?company=' + company).json['items'] == []
