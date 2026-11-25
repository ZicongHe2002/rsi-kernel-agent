import copy
from dataclasses import asdict
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from phase2c_fixtures import make_analysis_fixture
from rsi_kernel_agent.contracts import (Candidate, Decision, Evidence, EvaluationBundle, Plan, Run,
                                        RunEvent, Strategy, canonical_json)
from rsi_kernel_agent.profiler.analyzer import Analyzer
from rsi_kernel_agent.profiler.context import decode_context
from rsi_kernel_agent.profiler.inputs import AnalysisRequest, capture_inputs, load_manifest


class ProgressContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.f = make_analysis_fixture(self.temp.name)
        self.store = self.f['store']

    def request(self, sid, snapshot):
        raw = copy.deepcopy(self.f['request'])
        raw.update(schema_version=2, request_id='metric-route-' + sid)
        raw['strategies'] = [next(row for row in raw['strategies'] if row['strategy_id'] == sid)]
        raw['strategies'][0]['snapshot_id'] = snapshot['id']
        raw['environment'].update(design_round_id='metric-round', design_batch_id='metric-batch')
        raw['tool_versions']['route_progress'] = '1'
        return AnalysisRequest.from_dict(raw)

    async def capture(self, sid='A', snapshot=None):
        snapshot = snapshot or self.store.create_snapshot('task', 'metrics')
        return await capture_inputs(self.store, self.f['knowledge'], self.request(sid, snapshot))

    async def test_same_global_snapshot_is_shared_without_private_route_payloads(self):
        for sid in ('A', 'B'):
            self.store.append(Decision(sid + '-private', 'task', 'note', sid,
                                      sid + '-PRIVATE-CHAT', {'raw_dialogue': 'x' * 10000}), sid + '-private')
        snapshot = self.store.create_snapshot('task', 'shared-round')
        a = (await self.capture('A', snapshot)).to_dict()
        self.store.append(Decision('B-future', 'task', 'note', 'B', 'Later observation'), 'B-future')
        b = (await self.capture('B', snapshot)).to_dict()
        self.assertIn('progress:all', a['references'], 'Route input has no shared progress view')
        self.assertEqual(a['references']['progress:all'], b['references']['progress:all'])
        progress = a['references']['progress:all']['value']
        self.assertEqual(progress['snapshot_id'], snapshot['id'])
        self.assertEqual({card['strategy_id'] for card in progress['routes']}, {'A', 'B'})
        for data, sid in ((a, 'A'), (b, 'B')):
            self.assertEqual(set(data['strategies']), {sid})
            self.assertEqual(data['strategies'][sid]['history']['scope'], 'complete_metric_history')
            self.assertNotIn('PRIVATE-CHAT', canonical_json(data))
            self.assertNotIn('raw_dialogue', canonical_json(data))
            self.assertNotIn('B-future', canonical_json(data))
        self.assertEqual(load_manifest(self.store, (await self.capture('A', snapshot)).artifact_ref).to_dict(), a)

    async def test_full_metric_history_keeps_failures_and_unexecuted_candidates(self):
        self.store.append(Plan('A-new-plan', 'A', 'Change tile'), 'A-new-plan')
        identity = self.store.get('A-candidate').identity
        self.store.append(Candidate('A-failed-candidate', 'A-new-plan', 'A-parent', identity), 'A-failed-candidate')
        self.store.append(Run('A-failed-run', 'A-failed-candidate', 'context'), 'A-failed-run')
        self.store.transition(RunEvent('A-failed-start', 'A-failed-run', 'running', 'active', 'Fixture'), None, 'A-failed-start')
        self.store.transition(RunEvent('A-failed-end', 'A-failed-run', 'failed', 'finished', 'Compile failed'),
                              'A-failed-start', 'A-failed-end')
        self.store.append(Candidate('A-not-run', 'A-new-plan', 'A-parent', identity), 'A-not-run')
        data = (await self.capture()).to_dict()
        history = data['strategies']['A']['history']
        self.assertEqual(history.get('scope'), 'complete_metric_history')
        self.assertTrue(history['complete'])
        rows = history['rows']
        self.assertEqual([row['candidate_id'] for row in rows], ['A-candidate', 'A-failed-candidate', 'A-not-run'])
        self.assertEqual(rows[0]['latency']['value'], 10)
        self.assertEqual(rows[0]['latency']['clock'], 'synthetic')
        self.assertEqual(rows[1]['execution_status'], 'failed')
        self.assertEqual(rows[1]['failure_reason'], 'Compile failed')
        self.assertIsNone(rows[1]['latency']['value'])
        self.assertEqual(rows[2]['execution_status'], 'not_run')
        self.assertIsNone(rows[2]['run_id'])
        self.assertEqual(rows[0]['metrics']['mfu']['availability'], 'unavailable')
        self.assertIsNone(rows[0]['metrics']['mfu']['value'])
        self.assertNotIn('B-candidate', canonical_json(history))

    async def test_explicit_metric_provenance_survives_without_inventing_values(self):
        raw = self.store.artifacts.put(b'fixture metric observations')
        for name, value, unit in [('mfu', 0.42, 'ratio'), ('cycles', 1200, 'cycles')]:
            evidence = Evidence('A-' + name, 'A-run', 'derived_measurement' if name == 'mfu' else 'measured_counter',
                'available', 'completed', 'supported', value, unit, (raw,), (),
                {'provenance': 'synthetic', 'metric_id': name, 'scope': 'kernel', 'definition': 'Explicit fixture ' + name})
            self.store.append(evidence, evidence.id)
        data = (await self.capture()).to_dict()
        history = data['strategies']['A']['history']
        self.assertEqual(history.get('scope'), 'complete_metric_history')
        row = history['rows'][0]
        self.assertEqual(row['metrics']['mfu']['value'], 0.42)
        self.assertEqual(row['metrics']['mfu']['unit'], 'ratio')
        self.assertEqual(row['metrics']['mfu']['evidence_ids'], ['A-mfu'])
        self.assertEqual(row['metrics']['cycles']['value'], 1200)
        self.assertEqual(row['context_id'], 'context')
        self.assertEqual(row['metrics']['device_ms']['value'], 10.0)
        self.assertNotEqual(row['metrics']['device_ms']['clock'], row['latency']['clock'])
        self.assertIsNone(row['metrics']['host_ms']['value'])

    async def test_replay_rejects_shared_progress_or_metric_tampering(self):
        manifest = await self.capture()
        data = manifest.to_dict()
        self.assertIn('progress:all', data['references'])
        changed = copy.deepcopy(data)
        changed['references']['progress:all']['value']['routes'][0]['strategy_id'] = 'invented'
        ref = self.store.artifacts.put(canonical_json(changed).encode())
        with self.assertRaises(ValueError):
            load_manifest(self.store, ref)
        changed = copy.deepcopy(data)
        changed['strategies']['A']['history']['rows'][0]['latency']['value'] = 0
        ref = self.store.artifacts.put(canonical_json(changed).encode())
        with self.assertRaises(ValueError):
            load_manifest(self.store, ref)

    async def test_design_entry_uses_shared_metrics_by_default_and_ai_can_pause(self):
        from rsi_kernel_agent.workflow.design import begin_design_round, route_manifest, finish_route_design, complete_design_round
        from rsi_kernel_agent.profiler.outputs import publish_result
        from test_analyzer import proposal
        assessments = {sid: {'status': 'hypothesis', 'reason': 'Evaluate own latest results',
                             'hypothesis': 'Inspect metric trend', 'refs': []} for sid in ('A', 'B')}
        batch = await begin_design_round(self.store, self.f['knowledge'], 'task', 'context', 'new-input',
                                        assessments, parents={'A': 'A-parent', 'B': 'B-parent'})
        manifest = route_manifest(self.store, batch.id, 'A')
        self.assertIn('progress:all', manifest.to_dict()['references'])
        answer = proposal()
        for field in ('code_understanding', 'observations', 'hypotheses', 'plans'):
            answer[field] = [row for row in answer[field] if row['strategy_id'] == 'A']
        answer['plans'], answer['hypotheses'] = [], []
        answer['outcome'] = {'status': 'paused', 'reason': 'No useful next hypothesis after complete metrics and source review',
                             'refs': ['history:A'], 'restart_conditions': ['A new layout capability becomes available']}
        result = Analyzer(manifest).advance({'final': answer})
        pub = publish_result(self.store, manifest, result, 'paused-A')
        closed = finish_route_design(self.store, batch.id, 'A', pub.id)
        self.assertEqual(closed.details['outcome']['status'], 'paused')
        from rsi_kernel_agent.workflow.exploration import exploration_status
        state = exploration_status(self.store.history('task'), 'A')
        self.assertEqual(state['state'], 'paused')
        b = route_manifest(self.store, batch.id, 'B')
        self.assertEqual(manifest.to_dict()['references']['progress:all'], b.to_dict()['references']['progress:all'])
        shared = b.to_dict()['references']['progress:all']['value']
        self.assertFalse(next(r for r in shared['routes'] if r['strategy_id'] == 'A')['paused'])
        next_batch = await begin_design_round(self.store, self.f['knowledge'], 'task', 'context', 'next-shared',
            assessments, parents={'A': 'A-parent', 'B': 'B-parent'})
        new_a = route_manifest(self.store, next_batch.id, 'A').to_dict()
        card = next(r for r in new_a['references']['progress:all']['value']['routes'] if r['strategy_id'] == 'A')
        self.assertTrue(card['paused'])
        self.assertEqual(card['decision']['restart_conditions'], ['A new layout capability becomes available'])

    async def test_shared_other_route_metric_remains_a_historical_claim(self):
        from test_analyzer import proposal
        manifest = await self.capture()
        answer = proposal()
        for field in ('code_understanding', 'observations', 'hypotheses', 'plans'):
            answer[field] = [row for row in answer[field] if row['strategy_id'] == 'A']
        answer['observations'] = [{'id': 'shared-B', 'strategy_id': 'A', 'category': 'observed_fact',
            'text': 'Shared B timing is not A current measurement', 'refs': ['progress:all'],
            'predicate': {'ref': 'progress:all', 'path': ['routes', 1, 'latest_attempt', 'latency', 'value'], 'value': 12}}]
        result = Analyzer(manifest).advance({'final': answer})
        self.assertEqual(result['analysis']['observations'][0]['category'], 'historical_claim')

    async def test_interrupted_route_closure_recovers_missing_ai_decision(self):
        from unittest.mock import patch
        from rsi_kernel_agent.workflow.design import begin_design_round, route_manifest, finish_route_design
        from rsi_kernel_agent.profiler.outputs import publish_result
        from test_analyzer import proposal
        assessments = {'A': {'status': 'hypothesis', 'reason': 'Review', 'hypothesis': 'Tile', 'refs': []},
                       'B': {'status': 'deferred', 'reason': 'Other route', 'hypothesis': None, 'refs': []}}
        batch = await begin_design_round(self.store, self.f['knowledge'], 'task', 'context', 'decision-recovery',
                                         assessments, parents={'A': 'A-parent'})
        manifest = route_manifest(self.store, batch.id, 'A')
        answer = proposal()
        for field in ('code_understanding', 'observations', 'hypotheses', 'plans'):
            answer[field] = [r for r in answer[field] if r['strategy_id'] == 'A']
        result = Analyzer(manifest).advance({'final': answer})
        pub = publish_result(self.store, manifest, result, 'decision-recovery')
        with patch('rsi_kernel_agent.workflow.exploration.record_route_decision', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                finish_route_design(self.store, batch.id, 'A', pub.id)
        finish_route_design(self.store, batch.id, 'A', pub.id)
        decisions = [r for r in self.store.history('task') if type(r) is Decision
                     and r.details.get('type') == 'route_decision.v1']
        self.assertEqual(len(decisions), 1)

    async def test_offline_ai_pause_is_recorded_before_formal_runtime_binding(self):
        from rsi_kernel_agent.workflow.offline_preparation import begin_preparation, preparation_manifest, seal_preparation_route
        from rsi_kernel_agent.workflow.exploration import exploration_status
        from test_analyzer import proposal
        assessments = {'A': {'status': 'hypothesis', 'reason': 'Review history', 'hypothesis': 'Maybe new tile', 'refs': []},
                       'B': {'status': 'deferred', 'reason': 'Not in this fixture', 'hypothesis': None, 'refs': []}}
        prep = await begin_preparation(self.store, self.f['knowledge'], 'task', 'context', 'pause-before-TPU',
                                       assessments, parents={'A': 'A-parent'})
        manifest = preparation_manifest(self.store, prep.id, 'A')
        answer = proposal()
        for field in ('code_understanding', 'observations', 'hypotheses', 'plans'):
            answer[field] = [r for r in answer[field] if r['strategy_id'] == 'A']
        answer['plans'], answer['hypotheses'] = [], []
        answer['outcome'] = {'status': 'paused', 'reason': 'No testable new hypothesis in this input',
                             'refs': ['history:A'], 'restart_conditions': ['New evidence or method']}
        analyzer = Analyzer(manifest, max_turns=50, max_tool_calls=50)
        offset = 0
        while offset is not None:
            analyzer.advance({'tool_calls': [{'id': 'page-' + str(offset), 'name': 'context_page',
                'arguments': {'offset': offset, 'limit': 24000}}]})
            offset = analyzer.last_results[0]['result']['next_offset']
        result = analyzer.advance({'final': answer})
        marker = seal_preparation_route(self.store, prep.id, 'A', result, {}, None)
        self.assertEqual(exploration_status(self.store.history('task'), 'A')['state'], 'paused')
        self.assertEqual(seal_preparation_route(self.store, prep.id, 'A', result, {}, None), marker)
        decisions = [r for r in self.store.history('task') if type(r) is Decision
                     and r.details.get('type') == 'route_decision.v1']
        self.assertEqual(len(decisions), 1)
        self.assertFalse(any(type(r) is Decision and r.details.get('type') == 'workflow_state.v1'
                             for r in self.store.history('task')))

    async def test_trace_derived_timing_is_retained_in_metric_history(self):
        raw = self.store.artifacts.put(b'fixture trace-derived timing')
        self.store.append(Evidence('A-derived-time', 'A-run', 'trace_derived', 'available', 'completed',
            'supported', 9.0, 'ms', (raw,), (), {'provenance': 'synthetic'}), 'A-derived-time')
        self.store.append(EvaluationBundle('A-derived-evaluation', 'A-run', 'passed', 'valid', 9.0, True,
            ('A-derived-time',)), 'A-derived-evaluation')
        data = (await self.capture()).to_dict()
        self.assertEqual(data['strategies']['A']['history']['rows'][0]['latency']['value'], 9.0)
        self.assertEqual(data['strategies']['A']['history']['rows'][0]['latency']['evidence_kind'], 'trace_derived')

    async def test_manual_pause_after_ai_continue_is_visible_and_resume_wins_later(self):
        decision = {'state': 'active', 'reason': 'New layout hypothesis', 'refs': ['source:A'],
                    'restart_conditions': [], 'plan_ids': ['new-A']}
        self.store.append(Decision('ai-continue', 'task', 'note', 'A', 'AI continue',
            {'type': 'route_decision.v1', 'decision': decision}), 'ai-continue')
        self.store.append(Decision('human-pause', 'task', 'paused', 'A', 'User paused this route'), 'human-pause')
        data = (await self.capture(snapshot=self.store.create_snapshot('task', 'paused-view'))).to_dict()
        card = next(r for r in data['references']['progress:all']['value']['routes'] if r['strategy_id'] == 'A')
        self.assertTrue(card['paused'])
        self.store.append(Decision('human-resume', 'task', 'resumed', 'A', 'User resumed this route'), 'human-resume')
        data = (await self.capture(snapshot=self.store.create_snapshot('task', 'resumed-view'))).to_dict()
        card = next(r for r in data['references']['progress:all']['value']['routes'] if r['strategy_id'] == 'A')
        self.assertFalse(card['paused'])

    async def test_manual_resume_overrides_prior_ai_pause_for_scheduling(self):
        from rsi_kernel_agent.workflow.exploration import exploration_status
        pause = {'state': 'paused', 'reason': 'No useful new hypothesis', 'refs': ['history:A'],
                 'restart_conditions': ['New evidence'], 'plan_ids': []}
        self.store.append(Decision('ai-pause', 'task', 'note', 'A', pause['reason'],
            {'type': 'route_decision.v1', 'decision': pause}), 'ai-pause')
        self.assertEqual(exploration_status(self.store.history('task'), 'A')['state'], 'paused')
        self.store.append(Decision('explicit-resume', 'task', 'resumed', 'A', 'User resumed after new evidence'), 'explicit-resume')
        self.assertEqual(exploration_status(self.store.history('task'), 'A')['state'], 'active')

    async def test_route_controls_follow_record_order_in_live_and_snapshot_history(self):
        from rsi_kernel_agent.workflow.exploration import exploration_status
        controls = [
            ('ai-pause', 'A', 'note', 'paused', 'paused'),
            ('other-resume', 'B', 'resumed', None, 'paused'),
            ('user-resume', 'A', 'resumed', None, 'active'),
            ('user-pause', 'A', 'paused', None, 'paused'),
            ('ai-continue', 'A', 'note', 'active', 'active'),
            ('ai-pause-again', 'A', 'note', 'paused', 'paused'),
            ('user-resume-again', 'A', 'resumed', None, 'active'),
        ]
        for rid, sid, kind, state, expected in controls:
            details = {'type': 'route_decision.v1', 'decision': {'state': state}} if state else {}
            self.store.append(Decision(rid, 'task', kind, sid, rid, details), rid)
            records = self.store.history('task')
            encoded = [{'kind': type(record).__name__, 'payload': asdict(record)} for record in records]
            for label, history in (('live', records), ('snapshot', encoded)):
                with self.subTest(control=rid, representation=label):
                    self.assertEqual(exploration_status(history, 'A')['state'], expected)

    async def test_global_progress_exposes_hard_stop_and_reason(self):
        self.store.append(Decision('stop-A', 'task', 'note', 'A', 'Output contract disproved',
            {'type': 'exploration_state.v1', 'state': 'stopped', 'evidence_refs': ['A-benchmark']}), 'stop-A')
        manifest = (await self.capture()).to_dict()
        card = next(row for row in manifest['references']['progress:all']['value']['routes'] if row['strategy_id'] == 'A')
        self.assertEqual(card['state'], 'stopped')
        self.assertEqual(card['state_reason'], 'Output contract disproved')

    async def test_cross_route_parent_measurement_is_not_certified_as_current_route_fact(self):
        from test_analyzer import proposal
        self.store.append(Strategy('D', 'task', 'New decomposition'), 'D')
        snapshot = self.store.create_snapshot('task', 'cross-parent-metrics')
        request = self.request('A', snapshot).to_dict()
        request['request_id'] = 'metric-route-D'
        request['strategies'][0]['strategy_id'] = 'D'
        manifest = await capture_inputs(self.store, self.f['knowledge'], AnalysisRequest.from_dict(request))
        answer = proposal()
        for field in ('code_understanding', 'observations', 'hypotheses', 'plans'):
            answer[field] = [row for row in answer[field] if row['strategy_id'] == 'A']
            for row in answer[field]:
                row['strategy_id'] = 'D'
                row['refs'] = ['source:D']
        answer['observations'] = [{'id': 'A-parent-time', 'strategy_id': 'D', 'category': 'observed_fact',
            'text': 'A measured parent is not a D candidate result', 'refs': ['evidence:A-timing'],
            'predicate': {'ref': 'evidence:A-timing', 'path': ['value'], 'value': 10}}]
        result = Analyzer(manifest).advance({'final': answer})
        self.assertEqual(result['analysis']['observations'][0]['category'], 'historical_claim')
        self.assertEqual(manifest.to_dict()['references']['evidence:A-timing']['parent_strategy_id'], 'A')

    async def test_metric_projection_does_not_read_raw_arrays_or_xplane(self):
        from unittest.mock import patch
        raw_arrays = self.store.artifacts.put(b'large input arrays fixture')
        raw_trace = self.store.artifacts.put(b'raw XPlane fixture')
        self.store.append(Evidence('A-input-check', 'A-run', 'measured_counter', 'available', 'completed', 'supported',
            {'correctness': 'passed'}, None, (raw_arrays,), (), {'provenance': 'synthetic'}), 'A-input-check')
        self.store.append(Evidence('A-raw-profile', 'A-run', 'trace_derived', 'available', 'completed', 'supported',
            {'format': 'XPlane protobuf'}, None, (raw_trace,), (), {'provenance': 'synthetic'}), 'A-raw-profile')
        read = self.store.artifacts.read
        def forbid_raw(ref):
            if ref.digest in (raw_arrays.digest, raw_trace.digest):
                self.fail('Metric history read a raw NPZ/XPlane artifact')
            return read(ref)
        with patch.object(self.store.artifacts, 'read', side_effect=forbid_raw):
            data = (await self.capture()).to_dict()
        self.assertEqual(data['strategies']['A']['history']['rows'][0]['latency']['value'], 10)

    async def test_parent_benchmark_does_not_materialize_every_stage_array(self):
        from unittest.mock import patch
        raw = self.store.artifacts.put(b'raw correctness arrays not needed for a timing history')
        self.store.append(Evidence('A-correctness-raw', 'A-run', 'code_fact', 'available', 'completed', 'supported',
            {'stage': 'correctness', 'outputs': {'large_payload': 'not metric input'}}, None, (raw,), (),
            {'provenance': 'synthetic'}), 'A-correctness-raw')
        self.store.append(EvaluationBundle('A-rich-evaluation', 'A-run', 'passed', 'valid', 10, True,
            ('A-timing', 'A-correctness-raw')), 'A-rich-evaluation')
        snapshot = self.store.create_snapshot('task', 'large-raw-parent')
        request = self.request('A', snapshot).to_dict()
        request['strategies'][0]['benchmark_ids'] = ['A-rich-evaluation']
        read = self.store.artifacts.read
        def forbid_raw(ref):
            if ref.digest == raw.digest:
                self.fail('Current parent benchmark loaded unrelated raw correctness arrays')
            return read(ref)
        with patch.object(self.store.artifacts, 'read', side_effect=forbid_raw):
            manifest = await capture_inputs(self.store, self.f['knowledge'], AnalysisRequest.from_dict(request))
            replay = load_manifest(self.store, manifest.artifact_ref)
        self.assertEqual(replay.to_dict()['references']['benchmark:A-rich-evaluation']['availability'], 'available')
        self.assertNotIn('large_payload', canonical_json(replay.to_dict()))

    async def test_current_profile_retains_raw_identity_without_loading_xplane(self):
        from unittest.mock import patch
        from rsi_kernel_agent.contracts import ArtifactRef, ProfileBundle
        raw = self.store.artifacts.put(b'large raw XPlane fixture')
        original = self.store.get('A-trace')
        self.store.append(Evidence('A-raw-XPlane', 'A-run', 'trace_derived', 'available', 'completed', 'supported',
            {'format': 'XPlane protobuf'}, None, (raw,), (), {'provenance': 'synthetic'}), 'A-raw-XPlane')
        self.store.append(Evidence('A-compact-profile', 'A-run', 'trace_derived', 'available', 'completed', 'supported',
            {'format': 'PallasKernels.read_profile'}, None, original.artifacts, ('A-raw-XPlane',),
            {'provenance': 'synthetic'}), 'A-compact-profile')
        self.store.append(ProfileBundle('A-profile-compact', 'A-run', ('A-compact-profile',), 'available'), 'A-profile-compact')
        request = self.request('A', self.store.create_snapshot('task', 'profile-raw-reference')).to_dict()
        request['strategies'][0]['profile_ids'] = ['A-profile-compact']
        read = self.store.artifacts.read
        def forbid_raw(ref):
            if ref.digest == raw.digest:
                self.fail('Analysis input read raw XPlane instead of its compact profile')
            return read(ref)
        with patch.object(self.store.artifacts, 'read', side_effect=forbid_raw):
            manifest = await capture_inputs(self.store, self.f['knowledge'], AnalysisRequest.from_dict(request))
            data = load_manifest(self.store, manifest.artifact_ref).to_dict()
        self.assertIn('A-raw-XPlane', data['record_refs'])
        self.assertEqual(data['references']['evidence:A-raw-XPlane']['artifacts'], [])
        self.assertEqual(data['references']['evidence:A-raw-XPlane']['value']['artifacts'], [asdict(raw)])
        self.assertEqual(Analyzer(manifest, artifacts=self.store.artifacts).tools.call(
            'profile', {'strategy_id': 'A', 'profile_id': 'A-profile-compact'})['availability'], 'available')

    async def test_inconsistent_profile_statistics_are_not_exposed_as_device_measurement(self):
        from dataclasses import replace
        from rsi_kernel_agent.contracts import ArtifactRef
        import json
        evidence = self.store.get('A-trace')
        report = json.loads(self.store.artifacts.read(evidence.artifacts[0]))
        report['groups'][0]['median_us'] = 1
        ref = self.store.artifacts.put(canonical_json(report).encode())
        self.store.append(Evidence('A-bad-trace', 'A-run', 'trace_derived', 'available', 'completed', 'supported',
            {'format': 'PallasKernels.read_profile'}, None, (ref,), (), {'provenance': 'synthetic'}), 'A-bad-trace')
        data = (await self.capture()).to_dict()
        metric = data['strategies']['A']['history']['rows'][0]['metrics']['device_ms']
        self.assertEqual(metric['value'], 10.0)
        self.assertNotIn('A-bad-trace', metric['evidence_ids'])

    async def test_failed_measurement_upstream_cannot_become_available_mfu(self):
        ref = self.store.artifacts.put(b'raw counters fixture')
        self.store.append(Evidence('missing-counter', 'A-run', 'measured_counter', 'unavailable', 'failed',
            'inconclusive', None, 'cycles', (), (), {'provenance': 'synthetic', 'reason': 'Counter unavailable'}), 'missing-counter')
        self.store.append(Evidence('unsupported-mfu', 'A-run', 'derived_measurement', 'available', 'completed',
            'supported', .7, 'ratio', (ref,), ('missing-counter',),
            {'provenance': 'synthetic', 'metric_id': 'mfu', 'scope': 'kernel', 'definition': 'Computed from missing counter'}), 'unsupported-mfu')
        data = (await self.capture()).to_dict()
        self.assertEqual(data['strategies']['A']['history']['rows'][0]['metrics']['mfu']['availability'], 'unavailable')

    async def test_latest_round_keeps_all_candidates_and_failure_results(self):
        identity = self.store.get('A-candidate').identity
        for n in (1, 2):
            candidate = Candidate('A-round-' + str(n), 'A-initial', 'A-parent', identity)
            run = Run('A-round-run-' + str(n), candidate.id, 'context')
            self.store.append(candidate, candidate.id)
            self.store.append(run, run.id)
            self.store.append(Decision('request-' + str(n), 'task', 'note', run.id, 'Pinned execution',
                {'type': 'colab_execution_request.v1', 'request': {'job': {
                    'run_id': run.id, 'round_id': 'round-2', 'strategy_id': 'A', 'candidate_id': candidate.id,
                    'variant': {'cost': {'executions': 1, 'time_s': 1, 'tokens': 0}}}}}), 'request-' + str(n))
        data = (await self.capture()).to_dict()
        route = next(r for r in data['references']['progress:all']['value']['routes'] if r['strategy_id'] == 'A')
        self.assertEqual(route['latest_round_id'], 'round-2')
        self.assertEqual({row['candidate_id'] for row in route['latest_results']}, {'A-round-1', 'A-round-2'})

    async def test_analyzer_receives_metrics_without_replaying_old_publications(self):
        from rsi_kernel_agent.profiler.outputs import publish_result
        from test_analyzer import proposal
        original = await capture_inputs(self.store, self.f['knowledge'], AnalysisRequest.from_dict(self.f['request']))
        result = Analyzer(original, artifacts=self.store.artifacts).advance({'final': proposal()})
        publish_result(self.store, original, result, 'historical-analysis')
        from unittest.mock import patch
        with patch('rsi_kernel_agent.workflow.intake._publication', side_effect=AssertionError('Historical model replay')):
            manifest = await self.capture()
            loaded = load_manifest(self.store, manifest.artifact_ref)
        packet = Analyzer(loaded, artifacts=self.store.artifacts).packet()
        self.assertIn('progress:all', decode_context(packet['manifest'])['references'])
        self.assertIn('metric', packet['instructions'].lower())


if __name__ == '__main__':
    unittest.main()
