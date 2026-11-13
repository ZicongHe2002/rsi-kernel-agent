"""Local CPU checks for the real-round host scripts; no live DB or bridge."""
import asyncio
from copy import deepcopy
import importlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / 'src'), str(ROOT / 'tests')]
from phase3_fixtures import RealProtocolFixture
from phase2d_fixtures import make_workflow_fixture
from rsi_kernel_agent.contracts import ArtifactRef, Decision, RunEvent
from rsi_kernel_agent.workflow.evaluation import integrate_result
from rsi_kernel_agent.workflow.controller import Controller
from rsi_kernel_agent.workflow.execution import SyntheticBackend
from rsi_kernel_agent.workflow.rounds import prepare_round


class GateTests(RealProtocolFixture, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prepare(self.tmp.name)
        try:
            self.api = importlib.import_module('real_round_aux')
        except ImportError:
            self.fail('Real-round persistent Gate checks are not implemented')

    def test_gate_requires_store_completion_not_a_result_dictionary(self):
        job, receipts = self.execute_fixture()
        with self.assertRaises(ValueError):
            self.api.verified_gate_result(self.store, job)
        result = integrate_result(self.store, job, receipts)
        count = len(self.store.history('task'))
        self.assertEqual(self.api.verified_gate_result(self.store, job), result)
        self.assertEqual(len(self.store.history('task')), count)

    def test_unavailable_profile_blocks_even_valid_benchmark(self):
        job, receipts = self.execute_fixture(profile_failed=True)
        result = integrate_result(self.store, job, receipts)
        self.assertEqual(result['timing_validity'], 'valid')
        with self.assertRaisesRegex(ValueError, 'Profile'):
            self.api.verified_gate_result(self.store, job)

    def test_gate_rechecks_raw_receipt_artifacts(self):
        job, receipts = self.execute_fixture()
        integrate_result(self.store, job, receipts)
        ref = ArtifactRef(**receipts['correctness']['outputs']['raw_artifact_refs'][0])
        self.store.artifacts.path_for(ref).write_bytes(b'corrupted')
        with self.assertRaises(ValueError):
            self.api.verified_gate_result(self.store, job)

    def test_gate_rejects_job_relabelled_as_another_run(self):
        job, receipts = self.execute_fixture()
        integrate_result(self.store, job, receipts)
        changed = deepcopy(job)
        changed['candidate_id'] = 'another-candidate'
        with self.assertRaises(ValueError):
            self.api.verified_gate_result(self.store, changed)

    def test_negative_gate_accepts_only_actual_correctness_rejection(self):
        job = self.job()
        self.store.transition(RunEvent('run:start', 'run', 'running', 'active', 'CPU negative fixture'), None, 'run:start')
        outputs = self.outputs(job)
        output = outputs['correctness']
        output['correctness'] = 'failed'
        output['cases'][0]['passed'] = False
        output['cases'][0]['outputs'][0].update(passed=False, max_abs=1., max_rel=1., rms=1., tolerance_violations=1)
        receipts = {stage: self.receipt(job, stage, outputs[stage], 'failed' if stage == 'correctness' else 'succeeded')
                    for stage in ('generate', 'compile', 'correctness')}
        result = integrate_result(self.store, job, receipts)
        self.assertEqual(self.api.verified_gate_result(self.store, job, negative=True), result)
        with self.assertRaises(ValueError):
            self.api.verified_gate_result(self.store, job)


class RoundRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        try:
            self.api = importlib.import_module('real_round_aux')
        except ImportError:
            self.fail('Real-round recovery helpers are not implemented')
        self.data = await make_workflow_fixture(self.tmp.name)
        self.store = self.data['store']

    async def test_existing_round_keeps_pinned_knowledge_and_rejects_wrong_round(self):
        prepared = await prepare_round(self.store, self.data['knowledge'], 'task', 'context',
            ['A', 'B'], 'saved-round:1', self.data['request']['knowledge'])
        count = len(self.store.history('task'))
        restored = self.api.load_prepared(self.store, 'task', 'context', ['A', 'B'], 'saved-round:1')
        self.assertEqual(restored['manifest'].artifact_ref, prepared['manifest'].artifact_ref)
        self.assertEqual(restored['request']['knowledge'], self.data['request']['knowledge'])
        self.assertEqual(len(self.store.history('task')), count)
        with self.assertRaises(ValueError):
            self.api.load_prepared(self.store, 'task', 'wrong-context', ['A', 'B'], 'saved-round:1')
        self.assertIsNone(self.api.load_prepared(self.store, 'task', 'context', ['A', 'B'], 'other-round'))

    async def test_completed_session_digest_and_manifest_are_fixed(self):
        directory = Path(self.tmp.name) / 'recorded-session'
        self.assertEqual(self.api.completed_session(self.store, directory, self.data['manifest']), self.data['result'])
        result = json.loads((directory / 'result.json').read_bytes())
        result['model_label'] = 'edited after completion'
        (directory / 'result.json').write_text(json.dumps(result), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'digest'):
            self.api.completed_session(self.store, directory, self.data['manifest'])

    async def test_prepare_restores_saved_round_without_live_revision_read(self):
        script = importlib.import_module('prepare_real_round')
        round_id = 'real-runtime2-round:1'
        prepared = await prepare_round(self.store, self.data['knowledge'], 'task', 'context',
            ['A', 'B'], round_id, self.data['request']['knowledge'])
        gates = {'baseline-repair': {'task_id': 'task', 'context_id': 'context', 'strategy_id': 'A'},
                 'baseline-b': {'task_id': 'task', 'context_id': 'context', 'strategy_id': 'B'}}
        with patch.object(script, 'require_gates', return_value=(gates, {})), \
             patch.object(script, 'StdioKnowledge', side_effect=AssertionError('must use pinned revision')):
            result = await script.prepare(1, self.tmp.name)
            count = len(self.store.history('task'))
            self.assertEqual(await script.prepare(1, self.tmp.name), result)
            self.assertEqual(len(self.store.history('task')), count)
        self.assertEqual(result['manifest']['digest'], prepared['manifest'].artifact_ref.digest)

    async def test_changed_recovery_variants_are_rejected_without_overwrite(self):
        path = Path(self.tmp.name) / 'execution-input.json'
        self.api.write_once(path, {'variants': {'plan': ['original']}})
        before = path.read_bytes()
        self.api.write_once(path, {'variants': {'plan': ['original']}})
        with self.assertRaises(ValueError):
            self.api.write_once(path, {'variants': {'plan': ['changed']}})
        self.assertEqual(path.read_bytes(), before)

    async def test_previous_round_requires_exact_intake_and_completed_jobs(self):
        controller = Controller(self.store, 'task', SyntheticBackend(self.store, Path(self.tmp.name) / 'workspaces'))
        controller.accept_intake(self.data['intake'])
        with self.assertRaises(ValueError):
            self.api.completed_round(self.store, 'task', 'round:1')
        controller.run_until_idle()
        jobs = self.api.completed_round(self.store, 'task', 'round:1')
        self.assertEqual(len(jobs), 6)
        with self.assertRaises(ValueError):
            self.api.completed_round(self.store, 'task', 'unrelated-round')

    async def test_driver_resume_keeps_original_attempts_budget_and_completion(self):
        # Gate / manifest checks are separately tested above; replace only these
        # externally prepared inputs and the remote boundary for this CPU loop.
        script = importlib.import_module('run_real_round')
        target = Path(self.tmp.name) / 'real-round-1'
        shutil.copytree(Path(self.tmp.name) / 'recorded-session', target)
        variants = Path(self.tmp.name) / 'variants.json'
        variants.write_text(json.dumps(self.data['variants']), encoding='utf-8')
        gates = {'baseline-repair': {'task_id': 'task', 'context_id': 'context', 'strategy_id': 'A'},
                 'baseline-b': {'task_id': 'task', 'context_id': 'context', 'strategy_id': 'B'}}
        backend = SyntheticBackend(self.store, Path(self.tmp.name) / 'workspaces')
        controller = Controller(self.store, 'task', backend, limits=self.api.LIMITS)
        class KnowledgeContext:
            async def __aenter__(self_inner):
                return self.data['knowledge']
            async def __aexit__(self_inner, *args):
                return False
        with patch.object(script, 'require_gates', return_value=(gates, {})), \
             patch.object(script, 'load_prepared', return_value={'manifest': self.data['manifest']}), \
             patch.object(script, 'StdioKnowledge', return_value=KnowledgeContext()), \
             patch.object(script, 'colab_remote', return_value=None), \
             patch.object(script, 'ColabBackend', return_value=backend), \
             patch.object(script.time, 'sleep'), redirect_stdout(io.StringIO()):
            await script.run(1, variants, steps=1, directory=self.tmp.name)
            original_ids = set(controller.status()['jobs'])
            self.assertEqual(len(original_ids), 6)
            await script.run(1, variants, steps=100, directory=self.tmp.name)
            finished = controller.status()
            self.assertEqual(finished['workflow_state'], 'completed')
            count = len(self.store.history('task'))
            before_completion = (target / 'completed.json').read_bytes()
            await script.run(1, variants, steps=100, directory=self.tmp.name)
        self.assertEqual(set(controller.status()['jobs']), original_ids)
        self.assertEqual(controller.status()['spent'], finished['spent'])
        self.assertEqual(finished['spent']['executions'], 6)
        self.assertEqual(len(self.store.history('task')), count)
        self.assertEqual((target / 'completed.json').read_bytes(), before_completion)


if __name__ == '__main__':
    unittest.main(verbosity=2)
