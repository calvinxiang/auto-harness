"""Durable package search: short transactional transitions schedule ordinary jobs.

Controllers never wait for inference and never hold an execution slot. A run row
lock serializes its transition and child creation; a killed controller rolls back
both. All costly work uses the existing leased, fenced worker queue.
"""
import hashlib
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import Field, model_validator

from . import campaigns
from .db import connect
from .packages import baseline_package, package_snapshot
from .schemas import Input, JobOut
from .tasks import TASKS, HOLDOUT_TASKS

TERMINAL = ('succeeded', 'failed', 'cancelled')


class RunCreate(Input):
    name: str = Field(min_length=1, max_length=100)
    baseline_version_id: UUID | None = None
    development_task_ids: list[str] = Field(default_factory=lambda: list(TASKS), min_length=1, max_length=10)
    heldout_task_ids: list[str] = Field(default_factory=list, max_length=3)
    dimensions: list[Literal['tools', 'context', 'skills', 'control']] = Field(default_factory=lambda: ['tools', 'context', 'skills'], min_length=1, max_length=3)
    repetitions: int = Field(default=1, ge=1, le=2)
    max_rounds: int = Field(default=2, ge=1, le=3)
    patience: int = Field(default=2, ge=1, le=3)
    profile: campaigns.AgentProfile = Field(default_factory=campaigns.default_profile)

    @model_validator(mode='after')
    def plan(self):
        for values, allowed in [(self.development_task_ids, TASKS), (self.heldout_task_ids, HOLDOUT_TASKS),
                                (self.dimensions, ('tools', 'context', 'skills', 'control'))]:
            if len(values) != len(set(values)) or not set(values) <= set(allowed):
                raise ValueError('Unknown or duplicate plan item')
        return self


def visible(conn, org_id, run_id, user, role):
    row = conn.execute('SELECT * FROM optimization_runs WHERE id=%s AND org_id=%s AND (%s OR user_id=%s)',
                       (run_id, org_id, role == 'admin', user['id'])).fetchone()
    if not row:
        raise HTTPException(404, 'Optimization run not found')
    return row


def _update(conn, run, **values):
    allowed = {'phase', 'status', 'best_version_id', 'best_score', 'baseline_experiment_id',
               'heldout_experiment_id', 'evidence_experiment_id', 'round_number', 'plateau_rounds', 'stop_reason'}
    assert set(values) <= allowed
    assignments = ','.join(f'{key}=%s' for key in values)
    conn.execute(f'UPDATE optimization_runs SET {assignments},updated_at=now() WHERE id=%s',
                 (*values.values(), run['id']))
    run.update(values)


def _finish(conn, run, reason, status='succeeded'):
    _update(conn, run, status=status, phase='complete', stop_reason=reason)
    conn.execute('UPDATE optimization_runs SET finished_at=now() WHERE id=%s', (run['id'],))


def _experiment(conn, run, versions, suffix, heldout=False):
    # Serialize quota admission with manual submissions. Waiting for quota does
    # not create partial children and does not consume the search round budget.
    conn.execute('SELECT id FROM organizations WHERE id=%s FOR UPDATE', (run['org_id'],))
    active = conn.execute("SELECT count(*) AS n FROM experiments WHERE org_id=%s AND status='running'", (run['org_id'],)).fetchone()['n']
    if active >= 3:
        return None
    tasks = run['request']
    # Experiments require development tasks. A final check places the requested
    # held-out tasks in its actual held-out split, with a new incumbent development
    # confirmation first. Neither split is fed back after selection is frozen.
    body = campaigns.ExperimentCreate(name=('search-' + str(run['id']) + '-' + suffix), version_ids=versions,
        development_task_ids=tasks['development_task_ids'],
        heldout_task_ids=tasks['heldout_task_ids'] if heldout else [],
        repetitions=1 if heldout else tasks['repetitions'], profile=tasks['profile'])
    return campaigns.submit_experiment(conn, run['org_id'], {'id': run['user_id']}, 'admin', body,
        str(run['id']) + '-' + suffix, execution=run['execution'])


def submit(conn, org_id, user, role, body, key):
    request = body.model_dump(mode='json')
    conn.execute('SELECT id FROM organizations WHERE id=%s FOR UPDATE', (org_id,))
    if key:
        previous = conn.execute('SELECT * FROM optimization_runs WHERE org_id=%s AND user_id=%s AND idempotency_key=%s',
                                (org_id, user['id'], key)).fetchone()
        if previous:
            if previous['request'] != request:
                raise HTTPException(409, 'Idempotency key was used with a different optimization plan')
            return previous
    if conn.execute("SELECT 1 FROM optimization_runs WHERE org_id=%s AND status='running'", (org_id,)).fetchone():
        raise HTTPException(429, 'Organization already has an active optimization run')
    parent = campaigns.visible(conn, 'harness_versions', org_id, body.baseline_version_id, user, role) if body.baseline_version_id else None
    if parent is None:
        parent = campaigns.insert_version(conn, org_id, user['id'], 'optimization baseline', 'baseline', package_snapshot(baseline_package()))
    execution = campaigns.profile_execution(body.profile)
    run = conn.execute('''INSERT INTO optimization_runs
        (id,org_id,user_id,name,request,execution,idempotency_key,initial_version_id,best_version_id)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
        (uuid4(), org_id, user['id'], body.name, Jsonb(request), Jsonb(execution), key, parent['id'], parent['id'])).fetchone()
    # The controller creates baseline trials after admission; POST is just a
    # bounded transaction, even when all experiment slots are already occupied.
    return run


def _report_experiment(conn, experiment_id):
    if not experiment_id:
        return None
    campaigns.reconcile(conn, experiment_id)
    return campaigns.experiment_report(conn, conn.execute('SELECT * FROM experiments WHERE id=%s', (experiment_id,)).fetchone())


def _summary(report, version_id, split='development'):
    return next(s for s in report['summary'] if s['version_id'] == str(version_id) and s['split'] == split)


def _stop_or_validate(conn, run, reason):
    if run['request']['heldout_task_ids']:
        _update(conn, run, phase='heldout', stop_reason=reason)
    else:
        _finish(conn, run, reason)


def _new_round(conn, run):
    conn.execute('SELECT id FROM organizations WHERE id=%s FOR UPDATE', (run['org_id'],))
    active = conn.execute("SELECT count(*) AS n FROM jobs WHERE org_id=%s AND status IN ('queued','running') AND request->>'kind' IS DISTINCT FROM 'trial'", (run['org_id'],)).fetchone()['n']
    if active + len(run['request']['dimensions']) > 10:
        return
    parent = conn.execute('SELECT * FROM harness_versions WHERE id=%s', (run['best_version_id'],)).fetchone()
    # New proposals require the same fixed runtime. Old trials/retries can still
    # replay their saved source after a deployment, but search never mixes runtimes.
    current_runtime = hashlib.sha256(Path(__file__).with_name('runtime.py').read_text(encoding='utf-8').encode()).hexdigest()
    if parent['runtime_sha256'] != current_runtime:
        _finish(conn, run, 'runtime_changed', 'failed')
        return
    evidence_rows = conn.execute('''SELECT j.id,i.results FROM experiment_trials t JOIN jobs j ON j.id=t.job_id
        JOIN iterations i ON i.id=j.best_iteration_id WHERE t.experiment_id=%s AND t.version_id=%s
        AND t.split='development' AND j.status='succeeded' ORDER BY t.repetition,t.task_id''',
        (run['evidence_experiment_id'], run['best_version_id'])).fetchall()
    evidence = [task for row in evidence_rows for task in row['results']['tasks']]
    prior = conn.execute('SELECT number,decision FROM optimization_rounds WHERE run_id=%s ORDER BY number', (run['id'],)).fetchall()
    history = [{'round': r['number'], 'decision': r['decision']} for r in prior]
    number = run['round_number'] + 1
    proposals = {}
    for dimension in run['request']['dimensions']:
        job_id = uuid4()
        request = {'kind': 'proposal', 'version_id': str(parent['id']), 'dimension': dimension,
            'execution': run['execution'], 'task_ids': [], 'max_iterations': 0,
            'search_run_id': str(run['id']), 'round': number,
            'evidence_job_ids': [str(row['id']) for row in evidence_rows],
            'review_feedback': 'Use prior round outcomes to avoid repeating rejected ideas. Keep the change general and focused.'}
        output = {'evidence': evidence, 'search_history': history, 'parent_package_sha256': parent['package_sha256']}
        conn.execute('INSERT INTO jobs(id,org_id,user_id,request,output) VALUES(%s,%s,%s,%s,%s)',
                     (job_id, run['org_id'], run['user_id'], Jsonb(request), Jsonb(output)))
        proposals[dimension] = str(job_id)
    conn.execute('INSERT INTO optimization_rounds(run_id,number,parent_version_id,proposal_jobs) VALUES(%s,%s,%s,%s)',
                 (run['id'], number, parent['id'], Jsonb(proposals)))
    _update(conn, run, phase='proposing', round_number=number)


def _after_round(conn, run, decision):
    conn.execute("UPDATE optimization_rounds SET status='completed',decision=%s,finished_at=now() WHERE run_id=%s AND number=%s",
                 (Jsonb(decision), run['id'], run['round_number']))
    if run['best_score'] == 1:
        _stop_or_validate(conn, run, 'all_development_tasks_passed')
    elif run['plateau_rounds'] >= run['request']['patience']:
        _stop_or_validate(conn, run, 'plateau')
    elif run['round_number'] >= run['request']['max_rounds']:
        _stop_or_validate(conn, run, 'max_rounds')
    else:
        # phase=baseline here means incumbent evidence is ready for another round;
        # the initial baseline_experiment_id remains unchanged for the audit trail.
        _update(conn, run, phase='baseline')


def advance(conn, run):
    if run['phase'] == 'baseline':
        if not run['baseline_experiment_id']:
            exp = _experiment(conn, run, [run['initial_version_id']], 'baseline')
            if exp:
                _update(conn, run, baseline_experiment_id=exp['id'], evidence_experiment_id=exp['id'])
            return
        if run['best_score'] is None:
            report = _report_experiment(conn, run['baseline_experiment_id'])
            if report['status'] == 'running':
                return
            score = _summary(report, run['initial_version_id'])['score']
            if score is None:
                _finish(conn, run, 'baseline_evaluation_failed', 'failed')
                return
            _update(conn, run, best_score=score)
        if run['best_score'] == 1:
            _stop_or_validate(conn, run, 'all_development_tasks_passed')
        else:
            _new_round(conn, run)
        return
    if run['phase'] == 'heldout':
        if not run['heldout_experiment_id']:
            ids = list(dict.fromkeys([run['initial_version_id'], run['best_version_id']]))
            exp = _experiment(conn, run, ids, 'final-check', heldout=True)
            if exp:
                _update(conn, run, heldout_experiment_id=exp['id'])
            return
        report = _report_experiment(conn, run['heldout_experiment_id'])
        if report['status'] != 'running':
            _finish(conn, run, run['stop_reason'] if report['status'] == 'succeeded' else 'final_evaluation_failed',
                    'succeeded' if report['status'] == 'succeeded' else 'failed')
        return
    row = conn.execute('SELECT * FROM optimization_rounds WHERE run_id=%s AND number=%s', (run['id'], run['round_number'])).fetchone()
    jobs = conn.execute('SELECT * FROM jobs WHERE id=ANY(%s::uuid[])', (list(row['proposal_jobs'].values()),)).fetchall()
    if run['phase'] == 'proposing':
        if any(j['status'] not in TERMINAL for j in jobs):
            return
        candidates, outcomes = [], []
        parent = conn.execute('SELECT runtime_sha256 FROM harness_versions WHERE id=%s', (row['parent_version_id'],)).fetchone()
        for dimension in run['request']['dimensions']:
            job_id = row['proposal_jobs'][dimension]
            job = next(j for j in jobs if str(j['id']) == job_id)
            output = job['output'] or {}
            version_id = output.get('version_id')
            valid = job['status'] == 'succeeded' and version_id is not None
            if valid:
                candidate = conn.execute('SELECT runtime_sha256 FROM harness_versions WHERE id=%s', (version_id,)).fetchone()
                valid = candidate['runtime_sha256'] == parent['runtime_sha256']
            outcomes.append({'dimension': dimension, 'job_id': job_id, 'version_id': version_id,
                'validated': valid, 'status': job['status'], 'error': job['error'],
                'diagnosis': (output.get('proposal') or {}).get('diagnosis'),
                'rationale': (output.get('proposal') or {}).get('rationale')})
            if valid:
                candidates.append(version_id)
        decision = {'proposal_outcomes': outcomes, 'selected_version_id': str(run['best_version_id']), 'improved': False}
        if not candidates:
            _update(conn, run, plateau_rounds=run['plateau_rounds'] + 1)
            decision['reason'] = 'no_valid_candidates'
            _after_round(conn, run, decision)
            return
        exp = _experiment(conn, run, [run['best_version_id'], *candidates], 'round-' + str(row['number']))
        if exp:
            conn.execute("UPDATE optimization_rounds SET experiment_id=%s,status='comparing',decision=%s WHERE run_id=%s AND number=%s",
                         (exp['id'], Jsonb(decision), run['id'], row['number']))
            _update(conn, run, phase='comparing')
        return
    if run['phase'] == 'comparing':
        report = _report_experiment(conn, row['experiment_id'])
        if report['status'] == 'running':
            return
        reference = _summary(report, row['parent_version_id'])
        decision = row['decision']
        decision.update(reference_score=reference['score'], candidates=[])
        if reference['score'] is None:
            decision['reason'] = 'reference_evaluation_failed'
            conn.execute("UPDATE optimization_rounds SET status='completed',decision=%s,finished_at=now() WHERE run_id=%s AND number=%s",
                         (Jsonb(decision), run['id'], row['number']))
            _finish(conn, run, 'reference_evaluation_failed', 'failed')
            return
        selected, score = str(row['parent_version_id']), reference['score']
        for candidate_id in report['request']['version_ids'][1:]:
            summary = _summary(report, candidate_id)
            comparison = next(c for c in report['comparisons'] if c['version_id'] == str(candidate_id))
            eligible = summary['score'] is not None and summary['score'] > reference['score'] and not comparison['regressions']
            decision['candidates'].append({'version_id': str(candidate_id), 'score': summary['score'],
                'eligible': eligible, 'regressions': comparison['regressions'], 'errors': summary['errors']})
            if eligible and summary['score'] > score:
                selected, score = str(candidate_id), summary['score']
        improved = selected != str(row['parent_version_id'])
        decision.update(selected_version_id=selected, improved=improved,
                        reason='strict_gain_without_regression' if improved else 'no_eligible_improvement')
        _update(conn, run, best_version_id=UUID(selected), best_score=score,
                evidence_experiment_id=row['experiment_id'], plateau_rounds=0 if improved else run['plateau_rounds'] + 1)
        _after_round(conn, run, decision)


def advance_one():
    with connect() as conn:
        run = conn.execute("SELECT * FROM optimization_runs WHERE status='running' ORDER BY updated_at,id FOR UPDATE SKIP LOCKED LIMIT 1").fetchone()
        if run:
            advance(conn, run)
            conn.execute('UPDATE optimization_runs SET updated_at=now() WHERE id=%s', (run['id'],))


def report(conn, run):
    rounds = conn.execute('SELECT * FROM optimization_rounds WHERE run_id=%s ORDER BY number', (run['id'],)).fetchall()
    experiments = {}
    ids = [run['baseline_experiment_id'], run['heldout_experiment_id']] + [r['experiment_id'] for r in rounds]
    for experiment_id in ids:
        if experiment_id:
            exp = conn.execute('SELECT * FROM experiments WHERE id=%s', (experiment_id,)).fetchone()
            experiments[str(experiment_id)] = campaigns.experiment_report(conn, exp)
    for row in rounds:
        jobs = conn.execute('SELECT * FROM jobs WHERE id=ANY(%s::uuid[]) ORDER BY id', (list(row['proposal_jobs'].values()),)).fetchall()
        row['proposals'] = []
        for job in jobs:
            value = JobOut.model_validate(job).model_dump(mode='json')
            value['output'] = {k: v for k, v in (value['output'] or {}).items() if k != 'evidence'}
            row['proposals'].append(value)
    return {**run, 'rounds': rounds, 'experiments': experiments,
            'selection_policy': 'Strictly higher paired development score with no passing-trial regressions; held-out results never select or revise a version.'}


def cancel(conn, run):
    if run['status'] != 'running':
        return run
    rounds = conn.execute('SELECT * FROM optimization_rounds WHERE run_id=%s', (run['id'],)).fetchall()
    experiments = [run['baseline_experiment_id'], run['heldout_experiment_id']] + [r['experiment_id'] for r in rounds]
    jobs = [job_id for row in rounds for job_id in row['proposal_jobs'].values()]
    for exp in sorted({str(v) for v in experiments if v}):
        conn.execute("UPDATE experiments SET status='cancelled',finished_at=now() WHERE id=%s AND status='running'", (exp,))
        jobs.extend(str(r['job_id']) for r in conn.execute('SELECT job_id FROM experiment_trials WHERE experiment_id=%s', (exp,)).fetchall())
    if jobs:
        cancelled = conn.execute("UPDATE jobs SET status='cancelled',stop_reason='optimization_cancelled',finished_at=now(),updated_at=now(),lease_until=NULL WHERE id=ANY(%s::uuid[]) AND status IN ('queued','running') RETURNING id", (jobs,)).fetchall()
        for job in cancelled:
            conn.execute("UPDATE iterations SET status='interrupted',finished_at=now() WHERE job_id=%s AND status='running'", (job['id'],))
    conn.execute("UPDATE optimization_rounds SET status='cancelled',finished_at=now() WHERE run_id=%s AND status IN ('proposing','comparing')", (run['id'],))
    _finish(conn, run, 'cancelled_by_user', 'cancelled')
    return conn.execute('SELECT * FROM optimization_runs WHERE id=%s', (run['id'],)).fetchone()
