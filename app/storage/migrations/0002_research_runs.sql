CREATE TABLE research_runs (
    run_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id),
    brief_id TEXT NOT NULL REFERENCES research_briefs(brief_id),
    payload TEXT NOT NULL CHECK(json_valid(payload))
);
CREATE INDEX idx_research_runs_project ON research_runs(project_id);
