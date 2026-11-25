import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from phase2c_fixtures import make_analysis_fixture
from test_analyzer import proposal
from rsi_kernel_agent.contracts import (ArtifactRef, Candidate, Decision, ParentCandidate, Plan,
                                        Strategy, canonical_json, record_to_dict)
from rsi_kernel_agent.profiler.analyzer import Analyzer
from rsi_kernel_agent.profiler.inputs import AnalysisRequest, capture_inputs
from rsi_kernel_agent.profiler.outputs import publish_result
from rsi_kernel_agent.workflow.controller import Controller
from rsi_kernel_agent.workflow.execution import SyntheticBackend
from rsi_kernel_agent.workflow.intake import intake_publication


class ExplorationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNotNone(importlib.util.find_spec('rsi_kernel_agent.workflow.exploration'),
                             'Exploration protection must persist in the original Store')
        from rsi_kernel_agent.workflow import exploration
        self.api = exploration
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = make_analysis_fixture(self.tmp.name)
        self.store = self.f['store']
        self.store.append(Strategy('D', 'task', 'New decomposition'), 'new-D')
        self.controller = Controller(self.store, 'task', SyntheticBackend(self.store, Path(self.tmp.name) / 'workspaces'),
                                     limits={'executions': 30, 'max_concurrency': 2})

    def review(self, sid='D', **changes):
        value = {'validation_status': 'validated_mechanism_review', 'route_action': 'fork',
                 'task_relation': 'new_to_task', 'global_relation': 'new',
                 'strategy_id': sid, 'closest_method_id': None,
                 'mechanism_difference': 'Partition reduction and combine partial states separately',
                 'structural_change': 'decomposition', 'code_refs': ['source:A-candidate'],
                 'plan_refs': ['A-initial'], 'reviewed_strategy_ids': [r.id for r in self.store.history('task')
                                                                      if type(r) is Strategy],
                 'reviewed_method_ids': [], 'lineage': {'strategy_id': 'A', 'parent_candidate_id': 'A-candidate'},
                 'coverage': {'complete': True}, 'limitations': []}
        value.update(changes)
        return record_to_dict(self.store.artifacts.put(canonical_json(value).encode()))

    def comparisons(self):
        return [{'admission_id': r.id, 'relation': 'different',
                 'mechanism_difference': 'Keep the same input layout but split output state lifetime independently',
                 'refs': [r.id]} for r in self.store.history('task')
                if type(r) is Decision and r.details.get('type') == 'exploration_admission.v1']

    def admit(self, sid='D', rounds=5, max_active=4, review_ref=None):
        return self.api.admit_strategy(self.store, sid, initial_exploration_rounds=rounds,
            policy_revision='user-policy-v1', reason='Independent reduction research question',
            mechanism_difference='Partition reduction and combine partial states separately',
            method_refs=[], parent_ids=['A-parent'], review_ref=review_ref or self.review(sid), max_active=max_active)

    def status(self, sid='D'):
        return self.api.exploration_status(self.store.history('task'), sid)

    async def round(self, round_id='r1', scenarios=('success', 'success'), *, unchanged=False):
        snapshot = self.store.create_snapshot('task', 'input:' + round_id)
        request = copy.deepcopy(self.f['request'])
        binding = request['strategies'][0]
        binding.update(strategy_id='D', snapshot_id=snapshot['id'], run_id=None, profile_ids=[], benchmark_ids=[])
        request.update(request_id='analysis-' + round_id, strategies=[binding])
        manifest = await capture_inputs(self.store, self.f['knowledge'], AnalysisRequest.from_dict(request))
        answer = proposal()
        for field in ('code_understanding', 'observations', 'hypotheses', 'plans'):
            answer[field] = [row for row in answer[field] if row['strategy_id'] == 'A']
            for row in answer[field]:
                row['strategy_id'] = 'D'
                row['refs'] = ['source:D']
        specs = {}
        for i, plan in enumerate(answer['plans']):
            plan.update(id='D-' + round_id + '-' + str(i), parent_id='A-parent',
                        modification='Partition reduction ' + round_id + ':' + str(i), variants=['variant-' + str(i)])
            src = binding['source_artifact'] if unchanged else record_to_dict(self.store.artifacts.put(
                ('# synthetic partition ' + round_id + ':' + str(i)).encode()))
            cfg = binding['config_artifact'] if unchanged else record_to_dict(self.store.artifacts.put(
                canonical_json({'round': round_id, 'variant': i}).encode()))
            specs[plan['id']] = [{'label': plan['variants'][0], 'source_ref': src, 'config_ref': cfg,
                'build_ref': record_to_dict(self.store.artifacts.put(b'synthetic-build')),
                'scenario': scenarios[i], 'latency_ms': 20, 'priority': 0, 'device': 'synthetic:0',
                'cost': {'executions': 1, 'tokens': 0, 'time_s': 1}}]
        result = Analyzer(manifest, artifacts=self.store.artifacts).advance({'final': answer})
        pub = publish_result(self.store, manifest, result, 'publish:' + round_id)
        intake = await intake_publication(self.store, pub.id, round_id, specs, self.f['knowledge'])
        self.assertEqual({item['status'] for item in intake['items']}, {'accepted'})
        self.controller.accept_intake(intake)
        return snapshot['id']

    def feedback(self, snapshot, round_id='r1', change_kind='mechanism', key=None, **updates):
        jobs = [j['id'] for j in self.controller.status()['jobs'].values() if j['round_id'] == round_id]
        args = dict(input_snapshot_id=snapshot, hypothesis='Separate reduction partition ' + round_id,
                    change_kind=change_kind, job_ids=jobs, next_step='Retain mechanism; change combine layout',
                    key=key or 'feedback:' + round_id)
        args.update(updates)
        return self.api.record_round_feedback(self.store, 'task', round_id, 'D', **args)

    async def test_admission_policy_is_fixed_and_does_not_touch_budget(self):
        before = self.controller.status()
        first = self.admit(rounds=7)
        self.assertEqual(self.admit(rounds=7)['id'], first['id'])
        with self.assertRaises(ValueError):
            self.admit(rounds=5)
        self.assertEqual(self.status()['remaining_rounds'], 7)
        self.assertTrue(self.status()['protected'])
        after = self.controller.status()
        for field in ('limits', 'spent', 'commands', 'revision'):
            self.assertEqual(after[field], before[field])
        for rounds in (4, 9, True):
            with self.assertRaises(ValueError):
                self.admit(rounds=rounds)

    async def test_configured_targets_five_through_eight_are_preserved(self):
        for n in range(5, 9):
            sid = 'D' + str(n)
            self.store.append(Strategy(sid, 'task', 'Variant structure ' + str(n)), sid)
            self.admit(sid, rounds=n, max_active=10,
                       review_ref=self.review(sid, admission_comparisons=self.comparisons()))
            self.assertEqual(self.status(sid)['target_effective_rounds'], n)

    async def test_legacy_route_cannot_be_readmitted_or_renamed_to_refresh_protection(self):
        with self.assertRaises(ValueError):
            self.admit('A')
        initial = self.admit()
        self.store.append(Decision('rename-D', 'task', 'note', 'D', 'Display rename',
                                   {'display_name': 'A more attractive name'}), 'rename-D')
        self.assertEqual(self.admit()['id'], initial['id'])
        self.assertEqual(self.status()['remaining_rounds'], 5)
        with self.assertRaises(ValueError):
            self.admit(review_ref=self.review(route_action='retry', task_relation='tried'))

    async def test_full_admission_capacity_queues_without_losing_remaining_rounds(self):
        self.admit(max_active=1)
        self.store.append(Strategy('E', 'task', 'Independent combination'), 'E')
        queued = self.admit('E', max_active=1,
                            review_ref=self.review('E', admission_comparisons=self.comparisons()))
        self.assertEqual(queued['state'], 'deferred')
        self.assertTrue(self.status('E')['admission_queued'])
        self.assertEqual(self.status('E')['remaining_rounds'], 5)

    async def test_multiple_slower_candidates_count_one_synthetic_round_not_real(self):
        self.admit()
        snapshot = await self.round()
        state = self.controller.run_until_idle()
        before = copy.deepcopy(state['spent'])
        event = self.feedback(snapshot)
        again = self.feedback(snapshot, key='duplicate-delivery')
        self.assertEqual(event['id'], again['id'])
        self.assertEqual(self.status()['completed_effective_rounds'], 0)
        self.assertEqual(self.status()['synthetic_effective_rounds'], 1)
        self.assertEqual(self.status()['remaining_rounds'], 5)
        self.assertTrue(self.status()['protected'])
        self.assertEqual(self.controller.status()['spent'], before)
        from rsi_kernel_agent.store import ExperimentStore
        reopened = ExperimentStore(self.store.db_path, self.store.artifacts.root)
        self.assertEqual(self.api.exploration_status(reopened.history('task'), 'D'), self.status())

    async def test_correctness_failure_counts_feedback_not_performance_success(self):
        self.admit()
        snapshot = await self.round(scenarios=('correctness_failure', 'correctness_failure'))
        self.controller.run_until_idle()
        event = self.feedback(snapshot)
        self.assertEqual(event['outcome'], 'correctness_failure')
        self.assertFalse(event['performance_validated'])
        self.assertEqual(self.status()['synthetic_effective_rounds'], 1)

    async def test_repeat_syntax_infrastructure_and_unchanged_code_do_not_count(self):
        self.admit()
        snapshot = await self.round(unchanged=True)
        self.controller.run_until_idle()
        for kind in ('timing_repeat', 'abba', 'syntax_repair', 'infrastructure_retry', 'mechanism'):
            self.feedback(snapshot, change_kind=kind, key=kind)
        self.assertEqual(self.status()['synthetic_effective_rounds'], 0)
        self.assertEqual(self.status()['completed_effective_rounds'], 0)

    async def test_queued_and_unknown_work_cannot_be_finalized(self):
        self.admit()
        snapshot = await self.round()
        with self.assertRaises(ValueError):
            self.feedback(snapshot)
        class UnknownBackend:
            def lookup(self, operation):
                return {'status': 'unknown', 'receipt': None}
        self.controller.backend = UnknownBackend()
        self.controller.step()
        with self.assertRaises(ValueError):
            self.feedback(snapshot)
        self.assertEqual(self.status()['synthetic_effective_rounds'], 0)

    async def test_deferred_observation_can_be_completed_after_capacity_returns(self):
        self.admit()
        snapshot = await self.round(scenarios=('deferred', 'deferred'))
        self.controller.run_until_idle()
        event = self.feedback(snapshot)
        self.assertFalse(event['effective'])
        for job in self.controller.status()['jobs'].values():
            self.controller.resume_job(job['id'], 'Synthetic capacity is available')
        self.controller.run_until_idle()
        completed = self.feedback(snapshot, key='completed-after-defer')
        self.assertTrue(completed['effective'])
        self.assertEqual(self.status()['synthetic_effective_rounds'], 1)

    async def test_missing_job_or_invalid_feedback_identity_is_rejected(self):
        self.admit()
        snapshot = await self.round()
        self.controller.run_until_idle()
        jobs = list(self.controller.status()['jobs'])
        with self.assertRaises(ValueError):
            self.feedback(snapshot, job_ids=jobs[:1])
        with self.assertRaises(ValueError):
            self.feedback(snapshot, next_step='')
        post = self.store.create_snapshot('task', 'after-results')
        with self.assertRaises(ValueError):
            self.feedback(post['id'])

    async def test_no_compile_or_incomplete_attempts_count(self):
        self.admit()
        snapshot = await self.round(scenarios=('compile_failure', 'timeout'))
        self.controller.run_until_idle()
        event = self.feedback(snapshot)
        self.assertFalse(event['effective'])
        self.assertEqual(self.status()['synthetic_effective_rounds'], 0)

    async def test_research_retry_limit_is_idempotent_and_separate_from_rounds(self):
        self.admit()
        first = self.api.reserve_research_attempt(self.store, 'D', 'r1', 'search-1', limit=2)
        self.assertEqual(self.api.reserve_research_attempt(self.store, 'D', 'r1', 'search-1', limit=2)['id'], first['id'])
        self.api.reserve_research_attempt(self.store, 'D', 'r1', 'search-2', limit=2)
        with self.assertRaises(ValueError):
            self.api.reserve_research_attempt(self.store, 'D', 'r1', 'search-3', limit=2)
        with self.assertRaises(ValueError):
            self.api.reserve_research_attempt(self.store, 'D', 'r1', 'search-3', limit=3)
        self.assertEqual(self.status()['remaining_rounds'], 5)

    async def test_stop_requires_semantic_counterevidence_and_other_states_preserve_quota(self):
        self.admit()
        with self.assertRaises(ValueError):
            self.api.set_exploration_state(self.store, 'D', 'stopped', 'Measured slower', [], 'stop-1')
        misleading = record_to_dict(self.store.artifacts.put(canonical_json(
            {'kind': 'performance', 'claim': 'Slower than task best', 'refs': ['A-benchmark']}).encode()))
        with self.assertRaises(ValueError):
            self.api.set_exploration_state(self.store, 'D', 'stopped', 'Too slow', [misleading], 'stop-2')
        for state in ('needs_research', 'deferred', 'active'):
            self.api.set_exploration_state(self.store, 'D', state, 'Preserve remaining opportunity', [], state)
            self.assertEqual(self.status()['remaining_rounds'], 5)
        proof = record_to_dict(self.store.artifacts.put(canonical_json({'kind': 'contract_counterevidence',
            'claim': 'The proposed recurrence omits required cache values', 'refs': ['A-initial']}).encode()))
        self.api.set_exploration_state(self.store, 'D', 'stopped', 'Violates output contract', [proof], 'stop-3')
        self.assertFalse(self.status()['protected'])
        self.assertEqual(self.status()['state'], 'stopped')

    async def test_manual_pause_resume_cannot_clear_contract_stop(self):
        self.admit()
        proof = record_to_dict(self.store.artifacts.put(canonical_json({'kind': 'contract_counterevidence',
            'claim': 'The proposed recurrence omits required cache values', 'refs': ['A-initial']}).encode()))
        self.api.set_exploration_state(self.store, 'D', 'stopped', 'Contract evidence', [proof], 'stop')
        self.controller.pause('D', 'User additionally pauses the route')
        self.assertEqual(self.status()['state'], 'stopped')
        self.controller.resume('D', 'Remove only the user pause')
        self.assertEqual(self.status()['state'], 'stopped')
        self.assertFalse(self.status()['protected'])
        self.store.append(Decision('late-ai-active', 'task', 'note', 'D', 'Older analysis finished',
            {'type': 'route_decision.v1', 'decision': {'state': 'active'}}), 'late-ai-active')
        self.assertEqual(self.status()['state'], 'stopped')

    async def test_copied_structure_cannot_gain_a_new_protection_identity(self):
        first = self.admit()
        self.store.append(Strategy('copy-D', 'task', 'Renamed decomposition'), 'copy-D')
        copied = self.review('copy-D')
        with self.assertRaises(ValueError):
            self.admit('copy-D', review_ref=copied)
        self.assertEqual(self.status()['admission_id'], first['id'])
        self.assertIsNone(self.status('copy-D')['admission_id'])

    async def test_distinct_structure_can_reuse_parent_and_method_without_copying_protection(self):
        self.admit()
        self.store.append(Strategy('E', 'task', 'Different fusion boundary'), 'E')
        for relation in ('same', 'unknown'):
            comparisons = self.comparisons()
            comparisons[0]['relation'] = relation
            with self.subTest(relation=relation), self.assertRaises(ValueError):
                self.admit('E', review_ref=self.review('E', admission_comparisons=comparisons))
        event = self.admit('E', review_ref=self.review('E', admission_comparisons=self.comparisons()))
        self.assertEqual(event['strategy_id'], 'E')
        self.assertEqual(self.status('E')['remaining_rounds'], 5)

    async def test_fork_admission_accepts_review_catalog_plan_reference(self):
        self.admit()
        self.store.append(Strategy('E', 'task', 'Independent state layout'), 'E')
        comparisons = self.comparisons()
        comparisons[0]['refs'].append('plan:A-initial')
        admitted = self.admit('E', review_ref=self.review('E', admission_comparisons=comparisons))
        self.assertEqual(admitted['strategy_id'], 'E')

    async def test_review_cannot_be_replayed_for_an_unreviewed_route(self):
        for change in ({'strategy_id': 'Different'}, {'reviewed_strategy_ids': ['A']},
                       {'coverage': {'complete': False}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.admit(review_ref=self.review(**change))

    async def test_next_effective_round_must_read_previous_route_feedback(self):
        self.admit()
        first = await self.round('r1')
        second = await self.round('r2')
        self.controller.run_until_idle()
        self.feedback(first, 'r1')
        with self.assertRaises(ValueError):
            self.feedback(second, 'r2')
        self.assertEqual(self.status()['synthetic_effective_rounds'], 1)

    async def test_feedback_requires_the_snapshot_used_by_published_analysis(self):
        self.admit()
        stale = self.store.create_snapshot('task', 'unrelated-earlier-snapshot')
        snapshot = await self.round()
        self.controller.run_until_idle()
        with self.assertRaises(ValueError):
            self.feedback(stale['id'])
        self.assertTrue(self.feedback(snapshot)['effective'])

    async def test_research_question_must_have_feedback_from_its_own_plan(self):
        self.admit()
        snapshot = await self.round(scenarios=('success', 'compile_failure'), unchanged=True)
        self.controller.run_until_idle()
        event = self.feedback(snapshot, change_kind='research_question', hypothesis='Partition reduction r1:1')
        self.assertFalse(event['effective'])
        self.assertEqual(self.status()['synthetic_effective_rounds'], 0)

    async def test_valid_research_question_uses_its_own_successful_plan(self):
        self.admit()
        snapshot = await self.round(scenarios=('success', 'compile_failure'), unchanged=True)
        self.controller.run_until_idle()
        event = self.feedback(snapshot, change_kind='research_question', hypothesis='Partition reduction r1:0')
        self.assertTrue(event['effective'])
        self.assertEqual(self.status()['synthetic_effective_rounds'], 1)

    async def test_repair_only_success_does_not_become_a_mechanism_round(self):
        self.admit()
        snapshot = await self.round(scenarios=('compile_failure', 'compile_failure'))
        state = self.controller.run_until_idle()
        failed = next(job for job in state['jobs'].values() if job['state'] == 'failed')
        response = self.controller.repair_packet(failed['id'])
        variant = copy.deepcopy(failed['variant'])
        variant.update(source_ref=record_to_dict(self.store.artifacts.put(b'# syntax fix only\n')),
                       scenario='success')
        self.controller.submit_repair(failed['id'], {**response, 'variant': variant, 'reason': 'Fix syntax only'}, 'syntax')
        self.controller.run_until_idle()
        event = self.feedback(snapshot, change_kind='mechanism')
        self.assertFalse(event['effective'])
        self.assertEqual(self.status()['synthetic_effective_rounds'], 0)

    async def test_original_correctness_failure_still_counts_when_a_repair_succeeds(self):
        self.admit()
        snapshot = await self.round(scenarios=('correctness_failure', 'compile_failure'))
        state = self.controller.run_until_idle()
        failed = next(job for job in state['jobs'].values() if job['result']['classification'] == 'incorrect')
        response = self.controller.repair_packet(failed['id'])
        variant = copy.deepcopy(failed['variant'])
        variant.update(source_ref=record_to_dict(self.store.artifacts.put(b'# local implementation repair\n')),
                       scenario='success')
        self.controller.submit_repair(failed['id'], {**response, 'variant': variant, 'reason': 'Repair the implementation'}, 'repair')
        self.controller.run_until_idle()
        event = self.feedback(snapshot)
        self.assertTrue(event['effective'])
        self.assertEqual(event['outcome'], 'correctness_failure')
        self.assertFalse(event['performance_validated'])
        self.assertEqual(self.status()['synthetic_effective_rounds'], 1)

    async def test_stopped_route_reactivation_cannot_exceed_original_admission_capacity(self):
        self.admit(max_active=1)
        proof = record_to_dict(self.store.artifacts.put(canonical_json({'kind': 'contract_counterevidence',
            'claim': 'Initial structure omits the output contract', 'refs': ['A-initial']}).encode()))
        self.api.set_exploration_state(self.store, 'D', 'stopped', 'Contract evidence', [proof], 'stop')
        self.store.append(Strategy('E', 'task', 'Independent structure'), 'E')
        self.admit('E', max_active=1, review_ref=self.review('E', admission_comparisons=self.comparisons()))
        with self.assertRaises(ValueError):
            self.api.set_exploration_state(self.store, 'D', 'active', 'Conditions changed', [], 'resume')
        self.assertEqual(self.status()['state'], 'stopped')
        self.assertEqual(self.status()['remaining_rounds'], 5)
        self.assertEqual(self.status()['policy_revision'], 'user-policy-v1')
        self.api.set_exploration_state(self.store, 'E', 'stopped', 'Contract evidence', [proof], 'stop-E')
        restored = self.api.set_exploration_state(self.store, 'D', 'active', 'Conditions changed', [], 'resume')
        self.assertEqual(restored['state'], 'active')
        self.assertEqual(self.status()['remaining_rounds'], 5)
        self.assertEqual(self.api.set_exploration_state(self.store, 'D', 'active', 'Conditions changed', [], 'resume'), restored)

    async def test_relabeling_unchanged_timing_as_research_question_does_not_create_exploration(self):
        self.admit()
        snapshot = await self.round(unchanged=True)
        self.controller.run_until_idle()
        event = self.feedback(snapshot, change_kind='research_question',
                              hypothesis='Maybe it is faster with more samples')
        self.assertFalse(event['effective'])

    async def test_real_protocol_feedback_uses_fixed_evaluator_without_recounting_candidates(self):
        from phase3_fixtures import RealProtocolFixture
        from rsi_kernel_agent.workflow.execution import execution_contract
        with tempfile.TemporaryDirectory() as root:
            fixture = RealProtocolFixture()
            fixture.prepare(root)
            store = fixture.store
            snapshot = store.create_snapshot('task', 'real-protocol-input')
            request = {'schema_version': 1, 'request_id': 'real-protocol-analysis', 'task_id': 'task',
                       'workload_id': 'workload', 'knowledge': [], 'environment': {'fixture': 'CPU only'},
                       'tool_versions': {'fixture': '1'}, 'strategies': [{'strategy_id': 'A',
                       'parent_id': 'parent', 'context_id': 'context', 'snapshot_id': snapshot['id'], 'run_id': None,
                       'source_artifact': fixture.source, 'config_artifact': fixture.config,
                       'profile_ids': [], 'benchmark_ids': []}]}
            manifest = await capture_inputs(store, None, AnalysisRequest.from_dict(request))
            answer = proposal()
            for field in ('code_understanding', 'observations', 'hypotheses', 'plans'):
                answer[field] = [item for item in answer[field] if item['strategy_id'] == 'A']
            fixture.source = fixture.put(b'def kernel(x): return x + 0\n')
            fixture.bundle = fixture.put({'schema_version': 1,
                'entry': {'path': 'kernel.py', 'symbol': 'kernel'},
                'files': [{'path': 'kernel.py', 'artifact_ref': fixture.source, 'role': 'source'}]})
            fixture.build = fixture.put({'schema_version': 1, 'source_bundle_digest': fixture.bundle['digest'],
                'oracle_bundle_digest': fixture.oracle_bundle['digest'], 'runner_digest': fixture.runner_digest,
                'oracle_digest': fixture.oracle['digest']})
            fixture.variant.update(source_ref=fixture.source, source_bundle_ref=fixture.bundle, build_ref=fixture.build)
            variants = {}
            for item in answer['plans']:
                item.update(parent_id='parent', variants=[item['id']])
                variants[item['id']] = [{**fixture.variant, 'label': item['id']}]
            result = Analyzer(manifest, artifacts=store.artifacts).advance({'final': answer})
            publication = publish_result(store, manifest, result, 'real-protocol-publication')
            intake = await intake_publication(store, publication.id, 'real-protocol-round', variants)
            self.assertEqual({row['status'] for row in intake['items']}, {'accepted'})
            class CPUProtocolBackend:
                def __init__(self):
                    self.completed = {}

                def lookup(self, operation_id):
                    return self.completed.get(operation_id, {'status': 'definitely_not_started', 'receipt': None})

                def submit(self, execution_request):
                    job = execution_request['job']
                    stage = execution_request['stage']
                    receipt = fixture.receipt(job, stage, fixture.outputs(job)[stage])
                    answer = {'status': 'completed', 'receipt': receipt}
                    self.completed[execution_request['operation_id']] = answer
                    return answer
            controller = Controller(store, 'task', CPUProtocolBackend(), limits={'executions': 2})
            controller.accept_intake(intake)
            controller.run_until_idle()
            jobs = list(controller.status()['jobs'])
            args = dict(input_snapshot_id=snapshot['id'], hypothesis='Test the changed arithmetic path',
                        change_kind='mechanism', job_ids=jobs, next_step='Inspect dependency ordering', key='real-protocol-feedback')
            event = self.api.record_round_feedback(store, 'task', 'real-protocol-round', 'A', **args)
            self.assertFalse(event['effective'])
            self.assertEqual(event['provenance'], 'real')
            self.assertEqual(self.api.exploration_status(store.history('task'), 'A')['completed_effective_rounds'], 0)
            self.assertIn('no_valid_feedback', event['outcome'])
            self.assertEqual(self.api.exploration_status(store.history('task'), 'A')['synthetic_effective_rounds'], 0)
            self.assertEqual(self.api.record_round_feedback(store, 'task', 'real-protocol-round', 'A', **args)['id'], event['id'])

    async def test_memory_cards_expose_frozen_exploration_policy(self):
        from rsi_kernel_agent.memory import MemoryReader
        self.admit(rounds=8)
        snapshot = self.store.create_snapshot('task', 'exploration-cards')
        view = MemoryReader(self.store).open_snapshot(snapshot['id'])
        cards = {row['strategy_id']: row for row in view.status_cards('context')}
        self.assertEqual(cards['D']['initial_exploration']['remaining_rounds'], 8)
        self.assertIsNone(cards['A']['initial_exploration']['admission_id'])
        self.api.set_exploration_state(self.store, 'D', 'deferred', 'No authorized device', [], 'defer')
        self.assertEqual({row['strategy_id']: row for row in view.status_cards('context')}
                         ['D']['initial_exploration']['state'], 'active')

    async def test_status_accepts_memory_history_rows_and_preserves_legacy_defaults(self):
        self.admit()
        records = [{'kind': type(r).__name__, 'payload': record_to_dict(r)} for r in self.store.history('task')]
        self.assertEqual(self.api.exploration_status(records, 'D'), self.status())
        self.assertFalse(self.status('A')['protected'])
        self.assertIsNone(self.status('A')['admission_id'])


class BundleExplorationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from phase3_fixtures import RealProtocolFixture
        from rsi_kernel_agent.contracts import CandidateIdentity
        from rsi_kernel_agent.workflow.colab import ColabBackend
        from rsi_kernel_agent.workflow import exploration
        self.api = exploration
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = RealProtocolFixture()
        self.f.prepare(self.tmp.name)
        self.store = self.f.store
        self.helper = self.f.put(b'def helper(x): return x\n')
        files = [{'path': 'kernel.py', 'artifact_ref': self.f.source, 'role': 'source'},
                 {'path': 'helper.py', 'artifact_ref': self.helper, 'role': 'dependency'}]
        self.parent_bundle = self.f.put({'schema_version': 1, 'entry': {'path': 'kernel.py', 'symbol': 'kernel'}, 'files': files})
        self.parent_build = self.f.put({'schema_version': 1, 'source_bundle_digest': self.parent_bundle['digest'],
            'oracle_bundle_digest': self.f.oracle_bundle['digest'], 'runner_digest': self.f.runner_digest,
            'oracle_digest': self.f.oracle['digest']})
        identity = CandidateIdentity(self.f.source['digest'], self.f.config['digest'], self.parent_build['digest'])
        self.store.append(ParentCandidate('bundled-parent', 'task', identity), 'bundled-parent')
        fixture = self.f
        store = self.store

        class LocalContractRemote:
            runtime_id = fixture.environment['runtime_session_id']
            nonce = 'CPU-producer-contract-fixture'
            root = '/content/CPU-producer-contract-fixture'

            def __init__(self):
                self.operations = {}

            def lookup(self, operation_id):
                return self.operations.get(operation_id, {'status': 'definitely_not_started', 'receipt': None})

            def submit(self, execution_request, payload):
                job = execution_request['job']
                stage = execution_request['stage']
                variant = job['variant']
                bundle = json.loads(store.artifacts.read(ArtifactRef(**variant['source_bundle_ref'])))
                outputs = fixture.outputs(job)
                for output in outputs.values():
                    if 'identity' in output:
                        output['identity']['loaded_files'] = [
                            {'path': item['path'], 'digest': item['artifact_ref']['digest']} for item in bundle['files']]
                        output['identity']['source_bundle_digest'] = variant['source_bundle_ref']['digest']
                        output['identity']['config_digest'] = variant['config_ref']['digest']
                        output['identity']['specialization_digest'] = variant['config_ref']['digest']
                outputs['generate'].update(source_digest=variant['source_ref']['digest'],
                    config_digest=variant['config_ref']['digest'], build_digest=variant['build_ref']['digest'],
                    source_bundle_digest=variant['source_bundle_ref']['digest'])
                output = outputs[stage]
                body = {name: execution_request[name] for name in ('schema_version', 'backend_id', 'operation_id',
                    'request_digest', 'stage', 'candidate_identity', 'parent_identity', 'provenance')}
                body.update({name: job[name] for name in ('contract_digest', 'run_id', 'candidate_id', 'parent_id', 'context_id')})
                refs = [*output.get('raw_artifact_refs', []), *output.get('raw_xplane_refs', [])]
                if output.get('report_ref'):
                    refs.append(output['report_ref'])
                body.update(status='succeeded', outputs=output,
                    cost=variant['cost'] if stage == 'generate' else {'executions': 0, 'tokens': 0, 'time_s': 0},
                    artifact_refs=refs)
                result = {'status': 'completed', 'receipt': body,
                          'artifacts': [{'ref': ref, 'path': 'local-fixture:' + ref['digest']} for ref in refs]}
                self.operations[execution_request['operation_id']] = result
                return result

        self.controller = Controller(self.store, 'task', ColabBackend(self.store, LocalContractRemote()),
                                     limits={'executions': 6, 'max_concurrency': 1})

    async def run_bundle_round(self, round_id, *, helper=None, reverse=False, parent_id='bundled-parent'):
        from rsi_kernel_agent.workflow.real_protocol import load_bundle
        snapshot = self.store.create_snapshot('task', round_id + ':input')
        parent = self.store.get(parent_id)
        request = {'schema_version': 1, 'request_id': round_id + ':analysis', 'task_id': 'task',
                   'workload_id': 'workload', 'knowledge': [], 'environment': {'fixture': 'CPU only, no TPU'},
                   'tool_versions': {'fixture': '1'}, 'strategies': [{'strategy_id': 'A',
                   'parent_id': parent_id, 'context_id': 'context', 'snapshot_id': snapshot['id'], 'run_id': None,
                   'source_artifact': self.f.source, 'config_artifact': self.f.config,
                   'profile_ids': [], 'benchmark_ids': []}]}
        manifest = await capture_inputs(self.store, None, AnalysisRequest.from_dict(request))
        answer = proposal()
        for field in ('code_understanding', 'observations', 'hypotheses', 'plans'):
            answer[field] = [item for item in answer[field] if item['strategy_id'] == 'A']
        bundle = load_bundle(self.store, self.parent_bundle)
        if helper is not None:
            bundle['files'][1]['artifact_ref'] = helper
        if reverse:
            bundle['files'].reverse()
        bundle_ref = self.f.put(bundle)
        build = self.f.put({'schema_version': 1, 'source_bundle_digest': bundle_ref['digest'],
            'oracle_bundle_digest': self.f.oracle_bundle['digest'], 'runner_digest': self.f.runner_digest,
            'oracle_digest': self.f.oracle['digest']})
        variants = {}
        for plan in answer['plans']:
            plan.update(parent_id=parent_id, variants=[plan['id']])
            variants[plan['id']] = [{**self.f.variant, 'label': plan['id'],
                                    'source_bundle_ref': bundle_ref, 'build_ref': build}]
        result = Analyzer(manifest, artifacts=self.store.artifacts).advance({'final': answer})
        publication = publish_result(self.store, manifest, result, round_id + ':publication')
        intake = await intake_publication(self.store, publication.id, round_id, variants)
        self.assertEqual({row['status'] for row in intake['items']}, {'accepted'})
        self.controller.accept_intake(intake)
        state = self.controller.run_until_idle()
        jobs = [j for j in state['jobs'].values() if j['round_id'] == round_id]
        self.assertTrue(all(j['state'] == 'completed' for j in jobs))
        return self.api.record_round_feedback(self.store, 'task', round_id, 'A',
            input_snapshot_id=snapshot['id'], hypothesis='Investigate helper reduction ' + round_id,
            change_kind='mechanism', job_ids=[j['id'] for j in jobs], next_step='Inspect the dependency result',
            key=round_id + ':feedback')

    async def test_dependency_only_source_change_counts_with_original_colab_intent(self):
        helper = self.f.put(b'def helper(x): return x + 0\n')
        event = await self.run_bundle_round('dependency-r1', helper=helper)
        self.assertTrue(event['effective'])
        self.assertEqual(len(event['change_identities']), 1)
        self.assertTrue(all(row['producer_verified'] for row in event['feedback_refs']))
        repeated = await self.run_bundle_round('dependency-r2', helper=helper, reverse=True)
        self.assertFalse(repeated['effective'])
        self.assertEqual(self.api.exploration_status(self.store.history('task'), 'A')['completed_effective_rounds'], 1)

    async def test_bundle_order_and_build_identity_only_do_not_create_exploration(self):
        event = await self.run_bundle_round('metadata-r1', reverse=True)
        self.assertFalse(event['effective'])
        self.assertEqual(event['change_identities'], [])

    async def test_parent_build_must_match_its_exact_source(self):
        from rsi_kernel_agent.contracts import CandidateIdentity
        wrong_source = self.f.put(b'def other(x): return x\n')
        wrong_bundle = self.f.put({'schema_version': 1, 'entry': {'path': 'kernel.py', 'symbol': 'kernel'},
            'files': [{'path': 'kernel.py', 'artifact_ref': wrong_source, 'role': 'source'}]})
        wrong_build = self.f.put({'schema_version': 1, 'source_bundle_digest': wrong_bundle['digest'],
            'oracle_bundle_digest': self.f.oracle_bundle['digest'], 'runner_digest': self.f.runner_digest,
            'oracle_digest': self.f.oracle['digest']})
        identity = CandidateIdentity(self.f.source['digest'], self.f.config['digest'], wrong_build['digest'])
        self.store.append(ParentCandidate('wrong-build-parent', 'task', identity), 'wrong-build-parent')
        event = await self.run_bundle_round('wrong-parent', helper=self.f.put(b'def helper(x): return x + 0\n'),
                                          parent_id='wrong-build-parent')
        self.assertFalse(event['effective'])
        self.assertTrue(any(row.get('change_comparison', {}).get('status') == 'unknown' for row in event['feedback_refs']))


if __name__ == '__main__':
    unittest.main()
