import copy
from dataclasses import asdict
from difflib import unified_diff
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from phase2d_fixtures import make_workflow_fixture
from test_analyzer import proposal
from rsi_kernel_agent.contracts import ArtifactRef, Decision, Strategy, canonical_json
from rsi_kernel_agent.profiler.analyzer import Analyzer
from rsi_kernel_agent.profiler.outputs import publish_result
from rsi_kernel_agent.workflow.controller import Controller
from rsi_kernel_agent.workflow.design import (begin_design_round, complete_design_round,
    finish_route_design, route_manifest)
from rsi_kernel_agent.workflow.execution import SyntheticBackend
from rsi_kernel_agent.workflow.exploration import admit_strategy, set_exploration_state
from rsi_kernel_agent.workflow.intake import intake_publication


class DesignRepairTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = await make_workflow_fixture(self.tmp.name, intake=False,
                                            scenarios={'A-P1': ['compile_failure']})
        self.store = self.f['store']
        self.backend = SyntheticBackend(self.store, Path(self.tmp.name) / 'workspaces')
        self.controller = Controller(self.store, 'task', self.backend,
                                     limits={'executions': 3, 'max_concurrency': 1, 'max_repairs': 1})
        assessments = {sid: {'status': 'hypothesis' if sid == 'A' else 'deferred',
                            'reason': 'Test bounded implementation repair',
                            'hypothesis': 'Change tile only' if sid == 'A' else None,
                            'refs': []} for sid in 'ABCDE'}
        batch = await begin_design_round(self.store, self.f['knowledge'], 'task', 'context',
            'repair-design', assessments, parents={'A': 'A-parent'}, knowledge_refs={})
        manifest = route_manifest(self.store, batch.id, 'A')
        answer = proposal()
        for name in ('code_understanding', 'observations', 'hypotheses', 'plans'):
            answer[name] = [item for item in answer[name] if item['strategy_id'] == 'A']
        answer['plans'] = answer['plans'][:1]
        answer['plans'][0]['variants'] = [v['label'] for v in self.f['variants']['A-P1']]
        result = Analyzer(manifest).advance({'final': answer})
        self.publication = publish_result(self.store, manifest, result, 'repair-publication')
        finish_route_design(self.store, batch.id, 'A', self.publication.id)
        complete_design_round(self.store, batch.id)
        intake = await intake_publication(self.store, self.publication.id, 'repair-design',
                                          self.f['variants'], self.f['knowledge'])
        self.controller.accept_intake(intake)
        state = self.controller.run_until_idle()
        self.failed = next(job for job in state['jobs'].values() if job['state'] == 'failed')

    def response(self):
        packet = self.controller.repair_packet(self.failed['id'])
        before = self.store.artifacts.read(ArtifactRef(**self.failed['variant']['source_ref'])).decode('utf-8')
        after = before.replace('acc = zeros', 'acc = initialized_zeros')
        variant = copy.deepcopy(self.failed['variant'])
        variant.update(source_ref=asdict(self.store.artifacts.put(after.encode('utf-8'))), scenario='success')
        diff = ''.join(unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                    fromfile='failed/source', tofile='repair/source')).encode('utf-8')
        review = {'classification': 'implementation_fix', 'preserves_plan_hypothesis': True,
                  'plan_id': self.failed['plan_id'], 'failure_run_id': self.failed['run_id'],
                  'diff_ref': asdict(self.store.artifacts.put(diff)),
                  'reason': 'Host review: fix the missing allocation symbol without changing the tile hypothesis.'}
        return {**packet, 'variant': variant, 'reason': 'Fix unresolved allocation symbol', 'repair_review': review}

    async def test_packet_binds_original_published_plan_and_failure(self):
        packet = self.controller.repair_packet(self.failed['id'])
        self.assertIn('published_plan', packet)
        self.assertEqual(packet['plan_id'], self.failed['plan_id'])
        self.assertEqual(packet['failure_run_id'], self.failed['run_id'])
        self.assertEqual(packet['published_plan']['publication_id'], self.publication.id)
        self.assertEqual(packet['published_plan']['mechanism'], 'tile')
        self.assertEqual(packet['published_plan']['modification'], 'change tile only')
        self.assertEqual(packet['published_plan']['refs'], ['source:A'])
        self.assertEqual(packet['published_plan']['validation']['correctness'], ['reference including tails'])

    async def test_unreviewed_new_hypothesis_cannot_enter_repair(self):
        response = self.response()
        del response['repair_review']
        before = self.controller.status()
        with self.assertRaises(ValueError):
            self.controller.submit_repair(self.failed['id'], response, 'unreviewed')
        self.assertEqual(self.controller.status(), before)

    async def test_wrong_hypothesis_or_review_bindings_are_rejected_without_charge(self):
        for changes in ({'classification': 'new_optimization_hypothesis'},
                        {'preserves_plan_hypothesis': False},
                        {'preserves_plan_hypothesis': 1},
                        {'plan_id': 'A-initial'}, {'failure_run_id': 'A-run'}):
            with self.subTest(changes=changes):
                response = self.response()
                response['repair_review'].update(changes)
                before = self.controller.status()
                with self.assertRaises(ValueError):
                    self.controller.submit_repair(self.failed['id'], response, 'bad-review')
                self.assertEqual(self.controller.status(), before)

    async def test_diff_artifact_must_match_exact_failed_and_repaired_source(self):
        response = self.response()
        response['repair_review']['diff_ref'] = asdict(self.store.artifacts.put(b'plausible but unrelated diff'))
        with self.assertRaises(ValueError):
            self.controller.submit_repair(self.failed['id'], response, 'wrong-diff')
        response = self.response()
        response['variant']['source_ref'] = asdict(self.store.artifacts.put(b'entirely different source'))
        with self.assertRaises(ValueError):
            self.controller.submit_repair(self.failed['id'], response, 'changed-after-review')

    async def test_config_and_build_cannot_change_behind_source_review(self):
        for name in ('config_ref', 'build_ref'):
            with self.subTest(name=name):
                response = self.response()
                response['variant'][name] = asdict(self.store.artifacts.put(b'{"unreviewed": true}'))
                with self.assertRaises(ValueError):
                    self.controller.submit_repair(self.failed['id'], response, 'hidden-change-' + name)

    async def test_valid_fix_preserves_parent_budget_and_persisted_review_on_retry(self):
        response = self.response()
        repaired = self.controller.submit_repair(self.failed['id'], response, 'fix')
        self.assertEqual(self.controller.submit_repair(self.failed['id'], response, 'fix'), repaired)
        before = self.controller.status()
        self.assertEqual(before['spent']['executions'], 1)
        reviews = [r for r in self.store.history('task') if type(r) is Decision
                   and r.details.get('type') == 'workflow_repair_review.v1']
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0].details['repair_review'], response['repair_review'])
        self.assertEqual(reviews[0].details['source_before'], self.failed['variant']['source_ref'])
        self.assertEqual(reviews[0].details['source_after'], response['variant']['source_ref'])
        state = self.controller.run_until_idle()
        self.assertEqual(state['jobs'][repaired]['state'], 'completed')
        child = self.store.get(state['jobs'][repaired]['candidate_id'])
        self.assertEqual(self.store.get(child.parent_id).candidate_id, self.failed['candidate_id'])
        self.assertEqual(state['jobs'][self.failed['id']]['state'], 'failed')
        self.assertEqual(state['spent']['executions'], 2)
        self.assertEqual(state['limits']['executions'], 3)
        self.assertFalse(any(key.startswith('budget:') for key in state['commands']))

    async def test_lost_response_repairs_review_decision_from_journal(self):
        response = self.response()
        append = self.store.append
        interrupted = False

        def interrupt(record, key):
            nonlocal interrupted
            if type(record) is Decision and record.details.get('type') == 'workflow_repair_review.v1' and not interrupted:
                interrupted = True
                raise RuntimeError('crash at review publication')
            return append(record, key)

        with patch.object(self.store, 'append', interrupt):
            with self.assertRaises(RuntimeError):
                self.controller.submit_repair(self.failed['id'], response, 'resume')
        controller = Controller(self.store, 'task', self.backend)
        repaired = controller.submit_repair(self.failed['id'], response, 'resume')
        reviews = [r for r in self.store.history('task') if type(r) is Decision
                   and r.details.get('type') == 'workflow_repair_review.v1']
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0].details['job_id'], repaired)
        self.assertEqual(controller.status()['spent']['executions'], 1)

    def admit_new_route(self):
        self.store.append(Strategy('new-D', 'task', 'New structural route'), 'new-D')
        review = {'validation_status': 'validated_mechanism_review', 'route_action': 'fork',
                  'task_relation': 'new_to_task', 'strategy_id': 'new-D',
                  'structural_change': 'decomposition', 'mechanism_difference': 'Separate partial reduction lifecycle',
                  'code_refs': ['source:A-candidate'], 'coverage': {'complete': True},
                  'lineage': {'strategy_id': 'A', 'parent_candidate_id': 'A-candidate'},
                  'reviewed_strategy_ids': list('ABCDE'), 'admission_comparisons': []}
        admit_strategy(self.store, 'new-D', policy_revision='policy-1', reason='Independent decomposition',
            mechanism_difference=review['mechanism_difference'], method_refs=[], parent_ids=['A-parent'],
            review_ref=asdict(self.store.artifacts.put(canonical_json(review).encode())))

    async def test_stopped_route_cannot_enter_device_queue_after_intake(self):
        from rsi_kernel_agent.workflow.design import ready_design_jobs
        self.admit_new_route()
        state = self.controller.status()
        job = copy.deepcopy(self.failed)
        job.update(id='stopped-job', strategy_id='new-D', round_id='next', state='queued', reserved=False)
        job.pop('publication_id', None)
        job.pop('design_batch_id', None)
        state['jobs'] = {job['id']: job}
        proof = asdict(self.store.artifacts.put(canonical_json({'kind': 'contract_counterevidence',
            'claim': 'New layout violates a required output constraint', 'refs': ['A-initial']}).encode()))
        set_exploration_state(self.store, 'new-D', 'stopped', 'Contract disproved', [proof], 'stop')
        self.assertNotIn('stopped-job', ready_design_jobs(self.store, state))
        self.assertEqual(state['spent'], self.controller.status()['spent'])

    async def test_evidence_directed_route_can_close_without_fixed_round_quota(self):
        self.admit_new_route()
        assessments = {sid: {'status': 'hypothesis' if sid == 'new-D' else 'deferred',
                            'reason': 'Inspect protected route', 'hypothesis': 'Layout' if sid == 'new-D' else None,
                            'refs': []} for sid in [*'ABCDE', 'new-D']}
        batch = await begin_design_round(self.store, self.f['knowledge'], 'task', 'context',
            'no-plan-counterevidence', assessments, parents={'new-D': 'A-parent'})
        manifest = route_manifest(self.store, batch.id, 'new-D')
        answer = proposal()
        for field in ('code_understanding', 'observations', 'hypotheses', 'plans'):
            answer[field] = [item for item in answer[field] if item['strategy_id'] == 'A']
            for item in answer[field]:
                item['strategy_id'] = 'new-D'
                item['refs'] = ['source:new-D']
        answer['plans'] = []
        answer['outcome'] = {'status': 'inapplicable', 'reason': 'The proposed layout cannot preserve the required cache contract',
                             'refs': ['source:new-D']}
        result = Analyzer(manifest).advance({'final': answer})
        publication = publish_result(self.store, manifest, result, 'no-proof')
        closed = finish_route_design(self.store, batch.id, 'new-D', publication.id)
        self.assertEqual(closed.details['outcome']['status'], 'inapplicable')

    async def test_user_pause_does_not_require_finishing_initial_exploration(self):
        self.admit_new_route()
        before = self.controller.status()
        self.controller.pause('new-D', 'User requests pause')
        after = self.controller.status()
        self.assertIn('new-D', after['paused'])
        self.assertEqual(after['spent'], before['spent'])
        self.assertEqual(after['limits'], before['limits'])


if __name__ == '__main__':
    unittest.main()
