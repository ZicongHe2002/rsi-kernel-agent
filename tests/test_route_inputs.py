import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from phase2c_fixtures import make_analysis_fixture
from test_analyzer import proposal
from rsi_kernel_agent.contracts import Candidate, Decision, Plan, Strategy, canonical_json
from rsi_kernel_agent.profiler.analyzer import Analyzer
from rsi_kernel_agent.profiler.context import decode_context
from rsi_kernel_agent.profiler.inputs import AnalysisRequest, capture_inputs, load_manifest
from rsi_kernel_agent.profiler.outputs import publish_result
from rsi_kernel_agent.workflow.intake import _publication


class RouteInputTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fixture = make_analysis_fixture(self.tmp.name)
        self.store = self.fixture['store']

    def request(self, sid='A', snapshot=None):
        raw = copy.deepcopy(self.fixture['request'])
        raw['schema_version'] = 2
        raw['request_id'] = 'route-analysis-' + sid
        raw['environment'].update(design_round_id='design-r1', design_batch_id='design-batch-r1')
        raw['strategies'] = [next(row for row in raw['strategies'] if row['strategy_id'] == sid)]
        if snapshot:
            raw['strategies'][0]['snapshot_id'] = snapshot['id']
        return raw

    async def capture(self, raw=None):
        return await capture_inputs(self.store, self.fixture['knowledge'], AnalysisRequest.from_dict(raw or self.request()))

    def answer(self, sid='A', plans=1):
        answer = proposal()
        for key in ('code_understanding', 'observations', 'hypotheses', 'plans'):
            answer[key] = [row for row in answer[key] if row['strategy_id'] == sid]
        answer['plans'] = answer['plans'][:plans]
        return answer

    async def test_packets_contain_only_own_history_and_shared_contracts(self):
        for sid in 'AB':
            self.store.append(Decision(sid + '-private', 'task', 'note', sid,
                                      sid + '-PRIVATE-RESEARCH', {'private_hypothesis': sid + '-secret'}), sid + '-private')
        snapshot = self.store.create_snapshot('task', 'route-private-input')
        packets = {sid: Analyzer(await self.capture(self.request(sid, snapshot))).packet() for sid in 'AB'}
        for sid, other in (('A', 'B'), ('B', 'A')):
            data = decode_context(packets[sid]['manifest'])
            text = canonical_json(packets[sid])
            self.assertIn(sid + '-PRIVATE-RESEARCH', text)
            self.assertNotIn(other + '-PRIVATE-RESEARCH', text)
            self.assertNotIn(other + '-private', data['record_refs'])
            self.assertNotIn(other + '-candidate', data['record_refs'])
            self.assertEqual(set(data['strategies']), {sid})
            self.assertEqual(set(data['strategies'][sid]['history']['snapshot']['strategy_counts']), {sid})
            self.assertIn('task', data['record_refs'])
            self.assertIn('workload', data['record_refs'])
            self.assertIn('context', data['record_refs'])
            self.assertFalse(data['consistency']['combined_atomic_snapshot'])

    async def test_embedded_global_journal_cannot_leak_other_route_jobs(self):
        details = {'type': 'workflow_state.v1', 'revision': 1, 'previous_id': None,
                   'state_digest': '0' * 64, 'state': {'task_id': 'task', 'revision': 1,
                   'jobs': {'A-job': {'strategy_id': 'A', 'reason': 'A-visible-feedback'},
                            'B-job': {'strategy_id': 'B', 'reason': 'B-private-feedback'}},
                   'limits': {'executions': 4, 'weights': {'A': 1, 'B': 1}},
                   'spent': {'executions': 2}, 'served': {'A': 1, 'B': 1},
                   'paused': ['B'], 'cancelled': False, 'intakes': {}, 'commands': {},
                   'control_events': [{'strategy_id': 'B', 'reason': 'B-private-control'}]}}
        self.store.append(Decision('shared-state', 'task', 'note', 'A-initial', 'Journal event', details), 'shared-state')
        snapshot = self.store.create_snapshot('task', 'embedded-journal')
        manifest = await self.capture(self.request(snapshot=snapshot))
        data = manifest.to_dict()
        text = canonical_json(Analyzer(manifest).packet())
        self.assertIn('A-visible-feedback', text)
        self.assertNotIn('B-private-feedback', text)
        self.assertNotIn('B-private-control', text)
        self.assertIn('shared-state', data['record_refs'])
        self.assertEqual(data['route_context']['scoped_records'][0]['record_id'], 'shared-state')
        self.assertEqual(data['route_context']['scoped_records'][0]['original_digest'], data['record_refs']['shared-state']['digest'])
        self.assertEqual(load_manifest(self.store, manifest.artifact_ref).to_dict(), data)

    async def test_paginated_full_history_is_covered_and_later_feedback_excluded(self):
        for i in range(110):
            self.store.append(Decision('A-note-' + str(i), 'task', 'note', 'A', 'Earlier hypothesis ' + str(i)), 'A-note-' + str(i))
        snapshot = self.store.create_snapshot('task', 'large-route-history')
        self.store.append(Decision('A-later', 'task', 'note', 'A', 'Feedback after input freeze'), 'A-later')
        manifest = await self.capture(self.request(snapshot=snapshot))
        data = manifest.to_dict()
        coverage = data['route_context']['history_coverage']
        history = data['strategies']['A']['history']
        self.assertGreater(len(coverage['pages']), 1)
        self.assertEqual(coverage['pages'][0], {'offset': 0, 'count': 100, 'next_offset': 100})
        self.assertIsNone(coverage['pages'][-1]['next_offset'])
        self.assertEqual(coverage['expected_records'], history['total'])
        self.assertEqual(coverage['read_records'], history['total'])
        self.assertEqual(sum(page['count'] for page in coverage['pages']), history['total'])
        self.assertEqual(coverage['missing_ids'], [])
        self.assertEqual(coverage['dependency_ids'], history['dependency_ids'])
        self.assertEqual(len(history['records']), history['total'])
        self.assertTrue(history['complete'])
        self.assertIn('A-note-109', data['record_refs'])
        self.assertNotIn('A-later', canonical_json(data))
        self.assertEqual(load_manifest(self.store, manifest.artifact_ref).to_dict(), data)

    async def test_large_snapshot_is_read_once_while_route_history_remains_paginated(self):
        for i in range(105):
            self.store.append(Decision('A-page-' + str(i), 'task', 'note', 'A', 'Historical feedback'), 'A-page-' + str(i))
        snapshot = self.store.create_snapshot('task', 'snapshot-once')
        read_page = self.store.snapshot_page
        calls = []

        def observe(*args, **kwargs):
            calls.append((args, kwargs))
            return read_page(*args, **kwargs)

        with patch.object(self.store, 'snapshot_page', observe):
            data = (await self.capture(self.request(snapshot=snapshot))).to_dict()
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(data['route_context']['history_coverage']['pages']), 2)
        self.assertIn('A-page-104', data['record_refs'])

    async def test_explicit_cross_route_parent_includes_only_its_dependency_chain(self):
        self.store.append(Strategy('D', 'task', 'Independent decomposition'), 'D')
        self.store.append(Plan('A-unrelated', 'A', 'A-PRIVATE-UNRELATED-PLAN'), 'A-unrelated')
        self.store.append(Candidate('A-unrelated-candidate', 'A-unrelated', 'A-parent', self.store.get('A-parent').identity), 'A-unrelated-candidate')
        self.store.append(Decision('A-unrelated-note', 'task', 'note', 'A-unrelated', 'A-PRIVATE-UNRELATED-NOTE'), 'A-unrelated-note')
        snapshot = self.store.create_snapshot('task', 'fork-parent-input')
        raw = self.request(snapshot=snapshot)
        raw['request_id'] = 'route-analysis-D'
        raw['strategies'][0]['strategy_id'] = 'D'
        manifest = await self.capture(raw)
        data = manifest.to_dict()
        explicit = data['route_context']['explicit_dependencies']
        for rid in ('A-parent', 'A-candidate', 'A-initial', 'A-run', 'A-profile', 'A-trace', 'baseline'):
            self.assertIn(rid, explicit)
            self.assertIn(rid, data['record_refs'])
        self.assertNotIn('A-unrelated', canonical_json(data))
        self.assertNotIn('B-run', data['record_refs'])
        self.assertNotIn('A-PRIVATE-UNRELATED', canonical_json(Analyzer(manifest).packet()))
        self.assertEqual(data['strategies']['D']['parent_strategy_id'], 'A')
        self.assertEqual(data['references']['profile:A-profile']['parent_strategy_id'], 'A')
        self.assertEqual(data['strategies']['D']['run']['id'], 'A-run')
        self.assertEqual(load_manifest(self.store, manifest.artifact_ref).to_dict(), data)
        self.assertEqual(Analyzer(manifest, artifacts=self.store.artifacts).tools.call(
            'profile', {'strategy_id': 'D', 'profile_id': 'A-profile'})['profile_id'], 'A-profile')

    async def test_cross_route_parent_does_not_pull_linked_sibling_candidate(self):
        from rsi_kernel_agent.associations import MemoryWriter
        self.store.append(Strategy('D', 'task', 'New layout'), 'D')
        identity = self.store.get('A-parent').identity
        self.store.append(Candidate('A-sibling', 'A-initial', 'baseline', identity), 'A-sibling')
        self.store.append(Decision('A-sibling-private', 'task', 'note', 'A-sibling', 'PRIVATE-SIBLING-IDEA'), 'A-sibling-private')
        writer = MemoryWriter(self.store)
        writer.bind_plan('A-initial', 'baseline', 'bind-A')
        pin = self.fixture['request']['knowledge'][0]
        method = await self.fixture['knowledge'].get_method(pin['method_id'])
        writer.link_method(method, pin['note_id'], pin['revision'], 'A-initial', 'baseline',
                           'Sibling application only', 'sibling-link', candidate_id='A-sibling')
        snapshot = self.store.create_snapshot('task', 'sibling-context')
        raw = self.request(snapshot=snapshot)
        raw['strategies'][0]['strategy_id'] = 'D'
        data = (await self.capture(raw)).to_dict()
        self.assertIn('A-candidate', data['record_refs'])
        self.assertNotIn('A-sibling', data['record_refs'])
        self.assertNotIn('PRIVATE-SIBLING-IDEA', canonical_json(data))
        own = (await self.capture(self.request(snapshot=snapshot))).to_dict()
        self.assertIn('A-sibling', own['record_refs'])

    async def test_parent_run_from_different_candidate_is_rejected(self):
        raw = self.request()
        raw['strategies'][0]['run_id'] = 'B-run'
        raw['strategies'][0]['profile_ids'] = ['B-profile']
        raw['strategies'][0]['benchmark_ids'] = ['B-benchmark']
        with self.assertRaisesRegex(ValueError, 'exact strategy parent'):
            await self.capture(raw)

    async def test_v2_replay_rejects_forged_coverage_and_foreign_record_injection(self):
        manifest = await self.capture()
        original = manifest.to_dict()
        forged = copy.deepcopy(original)
        forged['route_context']['history_coverage']['read_records'] -= 1
        variants = [forged]
        forged = copy.deepcopy(original)
        forged['route_context']['explicit_dependencies'].append('B-run')
        variants.append(forged)
        legacy = await capture_inputs(self.store, self.fixture['knowledge'], AnalysisRequest.from_dict(self.fixture['request']))
        forged = copy.deepcopy(original)
        forged['record_refs']['B-run'] = legacy.to_dict()['record_refs']['B-run']
        variants.append(forged)
        for data in variants:
            ref = self.store.artifacts.put(canonical_json(data).encode('utf-8'))
            with self.subTest(data=data['route_context']), self.assertRaises(ValueError):
                load_manifest(self.store, ref)

    async def test_v2_requires_one_route_and_batch_binding(self):
        for change in ('multiple', 'missing-round', 'missing-batch'):
            raw = self.request()
            if change == 'multiple':
                raw['strategies'].append(copy.deepcopy(self.fixture['request']['strategies'][1]))
            else:
                raw['environment'].pop('design_round_id' if change == 'missing-round' else 'design_batch_id')
            with self.subTest(change=change), self.assertRaises(ValueError):
                AnalysisRequest.from_dict(raw)

    async def test_single_plan_can_publish_and_replay_without_fabricated_alternative(self):
        manifest = await self.capture()
        analyzer = Analyzer(manifest)
        result = analyzer.advance({'final': self.answer()})
        self.assertEqual(len(result['plan_set']['plans']), 1)
        self.assertEqual(result['plan_set']['schema_version'], 2)
        self.assertFalse(result['winner_selected'])
        marker = publish_result(self.store, manifest, result, 'single-route-publication')
        _, replayed = _publication(self.store, marker)
        self.assertEqual(replayed, result)
        self.assertEqual(len(marker.details['plans']), 1)

    async def test_zero_plan_outcomes_remain_explicit_and_publishable(self):
        manifest = await self.capture()
        for status in ('needs_research', 'inapplicable', 'deferred', 'awaiting_conditions'):
            answer = self.answer(plans=0)
            answer['hypotheses'] = []
            answer['outcome'] = {'status': status, 'reason': 'Need missing target measurements', 'refs': ['source:A']}
            result = Analyzer(manifest).advance({'final': answer})
            self.assertEqual(result['plan_set']['plans'], [])
            self.assertEqual(result['plan_set']['outcome'], answer['outcome'])
            marker = publish_result(self.store, manifest, result, 'zero-plan-' + status)
            self.assertEqual(marker.details['plans'], [])
            self.assertEqual(_publication(self.store, marker)[1], result)

    async def test_zero_plans_without_reason_or_with_cross_route_refs_are_rejected(self):
        manifest = await self.capture()
        for outcome in (None, {'status': 'needs_research', 'reason': '', 'refs': ['source:A']},
                        {'status': 'needs_research', 'reason': 'Need evidence', 'refs': ['source:B']},
                        {'status': 'winner', 'reason': 'Choose A', 'refs': ['source:A']}):
            answer = self.answer(plans=0)
            if outcome is not None:
                answer['outcome'] = outcome
            with self.subTest(outcome=outcome), self.assertRaises(ValueError):
                Analyzer(manifest).advance({'final': answer})

    async def test_v1_capture_and_two_plan_requirement_remain_unchanged(self):
        manifest = await capture_inputs(self.store, self.fixture['knowledge'], AnalysisRequest.from_dict(self.fixture['request']))
        self.assertEqual(manifest.to_dict()['schema_version'], 1)
        self.assertNotIn('route_context', manifest.to_dict())
        answer = proposal()
        answer['plans'] = [answer['plans'][0], answer['plans'][2]]
        with self.assertRaisesRegex(ValueError, 'at least two'):
            Analyzer(manifest).advance({'final': answer})
        result = Analyzer(manifest).advance({'final': proposal()})
        self.assertEqual(result['plan_set']['schema_version'], 1)
        self.assertEqual(len(result['plan_set']['plans']), 4)
        self.assertEqual(load_manifest(self.store, manifest.artifact_ref).to_dict(), manifest.to_dict())


if __name__ == '__main__':
    unittest.main()
