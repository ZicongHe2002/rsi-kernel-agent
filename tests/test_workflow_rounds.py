"""Round preparation reads every card and complete participating histories."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from phase2d_fixtures import make_workflow_fixture
from rsi_kernel_agent.contracts import (Candidate, EvaluationBundle, Evidence, Plan,
    ProfileBundle, Run, RunEvent)


class RoundTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertTrue((Path(__file__).resolve().parents[1] / 'src/rsi_kernel_agent/workflow/rounds.py').exists(),
                        'Round preparation must reuse Memory and AnalysisRequest')
        from rsi_kernel_agent.workflow.rounds import prepare_round
        self.prepare = prepare_round
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = await make_workflow_fixture(self.tmp.name)

    async def call(self, **options):
        return await self.prepare(self.f['store'], self.f['knowledge'], 'task', 'context', ('A', 'B'),
                                  'round:2', self.f['request']['knowledge'], **options)

    async def test_all_cards_and_complete_independent_histories_enter_new_manifest(self):
        prepared = await self.call()
        self.assertEqual([c['strategy_id'] for c in prepared['cards']], list('ABCDE'))
        self.assertEqual(set(prepared['histories']), {'A', 'B'})
        self.assertTrue(all(h['complete'] for h in prepared['histories'].values()))
        data = prepared['manifest'].to_dict()
        self.assertEqual(data['strategies']['A']['run']['id'], 'A-run')
        self.assertEqual(data['strategies']['B']['run']['id'], 'B-run')
        self.assertEqual(data['strategies']['A']['history'], prepared['histories']['A'])
        self.assertGreater(data['strategies']['A']['history']['total'], 14)

    async def test_duplicate_round_is_frozen_after_later_best_changes(self):
        prepared = await self.call()
        store = self.f['store']
        store.append(Plan('later-plan', 'A', 'Later unmeasured plan'), 'later-plan')
        repeated = await self.call()
        self.assertEqual(repeated['manifest'].artifact_ref, prepared['manifest'].artifact_ref)
        self.assertEqual(repeated['snapshot'], prepared['snapshot'])
        self.assertEqual(repeated['histories'], prepared['histories'])

    async def test_explicit_wrong_strategy_parent_is_rejected(self):
        with self.assertRaises(ValueError):
            await self.call(parents={'A': 'B-parent', 'B': 'B-parent'}, parent_policy='explicit')

    async def test_round_preparation_cannot_race_an_active_controller(self):
        from rsi_kernel_agent.workflow.journal import WorkflowJournal
        with WorkflowJournal(self.f['store'], 'task').lock():
            with self.assertRaises(ValueError):
                await self.call()

    async def test_branch_best_selects_new_exact_candidate_and_missing_profile_stays_missing(self):
        store = self.f['store']
        job = self.f['intake']['items'][0]['jobs'][0]
        from rsi_kernel_agent.contracts import CandidateIdentity
        identity = CandidateIdentity(job['variant']['source_ref']['digest'], job['variant']['config_ref']['digest'],
                                     job['variant']['build_ref']['digest'])
        store.append(Candidate('new-A', job['plan_id'], 'A-parent', identity), 'new-A')
        store.append(Run('new-run', 'new-A', 'context'), 'new-run')
        store.transition(RunEvent('new-start', 'new-run', 'running', 'active', 'Synthetic'), None, 'new-start')
        store.transition(RunEvent('new-done', 'new-run', 'succeeded', 'finished', 'Synthetic'), 'new-start', 'new-done')
        artifact = store.artifacts.put(b'{"provenance":"synthetic","latency_ms":7}')
        store.append(Evidence('new-time', 'new-run', 'measured_timing', 'available', 'completed', 'supported',
                              7, 'ms', (artifact,), source={'provenance': 'synthetic'}), 'new-time')
        store.append(EvaluationBundle('new-eval', 'new-run', 'passed', 'valid', 7, True, ('new-time',)), 'new-eval')
        store.consider('new-eval', 'new-promote')
        prepared = await self.call()
        binding = prepared['manifest'].to_dict()['strategies']['A']
        self.assertEqual(binding['parent']['candidate_id'], 'new-A')
        self.assertEqual(binding['run']['id'], 'new-run')
        self.assertEqual(binding['profiles'], [])
        self.assertEqual(binding['benchmarks'][0]['id'], 'new-eval')
        self.assertEqual(prepared['cards'][0]['current_parent_candidate']['candidate_id'], 'new-A')


if __name__ == '__main__':
    unittest.main()
