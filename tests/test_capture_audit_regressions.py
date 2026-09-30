"""Synthetic capture regressions: Flask client + fixture SQLite, no SEFAZ calls."""
import base64
import json
import time
from sqlalchemy import select
from test_app import env
from test_agent_bridge import pair
from test_new_features import response
from app.agent_bridge import AgentCompanyLink, AgentDevice
from app.client_capture import StoredA1, cipher
from app.distribution import DistributionTask, DistributionState


def setup_capture(env):
    app, client, headers, company = env
    machine, auth, device, _ = pair(app, client, headers, company)
    certs = [{'thumbprint': ch * 40, 'store': 'CurrentUser', 'has_private_key': True} for ch in ('A', 'B')]
    poll = {'certificates': certs, 'capabilities': ['distribution', 'stored_a1']}
    assert machine.post('/api/agent/poll', json=poll, headers=auth).status_code == 200
    req = {'company': company, 'device': device, **certs[0], 'uf': '35'}
    return machine, auth, device, certs, poll, req


def test_unlinked_company_pending_a1_is_never_delivered(env):
    app, client, headers, company = env
    machine, auth, device, certs, poll, req = setup_capture(env)
    other = client.post('/api/companies', json={'name': 'Synthetic second company', 'document': '11222333000181'}, headers=headers).json['id']
    with app.session_factory.begin() as session:
        dev = session.get(AgentDevice, device)
        session.add(AgentCompanyLink(device_id=device, company_id=other, created_by=dev.created_by))
        session.add(DistributionState(company_id=other))
        session.add(StoredA1(company_id=other, metadata_json='{}', encrypted=cipher(app.storage).encrypt(json.dumps({'pfx': 'SYNTHETIC-NOT-A-CERTIFICATE', 'password': 'synthetic-password'}).encode()).decode()))
        session.add(DistributionTask(company_id=other, device_id=device, payload=json.dumps({'kind': 'distribution', 'a1': True, 'thumbprint': '', 'store': 'CurrentUser', 'document': '11222333000181', 'uf': '35', 'nsu': '000000000000000', 'key': ''})))
    assert client.post('/api/certificates/agents/' + device + '/revoke', json={'company': other}, headers=headers).status_code == 200
    result = machine.post('/api/agent/poll', json=poll, headers=auth)
    assert result.status_code == 200
    assert result.json['task'] is None
    assert 'synthetic-password' not in result.get_data(as_text=True)
    with app.session_factory() as session:
        assert session.scalar(select(DistributionTask).where(DistributionTask.company_id == other)).state == 'cancelled'


def test_switch_during_running_query_applies_to_automatic_continuation(env):
    app, client, headers, company = env
    machine, auth, device, certs, poll, req = setup_capture(env)
    assert client.post('/api/distribution', json=req, headers=headers).status_code == 200
    first = machine.post('/api/agent/poll', json=poll, headers=auth).json['task']
    assert first['thumbprint'] == certs[0]['thumbprint']
    assert client.post('/api/certificates/selection', json={'company': company, 'device_id': device, **certs[1]}, headers=headers).status_code == 200
    body = {'id': first['id'], 'ok': True, 'response': base64.b64encode(response(maximum='000000000000002')).decode()}
    assert machine.post('/api/agent/distribution-result', json=body, headers=auth).status_code == 200
    second = machine.post('/api/agent/poll', json=poll, headers=auth).json['task']
    assert second['thumbprint'] == certs[1]['thumbprint']
    assert second['nsu'] == '000000000000001'


def test_switch_to_another_device_routes_followup_without_old_device_poll(env):
    app, client, headers, company = env
    machine, auth, device, certs, poll, req = setup_capture(env)
    assert client.post('/api/distribution', json=req, headers=headers).status_code == 200
    first = machine.post('/api/agent/poll', json=poll, headers=auth).json['task']
    new_machine, new_auth, new_device, _ = pair(app, client, headers, company)
    assert new_machine.post('/api/agent/poll', json=poll, headers=new_auth).status_code == 200
    assert client.post('/api/certificates/selection', json={'company': company, 'device_id': new_device, **certs[1]}, headers=headers).status_code == 200
    body = {'id': first['id'], 'ok': True, 'response': base64.b64encode(response(maximum='000000000000002')).decode()}
    assert machine.post('/api/agent/distribution-result', json=body, headers=auth).status_code == 200
    next_task = new_machine.post('/api/agent/poll', json=poll, headers=new_auth).json['task']
    assert next_task and next_task['thumbprint'] == certs[1]['thumbprint']


def test_existing_pending_task_respects_persisted_cooldown(env):
    app, client, headers, company = env
    machine, auth, device, certs, poll, req = setup_capture(env)
    assert client.post('/api/distribution', json=req, headers=headers).status_code == 200
    with app.session_factory.begin() as session:
        session.get(DistributionState, company).next_allowed = time.time() + 3600
    assert machine.post('/api/agent/poll', json=poll, headers=auth).json['task'] is None
    with app.session_factory() as session:
        assert session.scalar(select(DistributionTask)).state == 'pending'


def test_no_progress_response_stops_automatic_repetition(env):
    app, client, headers, company = env
    machine, auth, device, certs, poll, req = setup_capture(env)
    assert client.post('/api/distribution', json=req, headers=headers).status_code == 200
    task = machine.post('/api/agent/poll', json=poll, headers=auth).json['task']
    body = {'id': task['id'], 'ok': True, 'response': base64.b64encode(response(last='000000000000000', maximum='000000000000002')).decode()}
    assert machine.post('/api/agent/distribution-result', json=body, headers=auth).status_code == 200
    with app.session_factory() as session:
        assert session.get(DistributionTask, task['id']).state == 'failed'
        assert session.get(DistributionState, company).nsu == '000000000000000'
        assert session.get(DistributionState, company).next_allowed == 0
        assert len(list(session.scalars(select(DistributionTask)))) == 1


def test_expired_capture_item_can_be_retried_once(env):
    from test_app import KEY
    from app.distribution import CaptureItem, CaptureBatch
    app, client, headers, company = env
    machine, auth, device, certs, poll, req = setup_capture(env)
    created = client.post('/api/distribution', json={**req, 'batch': True, 'keys': KEY}, headers=headers)
    bid = created.json['id']
    first = machine.post('/api/agent/poll', json=poll, headers=auth).json['task']
    with app.session_factory.begin() as session:
        session.get(DistributionTask, first['id']).expires = time.time() - 1
    assert client.post('/api/capture/batches/' + bid + '/control', json={'company': company, 'action': 'retry'}, headers=headers).status_code == 200
    retried = machine.post('/api/agent/poll', json=poll, headers=auth).json['task']
    assert retried and retried['id'] != first['id'] and retried['key'] == KEY
    with app.session_factory() as session:
        assert session.get(CaptureBatch, bid).state == 'active'
        assert session.scalar(select(CaptureItem).where(CaptureItem.batch_id == bid)).state == 'running'


def test_stale_worker_cannot_fail_another_owners_lease(env, monkeypatch):
    from test_app import KEY
    from app.db import Job
    from app.worker import DocumentProcessingService
    app, client, headers, company = env
    assert client.post('/api/keys', json={'company': company, 'keys': KEY}, headers=headers).status_code == 200
    service = DocumentProcessingService(app.session_factory, app.storage)
    jid, original = service.claim()
    with app.session_factory.begin() as session:
        job = session.get(Job, jid)
        job.owner = 'new-owner'
        job.attempts = 4
    monkeypatch.setattr(service, 'claim', lambda: (jid, original))
    assert service.once()
    with app.session_factory() as session:
        job = session.get(Job, jid)
        assert job.owner == 'new-owner' and job.state == 'running'
