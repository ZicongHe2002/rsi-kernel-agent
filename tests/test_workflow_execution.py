"""Synthetic backend/evaluator tests using the original durable Store."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rsi_kernel_agent.contracts import (
    Candidate, CandidateIdentity, Decision, EvaluationBundle, EvaluationContext, Evidence, ParentCandidate,
    Plan, Run, RunEvent, Strategy, Task, Workload, canonical_json,
)
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.workflow.execution import SyntheticBackend, make_request, execution_contract
from rsi_kernel_agent.workflow.evaluation import integrate_result


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = ExperimentStore(self.root / 'db.sqlite', self.root / 'artifacts')
        for rec in (Task('task', 'Synthetic software fixture', 'fixed-v1'),
                    Workload('workload', 'task', {'shape': [2, 2]}),
                    EvaluationContext('context', 'task', 'workload', {'device': 'synthetic'},
                                      {'correctness': {'rtol': 0}, 'timer': 'fixture'}, 'synthetic'),
                    Strategy('A', 'task', 'Route A'), Plan('plan', 'A', 'Explicit synthetic plan')):
            self.store.append(rec, 'setup:' + rec.id)
        refs = [self.store.artifacts.put(x) for x in (b'# never executed\n', b'{"tile":2}', b'synthetic-build-v1')]
        identity = CandidateIdentity(*(r.digest for r in refs))
        self.store.append(ParentCandidate('parent', 'task', identity), 'setup:parent')
        self.refs = refs
        self.identity = identity
        self.backend = SyntheticBackend(self.store, self.root / 'workspaces')

    def job(self, name='candidate', scenario='success', latency=10, parent='parent'):
        candidate = Candidate(name, 'plan', parent, self.identity)
        run = Run(name + '-run', name, 'context')
        for rec in (candidate, run):
            self.store.append(rec, 'setup:' + rec.id)
        contract, digest = execution_contract(self.store, 'context')
        return {'id': name + '-job', 'task_id': 'task', 'round_id': 'round-1', 'strategy_id': 'A',
                'plan_id': 'plan', 'parent_id': parent, 'context_id': 'context',
                'candidate_id': name, 'run_id': run.id, 'parent_evaluation_id': None,
                'contract': contract, 'contract_digest': digest,
                'variant': {'label': name, 'source_ref': asdict(self.refs[0]),
                            'config_ref': asdict(self.refs[1]), 'build_ref': asdict(self.refs[2]),
                            'scenario': scenario, 'latency_ms': latency, 'priority': 0,
                            'device': 'synthetic-device',
                            'cost': {'executions': 1, 'tokens': 0, 'time_s': 1.0}}}

    def start(self, job):
        run_id = job['run_id']
        self.store.transition(RunEvent(run_id + ':start', run_id, 'running', 'active', 'Synthetic start'),
                              None, run_id + ':start')

    def execute(self, job, backend=None):
        backend = backend or self.backend
        self.start(job)
        receipts = {}
        for stage in ('generate', 'compile', 'correctness', 'benchmark'):
            request = make_request(self.store, job, stage)
            response = backend.submit(request)
            while response['status'] == 'running':
                response = backend.poll(request['operation_id'])
            receipt = response['receipt']
            receipts[stage] = receipt
            if receipt['status'] != 'succeeded':
                break
        return receipts

    def test_receipt_survives_restart_before_result_integration_without_reexecution(self):
        job = self.job()
        receipts = self.execute(job)
        before = len(self.store.history('task'))
        reopened = ExperimentStore(self.root / 'db.sqlite', self.root / 'artifacts')
        backend = SyntheticBackend(reopened, self.root / 'workspaces')
        request = make_request(reopened, job, 'benchmark')
        self.assertEqual(backend.lookup(request['operation_id'])['receipt'], receipts['benchmark'])
        self.assertEqual(backend.submit(request)['receipt'], receipts['benchmark'])
        self.assertEqual(len(reopened.history('task')), before)
        result = integrate_result(reopened, job, receipts)
        count = len(reopened.history('task'))
        self.assertEqual(integrate_result(reopened, job, receipts), result)
        self.assertEqual(len(reopened.history('task')), count)
        self.assertEqual(reopened.best('task', 'context').id, result['evaluation_id'])
        self.assertEqual(result['provenance'], 'synthetic')

    def test_pending_operation_restarts_and_polls_same_durable_operation(self):
        job = self.job()
        self.start(job)
        backend = SyntheticBackend(self.store, self.root / 'workspaces', polls_before_completion=2)
        request = make_request(self.store, job, 'generate')
        self.assertEqual(backend.lookup(request['operation_id'])['status'], 'definitely_not_started')
        self.assertEqual(backend.submit(request)['status'], 'running')
        self.assertEqual(backend.poll(request['operation_id'])['status'], 'running')
        reopened = SyntheticBackend(self.store, self.root / 'workspaces')
        self.assertEqual(reopened.poll(request['operation_id'])['status'], 'completed')
        decisions = [r for r in self.store.history('task') if isinstance(r, Decision)
                     and r.details.get('type') == 'execution_receipt.v1']
        self.assertEqual(len(decisions), 1)

    def test_same_operation_with_different_request_is_rejected(self):
        job = self.job()
        self.start(job)
        request = make_request(self.store, job, 'generate')
        self.backend.submit(request)
        changed = deepcopy(job)
        changed['variant']['scenario'] = 'generation_failure'
        with self.assertRaises(ValueError):
            self.backend.submit(make_request(self.store, changed, 'generate'))

    def test_wrong_source_identity_and_weakened_contract_rejected(self):
        for mutation in ('source', 'contract'):
            with self.subTest(mutation=mutation):
                job = self.job('candidate-' + mutation)
                if mutation == 'source':
                    job['variant']['source_ref'] = asdict(self.store.artifacts.put(b'different source'))
                else:
                    job['contract']['context']['protocol']['correctness']['rtol'] = 100
                with self.assertRaises(ValueError):
                    make_request(self.store, job, 'generate')

    def test_stage_order_is_enforced(self):
        job = self.job()
        with self.assertRaises(ValueError):
            self.backend.submit(make_request(self.store, job, 'benchmark'))

    def test_isolated_workspaces_reject_changed_bytes(self):
        first, second = self.job('first'), self.job('second')
        self.start(first)
        self.start(second)
        r1 = self.backend.submit(make_request(self.store, first, 'generate'))['receipt']
        r2 = self.backend.submit(make_request(self.store, second, 'generate'))['receipt']
        path1 = Path(r1['outputs']['workspace']) / 'source.txt'
        path2 = Path(r2['outputs']['workspace']) / 'source.txt'
        self.assertNotEqual(path1, path2)
        path1.write_bytes(b'polluted')
        self.assertEqual(path2.read_bytes(), self.store.artifacts.read(self.refs[0]))
        with self.assertRaises(ValueError):
            self.backend.submit(make_request(self.store, first, 'compile'))

    def test_failure_classifications_keep_records_without_best(self):
        expected = {'generation_failure': ('generation_failed', 'failed'),
                    'compile_failure': ('compile_failed', 'failed'),
                    'correctness_failure': ('incorrect', 'failed'),
                    'execution_failure': ('execution_failed', 'failed'),
                    'timeout': ('incomplete', 'incomplete'),
                    'invalid_measurement': ('invalid_measurement', 'invalid'),
                    'incomplete': ('incomplete', 'incomplete'),
                    'inconclusive': ('correct_inconclusive', 'succeeded')}
        for scenario, (classification, state) in expected.items():
            with self.subTest(scenario=scenario):
                job = self.job('c-' + scenario, scenario, None if scenario == 'inconclusive' else 10)
                result = integrate_result(self.store, job, self.execute(job))
                self.assertEqual(result['classification'], classification)
                self.assertEqual(self.store.run_state(job['run_id'])['execution_status'], state)
                self.assertIsNone(self.store.best('task', 'context'))
                self.assertTrue(result['evaluation_id'])
                self.assertTrue(result['profile_id'])

    def test_forged_cross_run_and_corrupt_receipts_rejected(self):
        job = self.job()
        receipts = self.execute(job)
        forged = deepcopy(receipts)
        forged['benchmark']['outputs']['latency_ms'] = 0.1
        with self.assertRaises(ValueError):
            integrate_result(self.store, job, forged)
        other = self.job('other')
        self.start(other)
        with self.assertRaises(ValueError):
            integrate_result(self.store, other, receipts)
        ref = receipts['benchmark']['artifact_ref']
        from rsi_kernel_agent.contracts import ArtifactRef
        self.store.artifacts.path_for(ArtifactRef(**ref)).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            integrate_result(self.store, job, receipts)
        self.assertIsNone(self.store.best('task', 'context'))

    def test_missing_success_stage_is_not_accepted(self):
        job = self.job()
        receipts = self.execute(job)
        del receipts['correctness']
        with self.assertRaises(ValueError):
            integrate_result(self.store, job, receipts)

    def test_out_of_order_completions_cannot_replace_faster_best(self):
        older, newer = self.job('old', latency=12), self.job('new', latency=5)
        old_receipts, new_receipts = self.execute(older), self.execute(newer)
        good = integrate_result(self.store, newer, new_receipts)
        slow = integrate_result(self.store, older, old_receipts)
        self.assertEqual(self.store.best('task', 'context').id, good['evaluation_id'])
        self.assertFalse(self.store.get(slow['promotion_id']).details['task_promoted'])

    def test_unscheduled_run_cannot_execute(self):
        job = self.job()
        with self.assertRaises(ValueError):
            self.backend.submit(make_request(self.store, job, 'generate'))

    def test_variant_cannot_change_between_successful_stages(self):
        job = self.job()
        self.start(job)
        self.backend.submit(make_request(self.store, job, 'generate'))
        changed = deepcopy(job)
        changed['variant']['latency_ms'] = 0.1
        with self.assertRaises(ValueError):
            self.backend.submit(make_request(self.store, changed, 'compile'))

    def test_explicit_cancel_preserves_receipt_prefix_as_incomplete(self):
        job = self.job()
        self.start(job)
        receipt = self.backend.submit(make_request(self.store, job, 'generate'))['receipt']
        result = integrate_result(self.store, job, {'generate': receipt}, cancelled=True)
        self.assertEqual(result['classification'], 'cancelled')
        self.assertEqual(self.store.run_state(job['run_id'])['execution_status'], 'incomplete')
        self.assertEqual(result['cost'], job['variant']['cost'])
        self.assertIsNone(self.store.best('task', 'context'))

    def test_cancel_after_completed_benchmark_preserves_successful_fact(self):
        job = self.job()
        receipts = self.execute(job)
        result = integrate_result(self.store, job, receipts, cancelled=True)
        self.assertEqual(self.store.run_state(job['run_id'])['execution_status'], 'succeeded')
        self.assertEqual(self.store.best('task', 'context').id, result['evaluation_id'])

    def test_predicted_ancestor_cannot_supply_parent_comparison_timing(self):
        parent_job = self.job('parent-candidate')
        integrate_result(self.store, parent_job, self.execute(parent_job))
        parent = ParentCandidate('bound-parent', 'task', self.identity, parent_job['candidate_id'])
        raw = self.store.artifacts.put(b'synthetic prediction, not measurement')
        for record in (parent,
                       Evidence('prediction', parent_job['run_id'], 'model_predicted', 'available',
                                'completed', 'supported', 3, 'ms', (raw,), (), {'provenance': 'synthetic'}),
                       Evidence('derived', parent_job['run_id'], 'derived_measurement', 'available',
                                'completed', 'supported', 3, 'ms', (raw,), ('prediction',), {'provenance': 'synthetic'}),
                       EvaluationBundle('predicted-parent-benchmark', parent_job['run_id'], 'passed', 'valid', 3,
                                        True, ('derived',))):
            self.store.append(record, 'setup:' + record.id)
        child = self.job('child', parent=parent.id)
        child['parent_evaluation_id'] = 'predicted-parent-benchmark'
        receipts = self.execute(child)
        with self.assertRaises(ValueError):
            integrate_result(self.store, child, receipts)

    def test_resumed_deferred_scenario_executes_after_explicit_start(self):
        job = self.job(scenario='deferred')
        run_id = job['run_id']
        self.store.transition(RunEvent(run_id + ':defer', run_id, 'not_run', 'deferred', 'Synthetic defer'),
                              None, run_id + ':defer')
        self.store.transition(RunEvent(run_id + ':start', run_id, 'running', 'active', 'Explicit resume'),
                              run_id + ':defer', run_id + ':start')
        receipts = {}
        for stage in ('generate', 'compile', 'correctness', 'benchmark'):
            receipts[stage] = self.backend.submit(make_request(self.store, job, stage))['receipt']
        result = integrate_result(self.store, job, receipts)
        self.assertEqual(self.store.run_state(run_id)['execution_status'], 'succeeded')
        self.assertEqual(self.store.best('task', 'context').id, result['evaluation_id'])

    def test_zero_execution_cost_cannot_bypass_budget(self):
        job = self.job()
        job['variant']['cost']['executions'] = 0
        with self.assertRaises(ValueError):
            make_request(self.store, job, 'generate')

    def test_evaluator_intent_pins_cancellation_before_first_evidence_write(self):
        job = self.job()
        self.start(job)
        receipt = self.backend.submit(make_request(self.store, job, 'generate'))['receipt']

        class CrashAfterEvidence(ExperimentStore):
            def append(self, record, key):
                result = super().append(record, key)
                if isinstance(record, Evidence):
                    raise RuntimeError('Injected response loss after first Evidence commit')
                return result

        crashing = CrashAfterEvidence(self.root / 'db.sqlite', self.root / 'artifacts')
        with self.assertRaises(RuntimeError):
            integrate_result(crashing, job, {'generate': receipt}, cancelled=True)
        result = integrate_result(self.store, job, {'generate': receipt}, cancelled=False)
        self.assertEqual(result['classification'], 'cancelled')
        records = self.store.history('task')
        intent = next(r for r in records if isinstance(r, Decision)
                      and r.details.get('type') == 'workflow_evaluation_intent.v1')
        self.assertTrue(intent.details['cancelled'])
        self.assertLess(records.index(intent), next(i for i, r in enumerate(records) if isinstance(r, Evidence)))

    def test_failed_terminal_recovery_keeps_original_evaluator_intent_after_late_cancel(self):
        job = self.job(scenario='compile_failure')
        receipts = self.execute(job)

        class CrashAfterTerminal(ExperimentStore):
            def transition(self, event, expected_event_id, key):
                result = super().transition(event, expected_event_id, key)
                if event.execution_status == 'failed':
                    raise RuntimeError('Injected response loss after failed terminal commit')
                return result

        crashing = CrashAfterTerminal(self.root / 'db.sqlite', self.root / 'artifacts')
        with self.assertRaises(RuntimeError):
            integrate_result(crashing, job, receipts)
        result = integrate_result(self.store, job, receipts, cancelled=True)
        self.assertEqual(result['classification'], 'compile_failed')
        marker = next(r for r in self.store.history('task') if isinstance(r, Decision)
                      and r.details.get('type') == 'workflow_result.v1')
        self.assertFalse(marker.details['cancelled'])


if __name__ == '__main__':
    unittest.main()
