CREATE TABLE run_budgets (
    budget_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE REFERENCES research_runs(run_id),
    limits TEXT NOT NULL CHECK(json_valid(limits)),
    started_at TEXT NOT NULL,
    stopped_reason TEXT
);
CREATE TABLE budget_reservations (
    reservation_id TEXT PRIMARY KEY,
    budget_id TEXT NOT NULL REFERENCES run_budgets(budget_id),
    kind TEXT NOT NULL,
    estimated_tokens INTEGER NOT NULL CHECK(estimated_tokens >= 0),
    actual_tokens INTEGER CHECK(actual_tokens >= 0),
    state TEXT NOT NULL CHECK(state IN ('reserved','known','unknown')),
    detail TEXT NOT NULL CHECK(json_valid(detail))
);
CREATE INDEX idx_budget_reservations ON budget_reservations(budget_id);
