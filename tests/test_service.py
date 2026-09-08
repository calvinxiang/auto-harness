from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4
import hashlib

from fastapi.testclient import TestClient
import pytest

from service.api import app
from service import queue
from service.agent_source import baseline_code, render
from service.db import connect
from service.worker import process_job


def setup_org(client, name='team'):
    response = client.post('/organizations', json={'name': name, 'owner_name': 'owner'}, headers={'X-Bootstrap-Token': 'integration-test-bootstrap'})
    assert response.status_code == 201, response.text
    org = response.json()
    headers = {'Authorization': 'Bearer ' + org['api_token']}
    return org, headers, f'/organizations/{org["id"]}'


def result(task_ids, passes):
    tasks = [{'task_id': tid, 'status': 'passed' if i < passes else 'failed',
              'reward': 1.0 if i < passes else 0.0,
              'failure_summary': None if i < passes else 'Verifier rejected output',
              'trace': '[{"role":"tool","content":"Actual fixture output"}]'} for i, tid in enumerate(task_ids)]
    return {'tasks': tasks, 'score': passes / len(tasks), 'errors': 0, 'passed': passes, 'failed': len(tasks) - passes}


class PreflightFixture:
    def preflight(self, job, iteration, stop):
        return {'status': 'passed', 'stage': 'deterministic_test_fixture'}


def test_tenant_ownership_membership_and_idempotency():
    with TestClient(app) as client:
        org, admin, base = setup_org(client)
        member = client.post(base + '/members', headers=admin, json={'name': 'member'}).json()
        mh = {'Authorization': 'Bearer ' + member['api_token']}
        other, oh, other_base = setup_org(client, 'other')
        body = {'task_ids': ['fix-git'], 'max_iterations': 0}
        assert client.post(base + '/jobs', json=body).status_code == 401
        assert client.post(base + '/members', headers=mh, json={'name': 'bad'}).status_code == 403
        assert client.put(base + '/members/' + org['owner_id'], headers=admin, json={'role': 'member'}).status_code == 409
        response = client.post(base + '/jobs', headers={**mh, 'Idempotency-Key': 'same'}, json=body)
        assert response.status_code == 202
        job_id = response.json()['id']
        assert 'claim_token' not in response.json()
        repeated = client.post(base + '/jobs', headers={**mh, 'Idempotency-Key': 'same'}, json=body)
        assert repeated.json()['id'] == job_id
        assert client.post(base + '/jobs', headers={**mh, 'Idempotency-Key': 'same'}, json={**body, 'max_iterations': 1}).status_code == 409
        sibling = client.post(base + '/members', headers=admin, json={'name': 'sibling'}).json()
        sh = {'Authorization': 'Bearer ' + sibling['api_token']}
        for headers, route in [(sh, base), (oh, base), (oh, other_base)]:
            for suffix in ['', '/iterations', '/cancel']:
                action = client.post if suffix == '/cancel' else client.get
                assert action(route + '/jobs/' + job_id + suffix, headers=headers).status_code == 404
        assert client.get(base + '/jobs', headers=sh).json() == []
        assert len(client.get(base + '/jobs', headers=admin).json()) == 1
        assert client.delete(base + '/members/' + member['id'], headers=admin).status_code == 204
        assert client.get(base + '/jobs/' + job_id, headers=mh).status_code == 404
        assert client.get(base + '/jobs/' + job_id, headers=admin).status_code == 200
        assert client.post(base + '/jobs', headers=admin, json={'task_ids': ['../../evil']}).status_code == 422
        assert client.post(base + '/jobs', headers=admin, json={'task_ids': ['fix-git', 'fix-git']}).status_code == 422
        assert client.post(base + '/jobs', headers=admin, json={'max_iterations': 999}).status_code == 422


def test_optimization_history_and_plateau():
    with TestClient(app) as client:
        _, headers, base = setup_org(client)
        tasks = ['fix-git', 'regex-log', 'extract-elf']
        job_id = client.post(base + '/jobs', headers=headers, json={'task_ids': tasks, 'max_iterations': 4}).json()['id']
        class Runner(PreflightFixture):
            calls = 0
            def run(self, job, iteration, stop):
                self.calls += 1
                assert iteration['source_sha256'] == hashlib.sha256(iteration['agent_source'].encode()).hexdigest()
                return result(tasks, min(self.calls, 2))
        def optimizer(best, records):
            return {'diagnosis': 'Missing verification', 'rationale': 'Use output to verify',
                    'agent_code': best['agent_code'] + '\n# Focused code fixture iteration ' + str(len(records))}
        process_job(queue.claim(), Runner(), optimizer)
        job = client.get(base + '/jobs/' + job_id, headers=headers).json()
        history = client.get(base + '/jobs/' + job_id + '/iterations', headers=headers).json()
        assert job['status'] == 'succeeded'
        assert job['stop_reason'] == 'no_improvement'
        assert job['best_score'] == 2 / 3
        assert [r['accepted'] for r in history] == [True, True, False]
        assert job['best_iteration_id'] == history[1]['id']
        assert history[2]['proposal']['diagnosis'] == 'Missing verification'
        assert history[0]['results']['tasks'][0]['trace']
        assert history[1]['agent_code'] != history[0]['agent_code']
        assert history[1]['source_diff'].startswith('--- previous/agent.py')
        assert history[1]['validation']['status'] == 'passed'


def test_concurrent_claims_and_lease_fencing():
    with TestClient(app) as client:
        _, headers, base = setup_org(client)
        for _ in range(4):
            assert client.post(base + '/jobs', headers=headers, json={'task_ids': ['fix-git']}).status_code == 202
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = [r for r in pool.map(lambda _: queue.claim(), range(8)) if r]
        assert len(claims) == 4
        assert len({r['id'] for r in claims}) == 4
        old = claims[0]
        source, sha = render('A safe prompt')
        iteration = queue.begin_iteration(old, 0, source, sha, 'A safe prompt', None)
        with connect() as conn:
            conn.execute("UPDATE jobs SET lease_until=now()-interval '1 second' WHERE id=%s", (old['id'],))
        recovered = queue.claim()
        assert recovered['id'] == old['id']
        assert recovered['claim_token'] != old['claim_token']
        assert not queue.heartbeat(old)
        with pytest.raises(queue.LeaseLost):
            queue.complete_iteration(old, iteration, result(['fix-git'], 1))
        class Runner(PreflightFixture):
            def run(self, job, current, stop):
                assert current['agent_source'] == source
                return result(['fix-git'], 1)
        process_job(recovered, Runner())
        records = queue.history(old['id'])
        assert [r['status'] for r in records] == ['interrupted', 'completed']
        assert [r['attempt'] for r in records] == [1, 2]


def test_cancellation_fences_running_worker():
    with TestClient(app) as client:
        _, headers, base = setup_org(client)
        job_id = client.post(base + '/jobs', headers=headers, json={'task_ids': ['fix-git']}).json()['id']
        claimed = queue.claim()
        source, sha = render('prompt')
        iteration = queue.begin_iteration(claimed, 0, source, sha, 'prompt', None)
        assert client.post(base + '/jobs/' + job_id + '/cancel', headers=headers).json()['status'] == 'cancelled'
        assert not queue.heartbeat(claimed)
        with pytest.raises(queue.LeaseLost):
            queue.complete_iteration(claimed, iteration, result(['fix-git'], 1))
        assert queue.history(claimed['id'])[0]['status'] == 'interrupted'


def test_partial_infrastructure_failure_preserves_evidence():
    with TestClient(app) as client:
        _, headers, base = setup_org(client)
        job_id = client.post(base + '/jobs', headers=headers, json={'task_ids': ['fix-git']}).json()['id']
        class Runner(PreflightFixture):
            def run(self, job, iteration, stop):
                return {'score': 0, 'errors': 1, 'tasks': [{'task_id': 'fix-git', 'status': 'error', 'failure_summary': 'Container crashed'}]}
        process_job(queue.claim(), Runner())
        job = client.get(base + '/jobs/' + job_id, headers=headers).json()
        history = client.get(base + '/jobs/' + job_id + '/iterations', headers=headers).json()
        assert job['status'] == 'failed' and job['best_score'] is None
        assert history[0]['results']['tasks'][0]['failure_summary'] == 'Container crashed'


def test_completed_checkpoint_survives_worker_restart():
    with TestClient(app) as client:
        _, headers, base = setup_org(client)
        client.post(base + '/jobs', headers=headers, json={'task_ids': ['fix-git', 'regex-log'], 'max_iterations': 1})
        old = queue.claim()
        source, sha = render('baseline')
        iteration = queue.begin_iteration(old, 0, source, sha, 'baseline', None)
        queue.complete_iteration(old, iteration, result(['fix-git', 'regex-log'], 1))
        with connect() as conn:
            conn.execute("UPDATE jobs SET lease_until=now()-interval '1 second' WHERE id=%s", (old['id'],))
        recovered = queue.claim()
        assert recovered['next_iteration'] == 1
        class Runner(PreflightFixture):
            def run(self, job, current, stop):
                assert current['number'] == 1
                return result(['fix-git', 'regex-log'], 2)
        process_job(recovered, Runner(), lambda *_: {'agent_code': baseline_code() + '\n# Code recovery fixture'})
        assert len(queue.history(old['id'])) == 2
