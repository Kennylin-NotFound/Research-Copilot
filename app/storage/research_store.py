"""Transactional domain records with immutable, verified source files.

SQLite is authoritative. File publication precedes DB commit; a process crash
can leave an unreferenced directory, but committed rows never intentionally
reference an unpublished snapshot. This is not cross-resource exactly-once.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from uuid import UUID, uuid5

from app.domain.research import Evidence, ExtractedDocument, ResearchBrief, SourceSpec, SourceVersion, snapshot_digest, source_identity
from app.storage.extraction import extract_document, validate_raw

MIGRATIONS = Path(__file__).parent / 'migrations'


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class ResearchStore:
    def __init__(self, data_dir: Path):
        # data_dir is a trusted application dependency; CLI confines overrides
        # to repository/data. Internal object IDs are never user file paths.
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self._safe_path('product', 'research.sqlite3')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.sources_dir = self._safe_path('sources')
        self.sources_dir.mkdir(exist_ok=True)
        self.initialize()

    @staticmethod
    def assert_active(db, project_id):
        if db.execute('SELECT 1 FROM project_trash WHERE project_id=?', (str(project_id),)).fetchone():
            raise LookupError('项目已删除，请先在回收站恢复')

    def project_deleted(self, project_id):
        with self.reader() as db:
            return bool(db.execute('SELECT 1 FROM project_trash WHERE project_id=?', (str(project_id),)).fetchone())

    def _safe_path(self, *parts):
        candidate = self.data_dir.joinpath(*map(str, parts))
        current = self.data_dir
        for part in parts:
            current = current / str(part)
            if current.is_symlink() or current.is_junction():
                raise ValueError('Source storage cannot traverse links or junctions')
        if not candidate.resolve().is_relative_to(self.data_dir):
            raise ValueError('Storage path escaped configured data directory')
        return candidate

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=5000')
        return db

    @contextmanager
    def transaction(self):
        db = self._connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def reader(self):
        db = self._connect()
        try:
            yield db
        finally:
            db.close()

    def initialize(self):
        with self.reader() as db:
            db.execute('PRAGMA journal_mode=WAL')
        files = sorted(MIGRATIONS.glob('[0-9]*_*.sql'))
        known = {int(path.name.split('_', 1)[0]): path for path in files}
        if not files or len(known) != len(files) or sorted(known) != list(range(1, len(known) + 1)):
            raise ValueError('Migration files must have unique contiguous versions starting at 1')
        with self.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, name TEXT NOT NULL, sha256 TEXT NOT NULL)')
            applied = {row['version']: row for row in db.execute('SELECT * FROM schema_migrations')}
            if set(applied) - set(known) or sorted(applied) != list(range(1, len(applied) + 1)):
                raise ValueError('Unknown or discontinuous schema version; refusing migration')
            for number, path in known.items():
                # Text newline normalization keeps migration checksums portable.
                sql = path.read_text(encoding='utf-8')
                checksum = digest(sql.encode('utf-8'))
                if number in applied:
                    if applied[number]['sha256'] != checksum or applied[number]['name'] != path.stem:
                        raise ValueError('Previously applied migration changed; refusing migration')
                    continue
                statement = ''
                for line in sql.splitlines(keepends=True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        db.execute(statement)
                        statement = ''
                if statement.strip():
                    raise ValueError('Incomplete migration statement')
                db.execute('INSERT INTO schema_migrations VALUES(?,?,?)', (number, path.stem, checksum))

    def schema(self):
        with self.reader() as db:
            return {'migrations': [dict(row) for row in db.execute('SELECT * FROM schema_migrations ORDER BY version')],
                    'foreign_key_errors': [tuple(row) for row in db.execute('PRAGMA foreign_key_check')]}

    def save_brief(self, brief: ResearchBrief):
        brief = ResearchBrief.model_validate(brief)
        payload = brief.model_dump_json()
        with self.transaction() as db:
            self.assert_active(db, brief.project_id)
            existing = db.execute('SELECT payload FROM research_briefs WHERE brief_id=? OR (project_id=? AND project_revision=?)',
                                  (str(brief.brief_id), str(brief.project_id), brief.project_revision)).fetchone()
            if existing:
                if existing['payload'] != payload:
                    raise ValueError('Brief snapshots are immutable; create a new project revision')
                return brief
            db.execute('INSERT OR IGNORE INTO projects VALUES(?)', (str(brief.project_id),))
            db.execute('INSERT INTO research_briefs VALUES(?,?,?,?)', (str(brief.brief_id), str(brief.project_id), brief.project_revision, payload))
        return brief

    def get_brief(self, project_id: UUID, revision: int | None = None):
        project_id = str(UUID(str(project_id)))
        with self.reader() as db:
            self.assert_active(db, project_id)
            if revision is None:
                row = db.execute('SELECT payload FROM research_briefs WHERE project_id=? ORDER BY project_revision DESC LIMIT 1', (project_id,)).fetchone()
            else:
                row = db.execute('SELECT payload FROM research_briefs WHERE project_id=? AND project_revision=?', (project_id, revision)).fetchone()
        if row is None:
            raise LookupError('Research brief not found')
        return ResearchBrief.model_validate_json(row['payload'])

    def revise_project(self, project_id, inputs):
        """Compare-and-swap the project revision under the write transaction."""
        project_id = UUID(str(project_id))
        with self.transaction() as db:
            self.assert_active(db, project_id)
            latest = db.execute('SELECT MAX(project_revision) FROM research_briefs WHERE project_id=?', (str(project_id),)).fetchone()[0] or 0
            if latest != inputs.expected_revision:
                raise ValueError('Project revision changed; reload before editing')
            brief = inputs.brief(project_id, latest + 1)
            db.execute('INSERT OR IGNORE INTO projects VALUES(?)', (str(project_id),))
            db.execute('INSERT INTO research_briefs VALUES(?,?,?,?)', (str(brief.brief_id), str(project_id), brief.project_revision, brief.model_dump_json()))
        return brief

    def list_projects(self):
        with self.reader() as db:
            rows = db.execute('SELECT b.payload FROM research_briefs b WHERE NOT EXISTS(SELECT 1 FROM project_trash t WHERE t.project_id=b.project_id) AND b.project_revision=(SELECT MAX(project_revision) FROM research_briefs WHERE project_id=b.project_id) ORDER BY b.rowid DESC').fetchall()
        return [ResearchBrief.model_validate_json(row['payload']) for row in rows]

    def insert_run(self, run):
        with self.transaction() as db:
            self.assert_active(db, run['project_id'])
            latest = db.execute('SELECT brief_id FROM research_briefs WHERE project_id=? ORDER BY project_revision DESC LIMIT 1', (run['project_id'],)).fetchone()
            if not latest or latest['brief_id'] != run['brief_id']:
                raise ValueError('Project revision changed before execution')
            db.execute('INSERT INTO research_runs VALUES(?,?,?,?)', (run['run_id'], run['project_id'], run['brief_id'], json.dumps(run, ensure_ascii=False)))

    def update_run(self, run, *, publishing=False):
        with self.transaction() as db:
            self.assert_active(db, run['project_id'])
            from app.runtime.jobs import assert_lease
            assert_lease(db, run['run_id'], getattr(self, 'lease', None))
            if publishing:
                latest = db.execute('SELECT brief_id FROM research_briefs WHERE project_id=? ORDER BY project_revision DESC LIMIT 1', (run['project_id'],)).fetchone()
                if not latest or latest['brief_id'] != run['brief_id']:
                    raise ValueError('Project changed; this report is stale and cannot be published')
                for sid, expected in run.get('source_heads', {}).items():
                    current = db.execute('SELECT source_version_id FROM source_versions WHERE source_id=? AND project_id=? ORDER BY rowid DESC LIMIT 1', (sid, run['project_id'])).fetchone()
                    if not current or current[0] != expected:
                        raise ValueError('Source version changed; prior evidence needs review before publication')
            old = db.execute('SELECT payload FROM research_runs WHERE run_id=? AND project_id=? AND brief_id=?', (run['run_id'], run['project_id'], run['brief_id'])).fetchone()
            if not old:
                raise LookupError('Research run not found')
            if publishing and json.loads(old['payload'])['status'] in ('cancelled', 'interrupted'):
                raise ValueError('Cancelled or interrupted Run cannot publish')
            db.execute('UPDATE research_runs SET payload=? WHERE run_id=?', (json.dumps(run, ensure_ascii=False), run['run_id']))
            if run.get('conversation_id') and run['status'] not in ('queued', 'running'):
                content = run.get('answer_preview') or ('已保留阶段研究记录，请查看报告。' if run.get('report') else '本次研究未形成可交付的结论。')
                db.execute("UPDATE research_messages SET content=? WHERE run_id=? AND role='assistant' AND kind='research'", (content, run['run_id']))
            last = db.execute('SELECT COALESCE(MAX(seq),0) FROM research_events WHERE run_id=?', (run['run_id'],)).fetchone()[0]
            for event in run.get('events', []):
                if event['seq'] > last:
                    if event['seq'] != last + 1:
                        raise ValueError('Run events must have contiguous sequence numbers')
                    db.execute('INSERT INTO research_events VALUES(?,?,?)', (run['run_id'], event['seq'], json.dumps(event, ensure_ascii=False)))
                    last = event['seq']
            if publishing and run.get('job_id'):
                from app.runtime.memory import ProjectMemory
                ProjectMemory(self).publication(db, run)

    def events_after(self, project_id, run_id, after=0, limit=200):
        self.get_run(project_id, run_id)  # Ownership before replay.
        if after < 0 or not 1 <= limit <= 500:
            raise ValueError('Invalid event cursor or page size')
        with self.reader() as db:
            rows = db.execute('SELECT payload FROM research_events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?', (str(run_id), after, limit)).fetchall()
            high = db.execute('SELECT COALESCE(MAX(seq),0) FROM research_events WHERE run_id=?', (str(run_id),)).fetchone()[0]
        values = [json.loads(row[0]) for row in rows]
        return {'run_id': str(run_id), 'events': values, 'last_seq': values[-1]['seq'] if values else after, 'has_more': bool(values and values[-1]['seq'] < high)}

    def get_run(self, project_id, run_id):
        with self.reader() as db:
            self.assert_active(db, project_id)
            row = db.execute('SELECT payload FROM research_runs WHERE project_id=? AND run_id=?', (str(project_id), str(run_id))).fetchone()
        if row is None:
            raise LookupError('Research run not found')
        return json.loads(row['payload'])

    def project_usage(self, project_id):
        with self.reader() as db:
            count = db.execute('SELECT COUNT(*) FROM research_runs WHERE project_id=?', (str(project_id),)).fetchone()[0]
            metered = db.execute('SELECT COUNT(*) FROM run_budgets b JOIN research_runs r ON r.run_id=b.run_id WHERE r.project_id=?', (str(project_id),)).fetchone()[0]
            entries = db.execute('SELECT e.* FROM budget_reservations e JOIN run_budgets b ON b.budget_id=e.budget_id JOIN research_runs r ON r.run_id=b.run_id WHERE r.project_id=?', (str(project_id),)).fetchall()
        return {'runs': count, 'legacy_unmetered_runs': count - metered,
                'known_tokens': sum(item['actual_tokens'] or 0 for item in entries),
                'unknown_model_calls': sum(item['kind'] == 'model' and item['state'] != 'known' for item in entries),
                'external_calls': sum(item['kind'] in ('model', 'search_request', 'public_http', 'public_dns') for item in entries)}

    def list_runs(self, project_id):
        with self.reader() as db:
            self.assert_active(db, project_id)
            rows = db.execute('SELECT payload FROM research_runs WHERE project_id=? ORDER BY rowid DESC', (str(project_id),)).fetchall()
        return [json.loads(row['payload']) for row in rows]

    def _snapshot_dir(self, version: SourceVersion):
        return self._safe_path('sources', str(version.source_id), version.snapshot_sha256)

    @staticmethod
    def _write(path, content):
        with path.open('xb') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())

    def _publish_files(self, version, raw, text):
        target = self._snapshot_dir(version)
        if target.exists():
            raw_path = self._safe_path('sources', str(version.source_id), version.snapshot_sha256, 'raw.bin')
            text_path = self._safe_path('sources', str(version.source_id), version.snapshot_sha256, 'text.json')
            if raw_path.read_bytes() != raw or text_path.read_bytes() != text:
                raise ValueError('Existing snapshot files differ from expected content')
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        # Same parent/volume for atomic directory rename. Leftovers are never
        # treated as committed sources and are not automatically deleted.
        temporary = Path(tempfile.mkdtemp(prefix='.staging-', dir=target.parent))
        try:
            self._write(temporary / 'raw.bin', raw)
            self._write(temporary / 'text.json', text)
            temporary.rename(target)
        finally:
            if temporary.exists():
                for name in ('raw.bin', 'text.json'):
                    (temporary / name).unlink(missing_ok=True)
                temporary.rmdir()

    def save_source(self, spec: SourceSpec, raw: bytes):
        spec = SourceSpec.model_validate(spec)
        validate_raw(raw)
        # Only deterministic trusted extractors produce text positions. A model
        # or caller cannot attach invented text to unrelated source bytes.
        extracted = extract_document(spec.kind, raw, spec.sections)
        extracted.validate_kind(spec.kind)
        text = canonical_json(extracted.model_dump(mode='json'))
        source_id = source_identity(spec)
        snapshot = snapshot_digest(spec, digest(raw), digest(text))
        version = SourceVersion(source_version_id=uuid5(source_id, snapshot), source_id=source_id, spec=spec,
                                raw_sha256=digest(raw), text_sha256=digest(text), snapshot_sha256=snapshot,
                                raw_size=len(raw), unit_count=len(extracted.units), extractor=extracted.extractor)
        with self.transaction() as db:
            # Read current policy under the same write transaction as insertion.
            self.assert_active(db, spec.project_id)
            latest = db.execute('SELECT payload FROM research_briefs WHERE project_id=? ORDER BY project_revision DESC LIMIT 1', (str(spec.project_id),)).fetchone()
            if latest is None:
                raise LookupError('Research brief not found')
            if spec.kind not in ResearchBrief.model_validate_json(latest['payload']).source_policy.allowed_kinds:
                raise ValueError('Source kind excluded by this project source policy')
            previous = db.execute('SELECT payload FROM source_versions WHERE source_version_id=?', (str(version.source_version_id),)).fetchone()
            if previous:
                stored = SourceVersion.model_validate_json(previous['payload'])
                # Return the original retrieval timestamp; a duplicate observation
                # does not rewrite history. An observation timeline is not built yet.
                self.read_raw(stored.spec.project_id, stored.source_version_id)
                self.read_extracted(stored.spec.project_id, stored.source_version_id)
                return stored
            self._publish_files(version, raw, text)
            db.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?,?)', (str(source_id), str(spec.project_id), spec.kind, spec.origin))
            db.execute('INSERT INTO source_versions VALUES(?,?,?,?,?)', (str(version.source_version_id), str(source_id), str(spec.project_id), snapshot, version.model_dump_json()))
        return version

    def get_source(self, project_id: UUID, source_version_id: UUID):
        with self.reader() as db:
            self.assert_active(db, project_id)
            row = db.execute('SELECT payload FROM source_versions WHERE project_id=? AND source_version_id=?',
                             (str(UUID(str(project_id))), str(UUID(str(source_version_id))))).fetchone()
        if row is None:
            raise LookupError('Source version not found')
        return SourceVersion.model_validate_json(row['payload'])

    def list_sources(self, project_id: UUID):
        with self.reader() as db:
            self.assert_active(db, project_id)
            rows = db.execute('SELECT payload FROM source_versions WHERE project_id=? ORDER BY source_id, source_version_id', (str(UUID(str(project_id))),)).fetchall()
        return [SourceVersion.model_validate_json(row['payload']) for row in rows]

    def read_raw(self, project_id, source_version_id):
        version = self.get_source(project_id, source_version_id)
        return self._read_raw_version(version)

    def _read_raw_version(self, version):
        # Offline restore also verifies trash snapshots. Public reads must
        # resolve through get_source first, preserving the active-project gate.
        path = self._safe_path('sources', str(version.source_id), version.snapshot_sha256, 'raw.bin')
        raw = path.read_bytes()
        if len(raw) != version.raw_size or digest(raw) != version.raw_sha256:
            raise ValueError('Raw source checksum mismatch')
        return raw

    def read_extracted(self, project_id, source_version_id):
        version = self.get_source(project_id, source_version_id)
        return self._read_extracted_version(version)

    def _read_extracted_version(self, version):
        path = self._safe_path('sources', str(version.source_id), version.snapshot_sha256, 'text.json')
        raw = path.read_bytes()
        if digest(raw) != version.text_sha256:
            raise ValueError('Extracted text checksum mismatch')
        extracted = ExtractedDocument.model_validate_json(raw)
        extracted.validate_kind(version.spec.kind)
        if len(extracted.units) != version.unit_count or extracted.extractor != version.extractor:
            raise ValueError('Extracted text metadata mismatch')
        return extracted

    def _validate_evidence(self, evidence):
        source = self.get_source(evidence.project_id, evidence.source_version_id)
        if source.spec.body_state != 'full_text':
            raise ValueError('Search snippets cannot serve as full-text evidence')
        self.read_raw(evidence.project_id, evidence.source_version_id)
        extracted = self.read_extracted(evidence.project_id, evidence.source_version_id)
        matching = [unit for unit in extracted.units if unit.locator == evidence.locator]
        if not matching or evidence.quote not in matching[0].text:
            raise ValueError('Evidence quote does not match its source position')

    def save_evidence(self, evidence: Evidence):
        evidence = Evidence.model_validate(evidence)
        self._validate_evidence(evidence)
        payload = evidence.model_dump_json()
        with self.transaction() as db:
            existing = db.execute('SELECT payload FROM evidence WHERE evidence_id=?', (str(evidence.evidence_id),)).fetchone()
            if existing:
                if existing['payload'] != payload:
                    raise ValueError('Evidence records are immutable; use a new identity')
                return evidence
            db.execute('INSERT INTO evidence VALUES(?,?,?,?)', (str(evidence.evidence_id), str(evidence.source_version_id), str(evidence.project_id), payload))
        return evidence

    def get_evidence(self, project_id: UUID, evidence_id: UUID):
        with self.reader() as db:
            row = db.execute('SELECT payload FROM evidence WHERE project_id=? AND evidence_id=?',
                             (str(UUID(str(project_id))), str(UUID(str(evidence_id))))).fetchone()
        if row is None:
            raise LookupError('Evidence not found')
        evidence = Evidence.model_validate_json(row['payload'])
        self._validate_evidence(evidence)
        return evidence
