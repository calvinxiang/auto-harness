"""Postgres is both the durable queue and the source of truth.

Claims use SKIP LOCKED; every checkpoint is fenced by the current lease token.
No database transaction is held open while a benchmark or LLM runs.
"""
from uuid import uuid4
from psycopg.types.json import Jsonb
from .config import settings
from .db import connect


class LeaseLost(RuntimeError):
    pass


def claim():
    cfg = settings()
    with connect() as conn:
        # Capacity is shared by every worker, and retained until sandbox cleanup.
        capacity = conn.execute('SELECT capacity FROM execution_pool WHERE id=1 FOR UPDATE').fetchone()['capacity']
        used = conn.execute('SELECT COALESCE(sum(slots),0) AS n FROM execution_reservations').fetchone()['n']
        exhausted = conn.execute("""UPDATE jobs SET status='failed',stop_reason='worker_attempts_exhausted',
            error='{"code":"worker_attempts_exhausted","message":"Worker lease expired repeatedly"}',
            finished_at=now(),updated_at=now(),lease_until=NULL
            WHERE status='running' AND lease_until<now() AND attempts>=%s RETURNING id""", (cfg.max_attempts,)).fetchall()
        for row in exhausted:
            conn.execute("UPDATE iterations SET status='interrupted',finished_at=now() WHERE job_id=%s AND status='running'", (row['id'],))
        rows = conn.execute("""SELECT * FROM jobs WHERE (status='queued' OR
            (status='running' AND lease_until<now())) AND attempts<%s
            AND LEAST(2,GREATEST(1,jsonb_array_length(request->'task_ids')))<=%s
            AND NOT EXISTS (SELECT 1 FROM execution_reservations r WHERE r.job_id=jobs.id)
            AND (request->>'split' IS DISTINCT FROM 'heldout' OR NOT EXISTS (
                SELECT 1 FROM jobs d WHERE d.request->>'experiment_id'=jobs.request->>'experiment_id'
                AND d.request->>'split'='development' AND d.status IN ('queued','running')))
            ORDER BY (SELECT COALESCE(sum(r.slots),0) FROM execution_reservations r
                      JOIN jobs owner ON owner.id=r.job_id WHERE owner.org_id=jobs.org_id),
                     created_at,id FOR UPDATE SKIP LOCKED LIMIT 1""", (cfg.max_attempts, capacity-used)).fetchall()
        row = next((r for r in rows if min(2, max(1, len(r['request'].get('task_ids', [])))) <= capacity-used), None)
        if not row:
            return None
        conn.execute("UPDATE iterations SET status='interrupted',finished_at=now() WHERE job_id=%s AND status='running'", (row['id'],))
        job = conn.execute("""UPDATE jobs SET status='running',attempts=attempts+1,claim_token=%s,
            lease_until=now()+make_interval(secs=>%s),started_at=COALESCE(started_at,now()),updated_at=now()
            WHERE id=%s RETURNING *""", (uuid4(), cfg.lease_seconds, row['id'])).fetchone()
        slots = min(2, max(1, len(job['request'].get('task_ids', []))))
        conn.execute('INSERT INTO execution_reservations(claim_token,job_id,slots) VALUES(%s,%s,%s)',
                     (job['claim_token'], job['id'], slots))
        return job


def release_reservation(job):
    """Caller must finish scoped sandbox cleanup before returning capacity."""
    with connect() as conn:
        conn.execute('DELETE FROM execution_reservations WHERE claim_token=%s AND job_id=%s',
                     (job['claim_token'], job['id']))


def record_output(job, output):
    with connect() as conn:
        owned(conn, job)
        conn.execute('UPDATE jobs SET output=%s,updated_at=now() WHERE id=%s', (Jsonb(output), job['id']))


def complete_output(job, output):
    with connect() as conn:
        owned(conn, job)
        conn.execute("UPDATE jobs SET output=%s,status='succeeded',stop_reason='proposal_complete',finished_at=now(),updated_at=now(),lease_until=NULL WHERE id=%s",
                     (Jsonb(output), job['id']))


def heartbeat(job):
    with connect() as conn:
        return bool(conn.execute("""UPDATE jobs SET lease_until=now()+make_interval(secs=>%s),updated_at=now()
            WHERE id=%s AND claim_token=%s AND status='running' AND lease_until>now() RETURNING id""",
            (settings().lease_seconds, job['id'], job['claim_token'])).fetchone())


def owned(conn, job):
    row = conn.execute("""SELECT * FROM jobs WHERE id=%s AND claim_token=%s AND status='running'
        AND lease_until>now() FOR UPDATE""", (job['id'], job['claim_token'])).fetchone()
    if not row:
        raise LeaseLost('Job was cancelled or its worker lease was lost')
    return row


def history(job_id):
    with connect() as conn:
        return conn.execute('SELECT * FROM iterations WHERE job_id=%s ORDER BY number,attempt', (job_id,)).fetchall()


def begin_iteration(job, number, source, source_hash, prompt, proposal, agent_code=None, source_diff=None):
    with connect() as conn:
        owned(conn, job)
        return conn.execute("""INSERT INTO iterations
            (id,job_id,number,attempt,status,agent_source,source_sha256,prompt,proposal,agent_code,source_diff)
            VALUES (%s,%s,%s,%s,'running',%s,%s,%s,%s,%s,%s) RETURNING *""",
            (uuid4(), job['id'], number, job['attempts'], source, source_hash, prompt,
             Jsonb(proposal) if proposal else None, agent_code, source_diff)).fetchone()


def record_validation(job, iteration, validation, prompt=None):
    with connect() as conn:
        owned(conn, job)
        conn.execute('UPDATE iterations SET validation=%s,prompt=COALESCE(%s,prompt) WHERE id=%s',
                     (Jsonb(validation), prompt, iteration['id']))


def reject_invalid_candidate(job, iteration, validation):
    """Keep invalid source/proposal for review, without replacing the best version."""
    with connect() as conn:
        owned(conn, job)
        conn.execute("""UPDATE iterations SET status='failed',accepted=false,validation=%s,
            error='{"code":"invalid_candidate","message":"Agent validation failed"}',finished_at=now()
            WHERE id=%s""", (Jsonb(validation), iteration['id']))
        conn.execute("""UPDATE jobs SET status='failed',stop_reason='invalid_candidate',
            error='{"code":"invalid_candidate","message":"Agent validation failed; inspect iteration history"}',
            finished_at=now(),updated_at=now(),lease_until=NULL WHERE id=%s""", (job['id'],))


def complete_iteration(job, iteration, results):
    with connect() as conn:
        current = owned(conn, job)
        score = results['score']
        accepted = current['best_score'] is None or score > current['best_score']
        # No previously passing task may regress even if the aggregate score rises.
        if current['best_iteration_id']:
            best = conn.execute('SELECT results FROM iterations WHERE id=%s', (current['best_iteration_id'],)).fetchone()['results']
            new = {r['task_id']: r for r in results['tasks']}
            accepted = accepted and all(new[r['task_id']]['status'] == 'passed' for r in best['tasks'] if r['status'] == 'passed')
        stop = None
        if not accepted:
            stop = 'no_improvement'
        elif score == 1.0:
            stop = 'all_tasks_passed'
        elif iteration['number'] >= current['request']['max_iterations']:
            stop = 'max_iterations'
        conn.execute("""UPDATE iterations SET status='completed',results=%s,score=%s,accepted=%s,finished_at=now()
            WHERE id=%s""", (Jsonb(results), score, accepted, iteration['id']))
        return conn.execute("""UPDATE jobs SET next_iteration=%s,
            best_iteration_id=CASE WHEN %s THEN %s ELSE best_iteration_id END,
            best_score=CASE WHEN %s THEN %s ELSE best_score END,
            status=CASE WHEN %s THEN 'succeeded' ELSE 'running' END,stop_reason=%s,
            finished_at=CASE WHEN %s THEN now() ELSE NULL END,updated_at=now()
            WHERE id=%s RETURNING *""", (iteration['number'] + 1, accepted, iteration['id'], accepted,
            score, stop is not None, stop, stop is not None, job['id'])).fetchone()


def fail(job, error, iteration=None, results=None):
    with connect() as conn:
        owned(conn, job)
        if iteration:
            conn.execute("UPDATE iterations SET status='failed',error=%s,results=%s,finished_at=now() WHERE id=%s",
                         (Jsonb(error), Jsonb(results) if results else None, iteration['id']))
        conn.execute("""UPDATE jobs SET status='failed',error=%s,stop_reason=%s,
            finished_at=now(),updated_at=now(),lease_until=NULL WHERE id=%s""",
            (Jsonb(error), error['code'], job['id']))
