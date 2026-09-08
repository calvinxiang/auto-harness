import ast
import json
from pathlib import Path
import subprocess
import sys
import time
import socket
from fastapi.testclient import TestClient
import httpx
import pytest

from service.api import app
from service import queue
from service.agent_source import baseline_code, render
from service.config import settings
from service.db import connect
from service.optimizer import propose, OptimizationError
from service.runner import parse_results
from service.worker import process_job
from .test_service import setup_org, result, PreflightFixture


def test_results_account_for_missing_malformed_and_timeout(tmp_path):
    def trial(name, data):
        directory = tmp_path / name
        directory.mkdir()
        (directory / 'result.json').write_text(json.dumps(data))
    trial('one', {'task_name': 'fix-git', 'verifier_result': {'rewards': {'reward': 1}}})
    trial('two', {'task_name': 'regex-log', 'exception_info': {'exception_type': 'AgentTimeoutError'}})
    trial('three', {'task_name': 'extract-elf', 'verifier_result': {'rewards': {'reward': float('nan')}}})
    parsed = parse_results(tmp_path, ['fix-git', 'regex-log', 'extract-elf', 'nginx-request-logging'])
    assert parsed['score'] == 0.25
    assert parsed['errors'] == 2
    assert [r['status'] for r in parsed['tasks']] == ['passed', 'timeout', 'error', 'error']


def test_prompt_is_data_not_worker_code():
    injection = "'\n__import__('os').system('touch /tmp/should-never-exist')\n#"
    source, sha = render(injection)
    tree = ast.parse(source)
    policy = next(n for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id == 'POLICY_SOURCE')
    policy_tree = ast.parse(ast.literal_eval(policy.value))
    assignment = next(n for n in policy_tree.body if isinstance(n, ast.Assign) and n.targets[0].id == 'AGENT_INSTRUCTION')
    assert ast.literal_eval(assignment.value) == injection
    assert len(sha) == 64


@pytest.mark.parametrize('body', [
    {'diagnosis': 'x', 'rationale': 'y', 'agent_code': 'too short'},
    {'diagnosis': 'x', 'rationale': 'y', 'agent_code': 'p' * 60, 'code': 'unexpected field'},
])
def test_optimizer_rejects_invalid_proposals(monkeypatch, body):
    def post(*_, **kwargs):
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(body)}}]})
    monkeypatch.setattr(httpx.Client, 'post', post)
    with pytest.raises(OptimizationError):
        propose({'prompt': 'baseline', 'results': {'tasks': []}}, [])


def test_exhausted_worker_attempts_are_terminal():
    with TestClient(app) as client:
        _, headers, base = setup_org(client)
        job_id = client.post(base + '/jobs', headers=headers, json={'task_ids': ['fix-git']}).json()['id']
        for attempt in range(settings().max_attempts):
            claimed = queue.claim()
            assert claimed['attempts'] == attempt + 1
            with connect() as conn:
                conn.execute("UPDATE jobs SET lease_until=now()-interval '1 second' WHERE id=%s", (claimed['id'],))
            queue.release_reservation(claimed)  # Simulated runner cleanup completed.
        assert queue.claim() is None
        finished = client.get(base + '/jobs/' + job_id, headers=headers).json()
        assert finished['status'] == 'failed'
        assert finished['stop_reason'] == 'worker_attempts_exhausted'


def test_higher_aggregate_score_cannot_hide_regression():
    with TestClient(app) as client:
        _, headers, base = setup_org(client)
        ids = ['fix-git', 'regex-log', 'extract-elf']
        client.post(base + '/jobs', headers=headers, json={'task_ids': ids})
        job = queue.claim()
        source, sha = render('baseline')
        iteration = queue.begin_iteration(job, 0, source, sha, 'baseline', None)
        job = queue.complete_iteration(job, iteration, result(ids, 1))
        candidate = queue.begin_iteration(job, 1, source, sha, 'candidate', {})
        # New successes on B/C, but A regresses.
        regressed = result([ids[1], ids[2], ids[0]], 2)
        job = queue.complete_iteration(job, candidate, regressed)
        assert job['best_score'] == 1 / 3
        assert job['best_iteration_id'] == iteration['id']
        assert job['stop_reason'] == 'no_improvement'


def test_test_client_over_real_http(tmp_path):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'service.api:app', '--host', '127.0.0.1', '--port', str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    client = None
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                if httpx.get(f'http://127.0.0.1:{port}/health').status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        script = Path(__file__).resolve().parents[1] / 'test_client.py'
        client = subprocess.Popen([sys.executable, str(script), '--base-url', f'http://127.0.0.1:{port}', '--task-ids', 'fix-git', 'regex-log', '--max-iterations', '2', '--poll-interval', '0.1', '--timeout', '20'], cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        while time.monotonic() < deadline:
            job = queue.claim()
            if job:
                break
            time.sleep(0.1)
        else:
            pytest.fail('Client did not submit a job')
        class Runner(PreflightFixture):
            def run(self, job, iteration, stop):
                return result(job['request']['task_ids'], iteration['number'] + 1)
        process_job(job, Runner(), lambda *_: {'agent_code': baseline_code() + '\n# HTTP history fixture'})
        stdout, stderr = client.communicate(timeout=20)
        assert client.returncode == 0, stderr
        summary = json.loads(stdout)
        assert summary['job']['status'] == 'succeeded'
        assert len(summary['iterations']) == 2
        assert summary['iterations'][1]['agent_source']
        assert 'api_token' not in stdout
    finally:
        if client and client.poll() is None:
            client.kill()
            client.wait()
        server.terminate()
        server.wait(timeout=10)
