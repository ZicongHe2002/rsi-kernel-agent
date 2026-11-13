"""CPU/synthetic checks only; no production Store, MCP process or Colab."""
import asyncio
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
from dataclasses import asdict
import importlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / 'src'), str(ROOT / 'tests')]
from phase2d_fixtures import make_workflow_fixture
from rsi_kernel_agent.contracts import ArtifactRef, Task, canonical_json
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.profiler.outputs import publish_result
from rsi_kernel_agent.profiler.session import submit_response
from rsi_kernel_agent.workflow.controller import Controller
from rsi_kernel_agent.workflow.execution import SyntheticBackend
from rsi_kernel_agent.workflow.intake import intake_publication
from rsi_kernel_agent.workflow.scheduling import validate_limits
from real_round_aux import LIMITS
from test_analyzer import proposal


def modules(test):
    try:
        return (importlib.import_module('prepare_confirmation_round'),
                importlib.import_module('run_confirmation_round'))
    except ImportError:
        test.fail('Confirmation round drivers are not implemented')


def selection():
    return dict(schema_version=1, task_id='task', context_id='context', strategy_id='A',
        candidate_id='selected', baseline_candidate_id='baseline-candidate',
        round_id='confirmation:1', previous_round_ids=['real-runtime2-round:1', 'real-runtime2-round:2'],
        knowledge=[], plan_roles={'baseline': 'control', 'candidate': 'repeat'},
        sequence=[dict(role=role, label=label, priority=priority) for role, label, priority in
                  [('baseline', 'base-before', 40), ('candidate', 'candidate-first', 30),
                   ('candidate', 'candidate-second', 20), ('baseline', 'base-after', 10)]])


POLICY = {'version': 'p35-abba-v1', 'min_paired_reduction': .02, 'max_within_role_spread': .05}


class UnknownOperationTransport:
    """Hide one durable synthetic receipt, as a transient transport loss would."""
    def __init__(self, backend, unknown_lookups):
        self.backend = backend
        self.unknown_lookups = unknown_lookups
        self.operation_id = None
        self.submissions = []

    def lookup(self, operation_id):
        if operation_id == self.operation_id and self.unknown_lookups != 0:
            if self.unknown_lookups is not None:
                self.unknown_lookups -= 1
            return {'status': 'unknown'}
        return self.backend.lookup(operation_id)

    def submit(self, request):
        self.submissions.append(request['operation_id'])
        result = self.backend.submit(request)
        if self.operation_id is None:
            self.operation_id = request['operation_id']
            return {'status': 'unknown'}
        return result

    def poll(self, operation_id):
        return self.backend.poll(operation_id)


class PolicyDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.prepare, self.run = modules(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.store = ExperimentStore(self.directory / 'experiments.sqlite3', self.directory / 'artifacts')
        self.store.append(Task('task', 'Policy dispatch boundary fixture', 'v1'), 'task')
        self.controller = Controller(self.store, 'task', None, limits=LIMITS)
        self.spec = selection()
        self.input_path = self.directory / 'selection.json'
        self.input_path.write_text(canonical_json(self.spec), encoding='utf-8')
        self.policy_path = self.directory / 'p35-confirmation-policy.json'
        self.policy_path.write_text(canonical_json(POLICY), encoding='utf-8')
        self.target = self.prepare.target_for(self.directory, self.spec)
        self.target.mkdir()
        (self.target / 'selection.json').write_text(canonical_json(self.spec), encoding='utf-8')
        self.variants_path = self.directory / 'variants.json'
        origins = {role: {'variant': {'label': role, 'priority': 0}} for role in ('baseline', 'candidate')}
        variants, plans = {}, []
        for role, plan_id in self.spec['plan_roles'].items():
            rows = [row for row in self.spec['sequence'] if row['role'] == role]
            variants[plan_id] = [{**origins[role]['variant'], 'label': row['label'], 'priority': row['priority'],
                                 'cost': {'executions': 1, 'tokens': 0, 'time_s': 1800.0}} for row in rows]
            plans.append({'id': plan_id, 'strategy_id': 'A', 'variants': [row['label'] for row in rows]})
        self.variants_path.write_text(canonical_json(variants), encoding='utf-8')
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # Original prerequisites/session validation have integration coverage in
        # RecoveryTests. Here real Store/policy records exercise the dispatch
        # boundary, and any publication/intake/backend access is a test failure.
        prepared = {'manifest': SimpleNamespace(artifact_ref=ArtifactRef('a' * 64, 1)), 'histories': {}}
        self.stack.enter_context(patch.object(self.run, 'prerequisites',
            return_value=(self.controller.journal.load(), origins)))
        self.stack.enter_context(patch.object(self.run, 'load_prepared', return_value=prepared))
        self.stack.enter_context(patch.object(self.run, 'completed_session', return_value={'plan_set': {'plans': plans}}))
        for name in ('publish_result', 'intake_publication', 'Controller', 'colab_remote', 'ColabBackend'):
            self.stack.enter_context(patch.object(self.run, name, side_effect=AssertionError('Dispatch reached ' + name)))

    async def assert_dispatch_rejected_without_side_effects(self):
        before = len(self.store.history('task'))
        state = self.controller.journal.load()
        with self.assertRaises((ValueError, FileNotFoundError)):
            await self.run.run(self.input_path, self.variants_path, directory=self.directory)
        self.assertEqual(len(self.store.history('task')), before)
        self.assertEqual(self.controller.journal.load(), state)
        self.assertFalse((self.target / 'execution-input.json').exists())
        self.assertFalse((self.target / 'intake.json').exists())

    async def test_prepare_requires_policy_before_session_or_store_writes(self):
        self.policy_path.unlink()
        before = len(self.store.history('task'))
        with patch.object(self.prepare, 'prerequisites', return_value=None), \
             patch.object(self.prepare, 'load_prepared', side_effect=AssertionError('Preparation passed missing policy')):
            with self.assertRaises((ValueError, FileNotFoundError)):
                await self.prepare.prepare(self.input_path, directory=self.directory)
        self.assertEqual(len(self.store.history('task')), before)
        self.assertFalse((self.target / 'runtime-binding.json').exists())

    async def test_prepare_rejects_changed_saved_selection_before_freezing_policy(self):
        changed = deepcopy(self.spec)
        changed['knowledge'] = [{'method_id': 'changed-after-legacy-prepare'}]
        self.input_path.write_text(canonical_json(changed), encoding='utf-8')
        before = len(self.store.history('task'))
        with patch.object(self.prepare, 'prerequisites', return_value=None):
            with self.assertRaises(ValueError):
                await self.prepare.prepare(self.input_path, directory=self.directory)
        self.assertEqual(len(self.store.history('task')), before)
        self.assertEqual(json.loads((self.target / 'selection.json').read_bytes()), self.spec)

    async def test_run_rejects_missing_policy_before_publication(self):
        self.policy_path.unlink()
        await self.assert_dispatch_rejected_without_side_effects()

    async def test_run_rejects_missing_frozen_marker_before_publication(self):
        await self.assert_dispatch_rejected_without_side_effects()

    async def test_run_rejects_frozen_selection_mismatch_before_publication(self):
        from p35_publish_confirmation import freeze_policy
        changed = deepcopy(self.spec)
        changed['knowledge'] = [{'method_id': 'other-selection'}]
        freeze_policy(self.store, self.directory, changed)
        await self.assert_dispatch_rejected_without_side_effects()


class OrderTests(unittest.TestCase):
    def setUp(self):
        self.prepare, self.run = modules(self)

    def inputs(self):
        spec = selection()
        state = dict(task_id='task', jobs={}, limits=validate_limits(LIMITS),
                     spent={'executions': 12, 'tokens': 0, 'time_s': 21600}, served={'A': 7},
                     paused=[], cancelled=False)
        jobs = [dict(id='job-' + str(i), strategy_id='A', round_id=spec['round_id'],
                     state='queued', reserved=False,
                     variant={'label': row['label'], 'priority': row['priority'],
                              'cost': {'executions': 1, 'tokens': 0, 'time_s': 1800.0}})
                for i, row in enumerate(spec['sequence'])]
        return spec, state, jobs

    def test_original_scheduler_preview_is_abba_without_spending(self):
        spec, state, jobs = self.inputs()
        before = deepcopy(state)
        self.assertEqual(self.run.preview_order(state, jobs, spec), ['job-0', 'job-1', 'job-2', 'job-3'])
        self.assertEqual(state, before)

    def test_wrong_priority_paused_strategy_and_budget_shortfall_reject(self):
        spec, state, jobs = self.inputs()
        jobs[3]['variant']['priority'] = 99
        with self.assertRaises(ValueError): self.run.preview_order(state, jobs, spec)
        spec, state, jobs = self.inputs()
        state['paused'] = ['A']
        with self.assertRaises(ValueError): self.run.preview_order(state, jobs, spec)
        spec, state, jobs = self.inputs()
        state['spent']['executions'] = 13
        with self.assertRaises(ValueError): self.run.preview_order(state, jobs, spec)

    def test_invalid_abba_roles_and_duplicate_labels_reject(self):
        spec = selection()
        spec['sequence'][3]['role'] = 'candidate'
        with self.assertRaises(ValueError): self.prepare.validate_selection(spec)
        spec = selection()
        spec['sequence'][3]['label'] = 'base-before'
        with self.assertRaises(ValueError): self.prepare.validate_selection(spec)

    def test_previous_round_window_accepts_round1_only_or_original_pair(self):
        spec = selection()
        self.assertIs(self.prepare.validate_selection(spec), spec)
        spec['previous_round_ids'] = ['real-runtime2-round:1']
        try:
            validated = self.prepare.validate_selection(spec)
        except ValueError as exc:
            self.fail('Round1-only confirmation should be accepted: ' + str(exc))
        self.assertIs(validated, spec)

        for previous in (
            [],
            ['real-runtime2-round:2'],
            ['real-runtime2-round:2', 'real-runtime2-round:1'],
            ['real-runtime2-round:1', 'unknown-round'],
            ['real-runtime2-round:1', 'real-runtime2-round:1'],
            ['real-runtime2-round:1', 'real-runtime2-round:2', 'real-runtime2-round:3'],
        ):
            spec['previous_round_ids'] = previous
            with self.assertRaises(ValueError):
                self.prepare.validate_selection(spec)


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.prepare, self.run = modules(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        (self.directory / 'p35-confirmation-policy.json').write_text(canonical_json(POLICY), encoding='utf-8')
        modes = {key: ['success'] for key in ('A-P1', 'A-P2', 'B-P1', 'B-P2')}
        self.f = await make_workflow_fixture(self.directory, scenarios=modes, intake=False)
        self.store = self.f['store']
        self.backend = SyntheticBackend(self.store, self.directory / 'workspaces')
        self.controller = Controller(self.store, 'task', self.backend, limits=LIMITS)
        for index in (1, 2):
            marker = publish_result(self.store, self.f['manifest'], self.f['result'], 'prior:' + str(index))
            intake = await intake_publication(self.store, marker.id, 'real-runtime2-round:' + str(index),
                                              self.f['variants'], self.f['knowledge'])
            self.controller.accept_intake(intake)
            self.controller.run_until_idle()
        jobs = list(self.controller.status()['jobs'].values())
        self.baseline = next(j for j in jobs if j['round_id'] == 'real-runtime2-round:1' and j['strategy_id'] == 'A')
        self.selected = next(j for j in jobs if j['round_id'] == 'real-runtime2-round:2'
                             and j['strategy_id'] == 'A' and j['variant']['source_ref'] != self.baseline['variant']['source_ref'])
        self.spec = selection()
        self.spec.update(candidate_id=self.selected['candidate_id'],
                         baseline_candidate_id=self.baseline['candidate_id'], knowledge=self.f['request']['knowledge'])
        self.input_path = self.directory / 'selection.json'
        self.input_path.write_text(canonical_json(self.spec), encoding='utf-8')
        owner = self
        class KnowledgeContext:
            async def __aenter__(self): return owner.f['knowledge']
            async def __aexit__(self, *args): return False
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # Existing real receipt Gate verification has its own CPU contract tests.
        # Only real prerequisites and external transport are substituted here;
        # actual round markers, publication, budgets and Controller are exercised.
        self.stack.enter_context(patch.object(self.prepare, 'require_gates', return_value=(
            {'baseline-repair': self.baseline, 'baseline-b': self.baseline}, {})))
        self.stack.enter_context(patch.object(self.prepare, 'verified_gate_result',
                                             side_effect=lambda store, job: job['result']))
        self.stack.enter_context(patch.object(self.prepare, 'StdioKnowledge', return_value=KnowledgeContext()))
        self.stack.enter_context(patch.object(self.run, 'StdioKnowledge', return_value=KnowledgeContext()))
        self.stack.enter_context(patch.object(self.run, 'colab_remote', return_value=None))
        self.stack.enter_context(patch.object(self.run, 'ColabBackend', return_value=self.backend))
        self.stack.enter_context(patch.object(self.run.time, 'sleep'))

    async def prepare_session(self):
        output = await self.prepare.prepare(self.input_path, directory=self.directory)
        return Path(output['directory'])

    async def complete_session(self, target):
        manifest = self.prepare.load_prepared(self.store, 'task', 'context', ['A'], self.spec['round_id'])['manifest']
        answer = proposal()
        answer['code_understanding'] = answer['code_understanding'][:1]
        answer['plans'] = answer['plans'][:2]
        specs = {}
        for plan, role in zip(answer['plans'], ('baseline', 'candidate')):
            rows = [r for r in self.spec['sequence'] if r['role'] == role]
            plan.update(id=self.spec['plan_roles'][role], parent_id=manifest.to_dict()['strategies']['A']['parent_id'],
                        variants=[r['label'] for r in rows])
            origin = self.baseline if role == 'baseline' else self.selected
            specs[plan['id']] = [{**deepcopy(origin['variant']), 'label': r['label'], 'priority': r['priority'],
                                 'cost': {'executions': 1, 'tokens': 0, 'time_s': 1800.0}} for r in rows]
        response = target / 'test-response.json'
        response.write_text(canonical_json({'final': answer}), encoding='utf-8')
        submit_response(self.store, target, response)
        variants = self.directory / 'variants.json'
        variants.write_text(canonical_json(specs), encoding='utf-8')
        return variants

    async def test_prepare_recovery_preserves_manifest_and_rejects_changed_selection(self):
        target = await self.prepare_session()
        manifest_before = (target / 'manifest-ref.json').read_bytes()
        count = len(self.store.history('task'))
        with patch.object(self.prepare, 'StdioKnowledge', side_effect=AssertionError('must restore pinned session')):
            self.assertEqual(await self.prepare_session(), target)
        self.assertEqual(len(self.store.history('task')), count)
        self.assertEqual((target / 'manifest-ref.json').read_bytes(), manifest_before)
        changed = deepcopy(self.spec)
        changed['sequence'][0]['priority'] = 41
        self.input_path.write_text(canonical_json(changed), encoding='utf-8')
        with self.assertRaises(ValueError): await self.prepare_session()
        self.assertEqual((target / 'manifest-ref.json').read_bytes(), manifest_before)

    async def test_prepared_prior_round_window_cannot_be_changed(self):
        target = await self.prepare_session()
        original = (target / 'selection.json').read_bytes()
        count = len(self.store.history('task'))
        self.spec['previous_round_ids'] = ['real-runtime2-round:1']
        self.input_path.write_text(canonical_json(self.spec), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'selection|recovery input'):
            await self.prepare_session()
        self.assertEqual((target / 'selection.json').read_bytes(), original)
        self.assertEqual(len(self.store.history('task')), count)

    async def test_round1_only_preparation_pins_completed_round1_history(self):
        state = self.controller.journal.load()
        round1 = 'real-runtime2-round:1'
        state['jobs'] = {key: job for key, job in state['jobs'].items() if job['round_id'] == round1}
        state['intakes'] = {key: digest for key, digest in state['intakes'].items()
                            if self.store.get(key).details['result']['round_id'] == round1}
        self.controller.journal.save(state, 'Synthetic journal before round2 admission')
        self.selected = next(job for job in state['jobs'].values()
                             if job['strategy_id'] == 'A' and job['candidate_id'] != self.baseline['candidate_id'])
        self.spec.update(candidate_id=self.selected['candidate_id'], previous_round_ids=[round1])
        self.input_path.write_text(canonical_json(self.spec), encoding='utf-8')
        try:
            target = await self.prepare_session()
        except ValueError as exc:
            self.fail('Completed round1 should permit confirmation before round2: ' + str(exc))
        self.assertTrue((target / 'session.json').exists())
        prepared = self.prepare.load_prepared(self.store, 'task', 'context', ['A'], self.spec['round_id'])
        recorded = {row['payload']['id'] for history in prepared['histories'].values() for row in history['records']}
        self.assertTrue(all(job['run_id'] in recorded for job in state['jobs'].values() if job['strategy_id'] == 'A'))
        state = self.controller.journal.load()
        state['jobs'][self.baseline['id']]['state'] = 'blocked'
        self.controller.journal.save(state, 'Synthetic interrupted round1 prerequisite')
        with self.assertRaisesRegex(ValueError, 'Previous round still has unfinished'):
            await self.prepare_session()

    async def test_unfinished_prior_attempt_and_insufficient_budget_block_preparation(self):
        state = self.controller.journal.load()
        saved = deepcopy(state)
        state['jobs'][self.selected['id']]['state'] = 'blocked'
        self.controller.journal.save(state, 'Synthetic interrupted prerequisite')
        with self.assertRaises(ValueError): await self.prepare_session()
        state = self.controller.journal.load()
        state['jobs'] = saved['jobs']
        state['spent']['executions'] = 13
        self.controller.journal.save(state, 'Synthetic exhausted budget')
        with self.assertRaises(ValueError): await self.prepare_session()
        self.assertFalse(list(self.directory.glob('confirmation-*/session.json')))

    async def test_changed_candidate_artifact_rejected_before_new_attempt(self):
        target = await self.prepare_session()
        variants = await self.complete_session(target)
        data = json.loads(variants.read_bytes())
        data['repeat'][0]['source_ref'] = deepcopy(self.baseline['variant']['source_ref'])
        variants.write_text(canonical_json(data), encoding='utf-8')
        before = self.controller.status()['spent']
        with self.assertRaises(ValueError):
            await self.run.run(self.input_path, variants, directory=self.directory)
        self.assertEqual(self.controller.status()['spent'], before)
        self.assertEqual(len(self.controller.status()['jobs']), 8)

    async def test_unknown_operation_reconciles_within_steps_without_resubmit_or_charge(self):
        target = await self.prepare_session()
        variants = await self.complete_session(target)
        transport = UnknownOperationTransport(self.backend, unknown_lookups=1)
        with patch.object(self.run, 'ColabBackend', return_value=transport), redirect_stdout(io.StringIO()):
            await self.run.run(self.input_path, variants, directory=self.directory, steps=1)
            blocked = self.controller.status()
            job = next(j for j in blocked['jobs'].values() if j['state'] == 'blocked')
            operation = deepcopy(job['operation'])
            await self.run.run(self.input_path, variants, directory=self.directory, steps=2, interval=1)
        state = self.controller.status()
        resumed = state['jobs'][job['id']]
        self.assertEqual(resumed['state'], 'active')
        self.assertEqual(resumed['stage'], 'compile')
        self.assertEqual(resumed['receipts']['generate']['operation_id'], operation['operation_id'])
        self.assertNotIn('operation', resumed)
        self.assertEqual(state['spent'], blocked['spent'])
        self.assertEqual(set(state['jobs']), set(blocked['jobs']))
        self.assertEqual(resumed['run_id'], job['run_id'])
        self.assertEqual(resumed['attempt'], 0)
        self.assertEqual(transport.submissions, [operation['operation_id']])

    async def test_permanent_unknown_uses_step_budget_and_keeps_original_reservation(self):
        target = await self.prepare_session()
        variants = await self.complete_session(target)
        before = self.controller.status()
        transport = UnknownOperationTransport(self.backend, unknown_lookups=None)
        output = io.StringIO()
        with patch.object(self.run, 'ColabBackend', return_value=transport), redirect_stdout(output):
            await self.run.run(self.input_path, variants, directory=self.directory, steps=4, interval=1)
        state = self.controller.status()
        jobs = [j for j in state['jobs'].values() if j['round_id'] == self.spec['round_id']]
        blocked = next(j for j in jobs if j['state'] == 'blocked')
        self.assertEqual(len(output.getvalue().splitlines()), 4)
        self.assertEqual(len(jobs), 4)
        self.assertEqual(sum(j['state'] == 'queued' for j in jobs), 3)
        self.assertEqual(blocked['operation']['operation_id'], transport.operation_id)
        self.assertEqual(transport.submissions, [transport.operation_id])
        self.assertEqual(state['spent'], {'executions': before['spent']['executions'] + 1,
            'tokens': before['spent']['tokens'], 'time_s': before['spent']['time_s'] + 1800.0})
        self.assertFalse((target / 'completed.json').exists())

    async def test_blocked_without_operation_still_stops_before_step_budget(self):
        target = await self.prepare_session()
        variants = await self.complete_session(target)
        with redirect_stdout(io.StringIO()):
            await self.run.run(self.input_path, variants, directory=self.directory, steps=1)
        state = self.controller.journal.load()
        job = next(j for j in state['jobs'].values() if j['round_id'] == self.spec['round_id'] and j['reserved'])
        self.assertNotIn('operation', job)
        job.update(state='blocked', reason='Fixture requires manual reconciliation without operation')
        self.controller.journal.save(state, 'Synthetic blocked operationless job')
        output = io.StringIO()
        with redirect_stdout(output):
            await self.run.run(self.input_path, variants, directory=self.directory, steps=4, interval=1)
        after = self.controller.status()
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        self.assertEqual(after['jobs'][job['id']]['state'], 'blocked')
        self.assertEqual(after['spent'], state['spent'])

    async def test_partial_run_restart_keeps_ids_budget_and_abba_order(self):
        target = await self.prepare_session()
        variants = await self.complete_session(target)
        with redirect_stdout(io.StringIO()):
            await self.run.run(self.input_path, variants, directory=self.directory, steps=1)
            admitted = self.controller.status()
            jobs = [j for j in admitted['jobs'].values() if j['round_id'] == self.spec['round_id']]
            self.assertEqual(len(jobs), 4)
            self.assertEqual([j['variant']['label'] for j in jobs if j['reserved']], ['base-before'])
            await self.run.run(self.input_path, variants, directory=self.directory, steps=100)
            state = self.controller.status()
            self.assertEqual(state['workflow_state'], 'completed')
            self.assertEqual(state['spent'], {'executions': 12, 'tokens': 0, 'time_s': 7202.0})
            completion = (target / 'completed.json').read_bytes()
            count = len(self.store.history('task'))
            await self.run.run(self.input_path, variants, directory=self.directory, steps=100)
        self.assertEqual(len(self.store.history('task')), count)
        self.assertEqual((target / 'completed.json').read_bytes(), completion)
        self.assertEqual(self.controller.status()['spent'], state['spent'])
        labels = json.loads(completion)['order']
        self.assertEqual(labels, ['base-before', 'candidate-first', 'candidate-second', 'base-after'])
        changes = [r.details['state'] for r in self.store.history('task')
                   if getattr(r, 'details', {}).get('type') == 'workflow_state.v1']
        actual = []
        for journal in changes:
            for job in journal['jobs'].values():
                if job['round_id'] == self.spec['round_id'] and job['reserved'] and job['variant']['label'] not in actual:
                    actual.append(job['variant']['label'])
        self.assertEqual(actual, labels)


if __name__ == '__main__':
    unittest.main(verbosity=2)
