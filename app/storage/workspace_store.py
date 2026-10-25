"""Project preferences and durable conversations; research Runs remain authoritative."""
import json
from uuid import UUID, uuid4, uuid5

from app.domain.project import ProjectInput
from app.domain.research import ResearchBrief, RunBudget, utc_now

SCOPE_KEYS = ('question', 'template', 'dimensions', 'as_of', 'region', 'output_requirements')


def budget_values(value):
    return RunBudget.model_validate(value).model_dump(mode='json', exclude={'budget_id'})


class WorkspaceStore:
    def __init__(self, store):
        self.store = store

    def ensure(self, project_id, *, new=False, title=None, memory_enabled=None):
        brief = self.store.get_brief(project_id)
        now = utc_now().isoformat()
        pid = str(brief.project_id)
        with self.store.transaction() as db:
            self.store.assert_active(db, project_id)
            preferences = {'budget': budget_values(brief.budget), 'allow_network': brief.source_policy.allow_network, 'memory_enabled': bool(new) if memory_enabled is None else memory_enabled}
            db.execute('INSERT OR IGNORE INTO workspace_projects VALUES(?,?,?,1,?)', (pid, title or brief.question[:36], json.dumps(preferences), now))
            main_id = str(uuid5(brief.project_id, 'workspace:main'))
            db.execute('INSERT OR IGNORE INTO research_conversations VALUES(?,?,?,?,0,?,?)', (main_id, pid, '主研究对话', 'main', now, now))
            # Historical Runs have no durable human message. Preserve them as
            # explicit legacy records instead of inventing user utterances.
            rows = db.execute('SELECT r.payload FROM research_runs r WHERE r.project_id=? AND NOT EXISTS(SELECT 1 FROM research_messages m WHERE m.run_id=r.run_id)', (pid,)).fetchall()
            if rows:
                legacy_id = str(uuid5(brief.project_id, 'workspace:legacy'))
                db.execute('INSERT OR IGNORE INTO research_conversations VALUES(?,?,?,?,0,?,?)', (legacy_id, pid, '历史研究记录', 'legacy', now, now))
                for row in rows:
                    run = json.loads(row[0])
                    question = run.get('scope', {}).get('question')
                    if not question:
                        snapshot = db.execute('SELECT payload FROM research_briefs WHERE brief_id=?', (run['brief_id'],)).fetchone()
                        question = json.loads(snapshot[0])['question']
                    db.execute('INSERT OR IGNORE INTO research_messages(message_id,project_id,conversation_id,role,kind,content,run_id,created_at) VALUES(?,?,?,?,?,?,?,?)',
                               (str(uuid5(UUID(run['run_id']), 'legacy-message')), pid, legacy_id, 'assistant', 'legacy', question, run['run_id'], run['created_at']))
        return self.project(pid)

    def project(self, project_id):
        brief = self.store.get_brief(project_id)
        with self.store.reader() as db:
            profile = db.execute('SELECT * FROM workspace_projects WHERE project_id=?', (str(project_id),)).fetchone()
            active = db.execute("SELECT run_id FROM research_jobs WHERE project_id=? AND state IN ('queued','leased')", (str(project_id),)).fetchone()
        if not profile: return self.ensure(project_id)
        return {'project_id': str(brief.project_id), 'title': profile['title'], 'brief': brief.model_dump(mode='json'),
                'preferences': json.loads(profile['preferences']), 'settings_revision': profile['revision'],
                'updated_at': profile['updated_at'], 'active_run_id': active[0] if active else None}

    def projects(self):
        return [self.ensure(brief.project_id) for brief in self.store.list_projects()]

    def trash(self):
        with self.store.reader() as db:
            return [dict(row) for row in db.execute('SELECT t.project_id,t.deleted_at,COALESCE(w.title,json_extract(b.payload,\'$.question\')) AS title FROM project_trash t LEFT JOIN workspace_projects w ON w.project_id=t.project_id JOIN research_briefs b ON b.project_id=t.project_id AND b.project_revision=(SELECT MAX(project_revision) FROM research_briefs WHERE project_id=t.project_id) ORDER BY t.deleted_at DESC')]

    def delete_project(self, project_id):
        pid = str(project_id)
        with self.store.transaction() as db:
            if not db.execute('SELECT 1 FROM projects WHERE project_id=?', (pid,)).fetchone():
                raise LookupError('项目不存在')
            if db.execute("SELECT 1 FROM research_jobs WHERE project_id=? AND state IN ('queued','leased')", (pid,)).fetchone():
                raise ValueError('请先停止正在进行的研究，再删除项目')
            db.execute('INSERT OR IGNORE INTO project_trash VALUES(?,?)', (pid, utc_now().isoformat()))
            deleted_at = db.execute('SELECT deleted_at FROM project_trash WHERE project_id=?', (pid,)).fetchone()[0]
        return {'project_id': pid, 'deleted_at': deleted_at, 'deleted': True}

    def restore_project(self, project_id):
        with self.store.transaction() as db:
            if not db.execute('SELECT 1 FROM projects WHERE project_id=?', (str(project_id),)).fetchone():
                raise LookupError('项目不存在')
            db.execute('DELETE FROM project_trash WHERE project_id=?', (str(project_id),))
        return self.ensure(project_id)

    def save_settings(self, project_id, inputs):
        self.ensure(project_id)
        now = utc_now().isoformat()
        with self.store.transaction() as db:
            self.store.assert_active(db, project_id)
            row = db.execute('SELECT payload FROM research_briefs WHERE project_id=? ORDER BY project_revision DESC LIMIT 1', (str(project_id),)).fetchone()
            old = ResearchBrief.model_validate_json(row[0])
            profile = db.execute('SELECT * FROM workspace_projects WHERE project_id=?', (str(project_id),)).fetchone()
            if old.project_revision != inputs.expected_revision or profile['revision'] != inputs.expected_settings_revision:
                raise ValueError('项目设置已更新，请重新打开设置')
            candidate = ProjectInput(**{key: getattr(inputs, key) for key in SCOPE_KEYS},
                                     budget=old.budget, allow_network=old.source_policy.allow_network).brief(old.project_id, old.project_revision + 1)
            before, after = old.model_dump(mode='json'), candidate.model_dump(mode='json')
            if any(before[key] != after[key] for key in SCOPE_KEYS):
                if db.execute("SELECT 1 FROM research_jobs WHERE project_id=? AND state IN ('queued','leased')", (str(project_id),)).fetchone():
                    raise ValueError('研究进行中，请完成或停止后再修改研究范围')
                db.execute('INSERT INTO research_briefs VALUES(?,?,?,?)', (str(candidate.brief_id), str(project_id), candidate.project_revision, candidate.model_dump_json()))
            preferences = {'budget': budget_values(inputs.budget), 'allow_network': inputs.allow_network, 'memory_enabled': inputs.memory_enabled}
            encoded = json.dumps(preferences, sort_keys=True)
            if profile['title'] != inputs.title.strip() or json.loads(profile['preferences']) != preferences:
                db.execute('UPDATE workspace_projects SET title=?,preferences=?,revision=revision+1,updated_at=? WHERE project_id=?', (inputs.title.strip(), encoded, now, str(project_id)))
            elif any(before[key] != after[key] for key in SCOPE_KEYS):
                db.execute('UPDATE workspace_projects SET revision=revision+1,updated_at=? WHERE project_id=?', (now, str(project_id)))
        return self.project(project_id)

    def conversation(self, project_id, conversation_id):
        with self.store.reader() as db:
            self.store.assert_active(db, project_id)
            row = db.execute('SELECT * FROM research_conversations WHERE project_id=? AND conversation_id=?', (str(project_id), str(conversation_id))).fetchone()
        if not row: raise LookupError('对话不存在')
        return dict(row)

    def conversations(self, project_id):
        self.ensure(project_id)
        with self.store.reader() as db:
            return [dict(row) for row in db.execute('SELECT * FROM research_conversations WHERE project_id=? ORDER BY archived,CASE kind WHEN \'main\' THEN 0 WHEN \'independent\' THEN 1 ELSE 2 END,updated_at DESC', (str(project_id),))]

    def create_conversation(self, project_id, title):
        self.ensure(project_id)
        now, cid = utc_now().isoformat(), str(uuid4())
        with self.store.transaction() as db:
            self.store.assert_active(db, project_id)
            db.execute('INSERT INTO research_conversations VALUES(?,?,?,?,0,?,?)', (cid, str(project_id), title.strip(), 'independent', now, now))
        return self.conversation(project_id, cid)

    def edit_conversation(self, project_id, conversation_id, *, title=None, archived=None):
        value = self.conversation(project_id, conversation_id)
        if archived and value['kind'] == 'main': raise ValueError('主研究对话保留为项目默认入口')
        with self.store.transaction() as db:
            self.store.assert_active(db, project_id)
            if archived and db.execute("SELECT 1 FROM research_messages m JOIN research_jobs j ON j.run_id=m.run_id WHERE m.conversation_id=? AND j.state IN ('queued','leased')", (str(conversation_id),)).fetchone():
                raise ValueError('研究进行中，暂不能归档对话')
            db.execute('UPDATE research_conversations SET title=?,archived=?,updated_at=? WHERE conversation_id=? AND project_id=?', (title.strip() if title else value['title'], int(archived) if archived is not None else value['archived'], utc_now().isoformat(), str(conversation_id), str(project_id)))
        return self.conversation(project_id, conversation_id)

    def messages(self, project_id, conversation_id):
        self.conversation(project_id, conversation_id)
        with self.store.reader() as db:
            return [dict(row) for row in db.execute('SELECT * FROM research_messages WHERE project_id=? AND conversation_id=? ORDER BY seq', (str(project_id), str(conversation_id)))]

    @staticmethod
    def attach_messages(db, run, conversation_id, content, request_id):
        value = db.execute('SELECT * FROM research_conversations WHERE project_id=? AND conversation_id=?', (run['project_id'], str(conversation_id))).fetchone()
        if not value: raise LookupError('对话不存在')
        if value['archived']: raise ValueError('请先恢复已归档对话')
        now = utc_now().isoformat()
        for role, text in [('user', content), ('assistant', '')]:
            db.execute('INSERT INTO research_messages(message_id,project_id,conversation_id,role,kind,content,run_id,request_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                       (str(uuid5(UUID(run['run_id']), role)), run['project_id'], str(conversation_id), role, 'research', text, run['run_id'], str(request_id), now))
        db.execute('UPDATE research_conversations SET updated_at=? WHERE conversation_id=?', (now, str(conversation_id)))
        db.execute('UPDATE workspace_projects SET updated_at=? WHERE project_id=?', (now, run['project_id']))
