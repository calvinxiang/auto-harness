from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from threading import Event
from uuid import UUID

from fastapi.testclient import TestClient
import pytest

from service.api import app
from service import queue
from service.config import settings
from service.db import connect
from service.packages import HarnessPackage, baseline_package, package_snapshot
from service.worker import process_job, process_proposal, reap_stale_runs
from .test_service import setup_org, PreflightFixture, result


class Runner(PreflightFixture):
    def run(self, job, iteration, stop):
        return result(job['request']['task_ids'], 1)


def version(client, headers, base, **kwargs):
    response = client.post(base + '/harness-versions', headers=headers, json={'label': 'baseline', **kwargs})
    assert response.status_code == 201, response.text
    return response.json()


def experiment(client, headers, base, versions, **kwargs):
    response = client.post(base + '/experiments', headers=headers,
        json={'name': 'comparison', 'version_ids': [v['id'] for v in versions],
              'development_task_ids': ['fix-git', 'regex-log'], **kwargs})
    assert response.status_code == 202, response.text
    return response.json()


@pytest.mark.parametrize('path', ['../escape.py', '/absolute.py', 'a/../escape.py', 'a\\escape.py', 'a//b.py', 'runtime.py', '.hidden.py'])
def test_package_rejects_unsafe_paths(path):
    data = baseline_package()
    data['files'][path] = 'x=1'
    with pytest.raises(ValueError):
        HarnessPackage.model_validate(data)


def test_package_assets_hashes_changes_and_no_service_execution(tmp_path):
    package = baseline_package()
    package['files']['helpers.py'] = f"from pathlib import Path\nPath({str(tmp_path / 'executed')!r}).touch()\n"
    package['files']['skills/check/SKILL.md'] = 'Check requirements and verify observed output.'
    package['files']['config.json'] = '{"context_limit":12000}'
    package['skills'] = [{'name': 'check', 'description': 'Reusable verification procedure',
                          'path': 'skills/check/SKILL.md', 'resources': ['config.json']}]
    snapshot = package_snapshot(package, baseline_package())
    assert not (tmp_path/'executed').exists()
    assert 'helpers.py' in snapshot['changes']
    assert hashlib.sha256(snapshot['agent_source'].encode()).hexdigest() == snapshot['source_sha256']
    changed = deepcopy(package)
    changed['files']['config.json'] = '{"context_limit":24000}'
    other = package_snapshot(changed)
    assert other['package_sha256'] != snapshot['package_sha256']
    assert other['runtime_sha256'] == snapshot['runtime_sha256']
    assert other['source_sha256'] != snapshot['source_sha256']


def test_experiment_idempotency_tenant_visibility_and_version_ownership():
    with TestClient(app) as client:
        _, headers, base = setup_org(client)
        _, other, elsewhere = setup_org(client, 'other')
        v = version(client, headers, base)
        key = {**headers, 'Idempotency-Key': 'same-experiment'}
        exp = experiment(client, key, base, [v])
        assert experiment(client, key, base, [v])['id'] == exp['id']
        assert client.post(base + '/experiments', headers=key, json={
            'name': 'changed', 'version_ids': [v['id']], 'development_task_ids': ['fix-git']}).status_code == 409
        assert client.get(elsewhere + '/harness-versions/' + v['id'], headers=other).status_code == 404
        assert client.get(elsewhere + '/experiments/' + exp['id'], headers=other).status_code == 404
        assert client.post(elsewhere + '/experiments', headers=other, json={
            'name': 'leak', 'version_ids': [v['id']]}).status_code == 404
        member = client.post(base + '/members', headers=headers, json={'name': 'member'}).json()
        member_headers = {'Authorization': 'Bearer '+member['api_token']}
        assert client.get(base + '/harness-versions/' + v['id'], headers=member_headers).status_code == 404
        assert client.get(base + '/experiments/' + exp['id'], headers=member_headers).status_code == 404
        with connect() as conn:
            assert conn.execute('SELECT count(*) AS n FROM experiment_trials').fetchone()['n'] == 2


def test_trial_recovery_keeps_completed_work_and_fences_old_worker():
    with TestClient(app) as client:
        _, headers, base = setup_org(client)
        v = version(client, headers, base)
        exp = experiment(client, headers, base, [v])
        first = queue.claim()
        process_job(first, Runner())
        first_history = queue.history(first['id'])
        old = queue.claim()
        snapshot = v['agent_source']
        iteration = queue.begin_iteration(old, 0, snapshot, v['source_sha256'], '', None)
        with connect() as conn:
            conn.execute("UPDATE jobs SET lease_until=now()-interval '90 seconds' WHERE id=%s", (old['id'],))
        assert queue.claim() is None  # Capacity/attempt stays reserved until cleanup.
        cleaned = []
        reap_stale_runs(cleaner=lambda p: cleaned.append(str(p)))
        assert str(old['claim_token']) in cleaned[0]
        resumed = queue.claim()
        assert resumed['id'] == old['id'] and resumed['claim_token'] != old['claim_token']
        with pytest.raises(queue.LeaseLost):
            queue.complete_iteration(old, iteration, result(old['request']['task_ids'], 1))
        process_job(resumed, Runner())
        report = client.get(base + '/experiments/' + exp['id'], headers=headers).json()
        assert report['status'] == 'succeeded'
        assert report['summary'][0]['score'] == 1
        assert queue.history(first['id']) == first_history
        assert len(queue.history(old['id'])) == 2


def test_capacity_bound_and_failed_cleanup_do_not_release_slots():
    with TestClient(app) as client:
        _, headers, base = setup_org(client)
        v = version(client, headers, base)
        experiment(client, headers, base, [v], repetitions=3)
        with connect() as conn:
            conn.execute('UPDATE execution_pool SET capacity=2 WHERE id=1')
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = [j for j in pool.map(lambda _: queue.claim(), range(8)) if j]
        assert len(claims) == 2
        with connect() as conn:
            conn.execute("UPDATE jobs SET lease_until=now()-interval '90 seconds' WHERE id=%s", (claims[0]['id'],))
        def failed_cleanup(_):
            raise RuntimeError('Engine unavailable')
        with pytest.raises(RuntimeError):
            reap_stale_runs(failed_cleanup)
        assert queue.claim() is None
        reap_stale_runs(lambda _: None)
        recovered = queue.claim()
        assert recovered['id'] == claims[0]['id']
        assert queue.claim() is None


def test_cancellation_preserves_finished_trials_and_stops_children():
    with TestClient(app) as client:
        _, headers, base = setup_org(client)
        exp = experiment(client, headers, base, [version(client, headers, base)], repetitions=2)
        first = queue.claim()
        process_job(first, Runner())
        active = queue.claim()
        response = client.post(base + '/experiments/' + exp['id'] + '/cancel', headers=headers)
        assert response.status_code == 200 and response.json()['status'] == 'cancelled'
        assert not queue.heartbeat(active)
        assert queue.claim() is None
        report = client.get(base + '/experiments/' + exp['id'], headers=headers).json()
        assert sum(t['status'] == 'succeeded' for t in report['trials']) == 1
        assert sum(t['status'] == 'cancelled' for t in report['trials']) == 3


def test_heldout_waits_for_development_and_is_excluded_from_proposals():
    with TestClient(app) as client:
        _, headers, base = setup_org(client)
        v = version(client, headers, base)
        experiment(client, headers, base, [v], development_task_ids=['fix-git'], heldout_task_ids=['cancel-async-tasks'])
        dev = queue.claim()
        assert dev['request']['split'] == 'development' and queue.claim() is None
        process_job(dev, Runner())
        heldout = queue.claim()
        assert heldout['request']['split'] == 'heldout'
        process_job(heldout, Runner())
        assert client.post(base + '/harness-versions/' + v['id'] + '/proposals', headers=headers,
            json={'dimension': 'skills', 'evidence_job_ids': [str(heldout['id'])]}).status_code == 422


def test_independent_profiles_run_without_changing_worker_defaults():
    with TestClient(app) as client:
        _, headers, base = setup_org(client)
        v = version(client, headers, base)
        for model in ['first-model', 'second-model']:
            experiment(client, headers, base, [v], development_task_ids=['fix-git'],
                       profile={'agent_model': model, 'agent_timeout_seconds': 45})
        seen = []
        class ProfileRunner(Runner):
            def run(self, job, iteration, stop):
                seen.append(job['request']['execution']['agent_model'])
                assert iteration['agent_source'] == v['agent_source']
                return super().run(job, iteration, stop)
        process_job(queue.claim(), ProfileRunner())
        process_job(queue.claim(), ProfileRunner())
        assert set(seen) == {'first-model', 'second-model'}


def test_proposal_checkpoint_reuses_generated_package_and_preserves_lineage():
    with TestClient(app) as client:
        _, headers, base = setup_org(client)
        parent = version(client, headers, base)
        response = client.post(base + '/harness-versions/' + parent['id'] + '/proposals', headers=headers,
                               json={'dimension': 'context'})
        assert response.status_code == 202
        job = queue.claim()
        candidate = deepcopy(parent['package'])
        candidate['files']['context.json'] = '{"tail_groups":8}'
        output = {**job['output'], 'proposal': {'diagnosis': 'Exploratory', 'rationale': 'Limit context', 'package': candidate}}
        queue.record_output(job, output)
        job['output'] = output
        def forbidden_call(*_):
            raise AssertionError('A saved proposal must not be generated again')
        process_proposal(job, Runner(), Event(), forbidden_call)
        saved = client.get(base + '/jobs/' + str(job['id']), headers=headers).json()
        assert saved['status'] == 'succeeded'
        child = client.get(base + '/harness-versions/' + saved['output']['version_id'], headers=headers).json()
        assert child['parent_id'] == parent['id']
        assert 'context.json' in child['changes']


def test_saved_proposal_version_survives_runtime_deployment_change(monkeypatch):
    import service.worker as worker
    with TestClient(app) as client:
        _, headers, base = setup_org(client)
        parent = version(client, headers, base)
        child = version(client, headers, base, parent_id=parent['id'])
        client.post(base + '/harness-versions/' + parent['id'] + '/proposals', headers=headers, json={'dimension': 'skills'})
        job = queue.claim()
        job['output'] = {'proposal': {'package': child['package']}, 'version_id': child['id']}
        queue.record_output(job, job['output'])
        def must_not_render(*_):
            raise AssertionError('Checkpointed source must be reused')
        monkeypatch.setattr(worker, 'package_snapshot', must_not_render)
        class SavedRunner(Runner):
            def preflight(self, job, iteration, stop):
                assert iteration['agent_source'] == child['agent_source']
                return super().preflight(job, iteration, stop)
        process_proposal(job, SavedRunner(), Event())


def test_docker_cleanup_tolerates_disappearing_containers_but_not_engine_loss(monkeypatch):
    import subprocess
    from service.runner import docker_json
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 1, '[]', 'Error: No such object: removed\n'))
    assert docker_json(['inspect', 'removed'], missing_ok=True) == '[]'
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 1, '', 'Cannot connect to Docker daemon'))
    with pytest.raises(RuntimeError):
        docker_json(['inspect', 'removed'], missing_ok=True)


def test_package_optimizer_records_usage_and_rejects_truncated_output(monkeypatch):
    import httpx
    from service.package_optimizer import propose_package
    from service.optimizer import OptimizationError
    payload = {'diagnosis': 'Exploratory hypothesis', 'rationale': 'Change tool protocol', 'package': baseline_package()}
    response = {'usage': {'total_tokens': 42}, 'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(payload)}}]}
    def post(*args, **kwargs):
        assert kwargs['json']['model'] == 'optimizer-model'
        assert 'exploratory' in kwargs['json']['messages'][0]['content']
        return httpx.Response(200, json=response)
    monkeypatch.setattr(httpx.Client, 'post', post)
    proposal = propose_package(baseline_package(), 'tools', [], {'optimizer_model': 'optimizer-model'})
    assert proposal['usage']['total_tokens'] == 42 and proposal['evidence_tasks'] == 0
    response['choices'][0]['finish_reason'] = 'length'
    with pytest.raises(OptimizationError):
        propose_package(baseline_package(), 'tools', [], {'optimizer_model': 'optimizer-model'})
    response['choices'][0] = {'finish_reason': 'stop', 'message': {'content': '{broken JSON'}}
    with pytest.raises(OptimizationError) as exc:
        propose_package(baseline_package(), 'tools', [], {'optimizer_model': 'optimizer-model'})
    assert exc.value.details['raw_output'] == '{broken JSON'
    assert exc.value.details['usage']['total_tokens'] == 42


def test_proposal_idempotency_retains_original_profile_after_default_change(monkeypatch):
    with TestClient(app) as client:
        _, headers, base = setup_org(client)
        v = version(client, headers, base)
        route = base + '/harness-versions/' + v['id'] + '/proposals'
        headers = {**headers, 'Idempotency-Key': 'stable-proposal'}
        first = client.post(route, headers=headers, json={'dimension': 'tools'}).json()
        monkeypatch.setenv('OPTIMIZER_MODEL', 'different-default')
        settings.cache_clear()
        second = client.post(route, headers=headers, json={'dimension': 'tools'}).json()
        assert first['id'] == second['id']
        assert first['request']['execution'] == second['request']['execution']


def test_experiment_client_live_http_and_resume(tmp_path, monkeypatch):
    import socket
    import subprocess
    import sys
    import time
    from pathlib import Path
    import httpx
    import service.worker as worker_module
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'service.api:app', '--host', '127.0.0.1', '--port', str(port)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    process = None
    real_proposal = process_proposal
    def propose(parent, dimension, *_args, **_kwargs):
        package = deepcopy(parent)
        package['files'][dimension + '.json'] = '{}'
        return {'package': package, 'diagnosis': 'Synthetic client fixture', 'rationale': dimension}
    monkeypatch.setattr(worker_module, 'process_proposal',
        lambda job, runner, stop: real_proposal(job, runner, stop, optimizer=propose))
    command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'test_client.py'), '--experiment',
               '--base-url', url, '--state', 'state.json', '--output', 'report.json',
               '--task-ids', 'fix-git', '--repetitions', '1', '--poll-interval', '.05', '--timeout', '30']
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if httpx.get(url + '/health').status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(.1)
        process = subprocess.Popen(command, cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        while process.poll() is None and time.monotonic() < deadline:
            job = queue.claim()
            if job:
                process_job(job, Runner())
            else:
                time.sleep(.05)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr
        report = json.loads((tmp_path / 'report.json').read_text())
        assert len(report['history']) == 4 and len(report['versions']) == 4
        assert all(len(rows) == 1 for rows in report['history'].values())
        assert 'api_token' not in stdout and 'api_token' not in json.dumps(report)
        resumed = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=20)
        assert resumed.returncode == 0, resumed.stderr
        assert json.loads((tmp_path / 'report.json').read_text()) == report
        with connect() as conn:
            assert conn.execute('SELECT count(*) AS n FROM jobs').fetchone()['n'] == 7
    finally:
        if process and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        server.terminate()
        server.wait(timeout=5)
