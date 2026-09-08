"""Opt-in Linux/Docker failure drill. Uses a separate *_test database and no LLM.

Runs the real API, queue, worker lifecycle, package preflight and Docker cleanup.
Only the task payload is synthetic: a sandbox sleeps, then returns a fixture reward.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from uuid import uuid4

from fastapi.testclient import TestClient

from service import queue
from service.api import app
from service.config import settings
from service.db import connect
from service.migrate import migrate
from service.runner import HarborRunner, cleanup, docker_json
from service.worker import process_job, reap_stale_runs
from .test_service import result


class Payload(HarborRunner):
    def run(self, job, iteration, stop):
        root = Path(settings().artifacts_dir) / 'runs' / str(job['id']) / str(job['claim_token']) / 'payload'
        root.mkdir(parents=True, exist_ok=True)
        name = 'ops-trial-' + str(job['id'])
        docker_json(['run', '-d', '--name', name, '--network', 'none', '--read-only',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--memory', '64m',
            '--cpus', '.25', '--pids-limit', '32', '--user', '65534:65534',
            '--mount', f'type=bind,src={root},dst=/evidence,readonly',
            'python:3.12-slim', 'python', '-c', 'import time; time.sleep(4)'])
        while not stop.wait(.2):
            state = json.loads(docker_json(['inspect', name]))[0]['State']
            if not state['Running']:
                if state['ExitCode'] != 0:
                    raise RuntimeError('Synthetic task container interrupted')
                return {**result(job['request']['task_ids'], 1), 'fixture': 'synthetic-four-second-payload'}
        raise queue.LeaseLost()


def worker():
    while True:
        reap_stale_runs()
        job = queue.claim()
        if job:
            marker = Path(settings().artifacts_dir) / ('worker-' + str(os.getpid()) + '.json')
            marker.write_text(json.dumps({'job_id': str(job['id']), 'claim_token': str(job['claim_token'])}))
            process_job(job, Payload())
            marker.unlink(missing_ok=True)
        else:
            time.sleep(.15)


def owned_containers():
    ids = docker_json(['ps', '-aq']).split()
    containers = json.loads(docker_json(['inspect', *ids], missing_ok=True)) if ids else []
    prefix = settings().artifacts_dir.rstrip('/') + '/runs/'
    return [c for c in containers if any(m.get('Source', '').startswith(prefix) for m in c.get('Mounts', []))]


def main():
    cfg = settings()
    if not cfg.database_url.rsplit('/', 1)[-1].endswith('_test'):
        raise RuntimeError('Operational proof requires a dedicated *_test database')
    if sys.argv[1:] == ['worker']:
        worker()
        return
    os.environ.update(LEASE_SECONDS='6', HEARTBEAT_SECONDS='1', STALE_CLEANUP_GRACE_SECONDS='2',
                      BOOTSTRAP_TOKEN='integration-test-bootstrap',
                      ARTIFACTS_DIR='/artifacts/ops-' + uuid4().hex)
    settings.cache_clear()
    root = Path(settings().artifacts_dir)
    root.mkdir(parents=True)
    migrate()
    with connect() as conn:
        if conn.execute('SELECT count(*) AS n FROM jobs').fetchone()['n']:
            raise RuntimeError('Use an empty dedicated test database; existing results are never deleted')
        conn.execute('UPDATE execution_pool SET capacity=2 WHERE id=1')
    workers, logs = [], []
    observations = {'fixture': 'synthetic payload; real PostgreSQL, API, worker processes and Docker',
        'capacity': 2, 'workers': 4, 'lease_seconds': 6, 'cleanup_grace_seconds': 2,
        'peak_reserved_slots': 0, 'peak_running_containers': 0}

    def spawn():
        log = (root / ('worker-' + uuid4().hex + '.log')).open('w')
        logs.append(log)
        process = subprocess.Popen([sys.executable, '-m', 'tests.operational_proof', 'worker'],
                                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        workers.append(process)
        return process

    start = time.monotonic()
    try:
        with TestClient(app) as client:
            def organization(name):
                org = client.post('/organizations', json={'name': name, 'owner_name': 'Operator'},
                                  headers={'X-Bootstrap-Token': 'integration-test-bootstrap'}).json()
                return '/organizations/' + org['id'], {'Authorization': 'Bearer ' + org['api_token']}
            base, headers = organization('recovery-proof')
            other, other_headers = organization('cancellation-proof')
            def create(base, headers, name, repeats):
                v = client.post(base + '/harness-versions', headers=headers, json={'label': name}).json()
                body = {'name': name, 'version_ids': [v['id']], 'development_task_ids': ['fix-git', 'regex-log'], 'repetitions': repeats}
                key = {**headers, 'Idempotency-Key': name}
                first = client.post(base + '/experiments', headers=key, json=body)
                assert first.status_code == 202, first.text
                exp = first.json()
                assert client.post(base + '/experiments', headers=key, json=body).json()['id'] == exp['id']
                return exp['id']
            primary = create(base, headers, 'recovery', 3)
            cancelled = create(other, other_headers, 'cancel', 3)
            interrupted = create(base, headers, 'sandbox-interruption', 2)
            observations['idempotency'] = 'same IDs, no duplicate trials'
            assert client.get(other + '/experiments/' + primary, headers=other_headers).status_code == 404
            observations['tenant_isolation'] = 'cross-organization experiment returns 404'
            for _ in range(4):
                spawn()
            killed = cancelled_done = sandbox_killed = False
            preserved = {}
            old_job = old_iteration = None
            while time.monotonic() - start < 240:
                with connect() as conn:
                    jobs = conn.execute('SELECT * FROM jobs').fetchall()
                    slots = conn.execute('SELECT COALESCE(sum(slots),0) AS n FROM execution_reservations').fetchone()['n']
                containers = owned_containers()
                active = [c for c in containers if c['State']['Running']]
                observations['peak_reserved_slots'] = max(observations['peak_reserved_slots'], slots)
                observations['peak_running_containers'] = max(observations['peak_running_containers'], len(active))
                assert slots <= 2 and len(active) <= 2
                primary_jobs = [j for j in jobs if j['request']['experiment_id'] == primary]
                completed = [j for j in primary_jobs if j['status'] == 'succeeded']
                for j in completed:
                    preserved.setdefault(str(j['id']), queue.history(j['id']))
                for container in active:
                    name = container['Name'].lstrip('/')
                    if not name.startswith('ops-trial-'):
                        continue
                    job = next(j for j in jobs if str(j['id']) == name.removeprefix('ops-trial-'))
                    exp = job['request']['experiment_id']
                    if exp == cancelled and not cancelled_done:
                        response = client.post(other + '/experiments/' + cancelled + '/cancel', headers=other_headers)
                        assert response.status_code == 200
                        cancelled_done = True
                    elif exp == primary and completed and not killed:
                        owner = next(p for p in workers if p.poll() is None and
                            (root / f'worker-{p.pid}.json').exists() and
                            json.loads((root / f'worker-{p.pid}.json').read_text())['job_id'] == str(job['id']))
                        old_job, old_iteration = job, queue.history(job['id'])[-1]
                        # Kill the worker's process group, matching worker-container death;
                        # the remote engine's task container deliberately survives.
                        os.killpg(owner.pid, signal.SIGKILL)
                        owner.wait(timeout=5)
                        assert json.loads(docker_json(['inspect', name]))[0]['State']['Running']
                        with connect() as conn:
                            assert conn.execute('SELECT 1 FROM execution_reservations WHERE claim_token=%s', (job['claim_token'],)).fetchone()
                        observations['crash_left_container_and_reservation'] = True
                        spawn()
                        killed = True
                    elif exp == interrupted and not sandbox_killed:
                        docker_json(['kill', name])
                        sandbox_killed = True
                if killed and cancelled_done and sandbox_killed and all(j['status'] in ('succeeded', 'failed', 'cancelled') for j in jobs):
                    if slots == 0 and not containers:
                        break
                time.sleep(.25)
            else:
                raise TimeoutError('Failure drill did not finish')
            for job_id, history in preserved.items():
                assert queue.history(job_id) == history
            try:
                queue.complete_iteration(old_job, old_iteration, result(old_job['request']['task_ids'], 1))
                raise AssertionError('Stale worker write was accepted')
            except queue.LeaseLost:
                observations['stale_write_fencing'] = 'rejected'
            history = queue.history(old_job['id'])
            assert [r['status'] for r in history] == ['interrupted', 'completed']
            assert history[0]['source_sha256'] == history[1]['source_sha256']
            reports = [client.get(b + '/experiments/' + e, headers=h).json() for b, h, e in [
                (base, headers, primary), (other, other_headers, cancelled), (base, headers, interrupted)]]
            assert [r['status'] for r in reports] == ['succeeded', 'cancelled', 'failed']
            finished = [j for j in jobs if j['status'] == 'succeeded']
            elapsed = time.monotonic() - start
            observations.update(status='passed', elapsed_seconds=round(elapsed, 2),
                completed_trials=len(finished), preserved_completed_trials=len(preserved),
                successful_trials_per_minute=round(len(finished) * 60 / elapsed, 2),
                mean_queue_wait_seconds=round(sum((j['started_at']-j['created_at']).total_seconds() for j in finished)/len(finished), 2),
                mean_end_to_end_seconds=round(sum((j['finished_at']-j['created_at']).total_seconds() for j in finished)/len(finished), 2),
                recovered_attempts=[r['status'] for r in history], remaining_containers=0, remaining_reservations=0,
                experiment_states=[r['status'] for r in reports])
            (root / 'report.json').write_text(json.dumps(observations, indent=2))
            print(json.dumps({**observations, 'artifact_path': str(root / 'report.json')}, indent=2))
    finally:
        for process in workers:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        for log in logs:
            log.close()
        cleanup(root / 'runs')


if __name__ == '__main__':
    main()
