CREATE TABLE optimization_runs (
    id uuid PRIMARY KEY,
    org_id uuid NOT NULL REFERENCES organizations(id),
    user_id uuid NOT NULL REFERENCES users(id),
    name text NOT NULL,
    request jsonb NOT NULL,
    execution jsonb NOT NULL,
    idempotency_key text,
    status text NOT NULL DEFAULT 'running' CHECK(status IN ('running','succeeded','failed','cancelled')),
    phase text NOT NULL DEFAULT 'baseline' CHECK(phase IN ('baseline','proposing','comparing','heldout','complete')),
    initial_version_id uuid NOT NULL REFERENCES harness_versions(id),
    best_version_id uuid NOT NULL REFERENCES harness_versions(id),
    best_score double precision,
    baseline_experiment_id uuid REFERENCES experiments(id),
    heldout_experiment_id uuid REFERENCES experiments(id),
    evidence_experiment_id uuid REFERENCES experiments(id),
    round_number integer NOT NULL DEFAULT 0,
    plateau_rounds integer NOT NULL DEFAULT 0,
    stop_reason text,
    error jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    UNIQUE(org_id,user_id,idempotency_key)
);
CREATE INDEX optimization_runs_visibility ON optimization_runs(org_id,user_id,created_at);
CREATE INDEX optimization_runs_active ON optimization_runs(updated_at,id) WHERE status='running';

CREATE TABLE optimization_rounds (
    run_id uuid NOT NULL REFERENCES optimization_runs(id),
    number integer NOT NULL,
    parent_version_id uuid NOT NULL REFERENCES harness_versions(id),
    proposal_jobs jsonb NOT NULL,
    experiment_id uuid REFERENCES experiments(id),
    status text NOT NULL DEFAULT 'proposing' CHECK(status IN ('proposing','comparing','completed','cancelled')),
    decision jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    PRIMARY KEY(run_id,number)
);
