"""Local original-chain fixtures for already-admitted diagnostic recovery only."""
from contextlib import redirect_stdout
from copy import deepcopy
import importlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_p35_diagnostic_dispatch as dispatch
from rsi_kernel_agent.contracts import Candidate, Run, RunEvent, canonical_json
from rsi_kernel_agent.workflow.evaluation import integrate_result


class DiagnosticResumeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            self.resume = importlib.import_module('resume_p35_diagnostic')
        except ImportError:
            self.fail('The already-admitted diagnostic resume helper is missing')
        f = self.f = dispatch.DiagnosticRuntime4Tests()
        f.generation = 'runtime7'
        f.fixture_admission_round = 'fixture-prerequisite'
        f.addCleanup = self.addCleanup
        await f.asyncSetUp()
        original = f.origins['baseline']
        for label in ('length-row', 'length-smem'):
            job = deepcopy(original)
            job.update(id='recovery-job-' + label, candidate_id='recovery-candidate-' + label,
                       run_id='recovery-run-' + label, round_id='real-runtime7-recovery-round:2')
            job['variant']['label'] = label
            f.store.append(Candidate(job['candidate_id'], job['plan_id'], job['parent_id'],
                f.store.get(original['candidate_id']).identity), job['candidate_id'])
            f.store.append(Run(job['run_id'], job['candidate_id'], job['context_id']), job['run_id'])
            f.store.transition(RunEvent(job['run_id'] + ':start', job['run_id'], 'running', 'active',
                'CPU diagnostic resume fixture'), None, job['run_id'] + ':start')
            receipts = {stage: f.dynamic_receipt(job, stage)
                        for stage in ('generate', 'compile', 'correctness', 'benchmark', 'profile')}
            job.update(receipts=receipts, result=integrate_result(f.store, job, receipts))
            f.origins[label] = job
        state = f.controller.journal.load()
        state['jobs'] = {job['id']: job for job in f.origins.values()}
        state['spent'] = {'executions': 13, 'tokens': 0, 'time_s': 23400}
        f.controller.journal.save(state, 'CPU preserved prior thirteen reservations')
        self.write(f.directory / 'baseline-repair-runtime7-job.json', original)
        self.write(f.directory / 'active-runtime-binding.json', {'generation': 'runtime7'})
        await f.prepare_session()
        f.complete_session()
        f.backend.unknown = True
        with redirect_stdout(io.StringIO()):
            await f.run.run(f.selection_path, directory=f.directory, steps=1)
        self.stack = f.stack
        self.stack.enter_context(patch.object(self.resume, 'colab_remote', return_value=None))
        self.stack.enter_context(patch.object(self.resume, 'ColabBackend', return_value=f.backend))
        self.stack.enter_context(patch.object(self.resume.time, 'sleep'))

    def write(self, path, value):
        path.write_text(canonical_json(value), encoding='utf-8')

    def call(self, **kwargs):
        with redirect_stdout(io.StringIO()):
            return self.resume.resume(self.f.selection_path, directory=self.f.directory,
                                      steps=kwargs.pop('steps', 20), interval=1, **kwargs)

    async def test_unknown_recovery_keeps_original_operations_two_charges_and_idempotent_completion(self):
        f = self.f
        before = f.controller.status()
        operation = next(job['operation']['operation_id'] for job in before['jobs'].values()
                         if job['round_id'] == f.spec['round_id'] and job['state'] == 'blocked')
        count = len(f.backend.submissions)
        with patch.object(f.run, 'prerequisites', side_effect=AssertionError('Heavy gate replay')), \
             patch.object(f.run, 'publish_result', side_effect=AssertionError('Repeat publication')), \
             patch.object(f.run, 'intake_publication', side_effect=AssertionError('Repeat intake')):
            waiting = self.call(steps=2)
            self.assertFalse(waiting['completed'])
            self.assertEqual(len(f.backend.submissions), count)
            f.backend.unknown = False
            complete = self.call()
        self.assertTrue(complete['completed'])
        self.assertEqual(complete['spent'], {'executions': 15, 'tokens': 0, 'time_s': 27000})
        self.assertEqual(f.backend.submissions.count(operation), 1)
        self.assertEqual(len(f.backend.submissions), 10)
        state = f.controller.status()
        self.assertEqual({k: v for k, v in state['jobs'].items() if v['round_id'] != f.spec['round_id']},
                         {k: v for k, v in before['jobs'].items() if v['round_id'] != f.spec['round_id']})
        completed = (f.target / 'completed.json').read_bytes()
        history_size = len(f.store.history('task'))
        self.call()
        self.assertEqual(len(f.store.history('task')), history_size)
        self.assertEqual((f.target / 'completed.json').read_bytes(), completed)
        self.assertEqual(json.loads(completed)['charged_cost'], {'executions': 2, 'tokens': 0, 'time_s': 3600})

    async def test_frozen_file_or_artifact_tampering_rejects_before_backend_or_store_writes(self):
        f = self.f
        files = ['selection.json', 'runtime-binding.json', 'diagnostic-packet.json', 'variants.json',
                 'manifest-ref.json', 'execution-input.json', 'session.json', 'result.json',
                 'budget-checkpoint.json', 'intake.json']
        paths = [f.target / name for name in files]
        paths += [f.directory / 'active-runtime-binding.json', f.prep.scope_path(f.directory, f.spec),
                  f.directory / 'colab-config-runtime7.json', f.directory / 'baseline-repair-runtime7-job.json']
        publication = f.store.get(json.loads((f.target / 'intake.json').read_bytes())['publication_id'])
        for key in ('manifest', 'result'):
            paths.append(f.store.artifacts.path_for(dispatch.ArtifactRef(**publication.details[key])))
        for path in paths:
            original = path.read_bytes()
            with self.subTest(file=path.name):
                before = len(f.store.history('task'))
                submissions = list(f.backend.submissions)
                try:
                    path.write_bytes(b'{}')
                    with self.assertRaises((ValueError, KeyError, TypeError)):
                        self.call(steps=1)
                    self.assertEqual(len(f.store.history('task')), before)
                    self.assertEqual(f.backend.submissions, submissions)
                finally:
                    path.write_bytes(original)

    async def test_intake_digest_and_checkpoint_parent_bindings_are_required(self):
        f = self.f
        state = f.controller.journal.load()
        intake = json.loads((f.target / 'intake.json').read_bytes())
        state['intakes'][intake['id']] = '0' * 64
        f.controller.journal.save(state, 'CPU corrupted admitted intake digest')
        before = len(f.store.history('task'))
        with self.assertRaisesRegex(ValueError, 'intake|Intake'):
            self.call(steps=1)
        self.assertEqual(len(f.store.history('task')), before)

    async def test_coordinated_local_variant_and_input_edits_do_not_bypass_original_intake(self):
        f = self.f
        variants = json.loads((f.target / 'variants.json').read_bytes())
        variants['diagnostic-baseline'][0]['priority'] += 1
        execution = json.loads((f.target / 'execution-input.json').read_bytes())
        execution['variants'] = variants
        self.write(f.target / 'variants.json', variants)
        self.write(f.target / 'execution-input.json', execution)
        before = len(f.store.history('task'))
        with self.assertRaises(ValueError):
            self.call(steps=1)
        self.assertEqual(len(f.store.history('task')), before)

    async def test_paused_pair_does_not_resume_and_changed_parent_context_is_rejected(self):
        f = self.f
        state = f.controller.journal.load()
        state['paused'] = ['A']
        f.controller.journal.save(state, 'CPU explicit pause')
        submissions = list(f.backend.submissions)
        before = len(f.store.history('task'))
        self.assertFalse(self.call(steps=1)['completed'])
        self.assertEqual(len(f.store.history('task')), before)
        self.assertEqual(f.backend.submissions, submissions)
        baseline_path = f.directory / 'baseline-repair-runtime7-job.json'
        baseline = json.loads(baseline_path.read_bytes())
        baseline['context_id'] = 'different-formal-context'
        self.write(baseline_path, baseline)
        with self.assertRaises(ValueError):
            self.call(steps=1)

    async def test_config_change_during_polling_blocks_next_step(self):
        f = self.f
        config_path = f.directory / 'colab-config-runtime7.json'
        original = json.loads(config_path.read_bytes())
        self.resume.time.sleep.side_effect = lambda _: self.write(config_path, {**original, 'nonce': 'changed'})
        before = list(f.backend.submissions)
        with self.assertRaisesRegex(ValueError, 'config|Config|frozen'):
            self.call(steps=2)
        self.assertEqual(f.backend.submissions, before)

    async def test_frozen_prerequisite_intent_uses_original_completed_marker(self):
        f = self.f
        baseline_path = f.directory / 'baseline-repair-runtime7-job.json'
        baseline = json.loads(baseline_path.read_bytes())
        baseline.update(state='queued', stage='generate', result=None, receipts={}, reserved=False)
        self.write(baseline_path, baseline)
        before = list(f.backend.submissions)
        try:
            result = self.call(steps=1)
        except ValueError as error:
            self.fail('Frozen prerequisite intent with genuine completion must resume: ' + str(error))
        self.assertFalse(result['completed'])
        self.assertEqual(f.backend.submissions, before)
        self.assertEqual(result['spent']['executions'], 14)


if __name__ == '__main__':
    unittest.main(verbosity=2)
