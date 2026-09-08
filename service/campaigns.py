"""Immutable harness versions and durable experiment plans composed of queued jobs."""
from collections import defaultdict
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import Field, model_validator
from typing import Literal

from .config import execution_config
from .packages import HarnessPackage
from .schemas import Input
from .tasks import TASKS, HOLDOUT_TASKS


class AgentProfile(Input):
    agent_model: str = Field(default='gpt-4.1-mini', pattern=r'^[A-Za-z0-9._:/-]{1,100}$')
    agent_api: Literal['chat_completions', 'responses'] = 'chat_completions'
    agent_reasoning_effort: Literal['', 'none', 'low', 'medium', 'high', 'xhigh', 'max'] = ''
    agent_max_output_tokens: int = Field(default=4096, ge=256, le=32768)
    agent_timeout_seconds: int = Field(default=300, ge=30, le=300)
    max_steps: int = Field(default=80, ge=2, le=80)


def default_profile():
    return AgentProfile(**{k: v for k, v in execution_config().items() if k in AgentProfile.model_fields})


def profile_execution(profile):
    return {**execution_config(), **profile.model_dump(), 'task_concurrency': 1, 'agent_contract': 'harness-package-v1'}


class VersionCreate(Input):
    label: str = Field(min_length=1, max_length=100)
    parent_id: UUID | None = None
    dimension: Literal['baseline', 'tools', 'context', 'skills', 'mixed'] = 'baseline'
    package: HarnessPackage | None = None


class ProposalCreate(Input):
    dimension: Literal['tools', 'context', 'skills']
    evidence_job_ids: list[UUID] = Field(default_factory=list, max_length=20)
    review_feedback: str | None = Field(default=None, max_length=4000)


class ExperimentCreate(Input):
    name: str = Field(min_length=1, max_length=100)
    version_ids: list[UUID] = Field(min_length=1, max_length=4)
    development_task_ids: list[str] = Field(default_factory=lambda: list(TASKS), min_length=1, max_length=10)
    heldout_task_ids: list[str] = Field(default_factory=list, max_length=3)
    repetitions: int = Field(default=1, ge=1, le=3)
    profile: AgentProfile = Field(default_factory=default_profile)

    @model_validator(mode='after')
    def plan(self):
        if len(set(self.version_ids)) != len(self.version_ids):
            raise ValueError('Duplicate version')
        for values, allowed in [(self.development_task_ids, TASKS), (self.heldout_task_ids, HOLDOUT_TASKS)]:
            if len(set(values)) != len(values) or not set(values) <= set(allowed):
                raise ValueError('Unknown or duplicate task in split')
        return self


def visible(conn, table, org_id, object_id, user, role):
    if table not in ('harness_versions', 'experiments'):
        raise ValueError('Unknown resource')
    row = conn.execute(f'SELECT * FROM {table} WHERE id=%s AND org_id=%s AND (%s OR user_id=%s)',
                       (object_id, org_id, role == 'admin', user['id'])).fetchone()
    if not row:
        raise HTTPException(404, 'Resource not found')
    return row


def insert_version(conn, org_id, user_id, label, dimension, snapshot, parent_id=None, proposal=None):
    return conn.execute('''INSERT INTO harness_versions
        (id,org_id,user_id,parent_id,label,dimension,package,package_sha256,runtime_sha256,file_hashes,
         agent_source,source_sha256,changes,proposal)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
        (uuid4(), org_id, user_id, parent_id, label, dimension, Jsonb(snapshot['package']),
         snapshot['package_sha256'], snapshot['runtime_sha256'], Jsonb(snapshot['file_hashes']), snapshot['agent_source'],
         snapshot['source_sha256'], Jsonb(snapshot['changes']), Jsonb(proposal) if proposal else None)).fetchone()


def submit_experiment(conn, org_id, user, role, body, key):
    request = body.model_dump(mode='json')
    # Shared org row also serializes duplicate submissions and quota admission.
    conn.execute('SELECT id FROM organizations WHERE id=%s FOR UPDATE', (org_id,))
    if key:
        existing = conn.execute('SELECT * FROM experiments WHERE org_id=%s AND user_id=%s AND idempotency_key=%s',
                                (org_id, user['id'], key)).fetchone()
        if existing:
            if existing['request'] != request:
                raise HTTPException(409, 'Idempotency key was used with a different experiment')
            return existing
    count = conn.execute("SELECT count(*) AS n FROM experiments WHERE org_id=%s AND status='running'", (org_id,)).fetchone()['n']
    if count >= 3:
        raise HTTPException(429, 'Organization has 3 active experiments')
    versions = [visible(conn, 'harness_versions', org_id, v, user, role) for v in body.version_ids]
    if len({v['runtime_sha256'] for v in versions}) > 1:
        raise HTTPException(422, 'Comparison versions must share the same runtime')
    exp = conn.execute('''INSERT INTO experiments(id,org_id,user_id,name,request,idempotency_key)
                           VALUES(%s,%s,%s,%s,%s,%s) RETURNING *''',
                       (uuid4(), org_id, user['id'], body.name, Jsonb(request), key)).fetchone()
    execution = profile_execution(body.profile)
    for split, tasks, repeats in [('development', body.development_task_ids, body.repetitions),
                                  ('heldout', body.heldout_task_ids, 1)]:
        for repetition in range(repeats):
            # Same-transaction timestamps tie; random UUIDs randomize claim order
            # within the plan. Each task remains a separately fenced job.
            for task in tasks:
                for version in versions:
                    job_id = uuid4()
                    job_request = {'kind': 'trial', 'experiment_id': str(exp['id']), 'split': split,
                                   'version_id': str(version['id']), 'task_ids': [task],
                                   'max_iterations': 0, 'execution': execution}
                    conn.execute('INSERT INTO jobs(id,org_id,user_id,request) VALUES(%s,%s,%s,%s)',
                                 (job_id, org_id, user['id'], Jsonb(job_request)))
                    conn.execute('INSERT INTO experiment_trials VALUES(%s,%s,%s,%s,%s,%s)',
                                 (exp['id'], version['id'], split, repetition, task, job_id))
    return exp


def reconcile(conn, experiment_id=None):
    # Never called while holding a child job lock. Cancellation locks parent then
    # children, so aggregation follows the same order and cannot invert it.
    parents = conn.execute("SELECT id FROM experiments WHERE status='running' AND (%s::uuid IS NULL OR id=%s) FOR UPDATE",
                           (experiment_id, experiment_id)).fetchall()
    for parent in parents:
        states = conn.execute('SELECT j.status FROM experiment_trials t JOIN jobs j ON j.id=t.job_id WHERE t.experiment_id=%s',
                              (parent['id'],)).fetchall()
        if states and all(s['status'] in ('succeeded', 'failed', 'cancelled') for s in states):
            status = 'succeeded' if all(s['status'] == 'succeeded' for s in states) else 'failed'
            conn.execute('UPDATE experiments SET status=%s,finished_at=now() WHERE id=%s', (status, parent['id']))


def experiment_report(conn, experiment):
    trials = conn.execute('''SELECT t.*,j.status,j.attempts,j.started_at,j.finished_at,
                            j.best_score,j.error,j.created_at,j.output FROM experiment_trials t
                            JOIN jobs j ON j.id=t.job_id WHERE t.experiment_id=%s
                            ORDER BY t.split,t.repetition,t.task_id,t.version_id''', (experiment['id'],)).fetchall()
    groups = defaultdict(list)
    for trial in trials:
        groups[(str(trial['version_id']), trial['split'])].append(trial)
    summaries = []
    for (version, split), rows in groups.items():
        complete = all(r['status'] == 'succeeded' for r in rows)
        summaries.append({'version_id': version, 'split': split, 'trials': len(rows),
            'completed': sum(r['status'] == 'succeeded' for r in rows),
            'errors': sum(r['status'] in ('failed', 'cancelled') for r in rows),
            'passed': sum(r['status'] == 'succeeded' and (r['best_score'] or 0) >= .5 for r in rows),
            'score': sum(r['best_score'] or 0 for r in rows) / len(rows) if complete else None})
    baseline = str(experiment['request']['version_ids'][0])
    indexed = {(str(t['version_id']), t['split'], t['repetition'], t['task_id']): t for t in trials}
    comparisons = []
    for (version, split), rows in groups.items():
        if version == baseline:
            continue
        pairs = [(indexed[(baseline, split, r['repetition'], r['task_id'])], r) for r in rows]
        valid = [(b, r) for b, r in pairs if b['status'] == r['status'] == 'succeeded']
        comparisons.append({'baseline_version_id': baseline, 'version_id': version, 'split': split,
            'complete': len(valid) == len(pairs), 'paired_trials': len(valid),
            'regressions': [{'task_id': r['task_id'], 'repetition': r['repetition']} for b, r in valid
                            if (b['best_score'] or 0) >= .5 and (r['best_score'] or 0) < .5],
            'improvements': [{'task_id': r['task_id'], 'repetition': r['repetition']} for b, r in valid
                             if (b['best_score'] or 0) < .5 and (r['best_score'] or 0) >= .5]})
    return {**experiment, 'summary': summaries, 'comparisons': comparisons, 'trials': trials,
            'selection': 'No automatic promotion; compare complete results and task regressions.'}
