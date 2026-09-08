import logging
import hashlib
from pathlib import Path
import signal
import threading
import time
from datetime import datetime, timezone

from . import queue
from .agent_source import (AgentValidationError, baseline_code, baseline_prompt,
                           render_code, source_diff, validate_code)
from .config import settings, execution_config
from .db import connect
from .optimizer import propose
from .runner import HarborRunner, cleanup
from . import campaigns
from .packages import package_snapshot
from .package_optimizer import propose_package

log = logging.getLogger(__name__)


def process_proposal(job, runner, stop, optimizer=propose_package):
    with connect() as conn:
        parent = conn.execute('SELECT * FROM harness_versions WHERE id=%s AND org_id=%s',
                              (job['request']['version_id'], job['org_id'])).fetchone()
    output = job.get('output') or {}
    if 'proposal' not in output:
        args = (parent['package'], job['request']['dimension'], output.get('evidence', []), job['request']['execution'])
        output['proposal'] = optimizer(*args, review_feedback=job['request'].get('review_feedback'))
        queue.record_output(job, output)  # Retry reuses the proposal after this checkpoint.
    proposal = output['proposal']
    # Store an immutable version even when its dynamic contract fails, for review.
    if 'version_id' not in output:
        snapshot = package_snapshot(proposal['package'], parent['package'])
        if snapshot['package_sha256'] == parent['package_sha256']:
            raise ValueError('Optimizer returned unchanged package')
        with connect() as conn:
            queue.owned(conn, job)
            version = campaigns.insert_version(conn, job['org_id'], job['user_id'],
                job['request']['dimension'] + ' proposal', job['request']['dimension'], snapshot, parent['id'], proposal)
            output['version_id'] = str(version['id'])
            from psycopg.types.json import Jsonb
            conn.execute('UPDATE jobs SET output=%s WHERE id=%s', (Jsonb(output), job['id']))
    else:
        with connect() as conn:
            version = conn.execute('SELECT * FROM harness_versions WHERE id=%s AND org_id=%s',
                                   (output['version_id'], job['org_id'])).fetchone()
    # Deployment changes must not alter the already checkpointed runnable version.
    if hashlib.sha256(version['agent_source'].encode()).hexdigest() != version['source_sha256']:
        raise ValueError('Harness source hash mismatch')
    iteration = {'id': job['id'], 'number': 0, 'agent_source': version['agent_source']}
    output['validation'] = runner.preflight(job, iteration, stop)
    queue.record_output(job, output)
    if output['validation']['status'] != 'passed':
        raise ValueError('Package contract failed; proposed version retained')
    queue.complete_output(job, output)


def process_job(job, runner=None, optimizer=propose, shutdown=None):
    runner = runner or HarborRunner()
    stop = threading.Event()
    done = threading.Event()
    deadline = [time.monotonic() + max(0, (job['lease_until']-datetime.now(timezone.utc)).total_seconds()-2)]

    def watchdog():
        # Independent of the DB heartbeat: a blocked socket cannot keep executing
        # after the locally known lease deadline.
        while not done.wait(.25):
            if time.monotonic() >= deadline[0] or (shutdown and shutdown.is_set()):
                stop.set()
                return

    def renew():
        while not done.wait(settings().heartbeat_seconds):
            try:
                started = time.monotonic()
                if (shutdown and shutdown.is_set()) or not queue.heartbeat(job):
                    stop.set()
                    return
                deadline[0] = started + settings().lease_seconds - 2
            except Exception:
                # Fail closed: an uncertain lease never authorizes further writes.
                stop.set()
                return

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    guard = threading.Thread(target=watchdog, daemon=True)
    guard.start()
    iteration = None
    try:
        kind = job['request'].get('kind', 'optimization')
        if kind == 'proposal':
            process_proposal(job, runner, stop)
            return
        if kind != 'trial' and job['request'].get('execution', execution_config()) != execution_config():
            raise RuntimeError('Worker configuration differs from the submitted job snapshot')
        if kind == 'trial' and any(job['request']['execution'][key] != execution_config()[key]
                                   for key in ('dataset', 'harbor_version', 'sandbox_provider')):
            raise RuntimeError('Worker benchmark environment differs from the experiment snapshot')
        while job['status'] == 'running':
            if stop.is_set():
                raise queue.LeaseLost()
            records = queue.history(job['id'])
            number = job['next_iteration']
            retry = next((r for r in reversed(records) if r['number'] == number and r['status'] == 'interrupted'), None)
            if retry:
                prompt, proposal = retry['prompt'], retry['proposal']
                source, source_hash = retry['agent_source'], retry['source_sha256']
                code, diff = retry.get('agent_code'), retry.get('source_diff')
            else:
                proposal = None
                prompt = baseline_prompt()
                code = baseline_code()
                diff = None
                if kind == 'trial':
                    with connect() as conn:
                        version = conn.execute('SELECT * FROM harness_versions WHERE id=%s AND org_id=%s',
                                               (job['request']['version_id'], job['org_id'])).fetchone()
                    source, source_hash = version['agent_source'], version['source_sha256']
                    if hashlib.sha256(source.encode()).hexdigest() != source_hash:
                        raise ValueError('Harness source hash mismatch')
                    code = None
                elif number > 0:
                    best = next(r for r in records if r['id'] == job['best_iteration_id'])
                    proposal = optimizer(best, records)
                    code = proposal['agent_code']
                    prompt = best['prompt']
                    diff = source_diff(best.get('agent_code') or baseline_code(best['prompt']), code)
                if kind != 'trial':
                    source, source_hash = render_code(code)
            iteration = queue.begin_iteration(job, number, source, source_hash, prompt, proposal, code, diff)
            if code is not None:
                try:
                    prompt = validate_code(code)
                except AgentValidationError as exc:
                    queue.reject_invalid_candidate(job, iteration,
                        {'status': 'failed', 'stage': 'static', 'message': str(exc)})
                    return
            if code is not None or kind == 'trial':
                validation = runner.preflight(job, iteration, stop)
                queue.record_validation(job, iteration, validation, prompt)
                if validation['status'] != 'passed':
                    queue.reject_invalid_candidate(job, iteration, validation)
                    return
            results = runner.run(job, iteration, stop)
            if results['errors']:
                queue.fail(job, {'code': 'benchmark_infrastructure_error',
                    'message': 'One or more tasks lacked a valid result; inspect iteration results'}, iteration, results)
                return
            job = queue.complete_iteration(job, iteration, results)
            iteration = None
    except queue.LeaseLost:
        log.info('Job %s stopped after cancellation or lease loss', job['id'])
    except Exception as exc:
        # Exceptions are categorized; raw messages may contain provider credentials.
        log.error('Job %s failed (%s)', job['id'], type(exc).__name__)
        try:
            queue.fail(job, {'code': type(exc).__name__, 'message': 'Execution failed; inspect worker setup and persisted iteration evidence',
                            'details': getattr(exc, 'details', {})}, iteration, getattr(exc, 'results', None))
        except queue.LeaseLost:
            pass
    finally:
        done.set()
        thread.join(timeout=15)
        guard.join(timeout=1)
        try:
            if isinstance(runner, HarborRunner):
                cleanup(Path(settings().artifacts_dir) / 'runs' / str(job['id']) / str(job['claim_token']))
            queue.release_reservation(job)
        except Exception:
            log.error('Cleanup pending for job %s; capacity remains reserved', job['id'])
        with connect() as conn:
            campaigns.reconcile(conn)


def reap_stale_runs(cleaner=cleanup):
    # Reservations outlive expired leases. Grace lets a live but disconnected
    # worker stop its runner before another worker cleans and reuses the capacity.
    with connect() as conn:
        stale = conn.execute('''SELECT r.job_id AS id,r.claim_token FROM execution_reservations r
            JOIN jobs j ON j.id=r.job_id WHERE
            ((j.status='running' AND j.lease_until<now()-make_interval(secs=>%s)) OR
             (j.status<>'running' AND j.updated_at<now()-make_interval(secs=>%s)))''',
            (settings().stale_cleanup_grace_seconds, settings().stale_cleanup_grace_seconds)).fetchall()
    for reservation in stale:
        cleaner(Path(settings().artifacts_dir) / 'runs' / str(reservation['id']) / str(reservation['claim_token']))
        queue.release_reservation(reservation)


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    cfg = settings()
    shutdown = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: shutdown.set())
    if not cfg.openai_api_key:
        log.error('OPENAI_API_KEY is missing; worker will not claim jobs until configured and recreated')
        shutdown.wait()
        return
    while not shutdown.is_set():
        try:
            reap_stale_runs()
            job = queue.claim()
            if job:
                log.info('Claimed job %s attempt %s', job['id'], job['attempts'])
                process_job(job, shutdown=shutdown)
            else:
                shutdown.wait(2)
        except Exception as exc:
            log.error('Worker temporarily unavailable (%s)', type(exc).__name__)
            shutdown.wait(5)


if __name__ == '__main__':
    main()
