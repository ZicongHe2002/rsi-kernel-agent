"""CPU-only Runtime 6 subbatch guards; no production Store or remote calls."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / 'src')]
import p35_recovery_round as recovery
import p35_prepare_diagnostic_round as diagnostic
import test_p35_recovery_round as fixtures
from rsi_kernel_agent.contracts import (Candidate, CandidateIdentity, Decision, EvaluationContext,
                                       ParentCandidate, Plan, Run, Strategy, Task, Workload)
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.memory import MemoryReader
from rsi_kernel_agent.workflow.execution import stable_id


class Runtime6ScopeTests(unittest.TestCase):
    def setUp(self):
        self.gates = fixtures.RecoveryRoundTests.gates(self, 'context6', 'runtime6')

    def test_runtime6_selects_only_A_and_preserves_older_four_candidate_scope(self):
        self.assertEqual(recovery.recovery_identity(HERE, 'runtime6')[1], 'real-runtime6-recovery-round:2')
        self.assertEqual(recovery.recovery_strategies(self.gates, 'runtime6'), ['A'])
        self.assertEqual(recovery.recovery_strategies(self.gates, 'runtime5'), ['A', 'B'])

    def proposal(self):
        plans = [{'id': pid, 'strategy_id': 'A', 'parent_id': 'new-A-parent', 'variants': [label]}
                 for pid, label in (('R2-A-length-row', 'length-row'), ('R2-A-length-smem', 'length-smem'))]
        variants = {p['id']: [{'label': p['variants'][0], 'cost': {'executions': 1, 'tokens': 0, 'time_s': 1800}}]
                    for p in plans}
        prepared = {'request': {'strategies': [{'strategy_id': 'A', 'parent_id': 'new-A-parent'}]}}
        return {'plan_set': {'plans': plans}}, variants, prepared

    def test_two_plan_scope_rejects_extra_B_wrong_parent_or_unbudgeted_variant(self):
        result, variants, prepared = self.proposal()
        recovery.require_recovery_proposal('runtime6', self.gates, prepared, result, variants)
        for field, value in (('strategy_id', 'B'), ('parent_id', 'old-A-parent')):
            changed = deepcopy(result)
            changed['plan_set']['plans'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                recovery.require_recovery_proposal('runtime6', self.gates, prepared, changed, variants)
        variants['R2-A-length-row'].append(deepcopy(variants['R2-A-length-row'][0]))
        with self.assertRaises(ValueError):
            recovery.require_recovery_proposal('runtime6', self.gates, prepared, result, variants)

    def test_runtime5_loss_must_be_archived_before_reading_admission(self):
        with patch.object(recovery, 'require_archived_unsubmitted_runtime_loss', return_value={}), \
             patch.object(recovery, 'require_runtime4_loss', return_value={}), \
             patch.object(recovery, 'require_runtime5_loss', create=True, side_effect=ValueError('Runtime 5 not archived')), \
             patch.object(recovery, 'require_gates') as gates:
            with self.assertRaisesRegex(ValueError, 'Runtime 5 not archived'):
                recovery.require_recovery_context(object(), HERE, self.gates, generation='runtime6')
            gates.assert_not_called()

    def test_diagnostic_scope_accepts_generation6_but_not_recovery_round_id(self):
        selection = {'schema_version': 2, 'runtime_generation': 'runtime6', 'task_id': 'task',
                     'context_id': 'context6', 'strategy_id': 'A', 'round_id': 'diag6:1', 'knowledge': []}
        diagnostic.validate_selection(selection)
        selection['round_id'] = 'real-runtime6-recovery-round:2'
        with self.assertRaises(ValueError):
            diagnostic.validate_selection(selection)

    def test_diagnostic_needs_both_exact_A_candidates_with_actual_compile_feedback(self):
        jobs = [{'run_id': 'run-' + label, 'context_id': 'context6', 'strategy_id': 'A',
                 'variant': {'label': label}, 'contract': {'context': {'provenance': 'real'}}}
                for label in ('length-row', 'length-smem')]
        def row(store, job, **kwargs):
            return {'verification': 'verified', 'receipt_statuses': {'compile': 'failed'},
                    'result_marker_id': 'result-' + job['run_id']}
        with patch.object(diagnostic, 'verify_job', side_effect=row):
            self.assertEqual(len(diagnostic.require_recovery_feedback(object(), jobs, 'context6',
                generation='runtime6', strategy_id='A')), 2)
            for changed in (jobs[:1], [jobs[0], {**jobs[1], 'strategy_id': 'B'}],
                            [jobs[0], {**jobs[1], 'context_id': 'old'}]):
                with self.assertRaises(ValueError):
                    diagnostic.require_recovery_feedback(object(), changed, 'context6',
                        generation='runtime6', strategy_id='A')
        with patch.object(diagnostic, 'verify_job', return_value={
                'verification': 'verified', 'receipt_statuses': {'generate': 'succeeded'}}):
            with self.assertRaisesRegex(ValueError, 'compile feedback'):
                diagnostic.require_recovery_feedback(object(), jobs, 'context6', generation='runtime6', strategy_id='A')

    def test_A_only_history_keeps_ABBA_and_both_routes_in_whole_original_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            store = ExperimentStore(Path(root) / 'db.sqlite3', Path(root) / 'artifacts')
            records = [Task('task', 'CPU snapshot scope fixture', 'v1'), Workload('w', 'task', {}),
                       EvaluationContext('context3', 'task', 'w', {'fixture': True}, {'fixture': True}, 'synthetic'),
                       ParentCandidate('parent', 'task', CandidateIdentity('a' * 64, 'b' * 64, 'c' * 64))]
            jobs, associations = [], {}
            for sid in ('A', 'B'):
                records.extend([Strategy(sid, 'task', sid), Plan('plan-' + sid, sid, 'fixture'),
                                Candidate('candidate-' + sid, 'plan-' + sid, 'parent', records[3].identity),
                                Run('run-' + sid, 'candidate-' + sid, 'context3')])
                jobs.append({'strategy_id': sid, 'run_id': 'run-' + sid})
                records.append(Decision(stable_id('workflow-result', 'run-' + sid), 'task', 'note',
                                        'run-' + sid, 'fixture', {'type': 'fixture.v1'}))
                associations[sid] = Decision('association-' + sid, 'task', 'note', sid, 'fixture', {'type': 'fixture.v1'})
                records.append(associations[sid])
            result = Decision('abba-result', 'task', 'note', 'task', 'fixture', {'type': 'fixture.v1'})
            policy = Decision('abba-policy', 'task', 'note', 'task', 'fixture', {'type': 'fixture.v1'})
            for record in [*records, result, policy]:
                store.append(record, record.id)
            snapshot = store.create_snapshot('task', 'scope-fixture')
            view = MemoryReader(store).open_snapshot(snapshot['id'])
            prepared = {'snapshot': snapshot, 'histories': view.histories(['A'])}
            loaded = {'result': result, 'policy_note': policy, 'association_notes': associations,
                      'report': {'rows': [{'run_id': 'run-B'}]}}
            with patch.object(recovery, 'completed_round', return_value=jobs):
                recovery.require_runtime6_history(store, prepared, loaded, 'task', 'A')
                changed = deepcopy(prepared)
                changed['histories']['A']['records'].pop()
                with self.assertRaisesRegex(ValueError, 'complete selected A history'):
                    recovery.require_runtime6_history(store, changed, loaded, 'task', 'A')
            with patch.object(recovery, 'completed_round', return_value=[*jobs, {'run_id': 'missing-R1-B'}]):
                with self.assertRaisesRegex(ValueError, 'first-round feedback'):
                    recovery.require_runtime6_history(store, prepared, loaded, 'task', 'A')

    def test_new_snapshot_cannot_omit_runtime5_archive(self):
        old = fixtures.RecoveryRoundTests.gates(self, 'context3', 'runtime3')
        ids = ['archive3', 'intent3', 'archive4', 'intent4', 'archive5', 'intent5']
        store = SimpleNamespace(snapshot_page=lambda *a, **k: {
            'records': [SimpleNamespace(id=value) for value in ids]})
        with patch.object(recovery, 'require_archived_unsubmitted_runtime_loss', return_value={'decision_id': 'archive3', 'intent_id': 'intent3'}), \
             patch.object(recovery, 'require_runtime4_loss', return_value={'decision_id': 'archive4', 'intent_id': 'intent4'}), \
             patch.object(recovery, 'require_runtime5_loss', return_value={'decision_id': 'archive5', 'intent_id': 'intent5'}), \
             patch.object(recovery, 'require_gates', return_value=(old, {})), \
             patch.object(recovery, 'require_confirmation', return_value={}) as confirmation, \
             patch.object(recovery, 'require_runtime6_history'), patch.object(recovery, 'WorkflowJournal') as journal:
            journal.return_value.load.return_value = {'cancelled': False, 'jobs': {}}
            prepared = {'snapshot': {'id': 'snapshot6', 'record_count': 6}}
            result = recovery.require_recovery_context(store, HERE, self.gates, prepared=prepared, generation='runtime6')
            self.assertEqual(result['runtime5_archive']['decision_id'], 'archive5')
            self.assertIsNone(confirmation.call_args.kwargs['prepared'])
            ids.remove('intent5')
            with self.assertRaisesRegex(ValueError, 'Runtime 5'):
                recovery.require_recovery_context(store, HERE, self.gates, prepared=prepared, generation='runtime6')


if __name__ == '__main__':
    unittest.main()
