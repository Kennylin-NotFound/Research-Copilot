CREATE TABLE project_trash (
    project_id TEXT PRIMARY KEY REFERENCES projects(project_id),
    deleted_at TEXT NOT NULL
);
