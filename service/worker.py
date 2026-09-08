import logging
from pathlib import Path
import signal
import threading

from . import queue
from .agent_source import (AgentValidationError, baseline_code, baseline_prompt,
                           render_code, source_diff, validate_code)
from .config import settings, execution_config
from .db import connect
from .optimizer import propose
from .runner import HarborRunner, cleanup

log = logging.getLogger(__name__)


def process_job(job, runner=None, optimizer=propose, shutdown=None):
    runner = runner or HarborRunner()
    stop = threading.Event()
    done = threading.Event()

    def renew():
        while not done.wait(settings().heartbeat_seconds):
            try:
                if (shutdown and shutdown.is_set()) or not queue.heartbeat(job):
                    stop.set()
                    return
            except Exception:
                # Fail closed: an uncertain lease never authorizes further writes.
                stop.set()
                return

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    iteration = None
    try:
        if job['request'].get('execution', execution_config()) != execution_config():
            raise RuntimeError('Worker configuration differs from the submitted job snapshot')
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
                if number > 0:
                    best = next(r for r in records if r['id'] == job['best_iteration_id'])
                    proposal = optimizer(best, records)
                    code = proposal['agent_code']
                    prompt = best['prompt']
                    diff = source_diff(best.get('agent_code') or baseline_code(best['prompt']), code)
                source, source_hash = render_code(code)
            iteration = queue.begin_iteration(job, number, source, source_hash, prompt, proposal, code, diff)
            if code is not None:
                try:
                    prompt = validate_code(code)
                except AgentValidationError as exc:
                    queue.reject_invalid_candidate(job, iteration,
                        {'status': 'failed', 'stage': 'static', 'message': str(exc)})
                    return
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


def reap_stale_runs():
    root = Path(settings().artifacts_dir) / 'runs'
    if not root.exists():
        return
    for attempt in root.glob('*/*'):
        # Recheck each candidate: a one-time snapshot can race a fresh claim.
        with connect() as conn:
            active = conn.execute("SELECT 1 FROM jobs WHERE claim_token::text=%s AND status='running' AND lease_until>now()", (attempt.name,)).fetchone()
        if attempt.is_dir() and not active:
            cleanup(attempt)


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
