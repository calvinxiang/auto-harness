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
        exhausted = conn.execute("""UPDATE jobs SET status='failed',stop_reason='worker_attempts_exhausted',
            error='{"code":"worker_attempts_exhausted","message":"Worker lease expired repeatedly"}',
            finished_at=now(),updated_at=now(),lease_until=NULL
            WHERE status='running' AND lease_until<now() AND attempts>=%s RETURNING id""", (cfg.max_attempts,)).fetchall()
        for row in exhausted:
            conn.execute("UPDATE iterations SET status='interrupted',finished_at=now() WHERE job_id=%s AND status='running'", (row['id'],))
        row = conn.execute("""SELECT * FROM jobs WHERE (status='queued' OR
            (status='running' AND lease_until<now())) AND attempts<%s
            ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1""", (cfg.max_attempts,)).fetchone()
        if not row:
            return None
        conn.execute("UPDATE iterations SET status='interrupted',finished_at=now() WHERE job_id=%s AND status='running'", (row['id'],))
        return conn.execute("""UPDATE jobs SET status='running',attempts=attempts+1,claim_token=%s,
            lease_until=now()+make_interval(secs=>%s),started_at=COALESCE(started_at,now()),updated_at=now()
            WHERE id=%s RETURNING *""", (uuid4(), cfg.lease_seconds, row['id'])).fetchone()


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


def begin_iteration(job, number, source, source_hash, prompt, proposal):
    with connect() as conn:
        owned(conn, job)
        return conn.execute("""INSERT INTO iterations
            (id,job_id,number,attempt,status,agent_source,source_sha256,prompt,proposal)
            VALUES (%s,%s,%s,%s,'running',%s,%s,%s,%s) RETURNING *""",
            (uuid4(), job['id'], number, job['attempts'], source, source_hash, prompt,
             Jsonb(proposal) if proposal else None)).fetchone()


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
