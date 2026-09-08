"""Operator-only review and repeated evaluation of a saved agent version.

Uses the same runner/sandboxes; leaves API jobs and their history unchanged.
Reports/checkpoints live in the private artifacts volume. No held-out results are
sent to the optimizer. This is an evaluation tool, not an API authorization bypass.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import threading
from uuid import UUID, uuid4

from .agent_source import render_code, source_diff, validate_code
from .config import execution_config, settings
from .db import connect
from .evidence import trace_signals
from .optimizer import propose
from .runner import HarborRunner, read_text

HOLDOUT_TASKS = ['cancel-async-tasks', 'openssl-selfsigned-cert', 'large-scale-text-editing']
BENCHMARK_KEYS = ['dataset', 'agent_model', 'sandbox_provider', 'agent_timeout_seconds',
                  'max_steps', 'task_concurrency', 'harbor_version', 'agent_contract']


def now():
    return datetime.now(timezone.utc).isoformat()


def write_report(path, report):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    temporary.replace(path)


def check_comparison_config(expected, actual):
    if any(expected.get(k) != actual.get(k) for k in BENCHMARK_KEYS):
        raise ValueError('Benchmark settings differ from the saved version')


def evaluation_plan(development, holdout, repeats):
    if len(development) != len(set(development)) or len(holdout) != len(set(holdout)):
        raise ValueError('Duplicate evaluation tasks')
    if set(development) & set(holdout):
        raise ValueError('Held-out tasks must not occur in optimizer feedback')
    plan = []
    for split, tasks, count in [('development', development, repeats), ('heldout', holdout, 1)]:
        for repetition in range(count):
            order = ['baseline', 'candidate'] if repetition % 2 == 0 else ['candidate', 'baseline']
            for variant in order:
                plan.append({'split': split, 'repetition': repetition, 'variant': variant, 'task_ids': tasks})
    return plan


def proposal_feedback(previous, experiment_id):
    if any(run['split'] == 'heldout' for run in previous.get('runs', [])):
        raise ValueError('Held-out evaluation has started; do not use this experiment for optimizer feedback')
    return {'experiment_id': str(experiment_id), 'proposal': previous['proposal'],
            'review': previous['review']}


def assess_agent_failure(report, run_id, task_id, reason):
    """Explicit operator assessment, retaining the unmodified runner evidence.

    Missing rewards remain errors by default. An agent can also destroy its own
    sandbox so the verifier cannot run; never retry that away or award a pass.
    """
    if report['status'] != 'failed' or not reason.strip():
        raise ValueError('Assess only a stopped experiment, with a concrete evidence-based reason')
    run = next(r for r in report['runs'] if r['run_id'] == str(run_id))
    task = next(t for t in run['results']['tasks'] if t['task_id'] == task_id)
    if run['status'] != 'failed' or task['status'] != 'error':
        raise ValueError('Only a failed run with an unresolved task error can be assessed')
    run.setdefault('original_results', deepcopy(run['results']))
    run.setdefault('assessments', []).append({'task_id': task_id, 'classification': 'agent_failure',
        'reward': 0.0, 'reason': reason, 'recorded_at': now()})
    task.update(status='failed', reward=0.0,
                failure_summary='Operator-assessed agent failure; raw verifier error retained. ' + reason)
    tasks = run['results']['tasks']
    run['results'].update(passed=sum(t['status'] == 'passed' for t in tasks),
        failed=sum(t['status'] in ('failed', 'timeout') for t in tasks),
        errors=sum(t['status'] == 'error' for t in tasks),
        score=sum(t['reward'] or 0 for t in tasks) / len(tasks))
    if not run['results']['errors']:
        run['status'] = 'assessed'
    return report


def proposal_from_job(job_id, path, review_feedback=None):
    with connect() as conn:
        job = conn.execute('SELECT * FROM jobs WHERE id=%s', (job_id,)).fetchone()
        if not job or job['status'] != 'succeeded' or not job['best_iteration_id']:
            raise ValueError('Select a completed job with a best version')
        records = conn.execute('SELECT * FROM iterations WHERE job_id=%s ORDER BY number,attempt', (job_id,)).fetchall()
    best = deepcopy(next(r for r in records if r['id'] == job['best_iteration_id']))
    check_comparison_config(job['request']['execution'], execution_config())
    if best['agent_source'] != render_code(best['agent_code'])[0]:
        raise ValueError('Runtime changed; a fresh baseline is required for a fair comparison')
    if best['attempt'] != job['attempts']:
        raise ValueError('Raw evidence from an older worker attempt requires explicit artifact selection')
    root = Path(settings().artifacts_dir) / 'runs' / str(job_id) / str(job['claim_token']) / str(best['number'])
    source = root / 'agent.py'
    if not source.exists() or hashlib.sha256(source.read_bytes()).hexdigest() != best['source_sha256']:
        raise ValueError('Saved raw evidence does not match the baseline source')
    signals = {}
    for trial in (root / 'benchmark').glob('*/result.json'):
        result = json.loads(read_text(trial, 500000))
        signals[result['task_name']] = trace_signals(read_text(trial.parent / 'agent/trace.json', 2000000))
    for task in best['results']['tasks']:
        task['trace_signals'] = signals.get(task['task_id'], {'complete_trace': False})
    report = {'experiment_id': str(path.parent.name), 'status': 'proposing', 'created_at': now(),
              'source_job_id': str(job_id), 'source_iteration_id': str(best['id']),
              'execution': execution_config(), 'source_execution': job['request']['execution'],
              'baseline_agent_code': best['agent_code'], 'baseline_agent_source': best['agent_source'],
              'baseline_source_sha256': best['source_sha256'],
              'development_task_ids': job['request']['task_ids'], 'development_signals': signals,
              'optimizer_source_sha256': hashlib.sha256(Path(__file__).with_name('optimizer.py').read_bytes()).hexdigest()}
    report['review_feedback'] = review_feedback
    write_report(path, report)
    proposal = propose(best, records, review_feedback)
    source, sha = render_code(proposal['agent_code'])
    report.update(status='proposed', proposal=proposal, candidate_agent_source=source,
                  candidate_source_sha256=sha, diff=source_diff(best['agent_code'], proposal['agent_code']))
    write_report(path, report)
    validate_code(proposal['agent_code'])
    return report


def evaluate(report, path, repeats, review_reason, runner, stop):
    check_comparison_config(report['execution'], execution_config())
    for variant in ['baseline', 'candidate']:
        actual = hashlib.sha256(report[variant + '_agent_source'].encode()).hexdigest()
        if actual != report[variant + '_source_sha256']:
            raise ValueError('Agent snapshot has changed')
    plan = evaluation_plan(report['development_task_ids'], HOLDOUT_TASKS, repeats)
    if report.get('plan') and report['plan'] != plan:
        raise ValueError('Evaluation plan is already frozen; use the original repetitions')
    if not report.get('plan'):
        report['review'] = {'decision': 'evaluate', 'reason': review_reason}
    if report.get('error'):
        report.setdefault('interruptions', []).append({'error': report.pop('error'),
            'error_details': report.pop('error_details', {}), 'resumed_at': now()})
    report.update(plan=plan, status='evaluating')
    report.setdefault('runs', [])
    write_report(path, report)
    for index, entry in enumerate(plan):
        if any(r['plan_index'] == index and r['status'] in ('completed', 'assessed') for r in report['runs']):
            continue
        if stop.is_set():
            raise RuntimeError('Evaluation interrupted')
        job = {'id': uuid4(), 'claim_token': uuid4(), 'request': {'task_ids': entry['task_ids']}}
        iteration = {'id': uuid4(), 'number': 0, 'agent_source': report[entry['variant'] + '_agent_source']}
        run = {**entry, 'plan_index': index, 'run_id': str(job['id']), 'started_at': now(),
               'status': 'running', 'source_sha256': report[entry['variant'] + '_source_sha256']}
        report['runs'].append(run)
        write_report(path, report)
        print(json.dumps({'started': entry, 'run_id': run['run_id']}), flush=True)
        validation = runner.preflight(job, iteration, stop)
        run['validation'] = validation
        if validation['status'] != 'passed':
            run['status'] = 'failed'
            write_report(path, report)
            raise ValueError('Candidate failed sandbox contract')
        try:
            results = runner.run(job, iteration, stop)
        except Exception as exc:
            run.update(status='failed', error=type(exc).__name__, results=getattr(exc, 'results', None), finished_at=now())
            write_report(path, report)
            raise
        run.update(results=results, status='failed' if results['errors'] else 'completed', finished_at=now())
        write_report(path, report)
        print(json.dumps({'completed': entry, 'score': results['score'], 'errors': results['errors']}), flush=True)
        if results['errors']:
            raise RuntimeError('Infrastructure error; retain evidence and stop the experiment')
    report.update(status='completed', finished_at=now())
    write_report(path, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('propose')
    p.add_argument('--job-id', type=UUID, required=True)
    p.add_argument('--optimizer-model')
    p.add_argument('--reasoning-effort', choices=['none', 'low', 'medium', 'high', 'xhigh'])
    p.add_argument('--review-feedback', type=UUID, help='Prior proposal with a saved review, before any held-out evaluation')
    p = sub.add_parser('evaluate')
    p.add_argument('--experiment-id', type=UUID, required=True)
    p.add_argument('--repetitions', type=int, choices=[1, 2, 3], default=2)
    p.add_argument('--review-reason', required=True)
    p = sub.add_parser('assess-agent-failure', help='Record a zero-score agent failure while retaining raw verifier errors')
    p.add_argument('--experiment-id', type=UUID, required=True)
    p.add_argument('--run-id', type=UUID, required=True)
    p.add_argument('--task-id', required=True)
    p.add_argument('--reason', required=True)
    args = parser.parse_args()
    if args.command == 'propose':
        if args.optimizer_model:
            os.environ['OPTIMIZER_MODEL'] = args.optimizer_model
        if args.reasoning_effort:
            os.environ['OPTIMIZER_REASONING_EFFORT'] = args.reasoning_effort
        settings.cache_clear()
    experiment_id = uuid4() if args.command == 'propose' else args.experiment_id
    root = Path(settings().artifacts_dir) / 'experiments' / str(experiment_id)
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'report.json'
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    try:
        if args.command == 'propose':
            feedback = None
            if args.review_feedback:
                previous = json.loads((root.parent / str(args.review_feedback) / 'report.json').read_text())
                feedback = proposal_feedback(previous, args.review_feedback)
            report = proposal_from_job(args.job_id, path, feedback)
            print(json.dumps({'experiment_id': str(experiment_id), 'diagnosis': report['proposal']['diagnosis'],
                              'rationale': report['proposal']['rationale'], 'diff': report['diff']}), flush=True)
        elif args.command == 'assess-agent-failure':
            report = json.loads(path.read_text())
            assess_agent_failure(report, args.run_id, args.task_id, args.reason)
            write_report(path, report)
            print(json.dumps({'experiment_id': str(experiment_id), 'assessed_agent_failure': args.task_id,
                              'reward': 0.0, 'raw_results_preserved': True}), flush=True)
        else:
            report = json.loads(path.read_text())
            # Operator experiments do not have queue leases; their runner artifacts
            # must be outside the production worker's stale-job sweep.
            os.environ['ARTIFACTS_DIR'] = str(root / 'execution')
            settings.cache_clear()
            evaluate(report, path, args.repetitions, args.review_reason, HarborRunner(), stop)
    except Exception as exc:
        if path.exists():
            report = json.loads(path.read_text())
            report.update(status='failed', error=type(exc).__name__)
            report['error_details'] = getattr(exc, 'details', {})
            write_report(path, report)
        raise


if __name__ == '__main__':
    main()
