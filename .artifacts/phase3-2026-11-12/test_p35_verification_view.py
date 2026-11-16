"""Synthetic temporary Stores only; no production database, artifacts or bridge."""
from contextlib import closing
from dataclasses import replace
import hashlib
import importlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / 'src')]
from rsi_kernel_agent.contracts import (
    Candidate, CandidateIdentity, EvaluationContext, ParentCandidate, Plan,
    Run, RunEvent, Strategy, Task, Workload,
)
from rsi_kernel_agent.memory import MemoryReader
from rsi_kernel_agent.store import ExperimentStore, StoreError


class CountingStore(ExperimentStore):
    """Count real snapshot reader calls, including its original digest checks."""
    def __init__(self, *args):
        self.snapshot_calls = []
        super().__init__(*args)

    def snapshot_page(self, snapshot_id, offset=0, limit=100):
        self.snapshot_calls.append((snapshot_id, offset, limit))
        return super().snapshot_page(snapshot_id, offset, limit)


class VerificationViewTests(unittest.TestCase):
    def setUp(self):
        try:
            self.view_type = importlib.import_module('p35_verification_view').VerificationView
        except ImportError:
            self.fail('Independent VerificationView helper is not implemented')
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = CountingStore(self.root / 'store.sqlite3', self.root / 'artifacts')
        identity = CandidateIdentity(*[hashlib.sha256(x).hexdigest() for x in (b'source', b'config', b'build')])
        for record in (
            Task('task', 'Temporary snapshot cache fixture', 'v1'),
            Workload('workload', 'task', {'shape': [2, 2]}),
            EvaluationContext('context', 'task', 'workload', {'device': 'CPU fixture'}, {'timer': 'fixture'}, 'synthetic'),
            Strategy('A', 'task', 'A fixture'),
            Plan('plan', 'A', 'Original fixture plan'),
            ParentCandidate('parent', 'task', identity),
            Candidate('candidate', 'plan', 'parent', identity),
            Run('run', 'candidate', 'context'),
        ):
            self.store.append(record, record.id)
        self.snapshot = self.store.create_snapshot('task', 'original-snapshot')
        self.view = self.view_type(self.store)

    def test_all_page_boundaries_equal_the_original_reader(self):
        # Catches sliced page flags/offsets diverging from the original API.
        sid = self.snapshot['id']
        for offset, limit in ((0, 1), (0, 100), (1, 2), (7, 10), (8, 1)):
            with self.subTest(offset=offset, limit=limit):
                expected = self.store.snapshot_page(sid, offset, limit)
                self.assertEqual(self.view.snapshot_page(sid, offset, limit), expected)

    def test_memory_reader_reuses_complete_verified_snapshot_after_two_reads(self):
        # Catches re-parsing the whole frozen body for each Memory page.
        sid = self.snapshot['id']
        expected = MemoryReader(self.store).open_snapshot(sid, page_size=3)
        self.store.snapshot_calls.clear()
        actual = MemoryReader(self.view).open_snapshot(sid, page_size=1)
        self.assertEqual(actual.snapshot, expected.snapshot)
        self.assertEqual(actual.histories(('A',)), expected.histories(('A',)))
        self.assertEqual(self.store.snapshot_calls, [(sid, 0, 1), (sid, 0, 8)])
        MemoryReader(self.view).open_snapshot(sid, page_size=2)
        self.assertEqual(len(self.store.snapshot_calls), 2)

    def test_invalid_pagination_and_unknown_snapshot_keep_original_errors(self):
        # Catches bool/int acceptance, invalid offsets and failure caching.
        sid = self.snapshot['id']
        for offset, limit in ((-1, 1), (False, 1), (1.0, 1), (0, 0), (0, True), (0, 1.0), (9, 1)):
            with self.subTest(offset=offset, limit=limit), self.assertRaises(StoreError):
                self.view.snapshot_page(sid, offset, limit)
        for _ in range(2):
            with self.assertRaises(StoreError):
                self.view.snapshot_page('missing-snapshot')

    def test_mutating_nested_output_cannot_change_the_cached_snapshot(self):
        # Frozen dataclasses still contain mutable dicts; callers must detach.
        sid = self.snapshot['id']
        expected = self.store.snapshot_page(sid, limit=100)
        page = self.view.snapshot_page(sid, limit=100)
        page['snapshot']['strategy_counts']['A'] = 999
        page['projections']['best'].append({'fake': True})
        workload = next(record for record in page['records'] if record.id == 'workload')
        workload.spec['shape'].append(999)
        page['records'].clear()
        self.assertEqual(self.view.snapshot_page(sid, limit=100), expected)

    def test_live_append_and_run_state_are_not_cached(self):
        # Catches accidentally routing live history/get/run_state through cache.
        sid = self.snapshot['id']
        frozen = self.view.snapshot_page(sid, limit=100)
        self.assertEqual(len(self.view.history('task')), 8)
        initial_state = self.view.run_state('run')
        self.view.append(Plan('new-plan', 'A', 'Added after frozen snapshot'), 'new-plan')
        self.store.transition(RunEvent('start', 'run', 'running', 'active', 'Temporary fixture'), None, 'start')
        self.assertEqual(self.view.get('new-plan').description, 'Added after frozen snapshot')
        self.assertEqual(len(self.view.history('task')), 10)
        self.assertNotEqual(self.view.run_state('run'), initial_state)
        self.assertEqual(self.view.run_state('run')['execution_status'], 'running')
        self.assertEqual(self.view.snapshot_page(sid, limit=100), frozen)
        self.assertIs(self.view.artifacts, self.store.artifacts)

    def test_different_snapshot_versions_remain_isolated(self):
        # Catches cache keys based on task or version alone.
        old = self.view.snapshot_page(self.snapshot['id'], limit=100)
        self.store.append(Plan('later', 'A', 'New snapshot only'), 'later')
        newer = self.store.create_snapshot('task', 'new-snapshot')
        new = self.view.snapshot_page(newer['id'], limit=100)
        self.assertEqual(new['total'], 9)
        self.assertEqual(old['total'], 8)
        self.assertIn('later', [record.id for record in new['records']])
        self.assertEqual(self.view.snapshot_page(self.snapshot['id'], limit=100), old)

    def corrupt_snapshot(self):
        # Deliberate corruption only in this test's disposable database.
        with closing(sqlite3.connect(self.store.db_path)) as con:
            con.execute('DROP TRIGGER immutable_snapshots_UPDATE')
            con.execute("UPDATE snapshots SET body='{}' WHERE id=?", (self.snapshot['id'],))
            con.commit()

    def test_first_read_keeps_original_digest_rejection(self):
        # Catches loading raw SQL or persisting unverified cached data.
        self.corrupt_snapshot()
        with self.assertRaisesRegex(StoreError, 'digest'):
            self.view.snapshot_page(self.snapshot['id'])

    def test_new_instance_revalidates_after_previous_snapshot_was_pinned(self):
        # A cache is scoped to one verification view, never persisted/global.
        sid = self.snapshot['id']
        frozen = self.view.snapshot_page(sid, limit=100)
        self.corrupt_snapshot()
        self.assertEqual(self.view.snapshot_page(sid, limit=100), frozen)
        with self.assertRaisesRegex(StoreError, 'digest'):
            self.view_type(self.store).snapshot_page(sid)

    def test_view_snapshot_reads_do_not_write_database(self):
        # Catches a cache that uses a SQLite cache table or creates snapshots.
        def counts():
            with closing(sqlite3.connect(self.store.db_path)) as con:
                return {table: con.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                        for table in ('records', 'requests', 'snapshots', 'links')}
        before = counts()
        MemoryReader(self.view).open_snapshot(self.snapshot['id'], page_size=1)
        self.assertEqual(counts(), before)

    def test_inconsistent_probe_and_full_read_are_rejected_and_not_cached(self):
        # Catches silently accepting missing records or changed frozen metadata.
        store = self.store
        class ChangingPage:
            def __init__(self, mode):
                self.mode = mode
            def snapshot_page(self, snapshot_id, offset=0, limit=100):
                page = store.snapshot_page(snapshot_id, offset, limit)
                if limit > 1:
                    if self.mode == 'metadata':
                        page['snapshot']['digest'] = '0' * 64
                    else:
                        page['records'].pop()
                return page
        for mode in ('metadata', 'coverage'):
            with self.subTest(mode=mode):
                adapter = ChangingPage(mode)
                view = self.view_type(adapter)
                for _ in range(2):
                    with self.assertRaises(StoreError):
                        view.snapshot_page(self.snapshot['id'])

    def test_changed_probe_record_cannot_be_silently_replaced_by_full_page(self):
        # Catches mismatched records despite identical metadata and projections.
        store = self.store
        class ChangedProbe:
            def snapshot_page(self, snapshot_id, offset=0, limit=100):
                page = store.snapshot_page(snapshot_id, offset, limit)
                if limit == 1:
                    page['records'][0] = replace(page['records'][0], name='Different probe record')
                return page
        with self.assertRaises(StoreError):
            self.view_type(ChangedProbe()).snapshot_page(self.snapshot['id'])

    def test_metadata_boolean_alias_does_not_match_original_numeric_version(self):
        # Python equality alone treats True == 1, unlike canonical JSON identity.
        store = self.store
        class ChangedMetadata:
            def snapshot_page(self, snapshot_id, offset=0, limit=100):
                page = store.snapshot_page(snapshot_id, offset, limit)
                if limit > 1:
                    page['snapshot']['version'] = True
                return page
        with self.assertRaises(StoreError):
            self.view_type(ChangedMetadata()).snapshot_page(self.snapshot['id'])


if __name__ == '__main__':
    unittest.main()
