import copy
from dataclasses import asdict
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from phase2d_fixtures import make_workflow_fixture
from test_analyzer import proposal
from rsi_kernel_agent.contracts import Decision, Strategy
from rsi_kernel_agent.profiler.analyzer import Analyzer
from rsi_kernel_agent.profiler.outputs import publish_result
from rsi_kernel_agent.workflow.controller import Controller
from rsi_kernel_agent.workflow.execution import SyntheticBackend
from rsi_kernel_agent.workflow.intake import intake_publication


class DesignTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from rsi_kernel_agent.workflow import design
        self.api = design
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = await make_workflow_fixture(self.tmp.name, intake=False)
        self.store = self.f['store']
        self.backend = SyntheticBackend(self.store, Path(self.tmp.name) / 'workspaces')
        self.controller = Controller(self.store, 'task', self.backend,
            limits={'executions': 24 if 'slow_new_d' in self._testMethodName else 1, 'max_concurrency': 1})
        self.assessments = {sid: {'status': 'hypothesis' if sid in 'AB' else 'deferred',
                                'reason': 'Independent tile hypothesis' if sid in 'AB' else 'No source parent yet',
                                'hypothesis': 'Change tile' if sid in 'AB' else None, 'refs': []}
                            for sid in 'ABCDE'}

    async def begin(self, **options):
        args = dict(parents={'A': 'A-parent', 'B': 'B-parent'}, knowledge_refs={})
        args.update(options)
        return await self.api.begin_design_round(self.store, self.f['knowledge'], 'task', 'context',
                                                 'batch-1', self.assessments, **args)

    def publish(self, batch, sid, no_plan=False):
        manifest = self.api.route_manifest(self.store, batch.id, sid)
        answer = proposal()
        for field in ('code_understanding', 'observations', 'hypotheses', 'plans'):
            answer[field] = [row for row in answer[field] if row['strategy_id'] == sid]
        if not answer['hypotheses']:
            answer['hypotheses'] = [{'id': 'hypothesis-' + sid, 'strategy_id': sid, 'text': 'Independent route tile hypothesis',
                                     'refs': ['source:' + sid], 'alternatives': ['Retain existing tile']}]
        if no_plan:
            answer['plans'] = []
            answer['hypotheses'] = []
            answer['outcome'] = {'status': 'needs_research', 'reason': 'Need a supported layout model',
                                 'refs': ['source:' + sid]}
        else:
            for row in answer['plans']:
                row['variants'] = [v['label'] for v in self.f['variants'][row['id']]]
        result = Analyzer(manifest).advance({'final': answer})
        marker = publish_result(self.store, manifest, result, 'design-' + sid)
        self.api.finish_route_design(self.store, batch.id, sid, marker.id)
        return marker

    async def test_assessment_covers_every_route_and_input_is_frozen(self):
        del self.assessments['E']
        with self.assertRaises(ValueError):
            await self.begin()
        self.assessments['E'] = {'status': 'deferred', 'reason': 'No source', 'hypothesis': None, 'refs': []}
        batch = await self.begin()
        self.assertEqual(set(batch.details['assessments']), set('ABCDE'))
        before = self.api.route_manifest(self.store, batch.id, 'A').artifact_ref
        self.store.append(Decision('late-A', 'task', 'note', 'A', 'Later unrelated feedback'), 'late-A')
        self.assertEqual((await self.begin()).id, batch.id)
        self.assertEqual(self.api.route_manifest(self.store, batch.id, 'A').artifact_ref, before)

    async def test_stable_route_order_prevents_b_before_a_finishes(self):
        batch = await self.begin()
        with self.assertRaises(ValueError):
            self.api.route_manifest(self.store, batch.id, 'B')
        first = self.api.route_manifest(self.store, batch.id, 'A').to_dict()
        self.publish(batch, 'A')
        second = self.api.route_manifest(self.store, batch.id, 'B').to_dict()
        self.assertEqual(first['request']['strategies'][0]['snapshot_id'], second['request']['strategies'][0]['snapshot_id'])
        self.assertEqual(set(second['strategies']), {'B'})
        self.assertNotIn('late-A', str(second))
        self.assertNotIn('A-P1', str(second))

    async def test_no_submission_until_all_designs_close_then_all_plans_survive(self):
        batch = await self.begin()
        a = self.publish(batch, 'A')
        with self.assertRaises(ValueError):
            self.api.complete_design_round(self.store, batch.id)
        with self.assertRaises(ValueError):
            await intake_publication(self.store, a.id, 'batch-1', self.f['variants'], self.f['knowledge'])
        state = self.controller.run_until_idle()
        self.assertEqual(state['spent']['executions'], 0)
        self.assertEqual(state['jobs'], {})
        b = self.publish(batch, 'B')
        complete = self.api.complete_design_round(self.store, batch.id)
        self.assertEqual(len(complete.details['plans']), 4)
        self.assertEqual(self.api.complete_design_round(self.store, batch.id), complete)
        for index, publication in enumerate((a, b)):
            intake = await intake_publication(self.store, publication.id, 'batch-1', self.f['variants'], self.f['knowledge'])
            self.assertEqual({row['status'] for row in intake['items']}, {'accepted'})
            self.controller.accept_intake(intake)
            if index == 0:
                self.controller.step()
                self.assertEqual(self.controller.status()['spent']['executions'], 0)
        best = self.store.best('task', 'context')
        state = self.controller.run_until_idle()
        self.assertEqual(state['spent']['executions'], 1)
        self.assertEqual(state['limits']['executions'], 1)
        self.assertEqual(len(state['jobs']), 6)
        self.assertEqual(sum(j['state'] == 'deferred' for j in state['jobs'].values()), 5)
        self.assertFalse(any(k.startswith('budget:') for k in state['commands']))
        self.assertTrue(all(j['parent_id'] in ('A-parent', 'B-parent') for j in state['jobs'].values()))
        self.assertIsNotNone(best)

    async def test_rejected_peer_intake_does_not_open_common_queue(self):
        batch = await self.begin()
        a = self.publish(batch, 'A')
        b = self.publish(batch, 'B')
        self.api.complete_design_round(self.store, batch.id)
        accepted = await intake_publication(self.store, a.id, 'batch-1', self.f['variants'], self.f['knowledge'])
        rejected = await intake_publication(self.store, b.id, 'batch-1', {}, self.f['knowledge'])
        self.controller.accept_intake(accepted)
        self.controller.accept_intake(rejected)
        self.controller.step()
        self.assertEqual(self.controller.status()['spent']['executions'], 0)
        corrected = await intake_publication(self.store, b.id, 'batch-1', self.f['variants'], self.f['knowledge'])
        self.controller.accept_intake(corrected)
        self.controller.step()
        self.assertEqual(self.controller.status()['spent']['executions'], 1)

    async def test_no_plan_result_closes_route_without_making_candidates(self):
        batch = await self.begin()
        self.publish(batch, 'A', no_plan=True)
        self.publish(batch, 'B')
        completed = self.api.complete_design_round(self.store, batch.id)
        self.assertEqual(completed.details['outcomes']['A']['status'], 'needs_research')
        self.assertTrue(completed.details['outcomes']['A']['reason'])
        self.assertEqual(len(completed.details['plans']), 2)

    async def test_new_routes_wait_for_next_batch_and_cannot_join_frozen_batch(self):
        batch = await self.begin()
        self.store.append(Strategy('F', 'task', 'Later method'), 'F')
        self.publish(batch, 'A')
        self.publish(batch, 'B')
        complete = self.api.complete_design_round(self.store, batch.id)
        self.assertNotIn('F', complete.details['outcomes'])
        with self.assertRaises(ValueError):
            self.api.route_manifest(self.store, batch.id, 'F')

    async def test_publication_cannot_be_submitted_under_another_round(self):
        batch = await self.begin()
        a = self.publish(batch, 'A')
        self.publish(batch, 'B')
        self.api.complete_design_round(self.store, batch.id)
        with self.assertRaises(ValueError):
            await intake_publication(self.store, a.id, 'escaped-round', self.f['variants'], self.f['knowledge'])
        self.assertEqual(self.controller.status()['spent']['executions'], 0)

    async def test_real_branch_feedback_must_not_enter_an_open_batch(self):
        batch = await self.begin()
        a = self.publish(batch, 'A')
        publication = self.store.get(a.id)
        self.assertEqual(publication.details['type'], 'analysis_result.v1')
        with self.assertRaises(ValueError):
            await intake_publication(self.store, a.id, 'batch-1', self.f['variants'], self.f['knowledge'])
        self.assertEqual(self.controller.status()['jobs'], {})

    async def test_old_a_and_slow_new_d_get_plans_through_configured_protection(self):
        from rsi_kernel_agent.workflow.exploration import admit_strategy, exploration_status
        from rsi_kernel_agent.contracts import canonical_json
        self.store.append(Strategy('new-D', 'task', 'New partial reduction'), 'new-D')
        review = {'validation_status': 'validated_mechanism_review', 'route_action': 'fork',
                  'task_relation': 'new_to_task', 'strategy_id': 'new-D', 'structural_change': 'reduction',
                  'mechanism_difference': 'Independent partial reduction state and a separate combine',
                  'coverage': {'complete': True}, 'code_refs': ['source:A-candidate'],
                  'lineage': {'strategy_id': 'A', 'parent_candidate_id': 'A-candidate'},
                  'reviewed_strategy_ids': list('ABCDE'), 'admission_comparisons': []}
        admit_strategy(self.store, 'new-D', initial_exploration_rounds=6, policy_revision='policy-6',
            reason='Independent reduction question', mechanism_difference=review['mechanism_difference'],
            method_refs=[], parent_ids=['A-parent'], review_ref=asdict(self.store.artifacts.put(canonical_json(review).encode())))
        self.controller = Controller(self.store, 'task', self.backend)
        for turn in range(6):
            assessments = copy.deepcopy(self.assessments)
            assessments['B']['status'] = 'deferred'
            assessments['new-D'] = {'status': 'hypothesis', 'reason': 'Slow is not counterevidence',
                                    'hypothesis': 'Try another reduction partition ' + str(turn), 'refs': []}
            batch = await self.api.begin_design_round(self.store, self.f['knowledge'], 'task', 'context',
                'protected-' + str(turn), assessments, parents={'A': 'A-parent', 'new-D': 'A-parent'}, knowledge_refs={})
            publications = []
            specs = {}
            for sid in ('A', 'new-D'):
                manifest = self.api.route_manifest(self.store, batch.id, sid)
                answer = proposal()
                for field in ('code_understanding', 'observations', 'hypotheses', 'plans'):
                    answer[field] = [row for row in answer[field] if row['strategy_id'] == 'A']
                    for row in answer[field]:
                        row['strategy_id'] = sid
                        row['refs'] = ['source:' + sid]
                answer['plans'] = answer['plans'][:1]
                plan = answer['plans'][0]
                plan['id'] = sid + '-plan-' + str(turn)
                plan['variants'] = ['one']
                variant = copy.deepcopy(self.f['variants']['A-P1'][0])
                variant.update(label='one', scenario='success', latency_ms=30 if sid == 'new-D' else 8)
                specs[plan['id']] = [variant]
                result = Analyzer(manifest).advance({'final': answer})
                pub = publish_result(self.store, manifest, result, 'protected:' + str(turn) + ':' + sid)
                self.api.finish_route_design(self.store, batch.id, sid, pub.id)
                publications.append(pub)
            complete = self.api.complete_design_round(self.store, batch.id)
            self.assertEqual({p['strategy_id'] for p in complete.details['plans']}, {'A', 'new-D'})
            for pub in publications:
                intake = await intake_publication(self.store, pub.id, 'protected-' + str(turn), specs, self.f['knowledge'])
                self.controller.accept_intake(intake)
            self.controller.run_until_idle()
            status = exploration_status(self.store.history('task'), 'new-D')
            self.assertTrue(status['protected'])
            self.assertEqual(status['remaining_rounds'], 6)
        state = self.controller.status()
        self.assertEqual(state['spent']['executions'], 12)
        self.assertEqual(state['limits']['executions'], 24)
        self.assertEqual(sum(j['state'] == 'completed' and j['strategy_id'] == 'new-D'
                             for j in state['jobs'].values()), 6)

    async def test_missing_source_defers_only_affected_route_with_explicit_gap(self):
        self.f['store'].artifacts.path_for(self.f['manifest'].artifact_ref)
        self.assessments['C'] = {'status': 'needs_research', 'reason': 'Need source before analysis',
                                 'hypothesis': None, 'refs': []}
        batch = await self.begin()
        self.publish(batch, 'A')
        self.publish(batch, 'B')
        complete = self.api.complete_design_round(self.store, batch.id)
        self.assertEqual(complete.details['outcomes']['C']['status'], 'needs_research')
        self.assertIn('parent', complete.details['outcomes']['C']['reason'].lower())
        self.assertFalse(complete.details['outcomes']['C']['input_complete'])


if __name__ == '__main__':
    unittest.main()
