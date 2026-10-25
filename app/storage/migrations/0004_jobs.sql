CREATE TABLE research_jobs (
    job_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id),
    run_id TEXT NOT NULL UNIQUE REFERENCES research_runs(run_id),
    request_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('queued','leased','done','cancelled','blocked')),
    attempt INTEGER NOT NULL DEFAULT 0,
    lease_token TEXT,
    lease_until REAL,
    owner TEXT,
    UNIQUE(project_id,request_id)
);
CREATE UNIQUE INDEX idx_jobs_active_project ON research_jobs(project_id) WHERE state IN ('queued','leased');
CREATE TABLE research_attempts (
    attempt_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES research_jobs(job_id),
    number INTEGER NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    outcome TEXT,
    UNIQUE(job_id,number)
);
CREATE TABLE research_events (
    run_id TEXT NOT NULL REFERENCES research_runs(run_id),
    seq INTEGER NOT NULL,
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    PRIMARY KEY(run_id,seq)
);
INSERT INTO research_events SELECT r.run_id,json_extract(e.value,'$.seq'),e.value
FROM research_runs r,json_each(r.payload,'$.events') e;
