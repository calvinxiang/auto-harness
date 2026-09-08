import hashlib
import secrets
from typing import Annotated
from uuid import UUID, uuid4

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg.types.json import Jsonb

from .config import settings, execution_config
from .db import connect
from .schemas import (IterationOut, JobCreate, JobOut, MemberCreate,
                      MembershipUpdate, OrganizationCreate)
from .tasks import TASKS

app = FastAPI(title='Agent Optimization Service', version='1.0.0')
bearer = HTTPBearer(auto_error=False)


@app.exception_handler(psycopg.OperationalError)
async def database_unavailable(request, exc):
    return JSONResponse(status_code=503, content={'detail': 'Database temporarily unavailable'})


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def current_user(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    if credentials is None:
        raise HTTPException(401, 'Bearer token required', headers={'WWW-Authenticate': 'Bearer'})
    with connect() as conn:
        user = conn.execute('SELECT id,name FROM users WHERE token_hash=%s', (digest(credentials.credentials),)).fetchone()
    if not user:
        raise HTTPException(401, 'Invalid token', headers={'WWW-Authenticate': 'Bearer'})
    return user


User = Annotated[dict, Depends(current_user)]


def membership(conn, org_id, user, admin=False, lock=False):
    if lock:
        # Serialize membership mutations so concurrent demotions cannot remove all admins.
        conn.execute('SELECT id FROM organizations WHERE id=%s FOR UPDATE', (org_id,))
    row = conn.execute('SELECT role FROM memberships WHERE org_id=%s AND user_id=%s', (org_id, user['id'])).fetchone()
    if not row:
        raise HTTPException(404, 'Organization not found')
    if admin and row['role'] != 'admin':
        raise HTTPException(403, 'Administrator role required')
    return row['role']


def visible_job(conn, org_id, job_id, user, lock=False):
    role = membership(conn, org_id, user)
    suffix = ' FOR UPDATE' if lock else ''
    row = conn.execute('SELECT * FROM jobs WHERE id=%s AND org_id=%s' + suffix, (job_id, org_id)).fetchone()
    if not row or (role != 'admin' and row['user_id'] != user['id']):
        raise HTTPException(404, 'Job not found')
    return row


def create_user(conn, name):
    token = secrets.token_urlsafe(32)
    user_id = uuid4()
    conn.execute('INSERT INTO users(id,name,token_hash) VALUES (%s,%s,%s)', (user_id, name, digest(token)))
    return user_id, token


@app.get('/health')
def health():
    with connect() as conn:
        conn.execute('SELECT 1 FROM schema_migrations LIMIT 1')
    return {'status': 'ok'}


@app.get('/tasks')
def tasks(user: User):
    return {'dataset': 'terminal-bench@2.0', 'tasks': [{'id': k, 'reason': v} for k, v in TASKS.items()]}


@app.post('/organizations', status_code=201)
def create_organization(body: OrganizationCreate, x_bootstrap_token: Annotated[str | None, Header()] = None):
    configured = settings().bootstrap_token
    if not configured or not x_bootstrap_token or not secrets.compare_digest(configured, x_bootstrap_token):
        raise HTTPException(401, 'Valid bootstrap token required')
    org_id = uuid4()
    with connect() as conn:
        user_id, token = create_user(conn, body.owner_name)
        conn.execute('INSERT INTO organizations(id,name) VALUES (%s,%s)', (org_id, body.name))
        conn.execute("INSERT INTO memberships VALUES (%s,%s,'admin')", (org_id, user_id))
    return {'id': org_id, 'name': body.name, 'owner_id': user_id, 'api_token': token}


@app.get('/organizations')
def organizations(user: User):
    with connect() as conn:
        return conn.execute('SELECT o.id,o.name,m.role FROM organizations o JOIN memberships m ON m.org_id=o.id WHERE m.user_id=%s ORDER BY o.created_at', (user['id'],)).fetchall()


@app.get('/organizations/{org_id}/members')
def members(org_id: UUID, user: User):
    with connect() as conn:
        membership(conn, org_id, user, admin=True)
        return conn.execute('SELECT u.id,u.name,m.role FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.org_id=%s ORDER BY u.created_at', (org_id,)).fetchall()


@app.post('/organizations/{org_id}/members', status_code=201)
def add_member(org_id: UUID, body: MemberCreate, user: User):
    with connect() as conn:
        membership(conn, org_id, user, admin=True, lock=True)
        user_id, token = create_user(conn, body.name)
        conn.execute('INSERT INTO memberships VALUES (%s,%s,%s)', (org_id, user_id, body.role))
    return {'id': user_id, 'name': body.name, 'role': body.role, 'api_token': token}


@app.put('/organizations/{org_id}/members/{user_id}')
def set_membership(org_id: UUID, user_id: UUID, body: MembershipUpdate, user: User):
    with connect() as conn:
        membership(conn, org_id, user, admin=True, lock=True)
        if not conn.execute('SELECT id FROM users WHERE id=%s', (user_id,)).fetchone():
            raise HTTPException(404, 'User not found')
        protect_last_admin(conn, org_id, user_id, body.role)
        conn.execute('INSERT INTO memberships VALUES (%s,%s,%s) ON CONFLICT (org_id,user_id) DO UPDATE SET role=EXCLUDED.role', (org_id, user_id, body.role))
    return {'id': user_id, 'role': body.role}


def protect_last_admin(conn, org_id, user_id, new_role):
    admins = conn.execute("SELECT user_id FROM memberships WHERE org_id=%s AND role='admin'", (org_id,)).fetchall()
    if new_role != 'admin' and len(admins) == 1 and admins[0]['user_id'] == user_id:
        raise HTTPException(409, 'An organization must retain at least one administrator')


@app.delete('/organizations/{org_id}/members/{user_id}', status_code=204)
def remove_member(org_id: UUID, user_id: UUID, user: User):
    with connect() as conn:
        membership(conn, org_id, user, admin=True, lock=True)
        protect_last_admin(conn, org_id, user_id, None)
        if not conn.execute('DELETE FROM memberships WHERE org_id=%s AND user_id=%s RETURNING user_id', (org_id, user_id)).fetchone():
            raise HTTPException(404, 'Member not found')


@app.post('/organizations/{org_id}/jobs', status_code=202, response_model=JobOut)
def submit(org_id: UUID, body: JobCreate, user: User,
           idempotency_key: Annotated[str | None, Header(max_length=128, min_length=1)] = None):
    request = body.model_dump()
    with connect() as conn:
        membership(conn, org_id, user)
        if idempotency_key:
            # Transaction-scoped lock serializes concurrent retries of this exact request key.
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (f'{org_id}:{user["id"]}:{idempotency_key}',))
            existing = conn.execute('SELECT * FROM jobs WHERE org_id=%s AND user_id=%s AND idempotency_key=%s', (org_id, user['id'], idempotency_key)).fetchone()
            if existing:
                if {k: existing['request'][k] for k in request} != request:
                    raise HTTPException(409, 'Idempotency key was used with a different request')
                return existing
        # Bound queued/in-flight jobs per organization; serialize admissions.
        conn.execute('SELECT id FROM organizations WHERE id=%s FOR UPDATE', (org_id,))
        count = conn.execute("SELECT count(*) AS n FROM jobs WHERE org_id=%s AND status IN ('queued','running') AND request->>'kind' IS DISTINCT FROM 'trial'", (org_id,)).fetchone()['n']
        if count >= 10:
            raise HTTPException(429, 'Organization has 10 active jobs; retry after one finishes')
        request['execution'] = execution_config()
        return conn.execute('INSERT INTO jobs(id,org_id,user_id,request,idempotency_key) VALUES (%s,%s,%s,%s,%s) RETURNING *', (uuid4(), org_id, user['id'], Jsonb(request), idempotency_key)).fetchone()


@app.get('/organizations/{org_id}/jobs', response_model=list[JobOut])
def jobs(org_id: UUID, user: User, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    with connect() as conn:
        role = membership(conn, org_id, user)
        return conn.execute('SELECT * FROM jobs WHERE org_id=%s AND (%s OR user_id=%s) ORDER BY created_at DESC,id LIMIT %s OFFSET %s', (org_id, role == 'admin', user['id'], limit, offset)).fetchall()


@app.get('/organizations/{org_id}/jobs/{job_id}', response_model=JobOut)
def job(org_id: UUID, job_id: UUID, user: User):
    with connect() as conn:
        return visible_job(conn, org_id, job_id, user)


@app.get('/organizations/{org_id}/jobs/{job_id}/iterations', response_model=list[IterationOut])
def iterations(org_id: UUID, job_id: UUID, user: User):
    with connect() as conn:
        visible_job(conn, org_id, job_id, user)
        return conn.execute('SELECT * FROM iterations WHERE job_id=%s ORDER BY number,attempt', (job_id,)).fetchall()


@app.post('/organizations/{org_id}/jobs/{job_id}/cancel', response_model=JobOut)
def cancel(org_id: UUID, job_id: UUID, user: User):
    with connect() as conn:
        row = visible_job(conn, org_id, job_id, user, lock=True)
        if row['status'] in ('succeeded', 'failed', 'cancelled'):
            return row
        conn.execute("UPDATE iterations SET status='interrupted',finished_at=now() WHERE job_id=%s AND status='running'", (job_id,))
        return conn.execute("UPDATE jobs SET status='cancelled',stop_reason='cancelled_by_user',finished_at=now(),updated_at=now(),lease_until=NULL WHERE id=%s RETURNING *", (job_id,)).fetchone()


from .campaign_api import register as register_campaigns
register_campaigns(app, User, membership, visible_job)
