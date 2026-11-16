"""Local real-protocol continuation fixtures; no production Store or hardware."""
import asyncio
from copy import deepcopy
from hashlib import sha256
import importlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / 'src'), str(ROOT / 'tests')]
import test_workflow_budget as budget_tests
from test_analyzer import proposal
from rsi_kernel_agent.contracts import ArtifactRef, canonical_json
from rsi_kernel_agent.profiler.session import submit_response
from rsi_kernel_agent.store import StoreError
from rsi_kernel_agent.workflow.controller import Controller
from rsi_kernel_agent.workflow.execution import stable_id


class FixtureBackend:
    """Only replace Colab; preserve raw durable real-protocol receipt publication."""
    def __init__(self, fixture, lost_ack=False):
        self.fx, self.lost_ack = fixture, lost_ack

    def lookup(self, operation):
        try:
            marker = self.fx.store.get(stable_id('execution-receipt', operation))
        except StoreError:
            return {'status': 'definitely_not_started', 'receipt': None}
        ref = marker.details['artifact_ref']
        body = json.loads(self.fx.store.artifacts.read(ArtifactRef(**ref)))
        return {'status': 'completed', 'receipt': {**body, 'artifact_ref': ref}}

    def submit(self, request):
        receipt = self.fx.receipt(request['job'], request['stage'],
                                  self.fx.outputs(request['job'])[request['stage']])
        if self.lost_ack:
            self.lost_ack = False
            return {'status': 'unknown', 'receipt': None}
        return {'status': 'completed', 'receipt': receipt}

    def poll(self, operation):
        return self.lookup(operation)


class ContinuationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNotNone(importlib.util.find_spec('p35_continuation_round'),
                             'Bounded continuation helper is not implemented')
        self.api = importlib.import_module('p35_continuation_round')
        self.base = budget_tests.WorkflowBudgetTests()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.store, self.fx, self.root = self.base.store, self.base.fx, self.base.root
        self.previous = self.base.completed()
        grant = self.base.grant()
        grant['new_limits'].update(executions=3, time_s=3)
        grant['next_batch']['cost'].update(executions=2, time_s=2)
        self.entry = self.base.extend(grant)
        self.spec = {'schema_version': 1, 'task_id': 'task', 'round_id': 'round:2',
            'context_id': 'context', 'runtime_generation': 'runtime5',
            'runtime_id': 'local-fixture-session', 'strategies': ['A'],
            'grant_key': 'batch2', 'grant_digest': self.entry['digest'],
            'milestone': deepcopy(grant['milestone']), 'knowledge': [],
            'milestone_validation': {'final_memory_report': {'path': 'fixture', 'sha256': '0'*64}}}
        gates = {'baseline-repair': self.previous, 'baseline-b': self.previous}
        self.patch = patch.object(self.api, 'require_gates', return_value=(gates, {}))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        # This suite exercises the original integration chain at small costs.
        # Dedicated B2 evidence and route gates have separate unmocked tests.
        for name in ('validate_p35_milestone', 'validate_b2_variants'):
            patcher = patch.object(self.api, name)
            patcher.start(); self.addCleanup(patcher.stop)
        (self.root / 'colab-config-runtime5.json').write_text(json.dumps(
            {'runtime_id': 'local-fixture-session'}), encoding='utf-8')

    async def prepare(self):
        return await self.api.prepare(self.store, self.root, self.spec, None)

    async def author(self):
        prepared = await self.prepare()
        target = self.api.target_for(self.root, self.spec)
        response = target / 'fixture-response.json'
        response.write_text(json.dumps({'tool_calls': [
            {'id': 'source', 'name': 'source', 'arguments': {'strategy_id': 'A'}},
            {'id': 'history', 'name': 'history', 'arguments': {'strategy_id': 'A'}}]}), encoding='utf-8')
        submit_response(self.store, target, response)
        answer = proposal()
        answer['code_understanding'] = answer['code_understanding'][:1]
        answer['plans'] = answer['plans'][:2]
        variants = {}
        for i, plan in enumerate(answer['plans']):
            plan['parent_id'] = prepared['request']['strategies'][0]['parent_id']
            plan['variants'] = ['fixture-' + str(i)]
            variants[plan['id']] = [{**deepcopy(self.fx.variant), 'label': plan['variants'][0]}]
        response.write_text(json.dumps({'final': answer}), encoding='utf-8')
        submit_response(self.store, target, response)
        return variants

    async def test_prepare_freezes_original_snapshot_parent_and_readable_budget_note(self):
        first = await self.prepare()
        before = len(self.store.history('task'))
        second = await self.prepare()
        self.assertEqual(first['manifest'].artifact_ref, second['manifest'].artifact_ref)
        self.assertEqual(len(self.store.history('task')), before)
        history = first['histories']['A']['records']
        self.assertTrue(any(row['payload'].get('details', {}).get('type') ==
                            'workflow_continuation_inputs.v1' for row in history))
        self.assertEqual(first['request']['strategies'][0]['run_id'], 'run')
        self.assertIn('run', {row['payload']['id'] for row in history})
        self.assertEqual(Controller(self.store, 'task', None).status()['spent']['executions'], 1)

    async def test_dedicated_milestone_failure_prevents_prepare_and_store_writes(self):
        before = len(self.store.history('task'))
        with patch.object(self.api, 'validate_p35_milestone', side_effect=ValueError('B2 evidence missing')):
            with self.assertRaisesRegex(ValueError, 'B2 evidence missing'): await self.prepare()
        self.assertEqual(len(self.store.history('task')), before)

    async def test_missing_grant_wrong_context_runtime_and_milestone_rejected_before_prepare(self):
        before = len(self.store.history('task'))
        cases = [('grant_key', 'missing'), ('grant_digest', '0' * 64),
                 ('round_id', 'unfunded'), ('context_id', 'other'), ('runtime_id', 'other')]
        for field, value in cases:
            spec = deepcopy(self.spec)
            spec[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                await self.api.prepare(self.store, self.root, spec, None)
        spec = deepcopy(self.spec)
        spec['milestone']['rounds'][0]['run_ids'] = []
        with self.assertRaises(ValueError): await self.api.prepare(self.store, self.root, spec, None)
        self.assertEqual(len(self.store.history('task')), before)

    async def test_changed_frozen_spec_or_config_is_rejected(self):
        await self.prepare()
        changed = deepcopy(self.spec)
        changed['knowledge'] = [{'method_id': 'new', 'note_id': 'new', 'revision': '0' * 64}]
        with self.assertRaises(ValueError): await self.api.prepare(self.store, self.root, changed, None)
        (self.root / 'colab-config-runtime5.json').write_text('{"runtime_id":"different"}', encoding='utf-8')
        with self.assertRaises(ValueError): await self.prepare()

    async def test_real_chain_lost_ack_reuses_operations_and_does_not_charge_twice(self):
        variants = await self.author()
        first = await self.api.run(self.store, self.root, self.spec, variants, None,
                                   FixtureBackend(self.fx, lost_ack=True), steps=1)
        self.assertEqual(sum(j['state'] == 'blocked' for j in first['jobs'].values()), 1)
        state = await self.api.run(self.store, self.root, self.spec, variants, None,
                                   FixtureBackend(self.fx), steps=30)
        jobs = [j for j in state['jobs'].values() if j['round_id'] == 'round:2']
        self.assertEqual(len(jobs), 2)
        self.assertEqual({j['state'] for j in jobs}, {'completed'})
        self.assertEqual(state['spent'], {'executions': 3, 'tokens': 0, 'time_s': 3})
        self.assertTrue(all(j['result']['confirmed'] for j in jobs))
        old_count = len(self.store.history('task'))
        completed = (self.api.target_for(self.root, self.spec) / 'completed.json').read_bytes()
        again = await self.api.run(self.store, self.root, self.spec, variants, None,
                                   FixtureBackend(self.fx), steps=1)
        self.assertEqual(again['spent'], state['spent'])
        self.assertEqual(len(self.store.history('task')), old_count)
        self.assertEqual((self.api.target_for(self.root, self.spec) / 'completed.json').read_bytes(), completed)

    async def test_changed_or_overbudget_variants_rejected_without_dispatch(self):
        variants = await self.author()
        changed = deepcopy(variants)
        changed['A-P1'][0]['cost']['time_s'] = 2
        before = len(self.store.history('task'))
        with self.assertRaises(ValueError):
            await self.api.run(self.store, self.root, self.spec, changed, None, FixtureBackend(self.fx))
        self.assertEqual(len(self.store.history('task')), before)
        await self.api.run(self.store, self.root, self.spec, variants, None, FixtureBackend(self.fx), steps=1)
        changed = deepcopy(variants)
        changed['A-P1'][0]['priority'] = 1
        with self.assertRaises(ValueError):
            await self.api.run(self.store, self.root, self.spec, changed, None, FixtureBackend(self.fx))

    async def test_unrelated_job_and_refund_are_rejected_before_step(self):
        variants = await self.author()
        state = self.base.c.journal.load()
        state['jobs']['foreign'] = {**deepcopy(self.previous), 'id': 'foreign',
                                    'round_id': 'foreign', 'state': 'queued', 'reserved': False}
        self.base.c.journal.save(state, 'Foreign fixture work')
        with self.assertRaises(ValueError):
            await self.api.run(self.store, self.root, self.spec, variants, None, FixtureBackend(self.fx))
        state = self.base.c.journal.load()
        state['jobs'].pop('foreign')
        state['spent']['time_s'] = 0
        self.base.c.journal.save(state, 'Refund fixture')
        with self.assertRaises(ValueError):
            await self.api.run(self.store, self.root, self.spec, variants, None, FixtureBackend(self.fx))

    async def test_prepare_crash_after_pinned_round_resumes_same_manifest(self):
        original = self.api.start_session
        with patch.object(self.api, 'start_session', side_effect=RuntimeError('session boundary')):
            with self.assertRaises(RuntimeError): await self.prepare()
        state = self.base.c.journal.load()
        self.assertEqual(state['spent']['executions'], 1)
        prepared = await self.prepare()
        count = len(self.store.history('task'))
        repeated = await self.prepare()
        self.assertEqual(prepared['manifest'].artifact_ref, repeated['manifest'].artifact_ref)
        self.assertEqual(len(self.store.history('task')), count)

    async def test_intake_response_loss_recovers_original_candidates(self):
        variants = await self.author()
        original = self.api.intake_publication
        async def lost(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError('lost intake response')
        with patch.object(self.api, 'intake_publication', side_effect=lost):
            with self.assertRaises(RuntimeError):
                await self.api.run(self.store, self.root, self.spec, variants, None, FixtureBackend(self.fx))
        state = await self.api.run(self.store, self.root, self.spec, variants, None,
                                   FixtureBackend(self.fx), steps=30)
        self.assertEqual(len([j for j in state['jobs'].values() if j['round_id'] == 'round:2']), 2)
        self.assertEqual(state['spent']['executions'], 3)


if __name__ == '__main__': unittest.main(verbosity=2)
