CREATE TABLE projects (
    project_id TEXT PRIMARY KEY
);
CREATE TABLE research_briefs (
    brief_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id),
    project_revision INTEGER NOT NULL CHECK(project_revision >= 1),
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    UNIQUE(project_id, project_revision)
);
CREATE TABLE sources (
    source_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id),
    kind TEXT NOT NULL CHECK(kind IN ('web','pdf','text')),
    origin TEXT NOT NULL,
    UNIQUE(project_id, kind, origin),
    UNIQUE(source_id, project_id)
);
CREATE TABLE source_versions (
    source_version_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL CHECK(length(snapshot_sha256) = 64),
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    FOREIGN KEY(source_id, project_id) REFERENCES sources(source_id, project_id),
    UNIQUE(source_id, snapshot_sha256),
    UNIQUE(source_version_id, project_id)
);
CREATE TABLE evidence (
    evidence_id TEXT PRIMARY KEY,
    source_version_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    FOREIGN KEY(source_version_id, project_id) REFERENCES source_versions(source_version_id, project_id)
);
CREATE INDEX idx_briefs_project ON research_briefs(project_id, project_revision);
CREATE INDEX idx_versions_project ON source_versions(project_id, source_id);
CREATE INDEX idx_evidence_version ON evidence(project_id, source_version_id);
