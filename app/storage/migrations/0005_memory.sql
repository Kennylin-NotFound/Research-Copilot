CREATE TABLE research_memories (
    memory_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id),
    kind TEXT NOT NULL CHECK(kind IN ('scope','reading','finding','question')),
    origin_run_id TEXT REFERENCES research_runs(run_id),
    state TEXT NOT NULL CHECK(state IN ('active','conflict','stale','deleted','superseded')),
    revision INTEGER NOT NULL CHECK(revision>=1),
    payload TEXT NOT NULL CHECK(json_valid(payload))
);
CREATE INDEX idx_memories_project ON research_memories(project_id,kind,state);
CREATE TABLE memory_revisions (
    memory_id TEXT NOT NULL REFERENCES research_memories(memory_id),
    revision INTEGER NOT NULL,
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    PRIMARY KEY(memory_id,revision)
);
