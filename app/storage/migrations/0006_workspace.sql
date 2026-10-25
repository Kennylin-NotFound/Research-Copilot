CREATE TABLE workspace_projects (
    project_id TEXT PRIMARY KEY REFERENCES projects(project_id),
    title TEXT NOT NULL,
    preferences TEXT NOT NULL CHECK(json_valid(preferences)),
    revision INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
CREATE TABLE research_conversations (
    conversation_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id),
    title TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('main','independent','legacy')),
    archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(conversation_id,project_id)
);
CREATE UNIQUE INDEX idx_conversation_main ON research_conversations(project_id) WHERE kind='main';
CREATE TABLE research_messages (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user','assistant')),
    kind TEXT NOT NULL CHECK(kind IN ('research','legacy')),
    content TEXT NOT NULL,
    run_id TEXT REFERENCES research_runs(run_id),
    request_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(conversation_id,project_id) REFERENCES research_conversations(conversation_id,project_id),
    UNIQUE(conversation_id,request_id,role),
    UNIQUE(run_id,role)
);
CREATE INDEX idx_messages_conversation ON research_messages(project_id,conversation_id,seq);
