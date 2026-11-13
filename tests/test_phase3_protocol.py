"""Local CPU contract fixtures; these do not execute or validate a real TPU."""
from copy import deepcopy
import asyncio
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rsi_kernel_agent.contracts import (ArtifactRef, Candidate, CandidateIdentity, Decision, EvaluationContext,
    ParentCandidate, Plan, Run, RunEvent, Strategy, Task, Workload, canonical_json)
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.workflow.execution import execution_contract, make_request, stable_id, validate_variant
from rsi_kernel_agent.workflow.evaluation import integrate_result


from phase3_fixtures import RealProtocolFixture


class RealProtocolTests(RealProtocolFixture, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.prepare(self.temp.name)

    def test_real_requests_bind_dependency_bundle_and_separate_profile_operation(self):
        job = self.job()
        benchmark = make_request(self.store, job, 'benchmark')
        profile = make_request(self.store, job, 'profile')
        self.assertEqual(benchmark['backend_id'], 'colab.v1')
        self.assertEqual(profile['provenance'], 'real')
        self.assertNotEqual(benchmark['operation_id'], profile['operation_id'])
        self.assertEqual(benchmark['job']['run_id'], profile['job']['run_id'])
        self.assertNotIn('latency_ms', benchmark['job']['variant'])

    def test_changed_bundle_cannot_keep_original_candidate_build_identity(self):
        job = self.job()
        job['variant'] = deepcopy(self.variant)
        changed = json.loads(self.store.artifacts.read(ArtifactRef(**self.bundle)))
        changed['files'].append({'path': 'helper.py', 'artifact_ref': self.put(b'# changed helper'), 'role': 'dependency'})
        job['variant']['source_bundle_ref'] = self.put(changed)
        with self.assertRaisesRegex(ValueError, 'bundle'):
            make_request(self.store, job, 'compile')

    def test_real_variant_rejects_synthetic_latency_and_path_traversal(self):
        self.job()
        for changes in ({'latency_ms': 1.0}, {'scenario': 'success'}):
            with self.assertRaises(ValueError):
                validate_variant(self.store, {**self.variant, **changes}, provenance='real')
        bad = deepcopy(self.variant)
        bad['source_bundle_ref'] = self.put({'schema_version': 1, 'entry': {'path': '../kernel.py', 'symbol': 'kernel'},
            'files': [{'path': '../kernel.py', 'artifact_ref': self.source, 'role': 'source'}]})
        with self.assertRaises(ValueError):
            validate_variant(self.store, bad, provenance='real')

    def test_complete_fixture_archives_raw_profile_and_parsed_dependency_separately(self):
        job, receipts = self.execute_fixture()
        result = integrate_result(self.store, job, receipts)
        self.assertEqual(result['provenance'], 'real')
        self.assertEqual(result['latency_ms'], 3.0)
        self.assertEqual(self.store.best('task', 'context').id, result['evaluation_id'])
        profile = self.store.get(result['profile_id'])
        parsed = self.store.get(profile.evidence_ids[0])
        self.assertEqual(len(parsed.input_ids), 1)
        raw = self.store.get(parsed.input_ids[0])
        self.assertEqual(self.store.artifacts.read(raw.artifacts[0]), b'CPU fixture, not an XPlane protobuf')
        json.loads(self.store.artifacts.read(parsed.artifacts[0]))
        count = len(self.store.history('task'))
        self.assertEqual(integrate_result(self.store, job, receipts), result)
        self.assertEqual(len(self.store.history('task')), count)

    def test_wrong_imported_module_is_rejected_even_when_uploaded_digest_matches(self):
        def corrupt(outputs):
            for name in ('compile', 'correctness', 'benchmark', 'profile'):
                outputs[name]['identity'] = {**outputs[name]['identity'], 'module_path': '/content/old/kernel.py'}
        job, receipts = self.execute_fixture(corrupt)
        with self.assertRaisesRegex(ValueError, 'module'):
            integrate_result(self.store, job, receipts)
        self.assertIsNone(self.store.best('task', 'context'))

    def test_missing_cache_correctness_cannot_become_a_confirmed_measurement(self):
        job, receipts = self.execute_fixture(lambda out: out['correctness']['cases'][0]['outputs'].pop())
        with self.assertRaisesRegex(ValueError, 'output'):
            integrate_result(self.store, job, receipts)

    def test_timing_statistics_recomputed_from_all_raw_samples(self):
        job, receipts = self.execute_fixture(lambda out: out['benchmark'].update(latency_ms=0.1))
        with self.assertRaisesRegex(ValueError, 'median'):
            integrate_result(self.store, job, receipts)

    def test_device_interference_and_environment_drift_are_rejected(self):
        job, receipts = self.execute_fixture(lambda out: out['benchmark']['lease'].update(interference_observed=True))
        with self.assertRaisesRegex(ValueError, 'interference'):
            integrate_result(self.store, job, receipts)

    def test_profile_failure_preserves_benchmark_fact_but_prevents_promotion(self):
        job, receipts = self.execute_fixture(profile_failed=True)
        result = integrate_result(self.store, job, receipts)
        self.assertEqual(result['classification'], 'profile_failed')
        self.assertEqual(result['correctness'], 'passed')
        self.assertEqual(result['latency_ms'], 3.0)
        self.assertFalse(result['confirmed'])
        self.assertEqual(self.store.run_state('run')['execution_status'], 'incomplete')
        self.assertIsNone(self.store.best('task', 'context'))

    def test_original_analysis_profile_tool_reads_parsed_json_and_verifies_raw_upstream(self):
        from rsi_kernel_agent.profiler.inputs import AnalysisRequest, capture_inputs
        from rsi_kernel_agent.profiler.tools import AnalysisTools
        job, receipts = self.execute_fixture()
        result = integrate_result(self.store, job, receipts)
        parent = ParentCandidate('measured-parent', 'task', self.identity, 'candidate')
        self.store.append(parent, parent.id)
        snapshot = self.store.create_snapshot('task', 'measured-snapshot')
        request = AnalysisRequest.from_dict({'schema_version': 1, 'request_id': 'measured-analysis',
            'task_id': 'task', 'workload_id': 'workload', 'knowledge': [], 'environment': {'test': 'CPU'},
            'tool_versions': {'fixture': '1'}, 'strategies': [{'strategy_id': 'A', 'parent_id': parent.id,
                'context_id': 'context', 'snapshot_id': snapshot['id'], 'run_id': 'run',
                'source_artifact': self.source, 'config_artifact': self.config,
                'profile_ids': [result['profile_id']], 'benchmark_ids': [result['evaluation_id']]}]})
        manifest = asyncio.run(capture_inputs(self.store, None, request))
        report = AnalysisTools(manifest.to_dict(), self.store.artifacts).call('profile',
            {'strategy_id': 'A', 'profile_id': result['profile_id']})
        self.assertEqual(report['availability'], 'available')
        self.assertEqual(report['reports'][0]['operation_stats'][0]['median_us'], 2000)

    def test_controller_executes_profile_after_benchmark_without_another_run(self):
        from rsi_kernel_agent.workflow.controller import Controller
        fixture = self
        class CPUBackend:
            def __init__(self):
                self.completed, self.stages = {}, []
            def lookup(self, operation_id):
                return self.completed.get(operation_id, {'status': 'definitely_not_started', 'receipt': None})
            def submit(self, request):
                stage = request['stage']
                self.stages.append(stage)
                receipt = fixture.receipt(request['job'], stage, fixture.outputs(request['job'])[stage])
                result = {'status': 'completed', 'receipt': receipt}
                self.completed[request['operation_id']] = result
                return result
            def poll(self, operation_id):
                return self.lookup(operation_id)
        job = self.job()
        for key in ('candidate_id', 'run_id'):
            job.pop(key)
        intake = {'id': 'fixture-intake', 'task_id': 'task', 'items': [{'status': 'accepted', 'jobs': [job]}]}
        self.store.append(Decision(intake['id'], 'task', 'note', 'task', 'CPU fixture intake',
            {'type': 'workflow_intake.v1', 'request_digest': 'd' * 64, 'result': intake}), intake['id'])
        backend = CPUBackend()
        controller = Controller(self.store, 'task', backend)
        controller.accept_intake(intake)
        state = controller.run_until_idle()
        self.assertEqual(backend.stages, ['generate', 'compile', 'correctness', 'benchmark', 'profile'])
        self.assertEqual(state['workflow_state'], 'completed')
        actual = next(iter(state['jobs'].values()))
        self.assertEqual(actual['result']['provenance'], 'real')
        self.assertEqual(self.store.get(actual['result']['profile_id']).run_id, actual['run_id'])

    def test_exact_opaque_cache_nan_has_no_fabricated_numerical_metric(self):
        def nonfinite_cache(output):
            cache = output['correctness']['cases'][0]['outputs'][1]
            cache.update(finite=False, max_abs=None, max_rel=None, rms=None, metric_reason='Opaque tail bytes compared exactly')
        job, receipts = self.execute_fixture(nonfinite_cache)
        self.assertTrue(integrate_result(self.store, job, receipts)['confirmed'])

    def test_environment_version_change_cannot_share_comparison_context(self):
        def drift(output):
            output['benchmark']['environment'] = {**self.environment, 'jax': 'changed'}
        job, receipts = self.execute_fixture(drift)
        with self.assertRaisesRegex(ValueError, 'environment'):
            integrate_result(self.store, job, receipts)

    def test_later_correctness_raw_corruption_invalidates_promotion_evidence(self):
        job, receipts = self.execute_fixture()
        result = integrate_result(self.store, job, receipts)
        raw = ArtifactRef(**receipts['correctness']['outputs']['raw_artifact_refs'][0])
        self.store.artifacts.path_for(raw).write_bytes(b'corrupt raw inputs')
        decision = self.store.consider(result['evaluation_id'], 'reconsider-after-raw-corruption')
        self.assertFalse(decision.details['eligible'])
        self.assertEqual(decision.details['reason'], 'artifact_missing_or_corrupt')

    def test_real_repair_can_change_a_dependency_without_changing_entry_file(self):
        from rsi_kernel_agent.workflow.controller import Controller
        fixture = self
        class NeverExecuted:
            def lookup(self, operation_id):
                raise AssertionError('Repair submission must not execute')
        job = self.job()
        for key in ('candidate_id', 'run_id'):
            job.pop(key)
        intake = {'id': 'repair-intake', 'task_id': 'task', 'items': [{'status': 'accepted', 'jobs': [job]}]}
        self.store.append(Decision(intake['id'], 'task', 'note', 'task', 'CPU repair fixture intake',
            {'type': 'workflow_intake.v1', 'request_digest': 'e' * 64, 'result': intake}), intake['id'])
        controller = Controller(self.store, 'task', NeverExecuted())
        controller.accept_intake(intake)
        state = controller.journal.load()
        failed = next(iter(state['jobs'].values()))
        failed.update(state='failed', result={'classification': 'compile_failed'})
        controller.journal.save(state, 'CPU fixture failed compile')
        bundle = json.loads(self.store.artifacts.read(ArtifactRef(**self.bundle)))
        bundle['files'].append({'path': 'helper.py', 'artifact_ref': self.put(b'# changed dependency\n'), 'role': 'dependency'})
        bundle_ref = self.put(bundle)
        build = json.loads(self.store.artifacts.read(ArtifactRef(**self.build)))
        build['source_bundle_digest'] = bundle_ref['digest']
        variant = {**self.variant, 'source_bundle_ref': bundle_ref, 'build_ref': self.put(build)}
        response = {**controller.repair_packet(failed['id']), 'variant': variant, 'reason': 'Repair the dependency'}
        repaired_id = controller.submit_repair(failed['id'], response, 'helper-change')
        repaired = controller.status()['jobs'][repaired_id]
        self.assertNotEqual(repaired['candidate_id'], failed['candidate_id'])
        self.assertEqual(self.store.get(repaired['parent_id']).candidate_id, failed['candidate_id'])
        self.assertEqual(repaired['variant']['source_ref'], failed['variant']['source_ref'])
        self.assertNotEqual(repaired['variant']['build_ref'], failed['variant']['build_ref'])


if __name__ == '__main__':
    unittest.main()
