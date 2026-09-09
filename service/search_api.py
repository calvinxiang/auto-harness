from typing import Annotated
from uuid import UUID

from fastapi import Header, Query

from . import searches
from .db import connect


def register(app, User, membership):
    @app.post('/organizations/{org_id}/optimization-runs', status_code=202)
    def create(org_id: UUID, body: searches.RunCreate, user: User,
               idempotency_key: Annotated[str | None, Header(min_length=1, max_length=128)] = None):
        with connect() as conn:
            return searches.submit(conn, org_id, user, membership(conn, org_id, user), body, idempotency_key)

    @app.get('/organizations/{org_id}/optimization-runs')
    def listing(org_id: UUID, user: User, limit: int = Query(50, ge=1, le=100)):
        with connect() as conn:
            role = membership(conn, org_id, user)
            return conn.execute('SELECT * FROM optimization_runs WHERE org_id=%s AND (%s OR user_id=%s) ORDER BY created_at DESC LIMIT %s',
                                (org_id, role == 'admin', user['id'], limit)).fetchall()

    @app.get('/organizations/{org_id}/optimization-runs/{run_id}')
    def get(org_id: UUID, run_id: UUID, user: User):
        with connect() as conn:
            run = searches.visible(conn, org_id, run_id, user, membership(conn, org_id, user))
            return searches.report(conn, run)

    @app.post('/organizations/{org_id}/optimization-runs/{run_id}/cancel')
    def cancel(org_id: UUID, run_id: UUID, user: User):
        with connect() as conn:
            searches.visible(conn, org_id, run_id, user, membership(conn, org_id, user))
            run = conn.execute('SELECT * FROM optimization_runs WHERE id=%s FOR UPDATE', (run_id,)).fetchone()
            return searches.cancel(conn, run)
