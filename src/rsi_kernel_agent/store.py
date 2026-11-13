"""Local, append-only experiment metadata; no execution or scheduling backend.

SQLite serializes writers on one machine. Public mutations are transactional and
idempotent. Artifact publication precedes metadata; unused blobs are retained.
Evaluation verdicts are attestations from a trusted producer, not measurements
performed or correctness proofs independently established by this module.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid

from .artifacts import ArtifactStore
from .contracts import (
    AnalysisBundle, Candidate, Decision, EvaluationBundle, EvaluationContext,
    Evidence, ParentCandidate, Plan, ProfileBundle, Run, RunEvent, Strategy,
    Task, Workload, SCHEMA_VERSION, ValidationError, canonical_json,
    record_from_dict, record_to_dict,
)


class StoreError(ValueError):
    """Invalid relation, state, or database version."""


class ConflictError(StoreError):
    """An immutable identity, request key, or expected state conflicts."""


_SCHEMA = (
    """CREATE TABLE records (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL, task_id TEXT NOT NULL REFERENCES records(id),
        strategy_id TEXT REFERENCES records(id), payload TEXT NOT NULL,
        digest TEXT NOT NULL, schema_version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))""",
    'CREATE INDEX records_task ON records(task_id,seq)',
    """CREATE TABLE links (
        record_id TEXT NOT NULL REFERENCES records(id), field TEXT NOT NULL,
        target_id TEXT NOT NULL REFERENCES records(id),
        PRIMARY KEY(record_id,field,target_id))""",
    'CREATE INDEX links_target ON links(target_id,field)',
    """CREATE TABLE requests (
        key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, response TEXT NOT NULL)""",
    """CREATE TABLE best (
        task_id TEXT NOT NULL REFERENCES records(id),
        context_id TEXT NOT NULL REFERENCES records(id), scope TEXT NOT NULL,
        evaluation_id TEXT NOT NULL REFERENCES records(id), latency_ms REAL NOT NULL,
        PRIMARY KEY(task_id,context_id,scope))""",
    """CREATE TABLE frontier (
        task_id TEXT NOT NULL REFERENCES records(id),
        context_id TEXT NOT NULL REFERENCES records(id),
        candidate_id TEXT NOT NULL REFERENCES records(id),
        PRIMARY KEY(task_id,context_id,candidate_id))""",
    """CREATE TABLE snapshots (
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES records(id),
        version INTEGER NOT NULL, body TEXT NOT NULL, digest TEXT NOT NULL,
        UNIQUE(task_id,version))""",
) + tuple(
    f"CREATE TRIGGER immutable_{table}_{operation} BEFORE {operation} ON {table} "
    "BEGIN SELECT RAISE(ABORT, 'append-only table'); END"
    for table in ('records', 'links', 'requests', 'snapshots')
    for operation in ('UPDATE', 'DELETE')
)


def _digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class ExperimentStore:
    def __init__(self, db_path, artifact_root):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifacts = ArtifactStore(artifact_root)
        with self._connection() as con:
            version = con.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise StoreError(f'Unsupported database schema {version}')
            if version == SCHEMA_VERSION:
                self._check_schema(con)
            # Concurrent first opens can fail here without invoking SQLite's
            # busy handler. Retry only lock errors outside a transaction.
            deadline = time.monotonic() + 30
            while True:
                try:
                    con.execute('PRAGMA journal_mode=WAL')
                    break
                except sqlite3.OperationalError as exc:
                    code = getattr(exc, 'sqlite_errorcode', 0) & 0xff
                    if code not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.01)
            con.execute('BEGIN IMMEDIATE')
            # A second constructor may have initialized while we waited.
            version = con.execute('PRAGMA user_version').fetchone()[0]
            if version == 0:
                if con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                    raise StoreError('Refusing to initialize an unrelated database')
                for statement in _SCHEMA:
                    con.execute(statement)
                con.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
            elif version != SCHEMA_VERSION:
                raise StoreError(f'Unsupported database schema {version}')
            else:
                self._check_schema(con)
            con.commit()

    @staticmethod
    def _check_schema(con):
        for statement in _SCHEMA:
            name = statement.split()[2]
            row = con.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()
            if row is None or ' '.join(row[0].split()) != ' '.join(statement.split()):
                raise StoreError(f'Database schema is unrelated or damaged: {name}')

    @contextmanager
    def _connection(self):
        con = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        try:
            con.execute('PRAGMA foreign_keys=ON')
            con.execute('PRAGMA busy_timeout=30000')
            con.execute('PRAGMA synchronous=FULL')
            yield con
        finally:
            if con.in_transaction:
                con.rollback()
            con.close()

    def _write(self, operation, data, key, action):
        if not isinstance(key, str) or not key.strip() or len(key) > 256:
            raise StoreError('Idempotency key must be nonempty text of at most 256 characters')
        fingerprint = _digest(canonical_json({'operation': operation, 'data': data}))
        with self._connection() as con:
            con.execute('BEGIN IMMEDIATE')
            prior = con.execute('SELECT * FROM requests WHERE key=?', (key,)).fetchone()
            if prior:
                if prior['fingerprint'] != fingerprint:
                    raise ConflictError('Idempotency key reused with different request')
                return json.loads(prior['response'])
            result = action(con)
            con.execute('INSERT INTO requests VALUES (?,?,?)', (key, fingerprint, canonical_json(result)))
            con.commit()
            return result

    def _row(self, con, record_id, expected=None):
        row = con.execute('SELECT * FROM records WHERE id=?', (record_id,)).fetchone()
        if row is None:
            raise StoreError(f'Missing record: {record_id}')
        if expected and row['kind'] not in tuple(t.__name__ for t in expected):
            raise StoreError(f'Wrong record type for {record_id}: {row["kind"]}')
        return row

    @staticmethod
    def _decode(row):
        if row['schema_version'] != SCHEMA_VERSION or _digest(row['payload']) != row['digest']:
            raise StoreError('Record version or digest mismatch')
        return record_from_dict(row['kind'], json.loads(row['payload']))

    def _get(self, con, record_id, expected=None):
        return self._decode(self._row(con, record_id, expected))

    def get(self, record_id):
        with self._connection() as con:
            return self._get(con, record_id)

    def history(self, task_id):
        with self._connection() as con:
            self._row(con, task_id, (Task,))
            return [self._decode(row) for row in con.execute('SELECT * FROM records WHERE task_id=? ORDER BY seq', (task_id,))]

    def _relations(self, con, record):
        links = []
        rows = {}

        def ref(field, target, types):
            row = self._row(con, target, types)
            links.append((record.id, field, target))
            rows[field] = row
            return self._decode(row)

        if isinstance(record, Task):
            return record.id, None, links
        if isinstance(record, (Workload, Strategy, EvaluationContext, ParentCandidate, Decision)):
            ref('task_id', record.task_id, (Task,))
            task_id, strategy_id = record.task_id, None
            if isinstance(record, Strategy):
                strategy_id = record.id
            elif isinstance(record, EvaluationContext):
                ref('workload_id', record.workload_id, (Workload,))
            elif isinstance(record, ParentCandidate) and record.candidate_id is not None:
                parent = ref('candidate_id', record.candidate_id, (Candidate,))
                if parent.identity != record.identity:
                    raise StoreError('ParentCandidate identity differs from exact candidate')
            elif isinstance(record, Decision):
                types = {'promotion': (EvaluationBundle,), 'frontier': (Candidate,),
                         'paused': (Strategy,), 'resumed': (Strategy,),
                         'deferred': (Plan, Candidate, Run)}.get(record.kind)
                row = self._row(con, record.target_id, types)
                links.append((record.id, 'target_id', record.target_id))
                rows['target_id'] = row
                strategy_id = row['strategy_id']
        else:
            if isinstance(record, Plan):
                ref('strategy_id', record.strategy_id, (Strategy,))
                anchor = rows['strategy_id']
            elif isinstance(record, Candidate):
                ref('plan_id', record.plan_id, (Plan,))
                ref('parent_id', record.parent_id, (ParentCandidate,))
                anchor = rows['plan_id']
            elif isinstance(record, Run):
                ref('candidate_id', record.candidate_id, (Candidate,))
                ref('context_id', record.context_id, (EvaluationContext,))
                anchor = rows['candidate_id']
            else:
                run = ref('run_id', record.run_id, (Run,))
                anchor = rows['run_id']
                if isinstance(record, Evidence):
                    context = self._get(con, run.context_id, (EvaluationContext,))
                    if record.source.get('provenance') != context.provenance:
                        raise StoreError('Evidence provenance must match run context')
                    for artifact in record.artifacts:
                        try:
                            self.artifacts.verify(artifact)
                        except (ValueError, OSError) as exc:
                            raise StoreError(f'Invalid evidence artifact: {exc}') from exc
                    for evidence_id in record.input_ids:
                        evidence = ref('input_ids', evidence_id, (Evidence,))
                        input_run = self._get(con, evidence.run_id, (Run,))
                        if input_run.context_id != run.context_id:
                            raise StoreError('Derived evidence input has different context')
                elif isinstance(record, (EvaluationBundle, ProfileBundle, AnalysisBundle)):
                    for evidence_id in record.evidence_ids:
                        evidence = ref('evidence_ids', evidence_id, (Evidence,))
                        if evidence.run_id != record.run_id:
                            raise StoreError('Bundle evidence must belong to the same run')
                    if isinstance(record, AnalysisBundle):
                        for profile_id in record.profile_ids:
                            profile = ref('profile_ids', profile_id, (ProfileBundle,))
                            if profile.run_id != record.run_id:
                                raise StoreError('Analysis profile must belong to the same run')
            task_id, strategy_id = anchor['task_id'], anchor['strategy_id']
        if any(row['task_id'] != task_id for row in rows.values()):
            raise StoreError('Cross-task relation is not allowed')
        return task_id, strategy_id, links

    def _insert(self, con, record):
        payload = canonical_json(record_to_dict(record))
        kind = type(record).__name__
        prior = con.execute('SELECT kind,payload FROM records WHERE id=?', (record.id,)).fetchone()
        if prior:
            if prior['kind'] == kind and prior['payload'] == payload:
                return record.id
            raise ConflictError(f'Immutable record ID reused: {record.id}')
        task_id, strategy_id, links = self._relations(con, record)
        con.execute('INSERT INTO records(id,kind,task_id,strategy_id,payload,digest) VALUES (?,?,?,?,?,?)',
                    (record.id, kind, task_id, strategy_id, payload, _digest(payload)))
        con.executemany('INSERT INTO links VALUES (?,?,?)', links)
        return record.id

    def append(self, record, key):
        payload = record_to_dict(record)
        # Detach caller-owned mutable dictionaries before the transaction.
        record = record_from_dict(type(record).__name__, payload)
        if isinstance(record, RunEvent) or (isinstance(record, Decision) and record.kind in ('promotion', 'frontier')):
            raise StoreError('Use the validated state/projection operation')
        return self._write('append', {'kind': type(record).__name__, 'payload': payload}, key,
                           lambda con: self._insert(con, record))

    def _state(self, con, run_id):
        self._row(con, run_id, (Run,))
        row = con.execute("SELECT r.* FROM records r JOIN links l ON r.id=l.record_id "
                          "WHERE l.field='run_id' AND l.target_id=? AND r.kind='RunEvent' ORDER BY r.seq DESC LIMIT 1", (run_id,)).fetchone()
        if row is None:
            return {'execution_status': 'not_run', 'scheduling_status': 'ready', 'event_id': None}
        event = self._decode(row)
        return {'execution_status': event.execution_status, 'scheduling_status': event.scheduling_status, 'event_id': event.id}

    def run_state(self, run_id):
        with self._connection() as con:
            return self._state(con, run_id)

    def transition(self, event, expected_event_id, key):
        payload = record_to_dict(event)
        if not isinstance(event, RunEvent):
            raise StoreError('transition requires RunEvent')

        def action(con):
            if con.execute('SELECT 1 FROM records WHERE id=?', (event.id,)).fetchone():
                raise ConflictError('RunEvent ID already exists; retry with the original idempotency key')
            state = self._state(con, event.run_id)
            if state['event_id'] != expected_event_id:
                raise ConflictError('Run state changed; reload before transition')
            old = (state['execution_status'], state['scheduling_status'])
            new = (event.execution_status, event.scheduling_status)
            allowed = {
                ('not_run', 'ready'): {('not_run', 'deferred'), ('running', 'active')},
                ('not_run', 'deferred'): {('not_run', 'ready'), ('running', 'active')},
                ('running', 'active'): {(x, 'finished') for x in ('succeeded', 'failed', 'invalid', 'incomplete')},
            }
            if new not in allowed.get(old, set()):
                raise StoreError(f'Invalid run transition {old} -> {new}; retries use a new Run')
            return self._insert(con, event)

        return self._write('transition', {'event': payload, 'expected_event_id': expected_event_id}, key, action)

    def _eligibility(self, con, evaluation):
        state = self._state(con, evaluation.run_id)
        if state['execution_status'] != 'succeeded':
            return False, 'run_not_succeeded'
        if evaluation.correctness != 'passed' or evaluation.timing_validity != 'valid' or not evaluation.confirmed:
            return False, 'evaluation_not_confirmed_valid_and_correct'
        pending = list(evaluation.evidence_ids)
        evidence_by_id = {}
        while pending:
            evidence_id = pending.pop()
            if evidence_id in evidence_by_id:
                continue
            evidence = self._get(con, evidence_id, (Evidence,))
            evidence_by_id[evidence_id] = evidence
            pending.extend(evidence.input_ids)
            # Derived evidence depends on the entire immutable input graph.
            for artifact in evidence.artifacts:
                try:
                    self.artifacts.verify(artifact)
                except (ValueError, OSError):
                    return False, 'artifact_missing_or_corrupt'
        supported = False
        for evidence_id in evaluation.evidence_ids:
            evidence = evidence_by_id[evidence_id]
            if (evidence.run_id == evaluation.run_id and evidence.availability == 'available'
                    and evidence.request_status == 'completed' and evidence.claim_status == 'supported'
                    and evidence.evidence_kind in ('measured_timing', 'trace_derived', 'derived_measurement')
                    and evidence.unit == 'ms' and type(evidence.value) in (int, float)
                    and evidence.value == evaluation.latency_ms and evidence.artifacts):
                supported = True
        return (True, 'confirmed_by_producer') if supported else (False, 'missing_bound_timing_evidence')

    def consider(self, evaluation_id, key):
        def action(con):
            evaluation = self._get(con, evaluation_id, (EvaluationBundle,))
            run = self._get(con, evaluation.run_id, (Run,))
            row = self._row(con, run.id)
            eligible, reason = self._eligibility(con, evaluation)
            updates = {'branch_promoted': False, 'task_promoted': False}
            if eligible:
                for scope, flag in (('strategy:' + row['strategy_id'], 'branch_promoted'), ('task', 'task_promoted')):
                    prior = con.execute('SELECT latency_ms FROM best WHERE task_id=? AND context_id=? AND scope=?',
                                        (row['task_id'], run.context_id, scope)).fetchone()
                    if prior is None or evaluation.latency_ms < prior['latency_ms']:
                        con.execute('INSERT INTO best VALUES (?,?,?,?,?) ON CONFLICT(task_id,context_id,scope) '
                                    'DO UPDATE SET evaluation_id=excluded.evaluation_id,latency_ms=excluded.latency_ms',
                                    (row['task_id'], run.context_id, scope, evaluation.id, evaluation.latency_ms))
                        updates[flag] = True
            details = {'eligible': eligible, 'reason': reason, 'context_id': run.context_id, **updates}
            decision = Decision('decision:' + uuid.uuid4().hex, row['task_id'], 'promotion', evaluation.id, reason, details)
            return self._insert(con, decision)

        return self.get(self._write('consider', {'evaluation_id': evaluation_id}, key, action))

    def _validate_scope(self, con, task_id, context_id, strategy_id=None):
        self._row(con, task_id, (Task,))
        if self._row(con, context_id, (EvaluationContext,))['task_id'] != task_id:
            raise StoreError('Context belongs to another task')
        if strategy_id is not None and self._row(con, strategy_id, (Strategy,))['task_id'] != task_id:
            raise StoreError('Strategy belongs to another task')

    def best(self, task_id, context_id, strategy_id=None):
        with self._connection() as con:
            self._validate_scope(con, task_id, context_id, strategy_id)
            scope = 'task' if strategy_id is None else 'strategy:' + strategy_id
            row = con.execute('SELECT evaluation_id FROM best WHERE task_id=? AND context_id=? AND scope=?',
                              (task_id, context_id, scope)).fetchone()
            return self._get(con, row['evaluation_id']) if row else None

    def set_frontier(self, candidate_id, context_id, included, reason, key):
        if type(included) is not bool or not isinstance(reason, str) or not reason.strip():
            raise StoreError('Frontier decision requires a boolean and a reason')

        def action(con):
            candidate = self._row(con, candidate_id, (Candidate,))
            self._validate_scope(con, candidate['task_id'], context_id)
            args = (candidate['task_id'], context_id, candidate_id)
            if included:
                con.execute('INSERT OR IGNORE INTO frontier VALUES (?,?,?)', args)
            else:
                con.execute('DELETE FROM frontier WHERE task_id=? AND context_id=? AND candidate_id=?', args)
            decision = Decision('decision:' + uuid.uuid4().hex, candidate['task_id'], 'frontier', candidate_id, reason,
                                {'context_id': context_id, 'included': included})
            return self._insert(con, decision)

        data = {'candidate_id': candidate_id, 'context_id': context_id, 'included': included, 'reason': reason}
        return self.get(self._write('frontier', data, key, action))

    def frontier(self, task_id, context_id):
        with self._connection() as con:
            self._validate_scope(con, task_id, context_id)
            return [row[0] for row in con.execute('SELECT candidate_id FROM frontier WHERE task_id=? AND context_id=? ORDER BY candidate_id',
                                                 (task_id, context_id))]

    def create_snapshot(self, task_id, key):
        def action(con):
            self._row(con, task_id, (Task,))
            rows = list(con.execute('SELECT * FROM records WHERE task_id=? ORDER BY seq', (task_id,)))
            records = []
            counts = {row['id']: 0 for row in rows if row['kind'] == 'Strategy'}
            for row in rows:
                record = self._decode(row)
                records.append({'seq': row['seq'], 'kind': row['kind'], 'payload': record_to_dict(record)})
                if row['strategy_id'] is not None:
                    counts[row['strategy_id']] += 1
            version = con.execute('SELECT COALESCE(MAX(version),0)+1 FROM snapshots WHERE task_id=?', (task_id,)).fetchone()[0]
            metadata = {'id': 'snapshot:' + uuid.uuid4().hex, 'schema_version': SCHEMA_VERSION,
                        'task_id': task_id, 'version': version, 'high_watermark': rows[-1]['seq'],
                        'record_count': len(records), 'strategy_counts': counts}
            projections = {table: [dict(row) for row in con.execute(f'SELECT * FROM {table} WHERE task_id=? ORDER BY 1,2,3', (task_id,))]
                           for table in ('best', 'frontier')}
            body = canonical_json({'metadata': metadata, 'records': records, 'projections': projections})
            digest = _digest(body)
            con.execute('INSERT INTO snapshots VALUES (?,?,?,?,?)', (metadata['id'], task_id, version, body, digest))
            return {**metadata, 'digest': digest}

        return self._write('snapshot', {'task_id': task_id}, key, action)

    def snapshot_page(self, snapshot_id, offset=0, limit=100):
        if type(offset) is not int or offset < 0 or type(limit) is not int or limit <= 0:
            raise StoreError('Snapshot pagination requires nonnegative offset and positive limit')
        with self._connection() as con:
            row = con.execute('SELECT * FROM snapshots WHERE id=?', (snapshot_id,)).fetchone()
            if row is None:
                raise StoreError('Unknown snapshot')
            if _digest(row['body']) != row['digest']:
                raise StoreError('Snapshot digest mismatch')
            body = json.loads(row['body'])
        metadata = body['metadata']
        total = len(body['records'])
        if total != metadata['record_count'] or metadata['schema_version'] != SCHEMA_VERSION:
            raise StoreError('Snapshot version or count mismatch')
        if offset > total:
            raise StoreError('Offset exceeds snapshot coverage')
        end = min(offset + limit, total)
        return {'snapshot': {**metadata, 'digest': row['digest']},
                'records': [record_from_dict(x['kind'], x['payload']) for x in body['records'][offset:end]],
                'offset': offset, 'next_offset': end if end < total else None,
                'complete': offset == 0 and end == total, 'total': total,
                'projections': body['projections']}
