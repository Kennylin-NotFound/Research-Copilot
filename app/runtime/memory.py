"""Project-scoped, source-bound research memory; no LLM trust or preference mining."""
import hashlib
import json
import re
from uuid import UUID, uuid4, uuid5

from app.domain.research import utc_now


def content_key(claim, evidence):
    data = {'claim': ' '.join(claim.split()), 'evidence': sorted(evidence)}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class ProjectMemory:
    def __init__(self, store):
        self.store = store

    def _insert(self, db, value):
        encoded = json.dumps(value, ensure_ascii=False)
        db.execute('INSERT OR IGNORE INTO research_memories VALUES(?,?,?,?,?,?,?)',
                   (value['memory_id'], value['project_id'], value['kind'], value.get('origin_run_id'), value['state'], value['revision'], encoded))
        db.execute('INSERT OR IGNORE INTO memory_revisions VALUES(?,?,?)', (value['memory_id'], value['revision'], encoded))

    def publication(self, db, run):
        """Called inside the fenced Run publication transaction, never on read."""
        if not run.get('selected_finding_ids'):
            return
        brief = json.loads(db.execute('SELECT payload FROM research_briefs WHERE brief_id=?', (run['brief_id'],)).fetchone()[0])
        base = {'project_id': run['project_id'], 'origin_run_id': run['run_id'], 'brief_id': run['brief_id'],
                'project_revision': run['project_revision'], 'as_of': brief['as_of'], 'created_at': utc_now().isoformat(),
                'revision': 1, 'state': 'active', 'evidence_ids': [], 'source_version_ids': [], 'source_heads': run.get('source_heads', {}),
                'semantic_review': 'model_reviewed_not_human'}
        def add(kind, key, **values):
            value = base | {'kind': kind, 'memory_id': str(uuid5(UUID(run['run_id']), kind + ':' + key))} | values
            self._insert(db, value)
        add('scope', 'brief', text=brief['question'], dimensions=brief['dimensions'], region=brief['region'], provenance='user_brief', semantic_review='user_specified_scope')
        for sid in run.get('read_source_version_ids', []):
            source = db.execute('SELECT source_id FROM source_versions WHERE source_version_id=? AND project_id=?', (sid, run['project_id'])).fetchone()
            if not source:
                raise ValueError('Memory source is outside project')
            add('reading', sid, text='Read bounded source excerpts; not proof of full-document review.', outcome='read', source_version_ids=[sid], source_id=source[0])
        for finding in run['findings']:
            if finding['finding_id'] not in run['selected_finding_ids']:
                continue
            evidence_keys, source_ids = [], []
            for eid in finding['evidence_ids']:
                row = db.execute('SELECT payload FROM evidence WHERE evidence_id=? AND project_id=?', (eid, run['project_id'])).fetchone()
                if not row:
                    raise ValueError('Memory finding lacks same-project evidence')
                evidence = json.loads(row[0]); source_ids.append(evidence['source_version_id'])
                evidence_keys.append(json.dumps({key: evidence[key] for key in ('source_version_id','quote','locator')}, sort_keys=True, ensure_ascii=False))
            if not evidence_keys:
                raise ValueError('Uncited findings cannot enter research memory')
            content = finding['content']
            add('finding', finding['finding_id'], text=content['claim'], dimension=content['dimension'],
                claim_type=content['claim_type'], assessment=content['assessment'], uncertainty=content['uncertainty'],
                state='conflict' if content['assessment'] == 'conflicts' else 'active',
                evidence_ids=finding['evidence_ids'], source_version_ids=list(dict.fromkeys(source_ids)),
                content_key=content_key(content['claim'], evidence_keys))
        for number, question in enumerate(run.get('follow_up_options', [])[:5]):
            add('question', str(number), text=question, semantic_review='open_question_not_fact')

    def _revise(self, db, value, state, **changes):
        value = value | changes | {'state': state, 'revision': value['revision'] + 1, 'updated_at': utc_now().isoformat()}
        payload = json.dumps(value, ensure_ascii=False)
        db.execute('UPDATE research_memories SET state=?,revision=?,payload=? WHERE memory_id=?', (state, value['revision'], payload, value['memory_id']))
        db.execute('INSERT INTO memory_revisions VALUES(?,?,?)', (value['memory_id'], value['revision'], payload))
        return value

    def list(self, project_id, *, include_inactive=True):
        brief = self.store.get_brief(project_id)
        # Revalidate on every retrieval. An inactive memory is never silently
        # returned to the model when a file/source revision has changed.
        with self.store.reader() as db:
            values = [json.loads(row[0]) for row in db.execute('SELECT payload FROM research_memories WHERE project_id=? ORDER BY rowid DESC LIMIT 300', (str(project_id),))]
        invalid = {}
        for value in values:
            if value['state'] not in ('active', 'conflict'):
                continue
            reason = None
            if value['kind'] == 'finding' and not value['evidence_ids']:
                reason = 'finding_without_evidence'
            if value['project_revision'] != brief.project_revision:
                reason = 'project_revision_changed'
            try:
                for sid in value['source_version_ids']:
                    self.store.read_raw(project_id, UUID(sid)); self.store.read_extracted(project_id, UUID(sid))
                for eid in value['evidence_ids']: self.store.get_evidence(project_id, UUID(eid))
                with self.store.reader() as db:
                    for source_id, expected in value.get('source_heads', {}).items():
                        row = db.execute('SELECT source_version_id FROM source_versions WHERE source_id=? AND project_id=? ORDER BY rowid DESC LIMIT 1', (source_id, str(project_id))).fetchone()
                        if not row or row[0] != expected: reason = 'source_version_changed'
            except (ValueError, LookupError, OSError):
                reason = 'source_missing_or_corrupt'
            if reason: invalid[value['memory_id']] = reason
        if invalid:
            with self.store.transaction() as db:
                for mid, reason in invalid.items():
                    row = db.execute('SELECT payload,state FROM research_memories WHERE memory_id=? AND project_id=?', (mid, str(project_id))).fetchone()
                    if row and row['state'] in ('active', 'conflict'):
                        self._revise(db, json.loads(row['payload']), 'stale', stale_reason=reason)
            with self.store.reader() as db:
                values = [json.loads(row[0]) for row in db.execute('SELECT payload FROM research_memories WHERE project_id=? ORDER BY rowid DESC LIMIT 300', (str(project_id),))]
        return values if include_inactive else [value for value in values if value['state'] in ('active', 'conflict')]

    def retrieve(self, project_id, question, as_of, *, limit=6):
        if not 1 <= limit <= 12:
            raise ValueError('Memory limit must be 1..12')
        tokens = set(re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]', question.casefold()))
        values = [value for value in self.list(project_id, include_inactive=False) if value['as_of'] <= str(as_of)]
        def score(value):
            terms = set(re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]', value['text'].casefold()))
            return len(terms & tokens)
        values = sorted((value for value in values if score(value)), key=score, reverse=True)
        return values[:limit]

    def delete(self, project_id, memory_id):
        with self.store.transaction() as db:
            row = db.execute('SELECT payload FROM research_memories WHERE project_id=? AND memory_id=?', (str(project_id), str(memory_id))).fetchone()
            if not row: raise LookupError('Project memory not found')
            value = json.loads(row[0])
            if value['state'] == 'deleted': return value
            return self._revise(db, value, 'deleted', change_reason='user_deleted')

    def supersede(self, project_id, memory_id, replacement_id):
        if str(memory_id) == str(replacement_id): raise ValueError('A memory cannot replace itself')
        # Revalidate sources before allowing an explicit user replacement.
        self.list(project_id)
        with self.store.transaction() as db:
            rows = [db.execute('SELECT payload FROM research_memories WHERE project_id=? AND memory_id=?', (str(project_id), str(mid))).fetchone() for mid in (memory_id, replacement_id)]
            if not all(rows): raise LookupError('Same-project replacement memory not found')
            old, new = (json.loads(row[0]) for row in rows)
            if old['kind'] != new['kind'] or new['state'] not in ('active','conflict'):
                raise ValueError('Replacement must be a usable memory of the same kind')
            if old['state'] in ('deleted', 'superseded'): raise ValueError('Inactive memory cannot be replaced again')
            return self._revise(db, old, 'superseded', superseded_by=new['memory_id'], change_reason='user_confirmed_replacement')

    def exclude_source(self, project_id, source_id, reason):
        source = self.store.get_source(project_id, source_id)
        brief = self.store.get_brief(project_id)
        value = {'memory_id': str(uuid4()), 'project_id': str(project_id), 'kind': 'reading', 'origin_run_id': None,
                 'brief_id': str(brief.brief_id), 'project_revision': brief.project_revision, 'as_of': brief.as_of.isoformat(),
                 'created_at': utc_now().isoformat(), 'revision': 1, 'state': 'active', 'text': reason,
                 'outcome': 'excluded', 'source_version_ids': [str(source_id)], 'source_id': str(source.source_id),
                 'source_heads': {str(source.source_id): str(source_id)},
                 'evidence_ids': [], 'semantic_review': 'user_exclusion_not_fact'}
        with self.store.transaction() as db: self._insert(db, value)
        return value

    def delta(self, project_id, baseline_run_id, run):
        baseline = self.store.get_run(project_id, baseline_run_id)
        if baseline['status'] not in ('completed','partial') or not baseline.get('selected_finding_ids'):
            raise ValueError('Incremental baseline has no selected findings')
        # Compare exact statements with exact source/quote versions. No claim of
        # semantic equivalence, contradiction discovery or exhaustive coverage.
        values = self.list(project_id)
        old = [value for value in values if value['origin_run_id'] == str(baseline_run_id) and value['kind'] == 'finding']
        if not old:
            # Explicitly selected older reports can be compared without a
            # silent migration that turns all historical reports into memory.
            for finding in baseline['findings']:
                if finding['finding_id'] not in baseline.get('selected_finding_ids', []): continue
                parts, state = [], 'active'
                try:
                    for eid in finding['evidence_ids']:
                        evidence = self.store.get_evidence(project_id, UUID(eid)).model_dump(mode='json')
                        parts.append(json.dumps({key: evidence[key] for key in ('source_version_id','quote','locator')}, sort_keys=True, ensure_ascii=False))
                        source = self.store.get_source(project_id, UUID(evidence['source_version_id']))
                        with self.store.reader() as db:
                            head = db.execute('SELECT source_version_id FROM source_versions WHERE source_id=? ORDER BY rowid DESC LIMIT 1', (str(source.source_id),)).fetchone()[0]
                        if head != evidence['source_version_id']: state = 'stale'
                    if baseline['project_revision'] != run['project_revision']: state = 'stale'
                except (ValueError, LookupError, OSError):
                    state = 'stale'
                old.append({'text': finding['content']['claim'], 'content_key': content_key(finding['content']['claim'], parts), 'state': state})
        current = []
        for finding in run['findings']:
            if finding['finding_id'] not in run.get('selected_finding_ids', []): continue
            parts = []
            for eid in finding['evidence_ids']:
                evidence = self.store.get_evidence(project_id, UUID(eid)).model_dump(mode='json')
                parts.append(json.dumps({key: evidence[key] for key in ('source_version_id','quote','locator')}, sort_keys=True, ensure_ascii=False))
            current.append({'claim': finding['content']['claim'], 'content_key': content_key(finding['content']['claim'], parts)})
        valid = {value['content_key'] for value in old if value['state'] in ('active','conflict')}
        new_keys = {value['content_key'] for value in current}
        return {'baseline_run_id': str(baseline_run_id), 'comparison': 'exact_claim_and_evidence_not_semantic',
                'added': [v['claim'] for v in current if v['content_key'] not in valid],
                'unchanged': [v['claim'] for v in current if v['content_key'] in valid],
                'needs_review': [v['text'] for v in old if v['state'] not in ('active','conflict')],
                'not_revisited': [v['text'] for v in old if v['state'] in ('active','conflict') and v['content_key'] not in new_keys],
                'changed': [v['text'] for v in old if v.get('superseded_by')],
                'warning': '新增文本不等于新事实；未形成相同记录不能判断事实变化或语义等价。替代/冲突需要复核。'}
