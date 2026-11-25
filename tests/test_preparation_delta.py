import copy
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from phase2c_fixtures import make_analysis_fixture
from test_offline_preparation import route_answer
from rsi_kernel_agent.contracts import Candidate, Decision, Evidence, EvaluationBundle, Plan, Run, RunEvent, canonical_json
from rsi_kernel_agent.profiler.analyzer import Analyzer
from rsi_kernel_agent.profiler.context import decode_context
from rsi_kernel_agent.profiler.inputs import load_manifest
from rsi_kernel_agent.profiler.outputs import publish_result
from rsi_kernel_agent.workflow.design import begin_design_round, complete_design_round, finish_route_design, route_manifest
from rsi_kernel_agent.workflow.offline_preparation import (
    begin_preparation, complete_preparation, preparation_manifest, require_delivery, seal_preparation_route,
)


def read_pages(analyzer):
    offset = 0
    while offset is not None:
        analyzer.advance({'tool_calls': [{'id': 'page-' + str(offset), 'name': 'context_page',
            'arguments': {'offset': offset, 'limit': 24000}}]})
        offset = analyzer.last_results[0]['result']['next_offset']


class PreparationDeltaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = make_analysis_fixture(self.temp.name)
        self.store = self.fixture['store']
        self.assessments = {
            'A': {'status': 'hypothesis', 'reason': 'Review route', 'hypothesis': 'New evidence', 'refs': []},
            'B': {'status': 'deferred', 'reason': 'Not part of this round', 'hypothesis': None, 'refs': []},
        }
        self.prep = await begin_preparation(self.store, self.fixture['knowledge'], 'task', 'context',
            'delta-preparation', self.assessments, parents={'A': 'A-parent'})
        self.original = preparation_manifest(self.store, self.prep.id, 'A')
        analyzer = Analyzer(self.original, artifacts=self.store.artifacts, max_turns=100, max_tool_calls=100)
        read_pages(analyzer)
        self.answer = route_answer('A', no_plan=True)
        self.answer['code_understanding'][0]['movement'] = ['OLD-ANALYSIS-CONTENT-' * 2000]
        self.result = analyzer.advance({'final': self.answer})
        self.closed = seal_preparation_route(self.store, self.prep.id, 'A', self.result, {}, None)
        complete_preparation(self.store, self.prep.id)
        context = self.store.get('context')
        self.current = replace(context, id='current-context', environment={**context.environment, 'runtime_session_id': 'runtime-new'})
        self.store.append(self.current, self.current.id)

    async def formal(self, key='delta-formal'):
        batch = await begin_design_round(self.store, self.fixture['knowledge'], 'task', self.current.id,
            key, self.assessments, parents={'A': 'A-parent'}, preparation_id=self.prep.id)
        return batch, route_manifest(self.store, batch.id, 'A')

    def final_answer(self):
        answer = route_answer('A', no_plan=True)
        answer['draft_review'] = {'preparation_id': self.prep.id,
            'route_result_digest': self.closed.details['result_digest'], 'plan_decisions': []}
        return answer

    async def test_formal_packet_is_delta_but_full_history_and_source_stay_readable(self):
        batch, manifest = await self.formal()
        original_bytes = canonical_json(manifest.to_dict())
        analyzer = Analyzer(manifest, artifacts=self.store.artifacts)
        packet = analyzer.packet()
        view = decode_context(packet['manifest'])
        self.assertEqual(view.get('read_scope'), 'runtime_binding_delta')
        self.assertEqual(view['strategy_id'], 'A')
        self.assertEqual(view['preparation']['route_result_digest'], self.closed.details['result_digest'])
        self.assertEqual(view['current']['context_id'], self.current.id)
        self.assertEqual(view['current']['parent']['id'], 'A-parent')
        self.assertEqual(view['history']['ref'], 'history:A')
        self.assertEqual(view['history']['scope'], 'complete_metric_history_available_on_demand')
        self.assertEqual(view['history']['changed_rows'], [])
        self.assertEqual(view['progress'], manifest.to_dict()['references']['progress:all']['value'])
        self.assertNotIn('OLD-ANALYSIS-CONTENT', canonical_json(view))
        self.assertNotIn('def matmul', canonical_json(view))
        self.assertFalse(view['reading_claims']['full_history_read_in_this_review'])
        self.assertEqual(packet['manifest_digest'], manifest.artifact_ref.digest)
        self.assertEqual(canonical_json(manifest.to_dict()), original_bytes)
        self.assertEqual(load_manifest(self.store, manifest.artifact_ref).artifact_ref, manifest.artifact_ref)
        analyzer.advance({'tool_calls': [
            {'id': 'history', 'name': 'history', 'arguments': {'strategy_id': 'A'}},
            {'id': 'source', 'name': 'source', 'arguments': {'strategy_id': 'A'}},
        ]})
        self.assertTrue(analyzer.last_results[0]['result']['complete'])
        self.assertEqual(len(analyzer.last_results[0]['result']['rows']), 1)
        self.assertIn('def matmul', analyzer.last_results[1]['result']['source'])

    async def test_delta_includes_new_candidate_and_changes_to_an_existing_run(self):
        raw = self.store.artifacts.put(b'local fixture updated timing')
        self.store.append(Evidence('A-updated-time', 'A-run', 'measured_timing', 'available', 'completed',
            'supported', 9.0, 'ms', (raw,), source={'provenance': 'synthetic'}), 'A-updated-time')
        self.store.append(EvaluationBundle('A-updated-evaluation', 'A-run', 'passed', 'valid', 9.0, True,
            ('A-updated-time',)), 'A-updated-evaluation')
        self.store.append(Plan('new-plan', 'A', 'Independent candidate'), 'new-plan')
        self.store.append(Candidate('new-candidate', 'new-plan', 'A-parent', self.store.get('A-candidate').identity), 'new-candidate')
        self.store.append(Run('new-run', 'new-candidate', self.current.id), 'new-run')
        start = RunEvent('new-start', 'new-run', 'running', 'active', 'Local fixture')
        self.store.transition(start, None, start.id)
        self.store.transition(RunEvent('new-fail', 'new-run', 'failed', 'finished', 'Compilation failed'), start.id, 'new-fail')
        _, manifest = await self.formal()
        view = decode_context(Analyzer(manifest).packet()['manifest'])
        self.assertEqual(view.get('read_scope'), 'runtime_binding_delta')
        self.assertEqual([row['run_id'] for row in view['history']['changed_rows']], ['A-run', 'new-run'])
        self.assertEqual(view['history']['changed_rows'][0]['latency']['value'], 9.0)
        self.assertEqual(view['history']['changed_rows'][1]['execution_status'], 'failed')
        self.store.append(Candidate('future-candidate', 'new-plan', 'A-parent', self.store.get('A-candidate').identity), 'future-candidate')
        again = decode_context(Analyzer(load_manifest(self.store, manifest.artifact_ref)).packet()['manifest'])
        self.assertEqual(again, view)
        self.assertNotIn('future-candidate', canonical_json(again))

    async def test_delta_coverage_is_required_and_does_not_claim_full_history_delivery(self):
        batch, manifest = await self.formal()
        result = Analyzer(manifest).advance({'final': self.final_answer()})
        with self.assertRaisesRegex(ValueError, 'coverage|delivery'):
            publish_result(self.store, manifest, result, 'unread-delta')
        analyzer = Analyzer(manifest, artifacts=self.store.artifacts, max_turns=100, max_tool_calls=100)
        read_pages(analyzer)
        result = analyzer.advance({'final': self.final_answer()})
        coverage = require_delivery(manifest.to_dict(), result)
        self.assertTrue(coverage['complete'])
        self.assertEqual(coverage.get('scope'), 'runtime_binding_delta')
        self.assertFalse(coverage['semantic_comprehension_verified'])
        marker = publish_result(self.store, manifest, result, 'read-delta')
        closed = finish_route_design(self.store, batch.id, 'A', marker.id)
        self.assertEqual(closed.details['outcome']['status'], 'needs_research')
        self.assertEqual(complete_design_round(self.store, batch.id).details['plans'], [])

    async def test_unversioned_formal_and_offline_inputs_keep_original_full_context(self):
        _, manifest = await self.formal()
        data = manifest.to_dict()
        data['request']['tool_versions'].pop('runtime_delta', None)
        data['format_revision'] = 2
        self.assertEqual(decode_context(Analyzer(data).packet()['manifest']), data)
        offline = self.original.to_dict()
        offline['format_revision'] = 2
        self.assertEqual(decode_context(Analyzer(offline).packet()['manifest']), offline)

    async def test_binding_rejects_changed_parent_protocol_and_knowledge_revision(self):
        for kind in ('parent', 'protocol', 'knowledge'):
            parents = {'A': 'A-parent'}
            context_id = self.current.id
            pins = None
            if kind == 'parent':
                parents['A'] = 'B-parent'
            elif kind == 'protocol':
                changed = replace(self.current, id='changed-context', protocol={**self.current.protocol, 'runner_digest': 'f' * 64})
                self.store.append(changed, changed.id)
                context_id = changed.id
            else:
                pins = {'A': [{'method_id': 'different', 'note_id': 'source.md', 'revision': 'f' * 64}]}
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                await begin_design_round(self.store, self.fixture['knowledge'], 'task', context_id,
                    'invalid-' + kind, self.assessments, parents=parents, knowledge_refs=pins, preparation_id=self.prep.id)

    async def test_sealed_paused_route_can_close_without_new_plans(self):
        assessments = copy.deepcopy(self.assessments)
        assessments['A'].update(status='paused', hypothesis=None, reason='No justified next hypothesis')
        paused_answer = copy.deepcopy(self.answer)
        paused_answer['outcome'] = {'status': 'paused', 'reason': 'No justified next hypothesis',
            'refs': ['history:A'], 'restart_conditions': ['New measurement evidence']}
        prep = await begin_preparation(self.store, self.fixture['knowledge'], 'task', 'context',
            'paused-preparation', self.assessments, parents={'A': 'A-parent'})
        manifest = preparation_manifest(self.store, prep.id, 'A')
        analyzer = Analyzer(manifest, artifacts=self.store.artifacts, max_turns=100, max_tool_calls=100)
        read_pages(analyzer)
        result = analyzer.advance({'final': paused_answer})
        seal_preparation_route(self.store, prep.id, 'A', result, {}, None)
        complete_preparation(self.store, prep.id)
        batch = await begin_design_round(self.store, self.fixture['knowledge'], 'task', self.current.id,
            'paused-formal', assessments, parents={'A': 'A-parent'}, preparation_id=prep.id)
        outcome = complete_design_round(self.store, batch.id).details['outcomes']['A']
        self.assertEqual(outcome['status'], 'paused')
        self.assertEqual(outcome['plans'], [])


if __name__ == '__main__':
    unittest.main()
