ALTER TABLE jobs ADD COLUMN output jsonb;

CREATE TABLE harness_versions (
    id uuid PRIMARY KEY,
    org_id uuid NOT NULL REFERENCES organizations(id),
    user_id uuid NOT NULL REFERENCES users(id),
    parent_id uuid REFERENCES harness_versions(id),
    label text NOT NULL,
    dimension text NOT NULL,
    package jsonb NOT NULL,
    package_sha256 text NOT NULL,
    runtime_sha256 text NOT NULL,
    file_hashes jsonb NOT NULL,
    agent_source text NOT NULL,
    source_sha256 text NOT NULL,
    changes jsonb NOT NULL,
    proposal jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX harness_versions_visibility ON harness_versions(org_id,user_id,created_at);

CREATE TABLE experiments (
    id uuid PRIMARY KEY,
    org_id uuid NOT NULL REFERENCES organizations(id),
    user_id uuid NOT NULL REFERENCES users(id),
    name text NOT NULL,
    request jsonb NOT NULL,
    idempotency_key text,
    status text NOT NULL DEFAULT 'running' CHECK(status IN ('running','succeeded','failed','cancelled')),
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    UNIQUE(org_id,user_id,idempotency_key)
);
CREATE INDEX experiments_visibility ON experiments(org_id,user_id,created_at);

CREATE TABLE experiment_trials (
    experiment_id uuid NOT NULL REFERENCES experiments(id),
    version_id uuid NOT NULL REFERENCES harness_versions(id),
    split text NOT NULL CHECK(split IN ('development','heldout')),
    repetition integer NOT NULL,
    task_id text NOT NULL,
    job_id uuid NOT NULL UNIQUE REFERENCES jobs(id),
    PRIMARY KEY(experiment_id,version_id,split,repetition,task_id)
);

-- One pool for this installation's sandbox engine. Change capacity with the
-- operator command, not by giving individual workers conflicting limits.
CREATE TABLE execution_pool (
    id integer PRIMARY KEY CHECK(id=1),
    capacity integer NOT NULL CHECK(capacity BETWEEN 1 AND 32)
);
INSERT INTO execution_pool VALUES(1,2);
CREATE TABLE execution_reservations (
    claim_token uuid PRIMARY KEY,
    job_id uuid NOT NULL REFERENCES jobs(id),
    slots integer NOT NULL CHECK(slots>0),
    created_at timestamptz NOT NULL DEFAULT now()
);
