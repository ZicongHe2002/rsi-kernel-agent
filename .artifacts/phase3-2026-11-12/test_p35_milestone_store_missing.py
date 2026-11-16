"""Local regression for Snapshot absence through original execution/archive reads."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / 'src'), str(HERE.parents[1] / 'tests')]

from rsi_kernel_agent.contracts import Decision, Task
from rsi_kernel_agent.store import ExperimentStore, StoreError
from rsi_kernel_agent.workflow.colab import ColabBackend
from rsi_kernel_agent.workflow.execution import _maybe, make_request, stable_id
from p35_b2_milestone import MilestoneStore
import test_runtime5_admission as runtime4_fixture


class MilestoneMissingRecordTests(unittest.TestCase):
    def minimal(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        store = ExperimentStore(root / 'experiments.sqlite3', root / 'artifacts')
        store.append(Task('task', 'Snapshot absence fixture', 'v1'), 'task')
        metadata = store.create_snapshot('task', 'before')
        return store, MilestoneStore(store, metadata)

    def test_original_maybe_recognizes_missing_snapshot_record(self):
        store, frozen = self.minimal()
        with self.assertRaises(StoreError) as missing:
            frozen.get('missing')
        self.assertEqual(str(missing.exception), 'Missing record: missing')
        self.assertIsNone(_maybe(frozen, 'missing'))

    def test_later_record_is_missing_without_live_get_fallback(self):
        store, frozen = self.minimal()
        store.append(Decision('later', 'task', 'note', 'task', 'After Snapshot'), 'later')
        later_snapshot = store.create_snapshot('task', 'after')
        with patch.object(store, 'get', side_effect=AssertionError('Read escaped frozen Snapshot')):
            self.assertIsNone(_maybe(frozen, 'later'))
            self.assertEqual(frozen.get('task').id, 'task')
        with self.assertRaisesRegex(ValueError, 'later than grant milestone'):
            frozen.snapshot_page(later_snapshot['id'])
        self.assertEqual([r.id for r in frozen.history('task')], ['task'])
        self.assertFalse(hasattr(frozen, 'append'))

    def test_runtime4_archive_missing_correctness_receipt_stays_unknown_in_snapshot(self):
        fixture = runtime4_fixture.Runtime4UnknownLossTests(
            methodName='test_unknown_submission_preserved_no_evaluation_or_budget_change_and_replay')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        archived = fixture.archive()
        metadata = fixture.store.create_snapshot('task', 'archived-runtime4')
        frozen = MilestoneStore(fixture.store, metadata)
        request = make_request(frozen, fixture.active, 'correctness')
        backend = ColabBackend(frozen, object())
        # This exact original _local_receipt call used to raise StoreError.
        self.assertIsNone(backend._local_receipt(request))
        before = frozen.history('task')
        with patch.object(fixture.store, '_write', side_effect=AssertionError('Read-only guard wrote')):
            self.assertEqual(fixture.api.require_archived_runtime4_loss(frozen, fixture.root), archived)
        # A later actual receipt must not retroactively alter the frozen view.
        fixture.receipt(fixture.active, 'correctness', fixture.outputs4['correctness'])
        self.assertIsNone(_maybe(frozen, stable_id('execution-receipt', request['operation_id'])))
        self.assertIsNone(backend._local_receipt(request))
        self.assertEqual(fixture.api.require_archived_runtime4_loss(frozen, fixture.root), archived)
        self.assertEqual(frozen.history('task'), before)
        with self.assertRaisesRegex(ValueError, 'Correctness has completed'):
            fixture.api.require_archived_runtime4_loss(fixture.store, fixture.root)


if __name__ == '__main__':
    unittest.main(verbosity=2)
