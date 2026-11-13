"""Intake preserves per-Plan outcomes and verified 2C publication identities."""
import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from phase2d_fixtures import make_workflow_fixture
from rsi_kernel_agent.contracts import Decision


class IntakeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertTrue((Path(__file__).resolve().parents[1] / 'src/rsi_kernel_agent/workflow/intake.py').exists(),
                        'Verified per-Plan intake must be implemented')
        from rsi_kernel_agent.workflow.intake import intake_publication
        self.intake = intake_publication
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = await make_workflow_fixture(self.tmp.name, intake=False)

    async def call(self, variants=None, round_id='round:1', knowledge='default'):
        return await self.intake(self.f['store'], self.f['publication'].id, round_id,
                                 self.f['variants'] if variants is None else variants,
                                 self.f['knowledge'] if knowledge == 'default' else knowledge)

    async def test_variable_candidates_preserve_bindings_and_all_outcomes(self):
        result = await self.call()
        self.assertEqual([x['status'] for x in result['items']], ['accepted'] * 4)
        self.assertEqual([len(x['jobs']) for x in result['items']], [2, 1, 1, 2])
        jobs = [j for item in result['items'] for j in item['jobs']]
        self.assertEqual(len({j['id'] for j in jobs}), 6)
        for job in jobs:
            self.assertEqual(job['parent_id'], job['strategy_id'] + '-parent')
            self.assertEqual(job['parent_evaluation_id'], job['strategy_id'] + '-benchmark')
            self.assertEqual(job['contract']['task']['contract_revision'], 'fixture-v1')
            self.assertEqual(job['contract']['context']['provenance'], 'synthetic')
        saved = self.f['store'].get(result['id'])
        self.assertEqual(saved.details['result'], result)

    async def test_bad_variant_rejects_one_plan_without_discarding_others(self):
        variants = copy.deepcopy(self.f['variants'])
        variants['A-P1'][0]['label'] = 'unpublished replacement'
        result = await self.call(variants)
        self.assertEqual([x['status'] for x in result['items']], ['rejected', 'accepted', 'accepted', 'accepted'])
        self.assertTrue(result['items'][0]['reason'])
        self.assertEqual(result['items'][0]['jobs'], [])

    async def test_duplicate_intake_returns_committed_result_without_extra_records(self):
        result = await self.call()
        before = len(self.f['store'].history('task'))
        self.assertEqual(await self.call(), result)
        self.assertEqual(len(self.f['store'].history('task')), before)
        variants = copy.deepcopy(self.f['variants'])
        variants['A-P1'][0]['priority'] = 5
        with self.assertRaises(ValueError):
            await self.call(variants)

    async def test_same_publication_cannot_create_experiments_in_another_round(self):
        await self.call()
        repeated = await self.call(round_id='round:another')
        self.assertEqual({item['status'] for item in repeated['items']}, {'rejected'})
        self.assertTrue(all(not item['jobs'] for item in repeated['items']))

    async def test_stale_knowledge_waits_and_preserves_pinned_publication(self):
        key = self.f['request']['knowledge'][0]['note_id']
        self.f['documents'][key] += b'\nEdited after publication\n'
        result = await self.call()
        self.assertEqual({x['status'] for x in result['items']}, {'waiting'})
        self.assertTrue(all(not x['jobs'] for x in result['items']))

    async def test_indirect_observation_knowledge_reference_also_requires_freshness(self):
        from rsi_kernel_agent.profiler.analyzer import Analyzer
        from rsi_kernel_agent.profiler.outputs import publish_result
        proposal = copy.deepcopy(self.f['result']['proposal'])
        knowledge_ref = 'knowledge:' + self.f['request']['knowledge'][0]['method_id']
        for plan in proposal['plans']:
            plan['refs'].remove(knowledge_ref)
        proposal['observations'][0]['refs'] = [knowledge_ref]
        proposal['plans'][0]['observation_ids'] = ['o1']
        result = Analyzer(self.f['manifest'], artifacts=self.f['store'].artifacts).run(lambda packet: {'final': proposal})
        self.f['publication'] = publish_result(self.f['store'], self.f['manifest'], result, 'indirect-reference')
        self.f['documents'][self.f['request']['knowledge'][0]['note_id']] += b'\nChanged\n'
        outcome = await self.call()
        self.assertEqual([row['status'] for row in outcome['items']], ['waiting', 'accepted', 'accepted', 'accepted'])

    async def test_unavailable_live_knowledge_is_explicit_waiting(self):
        result = await self.call(knowledge=None)
        self.assertEqual({x['status'] for x in result['items']}, {'waiting'})

    async def test_reference_corruption_cannot_schedule_even_with_completion_marker(self):
        ref = self.f['store'].get('A-timing').artifacts[0]
        self.f['store'].artifacts.path_for(ref).write_bytes(b'corrupted')
        result = await self.call()
        self.assertEqual({x['status'] for x in result['items']}, {'rejected'})
        self.assertEqual(sum(len(x['jobs']) for x in result['items']), 0)

    async def test_unpublished_plan_set_cannot_be_consumed(self):
        with self.assertRaises(ValueError):
            await self.intake(self.f['store'], 'A-initial', 'round:missing', {}, self.f['knowledge'])

    async def test_variant_budget_and_nonfinite_values_reject_locally(self):
        from rsi_kernel_agent.profiler.outputs import publish_result
        for field, value in [('priority', True), ('latency_ms', float('inf')), ('scenario', 'real_tpu')]:
            self.f['publication'] = publish_result(self.f['store'], self.f['manifest'], self.f['result'], 'invalid-variant:' + field)
            variants = copy.deepcopy(self.f['variants'])
            variants['B-P2'][0][field] = value
            result = await self.call(variants, round_id='bad:' + field)
            self.assertEqual(result['items'][-1]['status'], 'rejected')
            self.assertEqual(result['items'][0]['status'], 'accepted')


if __name__ == '__main__':
    unittest.main()
