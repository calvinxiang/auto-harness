from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from uuid import UUID

from fastapi.testclient import TestClient
import pytest

from service.api import app
from service import queue, searches
from service.db import connect
from service.worker import process_job, process_proposal
from .test_service import setup_org, PreflightFixture, result


TASKS = ['fix-git', 'regex-log', 'extract-elf']


def create(client, headers, base, **kwargs):
    response = client.post(base + '/optimization-runs', headers=headers, json={
        'name': 'automatic package optimization', 'development_task_ids': TASKS,
        'dimensions': ['tools', 'skills'], 'max_rounds': 2, 'patience': 2, **kwargs})
    assert response.status_code == 202, response.text
    return response.json()


def install_optimizer(monkeypatch, seen, invalid=False):
    import service.worker as worker
    real = process_proposal
    def propose(parent, dimension, evidence, execution, review_feedback=None, history=None):
        seen.append({'parent': deepcopy(parent), 'dimension': dimension, 'evidence': deepcopy(evidence),
                     'execution': deepcopy(execution), 'history': deepcopy(history or [])})
        if invalid:
            raise ValueError('Invalid proposal fixture')
        package = deepcopy(parent)
        package['files']['scenario.json'] = json.dumps({'dimension': dimension, 'generation': len(history or []) + 1})
        return {'package': package, 'diagnosis': 'General procedure fixture', 'rationale': dimension}
    monkeypatch.setattr(worker, 'process_proposal', lambda j, r, s: real(j, r, s, optimizer=propose))


class Runner(PreflightFixture):
    def run(self, job, iteration, stop):
        with connect() as conn:
            package = conn.execute('SELECT package FROM harness_versions WHERE id=%s', (job['request']['version_id'],)).fetchone()['package']
        scenario = json.loads(package['files'].get('scenario.json', '{}'))
        if job['request']['split'] == 'heldout':
            return result(job['request']['task_ids'], 0)  # Must never guide another round.
        passing = TASKS[:1]
        if scenario:
            passing = TASKS[1:] if scenario == {'dimension': 'skills', 'generation': 1} else TASKS[:2]
        return result(job['request']['task_ids'], int(job['request']['task_ids'][0] in passing))


def drain(client, headers, base, run_id, runner=None):
    for _ in range(180):
        searches.advance_one()
        job = queue.claim()
        if job:
            process_job(job, runner or Runner())
        run = client.get(base + '/optimization-runs/' + run_id, headers=headers).json()
        if run['status'] != 'running':
            return run
    raise AssertionError('Optimization did not finish')


def test_two_round_selection_rejects_regressions_and_never_learns_from_final_check(monkeypatch):
    seen = []
    install_optimizer(monkeypatch, seen)
    with TestClient(app) as client:
        _, headers, base = setup_org(client)
        initial = create(client, headers, base, heldout_task_ids=['cancel-async-tasks'])
        run = drain(client, headers, base, initial['id'])
        assert run['status'] == 'succeeded' and run['stop_reason'] == 'max_rounds'
        assert len(run['rounds']) == 2 and run['best_score'] == 2/3
        first, second = run['rounds']
        assert first['decision']['improved'] and not second['decision']['improved']
        assert str(second['parent_version_id']) == first['decision']['selected_version_id'] == run['best_version_id']
        rejected = next(c for c in first['decision']['candidates'] if c['regressions'])
        assert rejected['score'] == 2/3 and not rejected['eligible']
        assert len(seen) == 4
        assert all(t['task_id'] in TASKS for p in seen for t in p['evidence'])
        assert all(len(p['evidence']) == 3 for p in seen)
        later = [p for p in seen if p['history']]
        assert len(later) == 2 and later[0]['history'][0]['decision']['improved']
        assert all('scenario.json' in p['parent']['files'] for p in later)
        heldout = run['experiments'][run['heldout_experiment_id']]
        assert all(s['score'] == 0 for s in heldout['summary'] if s['split'] == 'heldout')
        with connect() as conn:
            assert conn.execute('SELECT count(*) AS n FROM execution_reservations').fetchone()['n'] == 0
        before = deepcopy(run)
        for _ in range(5):
            searches.advance_one()
        assert client.get(base + '/optimization-runs/' + initial['id'], headers=headers).json() == before
        assert 'claim_token' not in json.dumps(run)


def test_controller_transition_rollback_and_concurrent_controllers_do_not_duplicate_children():
    with TestClient(app) as client:
        _, headers, base = setup_org(client)
        run = create(client, headers, base)
        with pytest.raises(RuntimeError):
            with connect() as conn:
                row = conn.execute('SELECT * FROM optimization_runs WHERE id=%s FOR UPDATE', (run['id'],)).fetchone()
                searches.advance(conn, row)
                raise RuntimeError('Controller died before commit')
        with connect() as conn:
            assert conn.execute('SELECT count(*) AS n FROM experiments').fetchone()['n'] == 0
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda _: searches.advance_one(), range(18)))
        with connect() as conn:
            assert conn.execute('SELECT count(*) AS n FROM experiments').fetchone()['n'] == 1
            assert conn.execute('SELECT count(*) AS n FROM jobs').fetchone()['n'] == 3
        assert client.get(base + '/optimization-runs/' + run['id'], headers=headers).json()['phase'] == 'baseline'


def test_invalid_controller_state_fails_only_its_run_after_rollback(monkeypatch):
    with TestClient(app) as client:
        _, headers, base = setup_org(client)
        _, other, elsewhere = setup_org(client, 'other')
        first = create(client, headers, base)
        second = create(client, other, elsewhere)
        advance = searches.advance
        def faulty(conn, row):
            advance(conn, row)
            if str(row['id']) == first['id']:
                raise ValueError('Private diagnostic must not enter the public error')
        monkeypatch.setattr(searches, 'advance', faulty)
        searches.advance_one()
        failed = client.get(base + '/optimization-runs/' + first['id'], headers=headers).json()
        assert failed['status'] == 'failed' and failed['stop_reason'] == 'controller_error'
        assert not failed['experiments'] and 'Private diagnostic' not in json.dumps(failed)
        searches.advance_one()
        healthy = client.get(elsewhere + '/optimization-runs/' + second['id'], headers=other).json()
        assert healthy['status'] == 'running' and len(healthy['experiments']) == 1


def test_invalid_proposals_are_kept_and_stop_at_patience(monkeypatch):
    seen = []
    install_optimizer(monkeypatch, seen, invalid=True)
    with TestClient(app) as client:
        _, headers, base = setup_org(client)
        run = create(client, headers, base, dimensions=['context', 'control'], max_rounds=3, patience=1)
        done = drain(client, headers, base, run['id'])
        assert done['status'] == 'succeeded' and done['stop_reason'] == 'plateau'
        assert done['best_version_id'] == run['initial_version_id']
        assert len(done['rounds']) == 1 and len(seen) == 2
        assert done['rounds'][0]['decision']['reason'] == 'no_valid_candidates'
        assert all(p['status'] == 'failed' and p['error'] for p in done['rounds'][0]['proposals'])
        assert len(done['experiments']) == 1


def test_search_idempotency_access_and_cancellation_fence_all_children(monkeypatch):
    seen = []
    install_optimizer(monkeypatch, seen)
    with TestClient(app) as client:
        _, headers, base = setup_org(client)
        _, other, elsewhere = setup_org(client, 'other')
        key = {**headers, 'Idempotency-Key': 'automatic-search'}
        run = create(client, key, base)
        assert create(client, key, base)['id'] == run['id']
        assert client.post(base + '/optimization-runs', headers=key, json={'name': 'changed'}).status_code == 409
        assert client.post(base + '/optimization-runs', headers=headers, json={'name': 'another'}).status_code == 429
        assert client.get(elsewhere + '/optimization-runs/' + run['id'], headers=other).status_code == 404
        assert client.post(elsewhere + '/optimization-runs/' + run['id'] + '/cancel', headers=other).status_code == 404
        member = client.post(base + '/members', headers=headers, json={'name': 'member'}).json()
        mh = {'Authorization': 'Bearer ' + member['api_token']}
        assert client.get(base + '/optimization-runs', headers=mh).json() == []
        assert client.get(base + '/optimization-runs/' + run['id'], headers=mh).status_code == 404
        assert client.post(base + '/optimization-runs/' + run['id'] + '/cancel', headers=mh).status_code == 404
        searches.advance_one()
        for _ in TASKS:
            process_job(queue.claim(), Runner())
        searches.advance_one()  # baseline evidence -> proposals
        job = queue.claim()
        assert job['request']['kind'] == 'proposal'
        cancelled = client.post(base + '/optimization-runs/' + run['id'] + '/cancel', headers=headers).json()
        assert cancelled['status'] == 'cancelled'
        with pytest.raises(queue.LeaseLost):
            queue.record_output(job, {'attempt': 'late'})
        with connect() as conn:
            assert not conn.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running')").fetchone()
            assert conn.execute('SELECT count(*) AS n FROM execution_reservations').fetchone()['n'] == 1
        queue.release_reservation(job)  # No container in this fixture; cleanup is complete.
        searches.advance_one()
        assert not seen


def test_reference_infrastructure_failure_stops_without_proposals():
    class Broken(PreflightFixture):
        def run(self, job, iteration, stop):
            raise RuntimeError('Sandbox could not start')
    with TestClient(app) as client:
        _, headers, base = setup_org(client)
        run = create(client, headers, base)
        done = drain(client, headers, base, run['id'], Broken())
        assert done['status'] == 'failed' and done['stop_reason'] == 'baseline_evaluation_failed'
        assert done['best_version_id'] == run['initial_version_id'] and not done['rounds']


def test_all_pass_baseline_stops_before_any_optimizer_call():
    class Perfect(PreflightFixture):
        def run(self, job, iteration, stop):
            return result(job['request']['task_ids'], 1)
    with TestClient(app) as client:
        _, headers, base = setup_org(client)
        run = create(client, headers, base)
        done = drain(client, headers, base, run['id'], Perfect())
        assert done['status'] == 'succeeded' and done['stop_reason'] == 'all_development_tasks_passed'
        assert not done['rounds'] and done['best_score'] == 1


def test_run_freezes_execution_settings_and_rejects_new_runtime(monkeypatch):
    from service.config import settings
    with TestClient(app) as client:
        _, headers, base = setup_org(client)
        run = create(client, headers, base)
        frozen = run['execution']
        monkeypatch.setenv('AGENT_MODEL', 'changed-default')
        monkeypatch.setenv('OPTIMIZER_MODEL', 'changed-optimizer')
        settings.cache_clear()
        searches.advance_one()
        for _ in TASKS:
            job = queue.claim()
            assert job['request']['execution'] == frozen
            process_job(job, Runner())
        with connect() as conn:
            conn.execute("UPDATE harness_versions SET runtime_sha256='changed-runtime' WHERE id=%s", (run['initial_version_id'],))
        searches.advance_one()
        done = client.get(base + '/optimization-runs/' + run['id'], headers=headers).json()
        assert done['status'] == 'failed' and done['stop_reason'] == 'runtime_changed'
        assert not done['rounds']


def test_optimization_client_real_http_full_history_and_resume(tmp_path, monkeypatch):
    import socket
    import subprocess
    import sys
    import time
    from pathlib import Path
    import httpx

    seen = []
    install_optimizer(monkeypatch, seen)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'service.api:app', '--host', '127.0.0.1', '--port', str(port)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    process = None
    command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'test_client.py'), '--optimize',
        '--base-url', url, '--state', 'state.json', '--output', 'report.json',
        '--task-ids', *TASKS, '--dimensions', 'tools', 'skills', '--max-rounds', '2',
        '--poll-interval', '.02', '--timeout', '45']
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                if httpx.get(url + '/health').status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(.05)
        process = subprocess.Popen(command, cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        while process.poll() is None and time.monotonic() < deadline:
            searches.advance_one()
            job = queue.claim()
            if job:
                process_job(job, Runner())
            else:
                time.sleep(.02)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr
        report = json.loads((tmp_path / 'report.json').read_text())
        assert report['complete'] and len(report['run']['rounds']) == 2
        assert len(report['history']) == 21 and len(report['versions']) == 5
        assert all(rows[0]['agent_source'] for rows in report['history'].values())
        assert len(seen) == 4 and 'api_token' not in stdout
        resumed = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=15)
        assert resumed.returncode == 0, resumed.stderr
        assert json.loads((tmp_path / 'report.json').read_text()) == report
        with connect() as conn:
            assert conn.execute('SELECT count(*) AS n FROM optimization_runs').fetchone()['n'] == 1
            assert conn.execute('SELECT count(*) AS n FROM jobs').fetchone()['n'] == 25
    finally:
        if process and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        server.terminate()
        server.wait(timeout=5)
