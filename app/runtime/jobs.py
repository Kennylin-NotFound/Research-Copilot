"""SQLite queue, idempotency and fencing. No provider requests occur here."""
import hashlib
import json
import time
from uuid import uuid4


class LeaseLost(RuntimeError):
    pass


def assert_lease(db, run_id, lease):
    row = db.execute('SELECT * FROM research_jobs WHERE run_id=?', (run_id,)).fetchone()
    if row and (not lease or row['job_id'] != lease[0] or row['lease_token'] != lease[1]
                or row['state'] != 'leased' or row['lease_until'] <= time.time()):
        raise LeaseLost('Task lease expired, was cancelled or belongs to another worker')


def request_hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


class JobQueue:
    def __init__(self, store):
        self.store = store

    def existing(self, project_id, request_id, fingerprint):
        with self.store.reader() as db:
            row = db.execute('SELECT * FROM research_jobs WHERE project_id=? AND request_id=?', (str(project_id), str(request_id))).fetchone()
        if row and row['request_hash'] != fingerprint:
            raise ValueError('Request identity already used with different research inputs')
        return self.store.get_run(project_id, row['run_id']) if row else None

    def submit(self, run, request_id, fingerprint, *, message=None):
        # Run and Job either both commit, or neither does. The partial unique
        # index serializes simultaneous submissions for one project.
        with self.store.transaction() as db:
            self.store.assert_active(db, run['project_id'])
            row = db.execute('SELECT * FROM research_jobs WHERE project_id=? AND request_id=?', (run['project_id'], str(request_id))).fetchone()
            if row:
                if row['request_hash'] != fingerprint:
                    raise ValueError('Request identity already used with different research inputs')
                return json.loads(db.execute('SELECT payload FROM research_runs WHERE run_id=?', (row['run_id'],)).fetchone()[0])
            latest = db.execute('SELECT brief_id FROM research_briefs WHERE project_id=? ORDER BY project_revision DESC LIMIT 1', (run['project_id'],)).fetchone()
            if not latest or latest['brief_id'] != run['brief_id']:
                raise ValueError('Project revision changed before submission')
            if db.execute("SELECT 1 FROM research_jobs WHERE project_id=? AND state IN ('queued','leased')", (run['project_id'],)).fetchone():
                raise ValueError('This project already has an active research task')
            run['job_id'] = str(uuid4())
            run['request_id'] = str(request_id)
            run['source_heads'] = {}
            for sid in run['source_version_ids']:
                source = db.execute('SELECT source_id FROM source_versions WHERE source_version_id=? AND project_id=?', (sid, run['project_id'])).fetchone()
                if not source:
                    raise ValueError('Source is outside this project')
                head = db.execute('SELECT source_version_id FROM source_versions WHERE source_id=? ORDER BY rowid DESC LIMIT 1', (source['source_id'],)).fetchone()[0]
                run['source_heads'][source['source_id']] = head
            db.execute('INSERT INTO research_runs VALUES(?,?,?,?)', (run['run_id'], run['project_id'], run['brief_id'], json.dumps(run, ensure_ascii=False)))
            db.execute('INSERT INTO research_jobs(job_id,project_id,run_id,request_id,request_hash,state) VALUES(?,?,?,?,?,?)',
                       (run['job_id'], run['project_id'], run['run_id'], str(request_id), fingerprint, 'queued'))
            if message:
                from app.storage.workspace_store import WorkspaceStore
                WorkspaceStore.attach_messages(db, run, message['conversation_id'], message['content'], request_id)
        return run

    def claim(self, owner, ttl=30):
        now = time.time()
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM research_jobs WHERE state='queued' OR (state='leased' AND lease_until<=?) ORDER BY rowid LIMIT 1", (now,)).fetchone()
            if not row:
                return None
            # Never revive a completed/cancelled Run after a crash between
            # report commit and Job completion.
            run = json.loads(db.execute('SELECT payload FROM research_runs WHERE run_id=?', (row['run_id'],)).fetchone()[0])
            if run['status'] not in ('queued', 'running'):
                db.execute("UPDATE research_jobs SET state='done',lease_token=NULL,lease_until=NULL WHERE job_id=?", (row['job_id'],))
                db.execute("UPDATE research_attempts SET finished_at=?,outcome='terminal_reconciled' WHERE job_id=? AND finished_at IS NULL", (now, row['job_id']))
                return None
            db.execute("UPDATE research_attempts SET finished_at=?,outcome='lease_expired' WHERE job_id=? AND finished_at IS NULL", (now, row['job_id']))
            token, attempt_id = str(uuid4()), str(uuid4())
            number = row['attempt'] + 1
            db.execute("UPDATE research_jobs SET state='leased',attempt=?,owner=?,lease_token=?,lease_until=? WHERE job_id=?", (number, owner, token, now + ttl, row['job_id']))
            db.execute('INSERT INTO research_attempts VALUES(?,?,?,?,NULL,NULL)', (attempt_id, row['job_id'], number, now))
            return {'job_id': row['job_id'], 'run_id': row['run_id'], 'project_id': row['project_id'], 'lease_token': token, 'attempt': number, 'attempt_id': attempt_id}

    def heartbeat(self, claim, ttl=30):
        with self.store.transaction() as db:
            assert_lease(db, claim['run_id'], (claim['job_id'], claim['lease_token']))
            db.execute('UPDATE research_jobs SET lease_until=? WHERE job_id=?', (time.time() + ttl, claim['job_id']))

    def finish(self, claim, outcome):
        with self.store.transaction() as db:
            assert_lease(db, claim['run_id'], (claim['job_id'], claim['lease_token']))
            db.execute("UPDATE research_jobs SET state='done',lease_token=NULL,lease_until=NULL WHERE job_id=?", (claim['job_id'],))
            db.execute('UPDATE research_attempts SET finished_at=?,outcome=? WHERE attempt_id=?', (time.time(), outcome, claim['attempt_id']))

    def release(self, claim):
        with self.store.transaction() as db:
            assert_lease(db, claim['run_id'], (claim['job_id'], claim['lease_token']))
            db.execute("UPDATE research_jobs SET state='queued',lease_token=NULL,lease_until=NULL WHERE job_id=?", (claim['job_id'],))
            db.execute("UPDATE research_attempts SET finished_at=?,outcome='worker_shutdown' WHERE attempt_id=?", (time.time(), claim['attempt_id']))

    def cancel(self, project_id, run_id):
        from app.domain.research import utc_now
        with self.store.transaction() as db:
            row = db.execute('SELECT payload FROM research_runs WHERE project_id=? AND run_id=?', (str(project_id), str(run_id))).fetchone()
            if not row:
                raise LookupError('Research run not found')
            run = json.loads(row[0])
            if run['status'] not in ('queued', 'running'):
                return run
            run.update(status='cancelled', phase='finished', report=None, answer_preview=None, finished_at=utc_now().isoformat())
            event = {'seq': len(run['events']) + 1, 'run_id': str(run_id), 'event': 'cancelled', 'at': utc_now().isoformat()}
            run['events'].append(event)
            db.execute('INSERT INTO research_events VALUES(?,?,?)', (str(run_id), event['seq'], json.dumps(event, ensure_ascii=False)))
            db.execute('UPDATE research_runs SET payload=? WHERE run_id=?', (json.dumps(run, ensure_ascii=False), str(run_id)))
            db.execute("UPDATE research_jobs SET state='cancelled',lease_token=NULL,lease_until=NULL WHERE run_id=?", (str(run_id),))
            db.execute("UPDATE research_attempts SET finished_at=?,outcome='cancelled' WHERE job_id=(SELECT job_id FROM research_jobs WHERE run_id=?) AND finished_at IS NULL", (time.time(), str(run_id)))
            db.execute("UPDATE run_budgets SET stopped_reason=COALESCE(stopped_reason,'cancelled') WHERE run_id=?", (str(run_id),))
        return run

    def attempts(self, project_id, run_id):
        self.store.get_run(project_id, run_id)
        with self.store.reader() as db:
            return [dict(row) for row in db.execute('SELECT a.* FROM research_attempts a JOIN research_jobs j ON j.job_id=a.job_id WHERE j.run_id=? ORDER BY number', (str(run_id),))]
