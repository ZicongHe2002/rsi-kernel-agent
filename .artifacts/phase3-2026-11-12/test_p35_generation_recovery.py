"""CPU guards for repeated pre-dispatch Runtime replacement, generation 6 onward."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / 'src')]
import p35_recovery_round as recovery
import p35_prepare_diagnostic_round as diagnostic
import test_p35_recovery_round as fixtures


class RepeatedRecoveryTests(unittest.TestCase):
    def test_generation7_and_later_keep_the_same_two_A_scope(self):
        gates = fixtures.RecoveryRoundTests.gates(self, 'new', 'new-device')
        for generation in ('runtime7', 'runtime12'):
            self.assertEqual(recovery.recovery_identity(HERE, generation),
                (HERE / ('real-round-2-' + generation), 'real-' + generation + '-recovery-round:2'))
            self.assertEqual(recovery.recovery_strategies(gates, generation), ['A'])
            selection = {'schema_version': 2, 'runtime_generation': generation, 'task_id': 'task',
                         'context_id': 'new', 'strategy_id': 'A', 'round_id': 'diag-' + generation, 'knowledge': []}
            self.assertEqual(diagnostic.validate_selection(selection), selection)
            self.assertEqual(diagnostic.prior_rounds(selection)[1], 'real-' + generation + '-recovery-round:2')
        for invalid in ('runtime06', 'runtime0', 'runtime3', 'Runtime7', '../runtime7', 'runtime7/8'):
            with self.assertRaises(ValueError):
                recovery.recovery_identity(HERE, invalid)

    def test_runtime7_requires_runtime6_loss_and_captures_all_archive_ids(self):
        old = fixtures.RecoveryRoundTests.gates(self, 'context3', 'device3')
        new = fixtures.RecoveryRoundTests.gates(self, 'context7', 'device7')
        ids = [prefix + str(number) for number in (3, 4, 5, 6) for prefix in ('archive', 'intent')]
        store = SimpleNamespace(snapshot_page=lambda *a, **k: {'records': [SimpleNamespace(id=value) for value in ids]})
        def archived(number):
            return {'decision_id': 'archive' + str(number), 'intent_id': 'intent' + str(number),
                    'summary': {'context_id': 'context' + str(number),
                                'context_ids': ['context' + str(number), 'negative-context' + str(number)],
                                'runtime_id': 'device' + str(number)}}
        with patch.object(recovery, 'require_archived_unsubmitted_runtime_loss', return_value=archived(3)), \
             patch.object(recovery, 'require_runtime4_loss', return_value=archived(4)), \
             patch.object(recovery, 'require_runtime5_loss', return_value=archived(5)), \
             patch.object(recovery, 'require_completed_prerequisite_loss', create=True, return_value=archived(6)) as loss, \
             patch.object(recovery, 'require_gates', return_value=(old, {})), \
             patch.object(recovery, 'require_confirmation', return_value={}), \
             patch.object(recovery, 'require_runtime6_history'), patch.object(recovery, 'WorkflowJournal') as journal:
            journal.return_value.load.return_value = {'cancelled': False, 'jobs': {}}
            prepared = {'snapshot': {'id': 'new-snapshot', 'record_count': len(ids)}}
            result = recovery.require_recovery_context(store, HERE, new, prepared=prepared, generation='runtime7')
            loss.assert_called_with(store, HERE, 'runtime6')
            self.assertEqual(result['completed_prerequisite_losses'][0]['generation'], 'runtime6')
            ids.remove('intent6')
            with self.assertRaisesRegex(ValueError, 'completed prerequisite loss'):
                recovery.require_recovery_context(store, HERE, new, prepared=prepared, generation='runtime7')
            ids.append('intent6')
            for same in (fixtures.RecoveryRoundTests.gates(self, 'context6', 'device7'),
                         fixtures.RecoveryRoundTests.gates(self, 'negative-context6', 'device7'),
                         fixtures.RecoveryRoundTests.gates(self, 'context7', 'device6')):
                with self.assertRaisesRegex(ValueError, 'reuse'):
                    recovery.require_recovery_context(store, HERE, same, prepared=prepared, generation='runtime7')

    def test_runtime8_requires_both_previous_completed_prerequisite_losses(self):
        old = fixtures.RecoveryRoundTests.gates(self, 'context3', 'device3')
        new = fixtures.RecoveryRoundTests.gates(self, 'context8', 'device8')
        with patch.object(recovery, 'require_archived_unsubmitted_runtime_loss', return_value={}), \
             patch.object(recovery, 'require_runtime4_loss', return_value={}), \
             patch.object(recovery, 'require_runtime5_loss', return_value={}), \
             patch.object(recovery, 'require_completed_prerequisite_loss', create=True,
                side_effect=lambda s, d, g: {'summary': {'context_id': 'lost-' + g,
                    'context_ids': ['lost-' + g], 'runtime_id': 'lost-' + g}}) as loss, \
             patch.object(recovery, 'require_gates', return_value=(old, {})), \
             patch.object(recovery, 'require_confirmation', return_value={}), patch.object(recovery, 'WorkflowJournal') as journal:
            journal.return_value.load.return_value = {'cancelled': False, 'jobs': {}}
            recovery.require_recovery_context(object(), HERE, new, generation='runtime8')
            self.assertEqual([call.args[2] for call in loss.call_args_list], ['runtime6', 'runtime7'])
            loss.side_effect = ValueError('prior completed prerequisite loss missing')
            with self.assertRaisesRegex(ValueError, 'missing'):
                recovery.require_recovery_context(object(), HERE, new, generation='runtime8')


if __name__ == '__main__':
    unittest.main()
