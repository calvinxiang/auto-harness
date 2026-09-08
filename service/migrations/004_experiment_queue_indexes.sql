CREATE UNIQUE INDEX execution_reservations_job ON execution_reservations(job_id);
CREATE INDEX jobs_pending_development ON jobs((request->>'experiment_id'))
    WHERE status IN ('queued','running') AND request->>'split'='development';
CREATE INDEX experiments_active ON experiments(id) WHERE status='running';
