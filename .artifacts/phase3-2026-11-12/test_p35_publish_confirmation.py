"""Local Store tests only; no bridge or production experiment data."""
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import importlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / 'src')]
from rsi_kernel_agent.contracts import ArtifactRef, Decision, Task, canonical_json
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from test_confirmation_round import selection
from test_p35_confirmation_statistics import POLICY


class PolicyFreezeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.store = ExperimentStore(self.directory / 'experiments.sqlite3', self.directory / 'artifacts')
        self.store.append(Task('task', 'Local policy fixture', 'v1'), 'task')
        self.spec = selection()
        (self.directory / 'p35-confirmation-policy.json').write_text(canonical_json(POLICY), encoding='utf-8')
        try:
            self.api = importlib.import_module('p35_publish_confirmation')
        except ImportError:
            self.fail('Confirmation policy/publication helper is missing')

    def test_policy_is_saved_once_in_original_store_before_execution(self):
        note = self.api.freeze_policy(self.store, self.directory, self.spec)
        count = len(self.store.history('task'))
        self.assertEqual(self.api.freeze_policy(self.store, self.directory, self.spec), note)
        self.assertEqual(len(self.store.history('task')), count)
        self.assertEqual(note.details['type'], 'p35_confirmation_policy.v1')
        self.assertEqual(note.details['selection'], self.spec)
        self.assertEqual(self.store.artifacts.read(ArtifactRef(**note.details['policy_ref'])), canonical_json(POLICY).encode())
        self.assertEqual(note.target_id, 'task')

    def test_changed_policy_or_selection_rejected_without_record_append(self):
        self.api.freeze_policy(self.store, self.directory, self.spec)
        count = len(self.store.history('task'))
        changed = deepcopy(self.spec)
        changed['candidate_id'] = 'changed-candidate'
        with self.assertRaises(ValueError):
            self.api.freeze_policy(self.store, self.directory, changed)
        (self.directory / 'p35-confirmation-policy.json').write_text(canonical_json({**POLICY, 'min_paired_reduction': .01}))
        with self.assertRaises(ValueError):
            self.api.freeze_policy(self.store, self.directory, self.spec)
        self.assertEqual(len(self.store.history('task')), count)

    def test_policy_cannot_be_created_after_round_intake(self):
        # Only the persisted journal is needed: no operation is submitted.
        journal = WorkflowJournal(self.store, 'task')
        journal.save({'task_id': 'task', 'jobs': {'existing': {'round_id': self.spec['round_id']}}}, 'Local fixture intake')
        before = len(self.store.history('task'))
        with self.assertRaisesRegex(ValueError, 'before'):
            self.api.freeze_policy(self.store, self.directory, self.spec)
        self.assertEqual(len(self.store.history('task')), before)

    def test_dispatch_requires_original_policy_without_writes(self):
        self.assertTrue(hasattr(self.api, 'require_frozen_policy'), 'Read-only predispatch policy check is missing')
        before = len(self.store.history('task'))
        with self.assertRaisesRegex(ValueError, 'policy'):
            self.api.require_frozen_policy(self.store, self.directory, self.spec)
        self.assertEqual(len(self.store.history('task')), before)

    def test_intake_record_before_journal_also_prevents_late_freeze(self):
        note = Decision('intake-before-journal', 'task', 'note', 'task', 'Crash window fixture',
                        {'type': 'workflow_intake.v1', 'result': {'round_id': self.spec['round_id']}})
        self.store.append(note, note.id)
        before = len(self.store.history('task'))
        with self.assertRaisesRegex(ValueError, 'before'):
            self.api.freeze_policy(self.store, self.directory, self.spec)
        self.assertEqual(len(self.store.history('task')), before)

    def test_dispatch_rejects_wrong_task_or_target_note(self):
        self.assertTrue(hasattr(self.api, 'require_frozen_policy'), 'Read-only predispatch policy check is missing')
        from dataclasses import replace
        note = self.api.freeze_policy(self.store, self.directory, self.spec)
        original_get = self.store.get
        for changes in ({'task_id': 'other', 'target_id': 'other'}, {'target_id': 'other'}, {'task_id': 'other'}):
            with self.subTest(changes=changes), patch.object(self.store, 'get',
                side_effect=lambda rid: replace(note, **changes) if rid == note.id else original_get(rid)):
                with self.assertRaisesRegex(ValueError, 'policy'):
                    self.api.require_frozen_policy(self.store, self.directory, self.spec)
        note = self.api.freeze_policy(self.store, self.directory, self.spec)
        before = len(self.store.history('task'))
        self.assertEqual(self.api.require_frozen_policy(self.store, self.directory, self.spec), note)
        changed = {**self.spec, 'candidate_id': 'different-origin'}
        with self.assertRaisesRegex(ValueError, 'policy'):
            self.api.require_frozen_policy(self.store, self.directory, changed)
        (self.directory / 'p35-confirmation-policy.json').unlink()
        with self.assertRaises(ValueError):
            self.api.require_frozen_policy(self.store, self.directory, self.spec)
        self.assertEqual(len(self.store.history('task')), before)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        from test_p35_round2_gate import ConfirmationGateTests
        self.fixture = ConfirmationGateTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store, self.directory = self.fixture.store, self.fixture.directory
        self.api = importlib.import_module('p35_publish_confirmation')
        self.assertTrue(hasattr(self.api, 'publish_confirmation'), 'Verified confirmation publication is missing')
        from prepare_confirmation_round import target_for
        self.target = target_for(self.directory, self.fixture.selection)
        self.target.mkdir()
        for name, value in (
            ('selection.json', self.fixture.selection),
            ('completed.json', {'round_id': self.fixture.selection['round_id'],
                'order': [r['label'] for r in self.fixture.selection['sequence']],
                'results': {j['id']: j['result'] for j in self.fixture.jobs}}),
        ):
            (self.target / name).write_text(canonical_json(value), encoding='utf-8')
        intake = {'round_id': self.fixture.selection['round_id'],
                  'items': [{'status': 'accepted', 'jobs': self.fixture.jobs}]}
        note = Decision('intake-fixture', 'task', 'note', 'task', 'Local fixture intake',
                        {'type': 'workflow_intake.v1', 'result': intake})
        self.store.append(note, note.id)
        journal = WorkflowJournal(self.store, 'task')
        state = journal.load()
        state['intakes'] = {note.id: sha256(canonical_json(intake).encode()).hexdigest()}
        journal.save(state, 'Local fixture intake binding')

    def test_publication_recomputes_receipts_is_idempotent_and_visible_in_histories(self):
        # Fixture has real-protocol receipts but no NPZ: substitute only the
        # independent NPZ verifier, never the receipt or Store gates.
        with patch.object(self.api, 'verify_job', return_value={'verification': 'verified', 'fixture_only': True}):
            pointer = self.api.publish_confirmation(self.store, self.directory, self.fixture.selection, ['A', 'B'])
            before = len(self.store.history('task'))
            again = self.api.publish_confirmation(self.store, self.directory, self.fixture.selection, ['A', 'B'])
        self.assertEqual(pointer, again)
        self.assertEqual(len(self.store.history('task')), before)
        self.fixture.gate(self.fixture.frozen())
        report = __import__('json').loads(self.store.artifacts.read(ArtifactRef(**pointer['artifact_ref'])))
        self.assertEqual(report['statistics']['outcome'], 'neither_confirmed')
        self.assertEqual(report['rows'], self.fixture.rows)

    def test_incomplete_or_corrupted_results_cannot_publish(self):
        (self.target / 'completed.json').unlink()
        before = len(self.store.history('task'))
        with self.assertRaises(ValueError):
            self.api.publish_confirmation(self.store, self.directory, self.fixture.selection, ['A', 'B'])
        self.assertEqual(len(self.store.history('task')), before)
        self.assertFalse((self.directory / 'p35-confirmation-result.json').exists())

    def test_npz_verification_failure_prevents_publication(self):
        before = len(self.store.history('task'))
        with patch.object(self.api, 'verify_job', side_effect=ValueError('Raw NPZ comparison failed')):
            with self.assertRaisesRegex(ValueError, 'NPZ'):
                self.api.publish_confirmation(self.store, self.directory, self.fixture.selection, ['A', 'B'])
        self.assertEqual(len(self.store.history('task')), before)
        self.assertFalse((self.directory / 'p35-confirmation-result.json').exists())

    def test_replica_must_match_selected_origin_before_record_publication(self):
        # Use a valid but different origin, preserving the frozen selection and
        # receipts in the fixture. Only Store.get's origin lookup is replaced;
        # receipt loading, journal, append and snapshot behavior remain real.
        from dataclasses import replace
        origin_id = self.fixture.selection['baseline_candidate_id']
        original_get = self.store.get
        origin = original_get(origin_id)
        wrong = replace(origin, identity=replace(origin.identity, source_digest='f' * 64))
        before = len(self.store.history('task'))
        with patch.object(self.store, 'get', side_effect=lambda rid: wrong if rid == origin_id else original_get(rid)), \
             patch.object(self.api, 'verify_job', return_value={'verification': 'verified', 'fixture_only': True}):
            with self.assertRaisesRegex(ValueError, 'origin'):
                self.api.publish_confirmation(self.store, self.directory, self.fixture.selection, ['A', 'B'])
        self.assertEqual(len(self.store.history('task')), before)
        self.assertFalse((self.directory / 'p35-confirmation-result.json').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
