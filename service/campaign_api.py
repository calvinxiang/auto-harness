from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Header, HTTPException, Query
from psycopg.types.json import Jsonb

from . import campaigns
from .config import execution_config
from .db import connect
from .packages import baseline_package, package_snapshot
from .schemas import JobOut


def register(app, User, membership, visible_job):
    @app.post('/organizations/{org_id}/harness-versions', status_code=201)
    def create_version(org_id: UUID, body: campaigns.VersionCreate, user: User):
        with connect() as conn:
            role = membership(conn, org_id, user)
            parent = campaigns.visible(conn, 'harness_versions', org_id, body.parent_id, user, role) if body.parent_id else None
            package = body.package.model_dump() if body.package else baseline_package()
            try:
                snapshot = package_snapshot(package, parent['package'] if parent else None)
            except ValueError:
                raise HTTPException(422, 'Invalid harness package') from None
            return campaigns.insert_version(conn, org_id, user['id'], body.label, body.dimension, snapshot, body.parent_id)

    @app.get('/organizations/{org_id}/harness-versions')
    def versions(org_id: UUID, user: User, limit: int = Query(50, ge=1, le=100)):
        with connect() as conn:
            role = membership(conn, org_id, user)
            return conn.execute('''SELECT id,parent_id,label,dimension,package_sha256,source_sha256,created_at
                FROM harness_versions WHERE org_id=%s AND (%s OR user_id=%s) ORDER BY created_at DESC LIMIT %s''',
                (org_id, role == 'admin', user['id'], limit)).fetchall()

    @app.get('/organizations/{org_id}/harness-versions/{version_id}')
    def version(org_id: UUID, version_id: UUID, user: User):
        with connect() as conn:
            return campaigns.visible(conn, 'harness_versions', org_id, version_id, user, membership(conn, org_id, user))

    @app.post('/organizations/{org_id}/harness-versions/{version_id}/proposals', status_code=202, response_model=JobOut)
    def proposal(org_id: UUID, version_id: UUID, body: campaigns.ProposalCreate, user: User,
                 idempotency_key: Annotated[str | None, Header(max_length=128, min_length=1)] = None):
        with connect() as conn:
            role = membership(conn, org_id, user)
            parent = campaigns.visible(conn, 'harness_versions', org_id, version_id, user, role)
            evidence = []
            for job_id in body.evidence_job_ids:
                job = visible_job(conn, org_id, job_id, user)
                if job['request'].get('split') == 'heldout':
                    raise HTTPException(422, 'Held-out evidence cannot be used for proposals')
                if job['status'] != 'succeeded':
                    raise HTTPException(422, 'Evidence job must be completed')
                records = conn.execute('SELECT results FROM iterations WHERE job_id=%s AND status=%s', (job_id, 'completed')).fetchall()
                evidence.extend(task for record in records for task in (record['results'] or {}).get('tasks', []))
            request = {'kind': 'proposal', 'version_id': str(version_id), 'dimension': body.dimension,
                       'evidence_job_ids': [str(i) for i in body.evidence_job_ids],
                       'review_feedback': body.review_feedback,
                       'execution': execution_config(), 'task_ids': [], 'max_iterations': 0}
            conn.execute('SELECT id FROM organizations WHERE id=%s FOR UPDATE', (org_id,))
            if idempotency_key:
                existing = conn.execute('SELECT * FROM jobs WHERE org_id=%s AND user_id=%s AND idempotency_key=%s',
                                        (org_id, user['id'], idempotency_key)).fetchone()
                if existing:
                    if {k: v for k, v in existing['request'].items() if k != 'execution'} != {k: v for k, v in request.items() if k != 'execution'}:
                        raise HTTPException(409, 'Idempotency key was used with a different proposal')
                    return existing
            count = conn.execute("SELECT count(*) AS n FROM jobs WHERE org_id=%s AND status IN ('queued','running') AND request->>'kind' IS DISTINCT FROM 'trial'", (org_id,)).fetchone()['n']
            if count >= 10:
                raise HTTPException(429, 'Organization has 10 active jobs')
            # Evidence is frozen at admission; membership changes cannot expose new data later.
            return conn.execute('INSERT INTO jobs(id,org_id,user_id,request,idempotency_key,output) VALUES(%s,%s,%s,%s,%s,%s) RETURNING *',
                (uuid4(), org_id, user['id'], Jsonb(request), idempotency_key,
                 Jsonb({'evidence': evidence, 'parent_package_sha256': parent['package_sha256']}))).fetchone()

    @app.post('/organizations/{org_id}/experiments', status_code=202)
    def submit(org_id: UUID, body: campaigns.ExperimentCreate, user: User,
               idempotency_key: Annotated[str | None, Header(max_length=128, min_length=1)] = None):
        with connect() as conn:
            return campaigns.submit_experiment(conn, org_id, user, membership(conn, org_id, user), body, idempotency_key)

    @app.get('/organizations/{org_id}/experiments')
    def experiments(org_id: UUID, user: User, limit: int = Query(50, ge=1, le=100)):
        with connect() as conn:
            role = membership(conn, org_id, user)
            return conn.execute('SELECT * FROM experiments WHERE org_id=%s AND (%s OR user_id=%s) ORDER BY created_at DESC LIMIT %s',
                                (org_id, role == 'admin', user['id'], limit)).fetchall()

    @app.get('/organizations/{org_id}/experiments/{experiment_id}')
    def report(org_id: UUID, experiment_id: UUID, user: User):
        with connect() as conn:
            role = membership(conn, org_id, user)
            campaigns.visible(conn, 'experiments', org_id, experiment_id, user, role)
            campaigns.reconcile(conn, experiment_id)
            return campaigns.experiment_report(conn, campaigns.visible(conn, 'experiments', org_id, experiment_id, user, role))

    @app.post('/organizations/{org_id}/experiments/{experiment_id}/cancel')
    def cancel(org_id: UUID, experiment_id: UUID, user: User):
        with connect() as conn:
            role = membership(conn, org_id, user)
            campaigns.visible(conn, 'experiments', org_id, experiment_id, user, role)
            parent = conn.execute('SELECT * FROM experiments WHERE id=%s FOR UPDATE', (experiment_id,)).fetchone()
            if parent['status'] == 'running':
                conn.execute("UPDATE experiments SET status='cancelled',finished_at=now() WHERE id=%s", (experiment_id,))
                children = conn.execute('''UPDATE jobs SET status='cancelled',stop_reason='experiment_cancelled',
                    finished_at=now(),updated_at=now(),lease_until=NULL
                    WHERE id IN (SELECT job_id FROM experiment_trials WHERE experiment_id=%s)
                    AND status IN ('queued','running') RETURNING id''', (experiment_id,)).fetchall()
                for child in children:
                    conn.execute("UPDATE iterations SET status='interrupted',finished_at=now() WHERE job_id=%s AND status='running'", (child['id'],))
            return campaigns.visible(conn, 'experiments', org_id, experiment_id, user, role)
