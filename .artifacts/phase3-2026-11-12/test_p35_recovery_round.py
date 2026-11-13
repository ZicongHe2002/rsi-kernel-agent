"""CPU checks for explicit lost-runtime recovery; never opens the live Store."""
from copy import deepcopy
import importlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / 'src')]


class RecoveryRoundTests(unittest.TestCase):
    def setUp(self):
        try:
            self.api = importlib.import_module('p35_recovery_round')
        except ImportError:
            self.fail('Explicit recovery round gate is not implemented')

    def test_recovery_identity_is_separate_and_only_runtime4_is_supported(self):
        target, round_id = self.api.recovery_identity(HERE, 'runtime4')
        self.assertEqual(target, HERE / 'real-round-2-runtime4')
        self.assertEqual(round_id, 'real-runtime4-recovery-round:2')
        for value in ('runtime3', 'runtime5', '../runtime4', None):
            with self.assertRaises(ValueError):
                self.api.recovery_identity(HERE, value)

    def gates(self, context, runtime):
        return {name: {'task_id': 'task', 'context_id': context, 'strategy_id': strategy,
                      'candidate_id': candidate,
                      'contract': {'context': {'environment': {'runtime_session_id': runtime},
                                               'protocol': {'fixed': True}}},
                      'variant': {key: {'digest': name + key, 'size': 1}
                                  for key in ('source_ref', 'config_ref', 'build_ref', 'source_bundle_ref')}}
                for name, strategy, candidate in (('baseline-repair', 'A', 'repair'),
                                                  ('baseline-b', 'B', 'delayed'))}

    def invoke(self, new=None, prepared=None):
        old = self.gates('context3', 'device3')
        new = self.gates('context4', 'device4') if new is None else new
        state = {'cancelled': False, 'jobs': {}}
        store = SimpleNamespace(snapshot_page=lambda *a, **k: {'records': [SimpleNamespace(id='archive'), SimpleNamespace(id='intent')]})
        with patch.object(self.api, 'require_archived_unsubmitted_runtime_loss', return_value={'decision_id': 'archive', 'intent_id': 'intent'}) as archive, \
             patch.object(self.api, 'require_gates', return_value=(old, {})), \
             patch.object(self.api, 'require_confirmation', return_value={}) as confirmation, \
             patch.object(self.api, 'WorkflowJournal') as journal:
            journal.return_value.load.return_value = state
            self.api.require_recovery_context(store, HERE, new, prepared=prepared)
            archive.assert_called_once()
            self.assertEqual(confirmation.call_args.args[3], 'context3')
            self.assertEqual(confirmation.call_args.kwargs['prepared'], prepared)

    def test_historical_abba_keeps_its_context(self):
        self.invoke(prepared={'snapshot': {'id': 'fresh', 'record_count': 2}})

    def test_relabelled_context_or_changed_source_is_rejected(self):
        for path, value in ((('context_id',), 'context3'),
                            (('contract', 'context', 'environment', 'runtime_session_id'), 'device3'),
                            (('variant', 'source_ref', 'digest'), 'changed'),
                            (('strategy_id',), 'other')):
            changed = self.gates('context4', 'device4')
            ref = changed['baseline-repair']
            for key in path[:-1]:
                ref = ref[key]
            ref[path[-1]] = value
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.invoke(changed)

    def test_unarchived_loss_blocks_before_admission_reads(self):
        with patch.object(self.api, 'require_archived_unsubmitted_runtime_loss', side_effect=ValueError('missing archive')), \
             patch.object(self.api, 'require_gates') as gates:
            with self.assertRaisesRegex(ValueError, 'missing archive'):
                self.api.require_recovery_context(object(), HERE, self.gates('c4', 'd4'))
            gates.assert_not_called()

    def test_foreign_unfinished_jobs_block_recovery(self):
        old = self.gates('context3', 'device3')
        with patch.object(self.api, 'require_archived_unsubmitted_runtime_loss', return_value={}), \
             patch.object(self.api, 'require_gates', return_value=(old, {})), \
             patch.object(self.api, 'require_confirmation', return_value={}), \
             patch.object(self.api, 'WorkflowJournal') as journal:
            journal.return_value.load.return_value = {'cancelled': False, 'jobs': {
                'foreign': {'round_id': 'old', 'state': 'blocked'}}}
            with self.assertRaisesRegex(ValueError, 'unfinished'):
                self.api.require_recovery_context(object(), HERE, self.gates('context4', 'device4'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
