CREATE TABLE users (
    id uuid PRIMARY KEY,
    name text NOT NULL,
    token_hash text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE organizations (
    id uuid PRIMARY KEY,
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE memberships (
    org_id uuid NOT NULL REFERENCES organizations(id),
    user_id uuid NOT NULL REFERENCES users(id),
    role text NOT NULL CHECK (role IN ('admin','member')),
    PRIMARY KEY (org_id, user_id)
);
CREATE TABLE jobs (
    id uuid PRIMARY KEY,
    org_id uuid NOT NULL REFERENCES organizations(id),
    user_id uuid NOT NULL REFERENCES users(id),
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
    request jsonb NOT NULL,
    idempotency_key text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    lease_until timestamptz,
    claim_token uuid,
    attempts integer NOT NULL DEFAULT 0,
    next_iteration integer NOT NULL DEFAULT 0,
    best_iteration_id uuid,
    best_score double precision,
    stop_reason text,
    error jsonb,
    UNIQUE (org_id, user_id, idempotency_key)
);
CREATE INDEX jobs_claim_idx ON jobs (status, lease_until, created_at);
CREATE INDEX jobs_visibility_idx ON jobs (org_id, user_id, created_at);
CREATE TABLE iterations (
    id uuid PRIMARY KEY,
    job_id uuid NOT NULL REFERENCES jobs(id),
    number integer NOT NULL,
    attempt integer NOT NULL,
    status text NOT NULL CHECK (status IN ('running','completed','interrupted','failed')),
    agent_source text NOT NULL,
    source_sha256 text NOT NULL,
    prompt text NOT NULL,
    proposal jsonb,
    results jsonb,
    score double precision,
    accepted boolean,
    error jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    UNIQUE (job_id, number, attempt)
);
ALTER TABLE jobs ADD CONSTRAINT jobs_best_iteration_fk FOREIGN KEY (best_iteration_id) REFERENCES iterations(id);
CREATE INDEX iterations_job_idx ON iterations (job_id, number, attempt);
