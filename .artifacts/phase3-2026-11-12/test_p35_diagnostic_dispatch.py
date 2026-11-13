"""Temporary real-protocol fixtures; no bridge, TPU, or production Store access."""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
from dataclasses import asdict
import importlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / 'src'), str(ROOT / 'tests')]
from phase3_fixtures import RealProtocolFixture
from rsi_kernel_agent.contracts import (ArtifactRef, Candidate, CandidateIdentity, Decision, EvaluationContext,
    ParentCandidate, Plan, Run, RunEvent, Workload, canonical_json)
from rsi_kernel_agent.profiler.session import submit_response
from rsi_kernel_agent.workflow.controller import Controller
from rsi_kernel_agent.workflow.evaluation import integrate_result
from rsi_kernel_agent.workflow.execution import execution_contract
from rsi_kernel_agent.workflow.mla_baseline import workload_matrix
from rsi_kernel_agent.store import ConflictError
from real_round_aux import LIMITS


class DiagnosticDispatchTests(RealProtocolFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            self.prep = importlib.import_module('p35_prepare_diagnostic_round')
            self.run = importlib.import_module('p35_run_diagnostic_round')
        except ImportError:
            self.fail('Diagnostic prepare/run wrappers are not implemented')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.prepare(self.directory)
        new_db = self.directory / 'experiments.sqlite3'
        self.store.db_path.rename(new_db)
        self.store.db_path = new_db
        self.protocol['benchmark_case_id'] = 'decode-small'
        workload = Workload('mla-workload', 'task', {'kernel': 'mla_decode', 'interface': 'contiguous', 'cases': workload_matrix()})
        context = EvaluationContext('mla-context', 'task', workload.id, self.environment, self.protocol, 'real')
        self.store.append(workload, workload.id)
        self.store.append(context, context.id)
        self.store.append(Plan('origin-plan', 'A', 'Original archived implementation fixtures'), 'origin-plan')
        self.origins = {}
        for role, folder, round_id in (
            ('baseline', 'baseline-repair-1', getattr(self, 'fixture_baseline_round', 'real-runtime2-round:2')),
            ('single_tile', 'round1-single-tile', 'real-runtime2-round:1'),
        ):
            source = self.put((HERE / 'candidates' / folder / 'mla_decode.py').read_bytes())
            config = self.put({'schema_version': 1, 'kernel': 'mla_decode', 'case_overrides': {}})
            bundle = self.put({'schema_version': 1, 'entry': {'path': 'kernel.py', 'symbol': 'kernel'},
                'files': [{'path': 'kernel.py', 'artifact_ref': source, 'role': 'source'}]})
            build = self.put({'schema_version': 1, 'source_bundle_digest': bundle['digest'],
                'oracle_bundle_digest': self.oracle_bundle['digest'], 'runner_digest': self.runner_digest,
                'oracle_digest': self.oracle['digest']})
            variant = {**deepcopy(self.variant), 'label': 'single-tile' if role == 'single_tile' else 'baseline-repair',
                'source_ref': source, 'config_ref': config, 'build_ref': build, 'source_bundle_ref': bundle,
                'cost': {'executions': 1, 'tokens': 0, 'time_s': 1800.0}}
            identity = CandidateIdentity(source['digest'], config['digest'], build['digest'])
            cid, rid = 'origin-' + role, 'origin-run-' + role
            self.store.append(Candidate(cid, 'origin-plan', 'parent', identity), cid)
            self.store.append(Run(rid, cid, context.id), rid)
            contract, digest = execution_contract(self.store, context.id)
            job = {'id': 'origin-job-' + role, 'task_id': 'task', 'round_id': round_id,
                'strategy_id': 'A', 'plan_id': 'origin-plan', 'parent_id': 'parent', 'context_id': context.id,
                'candidate_id': cid, 'run_id': rid, 'variant': variant, 'contract': contract,
                'contract_digest': digest, 'parent_evaluation_id': None, 'reserved': True,
                'attempt': 0, 'repair_depth': 0, 'state': 'completed', 'stage': 'evaluate', 'reason': 'Fixture only'}
            self.store.transition(RunEvent(rid + ':start', rid, 'running', 'active', 'Fixture only'), None, rid + ':start')
            receipts = {stage: self.dynamic_receipt(job, stage) for stage in ('generate', 'compile', 'correctness', 'benchmark', 'profile')}
            job.update(receipts=receipts, result=integrate_result(self.store, job, receipts))
            self.origins[role] = job
        self.controller = Controller(self.store, 'task', None, limits=LIMITS)
        state = self.controller.journal.load()
        state['jobs'] = {job['id']: job for job in self.origins.values()}
        state['spent'] = {'executions': 2, 'tokens': 0, 'time_s': 3600.0}
        self.controller.journal.save(state, 'Local fixture original executions')
        self.spec = {'schema_version': 1, 'task_id': 'task', 'context_id': context.id,
                     'strategy_id': 'A', 'round_id': 'diagnostic:zero-v1', 'knowledge': []}
        self.selection_path = self.directory / 'selection.json'
        self.selection_path.write_text(canonical_json(self.spec), encoding='utf-8')
        (self.directory / 'active-runtime-binding.json').write_text(canonical_json({'generation': 'runtime3'}), encoding='utf-8')
        (self.directory / 'colab-config-runtime3.json').write_text(canonical_json({'runtime_id': self.environment['runtime_session_id']}), encoding='utf-8')
        owner = self
        class Knowledge:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
        class Backend:
            unknown = False
            results = {}
            submissions = []
            def lookup(self, identity):
                if self.unknown and identity in self.results:
                    return {'status': 'unknown'}
                return self.results.get(identity, {'status': 'definitely_not_started'})
            def submit(self, request):
                self.submissions.append(request['operation_id'])
                receipt = owner.dynamic_receipt(request['job'], request['stage'])
                self.results[request['operation_id']] = {'status': 'completed', 'receipt': receipt}
                return {'status': 'unknown'} if self.unknown else self.results[request['operation_id']]
            def poll(self, identity): return self.lookup(identity)
        self.backend = Backend()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # Completed gates/ABBA already have focused integration suites. These
        # fixtures isolate the new diagnostic admission and execution boundary.
        self.stack.enter_context(patch.object(self.prep, 'require_gates', return_value=(
            {'baseline-repair': self.origins['baseline'], 'baseline-b': {**self.origins['baseline'], 'strategy_id': 'B'}}, {})))
        self.stack.enter_context(patch.object(self.prep, 'require_confirmation', return_value=None))
        self.stack.enter_context(patch.object(self.prep, 'completed_round', side_effect=lambda s, t, r:
            [job for job in self.origins.values() if job['round_id'] == r]))
        self.stack.enter_context(patch.object(self.prep, 'StdioKnowledge', return_value=Knowledge()))
        self.stack.enter_context(patch.object(self.run, 'StdioKnowledge', return_value=Knowledge()))
        self.stack.enter_context(patch.object(self.run, 'colab_remote', return_value=None))
        self.stack.enter_context(patch.object(self.run, 'ColabBackend', return_value=self.backend))
        self.stack.enter_context(patch.object(self.run.time, 'sleep'))

    def dynamic_receipt(self, job, stage):
        self.source, self.config, self.build, self.bundle = [job['variant'][key]
            for key in ('source_ref', 'config_ref', 'build_ref', 'source_bundle_ref')]
        self.variant = job['variant']
        outputs = self.outputs(job)
        outputs['correctness']['cases'] = [{'id': case['id'], 'outputs': [
            {**{key: out[key] for key in ('name', 'shape', 'dtype')}, 'finite': True,
             'max_abs': 0., 'max_rel': 0., 'rms': 0., 'tolerance_violations': 0, 'passed': True,
             'metric_reason': None} for out in case['outputs']], 'passed': True}
            for case in job['contract']['workload']['spec']['cases']]
        return self.receipt(job, stage, outputs[stage])

    async def prepare_session(self):
        result = await self.prep.prepare(self.selection_path, directory=self.directory)
        self.target = Path(result['directory'])
        return json.loads((self.target / 'diagnostic-packet.json').read_bytes())

    def complete_session(self):
        packet = json.loads((self.target / 'packet.json').read_bytes())
        sid = 'A'
        binding = packet['manifest']['strategies'][sid]
        source, history = 'source:A', 'history:A'
        answer = {'code_understanding': [{'strategy_id': sid, **{key: ['Local fixture explanation'] for key in
            ('stages', 'loops', 'movement', 'dependencies', 'layout_memory', 'synchronization', 'edge_cases')}, 'refs': [source]}],
            'observations': [{'id': 'o1', 'strategy_id': sid, 'category': 'hypothesis', 'text': 'Unmeasured zero-length diagnostic', 'refs': [history], 'predicate': None}],
            'hypotheses': [{'id': 'h1', 'strategy_id': sid, 'text': 'Specialization may differ on empty input', 'refs': [source], 'alternatives': ['General path may also fail']}],
            'plans': [], 'unknowns': ['Fixture values are not hardware measurements']}
        for role, mechanism in (('baseline', 'Restore general baseline path'), ('single_tile', 'Exercise specialized direct path')):
            pid = 'diagnostic-' + role.replace('_', '-')
            answer['plans'].append({'id': pid, 'strategy_id': sid, 'parent_id': binding['parent_id'],
                'mechanism': mechanism, 'modification': mechanism + ' with common diagnostic resource config',
                'refs': [source, history], 'observation_ids': ['o1'], 'calculation_ids': [],
                'tradeoffs': ['Two runs only'], 'risks': ['Empty input may fail'], 'unknowns': ['Untested on device'],
                'variants': [pid], 'validation': {'correctness': ['Original oracle attention and opaque cache'],
                                                'performance': ['Archive fixed timing and Profile; no general speedup claim']}})
        response = self.target / 'fixture-response.json'
        response.write_text(canonical_json({'final': answer}), encoding='utf-8')
        submit_response(self.store, self.target, response)

    async def test_prepare_keeps_independent_contract_and_no_execution_budget(self):
        before = self.controller.status()
        packet = await self.prepare_session()
        manifest = json.loads((self.target / 'packet.json').read_bytes())['manifest']
        self.assertNotEqual(packet['context']['id'], 'mla-context')
        self.assertNotEqual(packet['workload']['id'], 'mla-workload')
        self.assertEqual(packet['context']['environment'], self.environment)
        self.assertEqual(packet['context']['protocol'], self.protocol)
        self.assertEqual(packet['workload']['spec']['cases'][0]['lengths'], [0])
        self.assertEqual(packet['configs']['baseline'], packet['configs']['single_tile'])
        self.assertEqual(set(manifest['strategies']), {'A'})
        self.assertIsNone(manifest['strategies']['A']['run'])
        self.assertEqual(manifest['strategies']['A']['benchmarks'], [])
        self.assertEqual(manifest['strategies']['A']['parent']['candidate_id'], 'origin-single_tile')
        self.assertEqual(self.controller.status()['spent'], before['spent'])
        self.assertEqual(set(self.controller.status()['jobs']), set(before['jobs']))
        history = len(self.store.history('task'))
        await self.prepare_session()
        self.assertEqual(len(self.store.history('task')), history)

    async def test_original_analyzer_intake_controller_resume_unknown_charges_exactly_two(self):
        await self.prepare_session()
        self.complete_session()
        self.backend.unknown = True
        with redirect_stdout(io.StringIO()):
            await self.run.run(self.selection_path, directory=self.directory, steps=1)
        state = self.controller.status()
        rows = [job for job in state['jobs'].values() if job['round_id'] == self.spec['round_id']]
        self.assertEqual(len(rows), 2)
        self.assertEqual(state['spent']['executions'], 3)
        self.assertEqual(sum(job['state'] == 'blocked' for job in rows), 1)
        operation = next(job['operation']['operation_id'] for job in rows if job['state'] == 'blocked')
        self.backend.unknown = False
        with redirect_stdout(io.StringIO()):
            await self.run.run(self.selection_path, directory=self.directory, steps=20, interval=1)
        state = self.controller.status()
        rows = [job for job in state['jobs'].values() if job['round_id'] == self.spec['round_id']]
        self.assertEqual(state['spent'], {'executions': 4, 'tokens': 0, 'time_s': 7200.0})
        self.assertEqual([job['state'] for job in rows], ['completed', 'completed'])
        self.assertTrue(all(job['parent_evaluation_id'] is None for job in rows))
        self.assertEqual(len({job['run_id'] for job in rows}), 2)
        self.assertEqual(self.backend.submissions.count(operation), 1)
        submissions = list(self.backend.submissions)
        with redirect_stdout(io.StringIO()):
            await self.run.run(self.selection_path, directory=self.directory, steps=1)
        self.assertEqual(self.backend.submissions, submissions)
        self.assertEqual(self.controller.status()['spent'], state['spent'])

    async def test_changed_frozen_variant_cannot_publish_or_spend(self):
        await self.prepare_session()
        self.complete_session()
        variants_path = self.target / 'variants.json'
        variants = json.loads(variants_path.read_bytes())
        variants['diagnostic-single-tile'][0]['config_ref'] = self.origins['single_tile']['variant']['config_ref']
        variants_path.write_text(canonical_json(variants), encoding='utf-8')
        before = len(self.store.history('task'))
        with self.assertRaises(ValueError):
            await self.run.run(self.selection_path, directory=self.directory)
        self.assertEqual(len(self.store.history('task')), before)
        self.assertEqual(self.controller.status()['spent']['executions'], 2)

    async def test_incomplete_session_cannot_publish_or_spend(self):
        await self.prepare_session()
        before = len(self.store.history('task'))
        with self.assertRaises((ValueError, FileNotFoundError)):
            await self.run.run(self.selection_path, directory=self.directory)
        self.assertEqual(len(self.store.history('task')), before)
        self.assertEqual(self.controller.status()['spent']['executions'], 2)

    async def test_budget_shortfall_rejects_before_diagnostic_records(self):
        state = self.controller.journal.load()
        state['spent'] = {'executions': 15, 'tokens': 0, 'time_s': 27000.0}
        self.controller.journal.save(state, 'Local exhausted budget')
        before = len(self.store.history('task'))
        with self.assertRaisesRegex(ValueError, 'budget'):
            await self.prep.prepare(self.selection_path, directory=self.directory)
        self.assertEqual(len(self.store.history('task')), before)

    async def test_other_round_pending_blocks_before_diagnostic_records(self):
        state = self.controller.journal.load()
        state['jobs']['origin-job-baseline']['state'] = 'blocked'
        self.controller.journal.save(state, 'Local other round interruption')
        before = len(self.store.history('task'))
        with self.assertRaises(ValueError):
            await self.prep.prepare(self.selection_path, directory=self.directory)
        self.assertEqual(len(self.store.history('task')), before)

    async def test_driver_lock_excludes_second_diagnostic_invocation(self):
        with self.run.driver_lock(self.store, 'task'):
            with self.assertRaises(ConflictError):
                with self.run.driver_lock(self.store, 'task'):
                    self.fail('Second driver acquired the same Task lock')

    async def test_new_round_cannot_repeat_the_frozen_two_execution_diagnostic(self):
        await self.prepare_session()
        changed = {**self.spec, 'round_id': 'diagnostic:zero-v2'}
        self.selection_path.write_text(canonical_json(changed), encoding='utf-8')
        before = len(self.store.history('task'))
        with self.assertRaisesRegex(ValueError, 'scope'):
            await self.prep.prepare(self.selection_path, directory=self.directory)
        self.assertEqual(len(self.store.history('task')), before)
        self.assertFalse(self.prep.target_for(self.directory, changed).exists())

    async def test_conflicting_prepared_round_rejects_before_freeze_then_corrected_round_can_prepare(self):
        await self.prep.prepare_round(self.store, None, 'task', 'mla-context', ['A'],
            self.spec['round_id'], [], parents={'A': 'parent'}, parent_policy='explicit')
        before = len(self.store.history('task'))
        artifact_files = {path.relative_to(self.store.artifacts.root): path.read_bytes()
                          for path in self.store.artifacts.root.rglob('*') if path.is_file()}
        with self.assertRaisesRegex(ValueError, 'round'):
            await self.prep.prepare(self.selection_path, directory=self.directory)
        self.assertEqual(len(self.store.history('task')), before)
        self.assertFalse((self.directory / 'p35-diagnostic-selection.json').exists())
        self.assertFalse(self.prep.target_for(self.directory, self.spec).exists())
        self.assertEqual({path.relative_to(self.store.artifacts.root): path.read_bytes()
                          for path in self.store.artifacts.root.rglob('*') if path.is_file()}, artifact_files)
        self.spec['round_id'] = 'diagnostic:corrected'
        self.selection_path.write_text(canonical_json(self.spec), encoding='utf-8')
        await self.prepare_session()
        self.assertTrue((self.target / 'session.json').is_file())

    async def test_extra_same_round_attempt_blocks_recovery_before_submission(self):
        await self.prepare_session()
        self.complete_session()
        self.backend.unknown = True
        with redirect_stdout(io.StringIO()):
            await self.run.run(self.selection_path, directory=self.directory, steps=1)
        state = self.controller.journal.load()
        extra = deepcopy(next(job for job in state['jobs'].values() if job['round_id'] == self.spec['round_id']))
        extra.update(id='unexpected-third-attempt', state='queued', reserved=False)
        state['jobs'][extra['id']] = extra
        self.controller.journal.save(state, 'Local invalid third attempt')
        before = len(self.store.history('task'))
        submitted = list(self.backend.submissions)
        self.backend.unknown = False
        with self.assertRaisesRegex(ValueError, 'exactly the two'):
            await self.run.run(self.selection_path, directory=self.directory)
        self.assertEqual(len(self.store.history('task')), before)
        self.assertEqual(self.backend.submissions, submitted)
        self.assertEqual(self.controller.status()['spent'], state['spent'])

    async def test_changed_budget_checkpoint_is_not_a_new_spending_authority(self):
        await self.prepare_session()
        self.complete_session()
        self.backend.unknown = True
        with redirect_stdout(io.StringIO()):
            await self.run.run(self.selection_path, directory=self.directory, steps=1)
        path = self.target / 'budget-checkpoint.json'
        checkpoint = json.loads(path.read_bytes())
        checkpoint['spent']['executions'] += 1
        path.write_text(canonical_json(checkpoint), encoding='utf-8')
        before = len(self.store.history('task'))
        submitted = list(self.backend.submissions)
        with self.assertRaisesRegex(ValueError, 'checkpoint differs'):
            await self.run.run(self.selection_path, directory=self.directory)
        self.assertEqual(len(self.store.history('task')), before)
        self.assertEqual(self.backend.submissions, submitted)


class DiagnosticRuntime4Tests(DiagnosticDispatchTests):
    async def asyncSetUp(self):
        self.fixture_baseline_round = 'real-runtime4-recovery-round:2'
        await super().asyncSetUp()
        self.recovery = importlib.import_module('p35_recovery_round')
        self.spec.update(schema_version=2, runtime_generation='runtime4', round_id='diagnostic:runtime4-zero')
        self.selection_path.write_text(canonical_json(self.spec), encoding='utf-8')
        # Fixture uses actual real-protocol results while mocking the separately
        # tested archive/ABBA admission gate. No new hardware is represented.
        (self.directory / 'colab-config-runtime4.json').write_text(canonical_json({
            'runtime_id': self.environment['runtime_session_id']}), encoding='utf-8')
        self.notes = {}
        for label in ('archive', 'archive-intent', 'abba-result', 'abba-policy', 'abba-A', 'abba-B'):
            note = Decision('fixture-' + label, 'task', 'note', 'A' if label == 'abba-A' else 'task',
                            'CPU historical evidence fixture', {'type': 'fixture_history.v1'})
            self.store.append(note, note.id)
            self.notes[label] = note
        loaded = {'archive': {'decision_id': self.notes['archive'].id, 'intent_id': self.notes['archive-intent'].id},
                  'confirmation': {'result': self.notes['abba-result'], 'policy_note': self.notes['abba-policy'],
                      'association_notes': {'A': self.notes['abba-A'], 'B': self.notes['abba-B']},
                      'report': {'rows': [{'run_id': job['run_id']} for job in self.origins.values()]}}}
        self.context_gate = self.stack.enter_context(patch.object(self.recovery, 'require_recovery_context', return_value=loaded))

    async def test_prepare_v2_freezes_runtime4_and_preserves_v1_scope(self):
        legacy = {key: value for key, value in self.spec.items() if key != 'runtime_generation'}
        legacy['schema_version'] = 1
        legacy['round_id'] = 'diagnostic:legacy-v1'
        old_path = self.directory / 'p35-diagnostic-selection.json'
        old_path.write_text(canonical_json(legacy), encoding='utf-8')
        before = old_path.read_bytes()
        packet = await self.prepare_session()
        self.assertEqual(old_path.read_bytes(), before)
        self.assertEqual(json.loads((self.directory / 'p35-diagnostic-selection-runtime4.json').read_bytes()), self.spec)
        self.assertEqual(json.loads((self.target / 'runtime-binding.json').read_bytes()), {'generation': 'runtime4'})
        self.assertEqual(packet['context']['environment'], self.environment)
        self.assertNotEqual(self.prep.target_for(self.directory, legacy), self.target)
        self.assertTrue(self.context_gate.call_args_list)
        self.assertTrue(all(call.kwargs['allowed_round_id'] == self.spec['round_id'] for call in self.context_gate.call_args_list))

    async def test_v2_rejects_other_generation_or_recovery_round_identity_before_writes(self):
        for update in ({'runtime_generation': 'runtime3'}, {'runtime_generation': '../runtime4'},
                       {'runtime_generation': 'runtime5'}, {'round_id': self.recovery.ROUND_ID}):
            changed = {**self.spec, **update}
            self.selection_path.write_text(canonical_json(changed), encoding='utf-8')
            before = len(self.store.history('task'))
            with self.assertRaises(ValueError):
                await self.prep.prepare(self.selection_path, directory=self.directory)
            self.assertEqual(len(self.store.history('task')), before)

    async def test_missing_archive_or_historical_confirmation_blocks_before_writes(self):
        self.context_gate.side_effect = ValueError('Missing archived Runtime3 loss')
        before = len(self.store.history('task'))
        with self.assertRaisesRegex(ValueError, 'archived'):
            await self.prep.prepare(self.selection_path, directory=self.directory)
        self.assertEqual(len(self.store.history('task')), before)
        self.assertFalse(self.target_for_if_any().exists())

    def target_for_if_any(self):
        return self.prep.target_for(self.directory, self.spec)

    async def test_v2_dispatch_uses_only_runtime4_remote_binding(self):
        await self.prepare_session()
        self.complete_session()
        with patch.object(self.run, 'colab_remote', return_value=None) as remote, redirect_stdout(io.StringIO()):
            await self.run.run(self.selection_path, directory=self.directory, steps=12)
        remote.assert_called_with(self.directory / 'colab-config-runtime4.json')
        state = self.controller.status()
        jobs = [j for j in state['jobs'].values() if j['round_id'] == self.spec['round_id']]
        self.assertEqual(len(jobs), 2)
        self.assertTrue(all(j['state'] == 'completed' for j in jobs))
        self.assertEqual(state['spent'], {'executions': 4, 'tokens': 0, 'time_s': 7200.0})


if __name__ == '__main__':
    unittest.main(verbosity=2)
