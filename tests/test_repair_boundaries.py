import copy
from dataclasses import replace
from difflib import unified_diff
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from test_workflow_controller import fixture
from rsi_kernel_agent.contracts import ArtifactRef, Decision, Strategy, canonical_json, record_to_dict
from rsi_kernel_agent.workflow.controller import Controller
from rsi_kernel_agent.workflow.execution import SyntheticBackend
from rsi_kernel_agent.workflow.exploration import admit_strategy, set_exploration_state


class RepairBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = await fixture(self.tmp.name, ['compile_failure'] * 6)
        self.store = self.f['store']
        self.backend = SyntheticBackend(self.store, Path(self.tmp.name) / 'workspaces')
        self.controller = Controller(self.store, 'task', self.backend,
            limits={'executions': 1, 'max_concurrency': 1})
        self.controller.accept_intake(self.f['intake'])
        state = self.controller.run_until_idle()
        self.failed = next(job for job in state['jobs'].values() if job['state'] == 'failed')

    def reviewed_response(self):
        packet = self.controller.repair_packet(self.failed['id'])
        before = self.store.artifacts.read(ArtifactRef(**self.failed['variant']['source_ref'])).decode('utf-8')
        after = before + 'pass\n'
        variant = copy.deepcopy(self.failed['variant'])
        variant.update(source_ref=record_to_dict(self.store.artifacts.put(after.encode())), scenario='success')
        diff = ''.join(unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                   fromfile='failed/source', tofile='repair/source')).encode()
        review = {'classification': 'implementation_fix', 'preserves_plan_hypothesis': True,
                  'plan_id': self.failed['plan_id'], 'failure_run_id': self.failed['run_id'],
                  'diff_ref': record_to_dict(self.store.artifacts.put(diff)),
                  'reason': 'Synthetic host review: fix an incomplete implementation without changing its hypothesis.'}
        return {**packet, 'variant': variant, 'reason': 'Complete the implementation', 'repair_review': review}

    def v2_publication(self):
        original = self.f['publication']
        manifest = self.f['manifest'].to_dict()
        manifest['request']['schema_version'] = 2
        details = copy.deepcopy(original.details)
        details['manifest'] = record_to_dict(self.store.artifacts.put(canonical_json(manifest).encode()))
        upgraded = replace(original, details=details)
        get = self.store.get
        return patch.object(self.store, 'get', side_effect=lambda rid: upgraded if rid == original.id else get(rid))

    async def test_v2_packet_binds_original_published_plan(self):
        with self.v2_publication():
            packet = self.controller.repair_packet(self.failed['id'])
        self.assertEqual(packet['plan_id'], self.failed['plan_id'])
        self.assertEqual(packet['failure_run_id'], self.failed['run_id'])
        self.assertEqual(packet['published_plan']['publication_id'], self.f['publication'].id)
        self.assertIn(packet['published_plan']['mechanism'], ('tile', 'pipeline'))
        self.assertTrue(packet['published_plan']['modification'])
        self.assertIn('correctness', packet['published_plan']['validation'])

    async def test_v1_packet_and_response_remain_compatible(self):
        response = self.reviewed_response()
        del response['repair_review']
        self.assertEqual(set(response) - {'variant', 'reason'},
            {'schema_version', 'job_id', 'candidate_id', 'run_id', 'contract_digest',
             'failure', 'provenance', 'parent_identity'})
        repaired = self.controller.submit_repair(self.failed['id'], response, 'legacy')
        self.assertEqual(self.controller.submit_repair(self.failed['id'], response, 'legacy'), repaired)
        self.assertEqual(self.controller.status()['spent']['executions'], 1)

    async def test_v2_rejects_unreviewed_repair_without_changing_state(self):
        with self.v2_publication():
            response = self.reviewed_response()
            del response['repair_review']
            before = self.controller.status()
            with self.assertRaises(ValueError):
                self.controller.submit_repair(self.failed['id'], response, 'unreviewed')
            self.assertEqual(self.controller.status(), before)

    async def test_v2_checks_review_bindings_diff_and_configuration(self):
        with self.v2_publication():
            for field, value in (
                ('classification', 'new_optimization_hypothesis'),
                ('preserves_plan_hypothesis', 1),
                ('plan_id', 'A-initial'),
                ('failure_run_id', 'A-run'),
                ('diff_ref', record_to_dict(self.store.artifacts.put(b'not the source diff'))),
            ):
                with self.subTest(field=field):
                    response = self.reviewed_response()
                    response['repair_review'][field] = value
                    before = self.controller.status()
                    with self.assertRaises(ValueError):
                        self.controller.submit_repair(self.failed['id'], response, 'bad-' + field)
                    self.assertEqual(self.controller.status(), before)
            for field in ('config_ref', 'build_ref'):
                with self.subTest(field=field):
                    response = self.reviewed_response()
                    response['variant'][field] = record_to_dict(self.store.artifacts.put(b'changed'))
                    with self.assertRaises(ValueError):
                        self.controller.submit_repair(self.failed['id'], response, 'bad-' + field)

    async def test_review_publication_recovers_from_committed_intent(self):
        with self.v2_publication():
            response = self.reviewed_response()
            append = self.store.append
            interrupted = False

            def lose_response(record, key):
                nonlocal interrupted
                if type(record) is Decision and record.details.get('type') == 'workflow_repair_review.v1' and not interrupted:
                    interrupted = True
                    raise RuntimeError('Simulated loss at repair review publication')
                return append(record, key)

            with patch.object(self.store, 'append', lose_response):
                with self.assertRaises(RuntimeError):
                    self.controller.submit_repair(self.failed['id'], response, 'recover')
            restored = Controller(self.store, 'task', self.backend)
            repaired = restored.submit_repair(self.failed['id'], response, 'recover')
            restored.submit_repair(self.failed['id'], response, 'recover')
        reviews = [r for r in self.store.history('task') if type(r) is Decision
                   and r.details.get('type') == 'workflow_repair_review.v1']
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0].details['job_id'], repaired)
        self.assertEqual(reviews[0].details['repair_review'], response['repair_review'])
        self.assertEqual(reviews[0].details['source_before'], self.failed['variant']['source_ref'])
        self.assertEqual(reviews[0].details['source_after'], response['variant']['source_ref'])
        child = self.store.get(restored.status()['jobs'][repaired]['candidate_id'])
        self.assertEqual(self.store.get(child.parent_id).candidate_id, self.failed['candidate_id'])
        self.assertEqual(restored.status()['spent']['executions'], 1)

    async def test_restart_step_recovers_review_before_any_new_execution(self):
        with self.v2_publication():
            response = self.reviewed_response()
            append = self.store.append

            def interrupt(record, key):
                if type(record) is Decision and record.details.get('type') == 'workflow_repair_review.v1':
                    raise RuntimeError('Lost review write')
                return append(record, key)

            with patch.object(self.store, 'append', interrupt):
                with self.assertRaises(RuntimeError):
                    self.controller.submit_repair(self.failed['id'], response, 'step-recovery')
        restored = Controller(self.store, 'task', self.backend)
        state = restored.step()
        repairs = [job for job in state['jobs'].values() if job.get('repair_of')]
        reviews = [r for r in self.store.history('task') if type(r) is Decision
                   and r.details.get('type') == 'workflow_repair_review.v1']
        self.assertEqual(len(repairs), 1)
        self.assertEqual(repairs[0]['state'], 'deferred')
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0].details['job_id'], repairs[0]['id'])
        self.assertEqual(state['spent']['executions'], 1)

    async def test_user_pause_records_reason_without_spending_exploration_quota(self):
        self.store.append(Strategy('new-D', 'task', 'New decomposition'), 'new-D')
        review = {'validation_status': 'validated_mechanism_review', 'route_action': 'fork',
                  'task_relation': 'new_to_task', 'strategy_id': 'new-D',
                  'structural_change': 'decomposition', 'mechanism_difference': 'Separate partial reduction lifecycle',
                  'code_refs': ['source:A-candidate'], 'coverage': {'complete': True},
                  'lineage': {'strategy_id': 'A', 'parent_candidate_id': 'A-candidate'},
                  'reviewed_strategy_ids': list('ABCDE'), 'admission_comparisons': []}
        admit_strategy(self.store, 'new-D', policy_revision='policy-1', reason='Independent decomposition',
            mechanism_difference=review['mechanism_difference'], method_refs=[], parent_ids=['A-parent'],
            review_ref=record_to_dict(self.store.artifacts.put(canonical_json(review).encode())))
        before = self.controller.status()
        with self.assertRaises(ValueError):
            self.controller.pause('new-D', '')
        self.assertEqual(self.controller.status(), before)
        self.controller.pause('new-D', 'User requests pause before more experiments')
        self.assertIn('new-D', self.controller.status()['paused'])
        self.assertEqual(self.controller.status()['spent'], before['spent'])
        self.assertEqual(self.controller.status()['limits'], before['limits'])


class RealRepairBoundaryTests(unittest.TestCase):
    def setUp(self):
        from phase3_fixtures import RealProtocolFixture
        from rsi_kernel_agent.contracts import RunEvent
        from rsi_kernel_agent.workflow.evaluation import integrate_result
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = RealProtocolFixture()
        self.f.prepare(self.tmp.name)
        self.store = self.f.store
        self.controller = Controller(self.store, 'task', None, limits={'executions': 1})
        job = self.f.job()
        proposal = {'id': 'local-proposal', 'strategy_id': 'A', 'parent_id': 'parent',
                    'mechanism': 'identity copy', 'modification': 'Copy complete output',
                    'refs': ['source:A'], 'validation': {'correctness': ['Complete output'],
                                                       'performance': ['Fixed public latency']}}
        publication = Decision('local-publication', 'task', 'note', 'task', 'CPU protocol fixture',
            {'type': 'analysis_result.v1', 'manifest': self.f.put({'request': {'schema_version': 2}}),
             'result': self.f.put({'plan_set': {'plans': [proposal]}}),
             'bundles': [], 'plans': [{'proposal_id': 'local-proposal', 'record_id': 'plan'}]})
        self.store.append(publication, publication.id)
        intake = {'id': 'local-intake', 'task_id': 'task', 'round_id': 'round:1',
                  'publication_id': publication.id, 'items': [{'plan_id': 'plan', 'proposal_id': 'local-proposal',
                    'strategy_id': 'A', 'status': 'accepted', 'jobs': [job]}]}
        self.store.append(Decision(intake['id'], 'task', 'note', 'task', 'CPU protocol fixture',
            {'type': 'workflow_intake.v1', 'request_digest': 'fixture', 'result': intake}), intake['id'])
        self.controller.accept_intake(intake)
        state = self.controller.journal.load()
        self.failed = state['jobs'][job['id']]
        run_id = self.failed['run_id']
        self.store.transition(RunEvent(run_id + ':start', run_id, 'running', 'active', 'CPU fixture'),
                              None, run_id + ':start')
        receipts = {'generate': self.f.receipt(self.failed, 'generate', self.f.outputs(self.failed)['generate']),
                    'compile': self.f.receipt(self.failed, 'compile',
                        {'error_type': 'CompileError', 'message': 'Local contract fixture, no TPU execution'}, 'failed')}
        self.failed.update(state='failed', result=integrate_result(self.store, self.failed, receipts),
                           reason='compile_failed', receipts=receipts)
        self.controller.journal.save(state, 'CPU fixture failure')

    def response(self):
        old = self.failed['variant']
        new = copy.deepcopy(old)
        before = self.store.artifacts.read(ArtifactRef(**old['source_ref'])).decode('utf-8')
        after = before + '\n'
        new['source_ref'] = self.f.put(after.encode())
        bundle = json.loads(self.store.artifacts.read(ArtifactRef(**old['source_bundle_ref'])))
        bundle['files'][0]['artifact_ref'] = new['source_ref']
        new['source_bundle_ref'] = self.f.put(bundle)
        build = json.loads(self.store.artifacts.read(ArtifactRef(**old['build_ref'])))
        build['source_bundle_digest'] = new['source_bundle_ref']['digest']
        new['build_ref'] = self.f.put(build)
        diff = ''.join(unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                   fromfile='failed/source', tofile='repair/source')).encode()
        review = {'classification': 'implementation_fix', 'preserves_plan_hypothesis': True,
                  'plan_id': self.failed['plan_id'], 'failure_run_id': self.failed['run_id'],
                  'diff_ref': self.f.put(diff), 'reason': 'CPU host-review fixture only'}
        return {**self.controller.repair_packet(self.failed['id']), 'variant': new,
                'reason': 'CPU protocol test of a limited source repair', 'repair_review': review}

    def test_real_build_can_update_only_the_source_bundle_identity(self):
        response = self.response()
        job_id = self.controller.submit_repair(self.failed['id'], response, 'real-build')
        repaired = self.controller.status()['jobs'][job_id]
        self.assertEqual(repaired['variant'], response['variant'])
        self.assertNotEqual(repaired['variant']['build_ref'], self.failed['variant']['build_ref'])
        self.assertEqual(self.controller.status()['spent']['executions'], 0)

    def test_real_repair_rejects_changed_runner_oracle_and_dependencies(self):
        for field in ('runner_digest', 'oracle_digest', 'oracle_bundle_digest'):
            with self.subTest(field=field):
                response = self.response()
                variant = response['variant']
                build = json.loads(self.store.artifacts.read(ArtifactRef(**variant['build_ref'])))
                build[field] = 'f' * 64
                variant['build_ref'] = self.f.put(build)
                with self.assertRaises(ValueError):
                    self.controller.submit_repair(self.failed['id'], response, field)
        response = self.response()
        variant = response['variant']
        bundle = json.loads(self.store.artifacts.read(ArtifactRef(**variant['source_bundle_ref'])))
        bundle['files'].append({'path': 'helper.py', 'artifact_ref': self.f.put(b'pass\n'), 'role': 'dependency'})
        variant['source_bundle_ref'] = self.f.put(bundle)
        build = json.loads(self.store.artifacts.read(ArtifactRef(**variant['build_ref'])))
        build['source_bundle_digest'] = variant['source_bundle_ref']['digest']
        variant['build_ref'] = self.f.put(build)
        with self.assertRaises(ValueError):
            self.controller.submit_repair(self.failed['id'], response, 'hidden-dependency')
        self.assertEqual(len(self.controller.status()['jobs']), 1)
        self.assertEqual(self.controller.status()['spent']['executions'], 0)


if __name__ == '__main__':
    unittest.main()
