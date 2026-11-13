"""Behavioral tests for the append-only experiment database (synthetic data only)."""
import dataclasses
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rsi_kernel_agent.contracts import (
    AnalysisBundle, Candidate, CandidateIdentity, Decision, EvaluationBundle,
    EvaluationContext, Evidence, ParentCandidate, Plan, ProfileBundle, Run,
    RunEvent, Strategy, Task, Workload, ValidationError,
)
from rsi_kernel_agent.store import ExperimentStore, StoreError, ConflictError


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = ExperimentStore(self.root / 'db.sqlite', self.root / 'artifacts')
        self.identity = CandidateIdentity(*[hashlib.sha256(x).hexdigest() for x in (b'src', b'cfg', b'build')])
        for record in (
            Task('task', 'Synthetic task', 'v1'),
            Workload('workload', 'task', {'shape': [2, 2]}),
            EvaluationContext('ctx', 'task', 'workload', {'device': 'CPU fixture'}, {'timer': 'fixture-v1'}, 'synthetic'),
            Strategy('A', 'task', 'strategy A'), Plan('plan', 'A', 'first plan'),
            ParentCandidate('baseline', 'task', self.identity),
            Candidate('candidate', 'plan', 'baseline', self.identity),
            Run('run', 'candidate', 'ctx'),
        ):
            self.store.append(record, record.id)

    def append(self, record):
        return self.store.append(record, record.id)

    def succeed(self, run='run'):
        self.store.transition(RunEvent(run + '-start', run, 'running', 'active', 'start'), None, run + '-start')
        self.store.transition(RunEvent(run + '-done', run, 'succeeded', 'finished', 'done'), run + '-start', run + '-done')

    def evaluation(self, eid='eval', latency=10, run='run', **changes):
        blob = self.store.artifacts.put(b'synthetic fixture timing')
        ev = Evidence(eid + '-timing', run, 'measured_timing', 'available', 'completed', 'supported',
                      latency, 'ms', (blob,), (), {'provenance': 'synthetic'})
        self.append(ev)
        bundle = EvaluationBundle(eid, run, 'passed', 'valid', latency, True, (ev.id,))
        self.append(dataclasses.replace(bundle, **changes))
        return eid

    def test_idempotency_conflicts_and_detached_payloads(self):
        spec = {'shape': [3]}
        item = Workload('other', 'task', spec)
        self.assertEqual(self.append(item), 'other')
        self.assertEqual(self.append(item), 'other')
        spec['shape'].append(4)
        self.assertEqual(self.store.get('other').spec, {'shape': [3]})
        with self.assertRaises(ConflictError):
            self.append(item)
        with self.assertRaises(ConflictError):
            self.store.append(Task('different', 'Different', 'v1'), 'other')
        self.assertEqual(sum(x.id == 'other' for x in self.store.history('task')), 1)

    def test_typed_relations_and_cross_task_context_rejected(self):
        with self.assertRaises(StoreError):
            self.append(Plan('badplan', 'task', 'wrong type'))
        self.append(Task('task2', 'Other', 'v1'))
        with self.assertRaises(StoreError):
            self.append(EvaluationContext('badctx', 'task2', 'workload', {'cpu': 1}, {'timer': 1}, 'synthetic'))
        self.append(Strategy('B2', 'task2', 'B'))
        self.append(Plan('plan2', 'B2', 'other'))
        with self.assertRaises(StoreError):
            self.append(Candidate('badcandidate', 'plan2', 'baseline', self.identity))

    def test_internal_parent_identity_is_exact_and_immutable(self):
        other = dataclasses.replace(self.identity, source_digest='0' * 64)
        with self.assertRaises(StoreError):
            self.append(ParentCandidate('bad-parent', 'task', other, 'candidate'))
        self.append(ParentCandidate('parent', 'task', self.identity, 'candidate'))
        self.append(Candidate('repair', 'plan', 'parent', other, 'repair'))
        self.assertEqual(self.store.get('repair').parent_id, 'parent')
        with closing(sqlite3.connect(self.root / 'db.sqlite')) as con:
            with self.assertRaises(sqlite3.DatabaseError):
                con.execute("UPDATE records SET payload='{}' WHERE id='candidate'")
            with self.assertRaises(sqlite3.DatabaseError):
                con.execute("DELETE FROM records WHERE id='candidate'")

    def test_status_events_cas_deferred_and_terminal(self):
        deferred = RunEvent('defer', 'run', 'not_run', 'deferred', 'budget')
        self.assertEqual(self.store.transition(deferred, None, 'defer'), 'defer')
        self.assertEqual(self.store.transition(deferred, None, 'defer'), 'defer')
        self.assertEqual(self.store.run_state('run')['execution_status'], 'not_run')
        with self.assertRaises(ConflictError):
            self.store.transition(RunEvent('stale', 'run', 'running', 'active', 'start'), None, 'stale')
        with self.assertRaises(StoreError):
            self.store.transition(RunEvent('skip', 'run', 'succeeded', 'finished', 'skip'), 'defer', 'skip')
        self.store.transition(RunEvent('ready', 'run', 'not_run', 'ready', 'resume'), 'defer', 'ready')
        self.store.transition(RunEvent('start', 'run', 'running', 'active', 'start'), 'ready', 'start')
        self.store.transition(RunEvent('fail', 'run', 'failed', 'finished', 'compile failure'), 'start', 'fail')
        with self.assertRaises(StoreError):
            self.store.transition(RunEvent('restart', 'run', 'running', 'active', 'retry'), 'fail', 'restart')
        with self.assertRaises(StoreError):
            self.append(RunEvent('bypass', 'run', 'running', 'active', 'direct'))

    def test_evidence_binding_missing_artifacts_and_provenance(self):
        ref = self.store.artifacts.put(b'raw')
        evidence = Evidence('e', 'run', 'measured_timing', 'available', 'completed', 'supported',
                            1, 'ms', (ref,), (), {'provenance': 'real'})
        with self.assertRaises(StoreError):
            self.append(evidence)
        self.append(dataclasses.replace(evidence, source={'provenance': 'synthetic'}))
        self.append(Run('run2', 'candidate', 'ctx'))
        with self.assertRaises(StoreError):
            self.append(EvaluationBundle('cross-run', 'run2', 'passed', 'valid', 1, True, ('e',)))
        with self.assertRaises(StoreError):
            self.append(ProfileBundle('profile', 'run2', ('e',), 'available'))
        self.append(ProfileBundle('profile', 'run', ('e',), 'available'))
        self.append(AnalysisBundle('analysis', 'run', ('e',), ('profile',), 'hypothesis', 'fixture hypothesis'))
        self.store.artifacts.path_for(ref).write_bytes(b'tampered')
        with self.assertRaises((StoreError, ValidationError)):
            self.append(dataclasses.replace(evidence, id='corrupt', source={'provenance': 'synthetic'}))

    def test_promotion_requires_success_and_supported_timing(self):
        eid = self.evaluation()
        rejected = self.store.consider(eid, 'pre-success')
        self.assertFalse(rejected.details['eligible'])
        self.assertIsNone(self.store.best('task', 'ctx'))
        self.succeed()
        accepted = self.store.consider(eid, 'promote')
        self.assertTrue(accepted.details['task_promoted'])
        self.assertEqual(accepted, self.store.consider(eid, 'promote'))
        self.assertEqual(self.store.best('task', 'ctx').id, eid)
        self.append(EvaluationBundle('unsupported', 'run', 'passed', 'valid', 0.1, True, ('eval-timing',)))
        self.assertFalse(self.store.consider('unsupported', 'unsupported-promote').details['eligible'])
        self.assertEqual(self.store.best('task', 'ctx').id, eid)
        with self.assertRaises(StoreError):
            self.append(Decision('fake', 'task', 'promotion', 'eval', 'bypass', {}))

    def test_corrupted_artifact_cannot_be_promoted(self):
        self.succeed()
        self.evaluation()
        ref = self.store.get('eval-timing').artifacts[0]
        self.store.artifacts.path_for(ref).write_bytes(b'changed after evidence append')
        decision = self.store.consider('eval', 'corrupt-promote')
        self.assertFalse(decision.details['eligible'])
        self.assertIsNone(self.store.best('task', 'ctx'))

    def test_corrupt_ancestor_of_derived_timing_cannot_be_promoted(self):
        self.succeed()
        raw = self.store.artifacts.put(b'raw synthetic sample')
        summary = self.store.artifacts.put(b'synthetic calculation summary')
        self.append(Evidence('raw', 'run', 'measured_timing', 'available', 'completed', 'supported',
                             10, 'ms', (raw,), (), {'provenance': 'synthetic'}))
        for name, parent in (('derived1', 'raw'), ('derived2', 'derived1')):
            self.append(Evidence(name, 'run', 'derived_measurement', 'available', 'completed', 'supported',
                                 10, 'ms', (summary,), (parent,), {'provenance': 'synthetic', 'formula': 'identity'}))
        self.append(EvaluationBundle('derived-eval', 'run', 'passed', 'valid', 10, True, ('derived2',)))
        self.store.artifacts.path_for(raw).write_bytes(b'corrupt upstream only')
        decision = self.store.consider('derived-eval', 'derived-promote')
        self.assertFalse(decision.details['eligible'])
        self.assertIsNone(self.store.best('task', 'ctx'))

    def test_frontier_is_separate_and_context_bound(self):
        self.store.set_frontier('candidate', 'ctx', True, 'explore', 'frontier-add')
        self.assertEqual(self.store.frontier('task', 'ctx'), ['candidate'])
        self.assertIsNone(self.store.best('task', 'ctx'))
        self.store.set_frontier('candidate', 'ctx', False, 'pause', 'frontier-remove')
        self.assertEqual(self.store.frontier('task', 'ctx'), [])
        decisions = [x for x in self.store.history('task') if isinstance(x, Decision)]
        self.assertEqual(len(decisions), 2)

    def test_snapshot_is_versioned_complete_and_frozen(self):
        snapshot = self.store.create_snapshot('task', 'snap1')
        page = self.store.snapshot_page(snapshot['id'], limit=2)
        self.assertFalse(page['complete'])
        self.assertEqual(page['next_offset'], 2)
        self.append(Plan('later', 'A', 'later'))
        full = self.store.snapshot_page(snapshot['id'], limit=1000)
        self.assertTrue(full['complete'])
        self.assertNotIn('later', [x.id for x in full['records']])
        self.assertEqual(len(full['records']), snapshot['record_count'])
        tail = self.store.snapshot_page(snapshot['id'], offset=2, limit=1000)
        self.assertFalse(tail['complete'])
        second = self.store.create_snapshot('task', 'snap2')
        self.assertEqual(second['version'], snapshot['version'] + 1)
        self.assertEqual(self.store.create_snapshot('task', 'snap1'), snapshot)
        with self.assertRaises(StoreError):
            self.store.snapshot_page(snapshot['id'], offset=-1)

    def test_schema_mismatch_rejected(self):
        path = self.root / 'future.sqlite'
        with closing(sqlite3.connect(path)) as con:
            con.execute('PRAGMA user_version=900')
        with self.assertRaises(StoreError):
            ExperimentStore(path, self.root / 'other-artifacts')

    def test_unrelated_version_one_and_damaged_schema_rejected(self):
        path = self.root / 'unrelated.sqlite'
        with closing(sqlite3.connect(path)) as con:
            con.execute('PRAGMA user_version=1')
            con.execute('CREATE TABLE unrelated (id TEXT)')
            con.commit()
        with self.assertRaises(StoreError):
            ExperimentStore(path, self.root / 'unrelated-artifacts')
        with closing(sqlite3.connect(self.root / 'db.sqlite')) as con:
            con.execute('DROP TRIGGER immutable_records_UPDATE')
            con.commit()
        with self.assertRaises(StoreError):
            ExperimentStore(self.root / 'db.sqlite', self.root / 'artifacts')

    def test_concurrent_first_open_initializes_one_usable_database(self):
        # First-open contention differs from concurrent writes to a ready DB.
        for attempt in range(8):
            path = self.root / f'fresh-{attempt}.sqlite'
            barrier = threading.Barrier(8)

            def create_store(index):
                barrier.wait(timeout=10)
                store = ExperimentStore(path, self.root / 'fresh-artifacts')
                return store.append(Task('shared', 'Shared synthetic task', 'v1'), 'shared-key')

            with ThreadPoolExecutor(max_workers=8) as executor:
                self.assertEqual(list(executor.map(create_store, range(8))), ['shared'] * 8)
            self.assertEqual(len(ExperimentStore(path, self.root / 'fresh-artifacts').history('shared')), 1)


if __name__ == '__main__':
    unittest.main()
