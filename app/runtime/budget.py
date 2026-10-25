"""SQLite atomic admissions shared by every child of one research Run.

Counts/time are admission limits. Token totals are a target: provider tokenization
can differ from estimates and an in-flight request can overrun. Unknown usage
retains its reservation instead of silently becoming zero.
"""
from contextlib import contextmanager
from datetime import datetime
import json
from uuid import uuid4

from app.domain.research import RunBudget, utc_now


class BudgetStopped(RuntimeError):
    pass


class BudgetLedger:
    def __init__(self, store, run):
        self.store = store
        self.run_id = run['run_id']
        self.limits = RunBudget.model_validate(run['budget'])
        self.id = str(self.limits.budget_id)
        with store.transaction() as db:
            old = db.execute('SELECT * FROM run_budgets WHERE run_id=?', (self.run_id,)).fetchone()
            if old:
                if old['limits'] != self.limits.model_dump_json() or old['budget_id'] != self.id:
                    raise ValueError('Existing Run budget is immutable; recovery cannot reset it')
            else:
                db.execute('INSERT INTO run_budgets VALUES(?,?,?,?,NULL)', (self.id, self.run_id, self.limits.model_dump_json(), run['created_at']))

    def stop(self, reason):
        with self.store.transaction() as db:
            from app.runtime.jobs import assert_lease
            assert_lease(db, self.run_id, getattr(self.store, 'lease', None))
            db.execute('UPDATE run_budgets SET stopped_reason=COALESCE(stopped_reason,?) WHERE budget_id=?', (reason, self.id))

    def _check(self, db, tokens=0, kind=None):
        from app.runtime.jobs import assert_lease
        assert_lease(db, self.run_id, getattr(self.store, 'lease', None))
        row = db.execute('SELECT * FROM run_budgets WHERE budget_id=?', (self.id,)).fetchone()
        reason = row['stopped_reason']
        if not reason and (utc_now() - datetime.fromisoformat(row['started_at'])).total_seconds() >= self.limits.max_wall_seconds:
            reason = 'wall_time_limit'
        run = json.loads(db.execute('SELECT payload FROM research_runs WHERE run_id=?', (self.run_id,)).fetchone()[0])
        if not reason and run['status'] in ('cancelled', 'interrupted', 'failed', 'partial', 'completed'):
            reason = 'run_is_terminal'
        counts = {item['kind']: item['n'] for item in db.execute('SELECT kind,COUNT(*) n FROM budget_reservations WHERE budget_id=? GROUP BY kind', (self.id,))}
        external = sum(counts.get(key, 0) for key in ('model', 'search_request', 'public_http', 'public_dns'))
        used = db.execute('SELECT COALESCE(SUM(COALESCE(actual_tokens,estimated_tokens)),0) FROM budget_reservations WHERE budget_id=?', (self.id,)).fetchone()[0]
        for crossed, label in (
            (kind in ('model', 'search_request', 'public_http', 'public_dns') and external >= self.limits.max_external_calls, 'external_call_limit'),
            (kind == 'tool' and counts.get('tool', 0) >= self.limits.max_tool_calls, 'tool_call_limit'),
            (kind == 'search' and counts.get('search', 0) >= self.limits.max_searches, 'search_limit'),
            (kind == 'source_read' and counts.get('source_read', 0) >= self.limits.max_source_reads, 'source_read_limit'),
            (tokens > 0 and used + tokens > self.limits.target_total_tokens, 'token_target_admission'),
        ):
            if crossed and not reason:
                reason = label
        return reason

    def reserve(self, kind, *, reservation_id=None, estimated_tokens=0, detail=None):
        key = reservation_id or str(uuid4())
        with self.store.transaction() as db:
            if db.execute('SELECT 1 FROM budget_reservations WHERE reservation_id=?', (key,)).fetchone():
                raise ValueError('Each actual attempt requires a fresh reservation identity')
            reason = self._check(db, estimated_tokens, kind)
            if reason:
                db.execute('UPDATE run_budgets SET stopped_reason=COALESCE(stopped_reason,?) WHERE budget_id=?', (reason, self.id))
            else:
                db.execute('INSERT INTO budget_reservations VALUES(?,?,?,?,NULL,?,?)',
                           (key, self.id, kind, estimated_tokens, 'reserved', json.dumps(detail or {}, ensure_ascii=False)))
        if reason:
            raise BudgetStopped(reason)
        return key

    def check(self):
        with self.store.transaction() as db:
            reason = self._check(db)
            if reason:
                db.execute('UPDATE run_budgets SET stopped_reason=COALESCE(stopped_reason,?) WHERE budget_id=?', (reason, self.id))
        if reason:
            raise BudgetStopped(reason)

    def settle(self, key, usage=None):
        tokens = usage.get('total_tokens') if isinstance(usage, dict) else None
        if tokens is not None and (not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0):
            raise ValueError('Usage tokens must be a nonnegative integer or unknown')
        with self.store.transaction() as db:
            row = db.execute('SELECT * FROM budget_reservations WHERE reservation_id=? AND budget_id=?', (key, self.id)).fetchone()
            if not row:
                raise LookupError('Budget reservation not found')
            if row['state'] == 'known':
                if row['actual_tokens'] != tokens:
                    raise ValueError('Usage settlement changed after becoming known')
                return
            detail = json.loads(row['detail']); detail['provider_usage'] = usage
            db.execute('UPDATE budget_reservations SET actual_tokens=?,state=?,detail=? WHERE reservation_id=?',
                       (tokens, 'known' if tokens is not None else 'unknown', json.dumps(detail, ensure_ascii=False), key))

    @contextmanager
    def request(self, kind, detail=None):
        key = self.reserve(kind, detail=detail)
        try:
            yield key
        finally:
            self.settle(key)  # Transport operations have no model token usage.

    def snapshot(self):
        with self.store.reader() as db:
            row = db.execute('SELECT * FROM run_budgets WHERE budget_id=?', (self.id,)).fetchone()
            entries = [dict(value) for value in db.execute('SELECT * FROM budget_reservations WHERE budget_id=? ORDER BY rowid', (self.id,))]
        for entry in entries:
            entry['detail'] = json.loads(entry['detail'])
        return {'budget_id': self.id, 'run_id': self.run_id, 'limits': self.limits.model_dump(mode='json'),
                'stopped_reason': row['stopped_reason'], 'reservations': entries,
                'known_tokens': sum(entry['actual_tokens'] or 0 for entry in entries),
                'token_target_exceeded': sum(entry['actual_tokens'] or 0 for entry in entries) > self.limits.target_total_tokens,
                'unknown_token_reservations': sum(entry['estimated_tokens'] for entry in entries if entry['kind'] == 'model' and entry['actual_tokens'] is None),
                'unknown_model_calls': sum(entry['kind'] == 'model' and entry['state'] != 'known' for entry in entries),
                'admission_tokens': sum(entry['actual_tokens'] if entry['actual_tokens'] is not None else entry['estimated_tokens'] for entry in entries),
                'external_calls': sum(entry['kind'] in ('model', 'search_request', 'public_http', 'public_dns') for entry in entries)}
