import copy
from dataclasses import asdict
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from phase2c_fixtures import make_analysis_fixture
from rsi_kernel_agent.contracts import (Candidate, CandidateIdentity, Decision, Evidence,
                                       EvaluationBundle, Plan, ProfileBundle, Run, RunEvent, canonical_json)
from rsi_kernel_agent.profiler.analyzer import Analyzer
from rsi_kernel_agent.profiler.inputs import AnalysisRequest, capture_inputs, load_manifest
from rsi_kernel_agent.profiler.tools import AnalysisTools
from test_analyzer import proposal


class CandidateEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.f = make_analysis_fixture(self.temp.name)
        self.store = self.f['store']

    def append(self, record):
        self.store.append(record, record.id)
        return record

    def candidate(self, name='A-new', owner='A', *, executed=True):
        source = self.store.artifacts.put(('def ' + name.replace('-', '_') + '():\n    return "PRIVATE-CANDIDATE-SOURCE"\n').encode())
        config = self.store.artifacts.put(canonical_json({'tile': 4, 'candidate': name}).encode())
        dependency = self.store.artifacts.put(b'DEPENDENCY-CONTENT = 7\n')
        bundle = self.store.artifacts.put(canonical_json({'schema_version': 1,
            'entry': {'path': 'kernel.py', 'symbol': name.replace('-', '_')}, 'files': [
                {'path': 'kernel.py', 'artifact_ref': asdict(source), 'role': 'source'},
                {'path': 'utils.py', 'artifact_ref': asdict(dependency), 'role': 'dependency'}]}).encode())
        build = self.store.artifacts.put(canonical_json({'schema_version': 1,
            'source_bundle_digest': bundle.digest, 'oracle_bundle_digest': '1' * 64,
            'runner_digest': '2' * 64, 'oracle_digest': '3' * 64}).encode())
        self.append(Plan(name + '-plan', owner, 'Distinct candidate'))
        self.append(Candidate(name, name + '-plan', owner + '-parent',
                              CandidateIdentity(source.digest, config.digest, build.digest)))
        self.append(Decision(name + '-source', 'task', 'note', name, 'Frozen source',
            {'type': 'workflow_candidate.v1', 'source_ref': asdict(source), 'config_ref': asdict(config),
             'build_ref': asdict(build), 'source_bundle_ref': asdict(bundle), 'provenance': 'synthetic'}))
        if executed:
            self.add_run(name, name + '-run')
        return source, config, dependency

    def add_run(self, candidate_id, run_id, *, succeeded=True):
        self.append(Run(run_id, candidate_id, 'context'))
        start = RunEvent(run_id + '-start', run_id, 'running', 'active', 'Fixture')
        self.store.transition(start, None, start.id)
        end = RunEvent(run_id + '-end', run_id, 'succeeded' if succeeded else 'failed', 'finished',
                       'Finished' if succeeded else 'Compile failed')
        self.store.transition(end, start.id, end.id)
        if succeeded:
            timing = self.store.artifacts.put(canonical_json({'samples_ms': [3.0, 5.0], 'latency_ms': 4.0}).encode())
            self.append(Evidence(run_id + '-timing', run_id, 'measured_timing', 'available', 'completed',
                'supported', 4.0, 'ms', (timing,), (), {'provenance': 'synthetic'}))
            self.append(EvaluationBundle(run_id + '-benchmark', run_id, 'passed', 'valid', 4.0, True,
                                         (run_id + '-timing',)))
            report = json.loads(self.store.artifacts.read(self.store.get('A-trace').artifacts[0]))
            report['profile'] = run_id + '.xplane.pb'
            profile = self.store.artifacts.put(canonical_json(report).encode())
            self.append(Evidence(run_id + '-trace', run_id, 'trace_derived', 'available', 'completed',
                'supported', {'format': 'PallasKernels.read_profile'}, None, (profile,), (),
                {'provenance': 'synthetic'}))
            self.append(ProfileBundle(run_id + '-profile', run_id, (run_id + '-trace',), 'available'))

    async def capture(self, *, snapshot=None):
        snapshot = snapshot or self.store.create_snapshot('task', 'candidate-catalog')
        raw = copy.deepcopy(self.f['request'])
        raw.update(schema_version=2, request_id='candidate-evidence')
        raw['strategies'] = [raw['strategies'][0]]
        raw['strategies'][0]['snapshot_id'] = snapshot['id']
        raw['environment'].update(design_round_id='candidate-round', design_batch_id='candidate-batch')
        raw['tool_versions']['route_progress'] = '1'
        return await capture_inputs(self.store, self.f['knowledge'], AnalysisRequest.from_dict(raw))

    def call(self, manifest, name, section, run_id=None):
        args = {'candidate_id': name, 'section': section}
        if run_id is not None:
            args['run_id'] = run_id
        return AnalysisTools(manifest.to_dict(), self.store.artifacts).call('candidate_evidence', args)

    async def test_nonparent_source_is_loaded_only_on_demand_with_full_identity(self):
        source, config, dependency = self.candidate()
        original = self.store.artifacts.read
        forbidden = {source.digest, config.digest, dependency.digest}
        def read(ref):
            self.assertNotIn(ref.digest, forbidden, 'Capture eagerly loaded nonparent source')
            return original(ref)
        with patch.object(self.store.artifacts, 'read', side_effect=read):
            manifest = await self.capture()
            replay = load_manifest(self.store, manifest.artifact_ref)
        self.assertNotIn('PRIVATE-CANDIDATE-SOURCE', canonical_json(manifest.to_dict()))
        self.assertEqual(replay.to_dict(), manifest.to_dict())
        result = self.call(manifest, 'A-new', 'source')
        self.assertEqual(result['availability'], 'available')
        self.assertEqual(result['strategy_id'], 'A')
        self.assertEqual(result['candidate_id'], 'A-new')
        self.assertEqual(result['parent_id'], 'A-parent')
        self.assertEqual(result['source_artifact'], asdict(source))
        self.assertIn('PRIVATE-CANDIDATE-SOURCE', result['source'])
        self.assertEqual(json.loads(result['config'])['tile'], 4)
        self.assertEqual({row['path'] for row in result['files']}, {'kernel.py', 'utils.py'})
        self.assertTrue(all(row['availability'] == 'available' for row in result['files']))
        self.assertFalse(result['current'])
        self.assertEqual(result['classification'], 'historical_claim')

    async def test_profile_and_benchmark_bind_to_selected_candidate_run(self):
        self.candidate('B-new', 'B')
        manifest = await self.capture()
        profile = self.call(manifest, 'B-new', 'profile', 'B-new-run')
        self.assertEqual(profile['availability'], 'available')
        self.assertEqual(profile['strategy_id'], 'B')
        self.assertTrue(profile['foreign_route'])
        self.assertFalse(profile['current'])
        self.assertEqual(profile['classification'], 'historical_claim')
        binding = profile['profiles'][0]['reports'][0]['binding']
        self.assertEqual(binding['strategy_id'], 'B')
        self.assertEqual(binding['candidate_id'], 'B-new')
        self.assertEqual(binding['run_id'], 'B-new-run')
        self.assertEqual(binding['parent_id'], 'B-parent')
        self.assertEqual(binding['declared_identity'], asdict(self.store.get('B-new').identity))
        benchmark = self.call(manifest, 'B-new', 'benchmark')
        self.assertEqual(benchmark['run_id'], 'B-new-run')
        self.assertEqual(benchmark['benchmarks'][0]['value']['latency_ms'], 4.0)
        self.assertEqual(benchmark['benchmarks'][0]['evidence'][0]['reports'][0]['samples_ms'], [3.0, 5.0])

    async def test_snapshot_rejects_future_candidate_future_run_and_wrong_run(self):
        self.candidate()
        snapshot = self.store.create_snapshot('task', 'before-future-candidates')
        manifest = await self.capture(snapshot=snapshot)
        self.candidate('A-future')
        self.add_run('A-new', 'A-new-future-run')
        for name, run_id in [('A-future', None), ('A-new', 'A-new-future-run'), ('A-new', 'B-run')]:
            with self.subTest(name=name, run=run_id), self.assertRaises(ValueError):
                self.call(manifest, name, 'benchmark', run_id)
        self.assertEqual(load_manifest(self.store, manifest.artifact_ref).to_dict(), manifest.to_dict())
        self.assertEqual(self.call(manifest, 'A-new', 'benchmark')['run_id'], 'A-new-run')

    async def test_multiple_runs_require_an_explicit_measurement_selection(self):
        self.candidate()
        self.add_run('A-new', 'A-new-second-run')
        manifest = await self.capture()
        for section in ('benchmark', 'profile'):
            with self.subTest(section=section), self.assertRaisesRegex(ValueError, 'run_id'):
                self.call(manifest, 'A-new', section)
        self.assertEqual(self.call(manifest, 'A-new', 'source')['availability'], 'available')
        self.assertEqual(self.call(manifest, 'A-new', 'profile', 'A-new-second-run')['run_id'], 'A-new-second-run')

    async def test_raw_arrays_and_xplane_remain_unread_with_explicit_missing_results(self):
        self.candidate()
        raw = self.store.artifacts.put(b'raw XPlane/NPZ must not be loaded')
        self.append(Evidence('raw-trace', 'A-new-run', 'trace_derived', 'available', 'completed', 'supported',
            {'format': 'XPlane protobuf'}, None, (raw,), (), {'provenance': 'synthetic'}))
        self.append(ProfileBundle('raw-only-profile', 'A-new-run', ('raw-trace',), 'available'))
        self.append(Evidence('raw-arrays', 'A-new-run', 'code_fact', 'available', 'completed', 'supported',
            {'stage': 'correctness', 'outputs': {'large_payload': 'not needed'}}, None, (raw,), (),
            {'provenance': 'synthetic'}))
        self.append(EvaluationBundle('rich-benchmark', 'A-new-run', 'passed', 'valid', 4.0, True,
            ('A-new-run-timing', 'raw-arrays')))
        self.candidate('A-unrun', executed=False)
        self.candidate('A-failed', executed=False)
        self.add_run('A-failed', 'A-failed-run', succeeded=False)
        original = self.store.artifacts.read
        def read(ref):
            self.assertNotEqual(ref.digest, raw.digest, 'On-demand evidence read raw arrays or XPlane')
            return original(ref)
        with patch.object(self.store.artifacts, 'read', side_effect=read):
            manifest = await self.capture()
            load_manifest(self.store, manifest.artifact_ref)
            profile = self.call(manifest, 'A-new', 'profile')
            benchmark = self.call(manifest, 'A-new', 'benchmark')
        raw_profile = next(row for row in profile['profiles'] if row['profile_id'] == 'raw-only-profile')
        self.assertEqual(raw_profile['availability'], 'unavailable')
        self.assertEqual(raw_profile['deferred_artifacts'], [asdict(raw)])
        self.assertNotIn('large_payload', canonical_json(benchmark))
        for name in ('A-unrun', 'A-failed'):
            for section in ('benchmark', 'profile'):
                result = self.call(manifest, name, section)
                self.assertEqual(result['availability'], 'unavailable')
                self.assertTrue(result['reason'])

    async def test_tampered_source_or_profile_is_never_returned_as_available(self):
        source, _, _ = self.candidate()
        manifest = await self.capture()
        self.store.artifacts.path_for(source).write_bytes(b'tampered')
        result = self.call(manifest, 'A-new', 'source')
        self.assertEqual(result['availability'], 'unavailable')
        self.assertNotIn('tampered', canonical_json(result))
        trace = self.store.get('A-new-run-trace').artifacts[0]
        self.store.artifacts.path_for(trace).write_bytes(b'{"forged":true}')
        self.assertEqual(self.call(manifest, 'A-new', 'profile')['availability'], 'unavailable')

    async def test_initial_packet_shares_latest_not_foreign_complete_catalog(self):
        from rsi_kernel_agent.profiler.context import decode_context
        self.candidate('B-old', 'B')
        self.candidate('B-latest', 'B')
        manifest = await self.capture()
        view = decode_context(Analyzer(manifest, artifacts=self.store.artifacts).packet()['manifest'])
        self.assertNotIn('candidate:B-old', view['references'])
        self.assertNotIn('B-old-run', canonical_json(view))
        self.assertIn('B-latest', canonical_json(view['references']['progress:all']))
        self.assertTrue(view['strategies']['A']['history']['complete'])
        detail = self.call(manifest, 'B-old', 'benchmark')
        self.assertEqual(detail['availability'], 'available')
        self.assertTrue(detail['foreign_route'])

    async def test_failed_stage_details_are_available_on_demand(self):
        self.candidate('B-failed', 'B', executed=False)
        self.add_run('B-failed', 'B-failed-run', succeeded=False)
        raw = self.store.artifacts.put(b'compiler output fixture')
        self.append(Evidence('B-compile-error', 'B-failed-run', 'measured_counter', 'available', 'completed',
            'supported', {'stage': 'compile', 'status': 'failed', 'outputs': {
                'error_type': 'CompileError', 'message': 'Shared-memory layout rejected'}}, None, (raw,), (),
            {'provenance': 'synthetic'}))
        manifest = await self.capture()
        result = self.call(manifest, 'B-failed', 'failure')
        self.assertEqual(result['availability'], 'available')
        self.assertEqual(result['failures'][0]['outputs']['message'], 'Shared-memory layout rejected')
        self.assertTrue(result['foreign_route'])

    async def test_cross_run_dependency_is_unavailable_without_blocking_other_routes(self):
        self.candidate('B-derived', 'B')
        raw = self.store.artifacts.put(b'foreign dependency')
        self.append(Evidence('B-cross-run', 'B-derived-run', 'derived_measurement', 'available', 'completed',
            'supported', 5.0, 'ms', (raw,), ('A-timing',), {'provenance': 'synthetic'}))
        self.append(EvaluationBundle('B-cross-evaluation', 'B-derived-run', 'passed', 'valid', 5.0, True, ('B-cross-run',)))
        manifest = await self.capture()
        result = self.call(manifest, 'B-derived', 'benchmark')
        invalid = next(row for row in result['benchmarks'] if row['evaluation_id'] == 'B-cross-evaluation')
        self.assertEqual(invalid['availability'], 'unavailable')
        self.assertTrue(manifest.to_dict()['strategies']['A']['history']['complete'])

    async def test_catalog_is_historical_when_used_in_a_factual_predicate(self):
        self.candidate('B-new', 'B')
        manifest = await self.capture()
        ref = 'candidate:B-new'
        answer = proposal()
        for field in ('code_understanding', 'observations', 'hypotheses', 'plans'):
            answer[field] = [row for row in answer[field] if row['strategy_id'] == 'A']
        answer['observations'] = [{'id': 'foreign-candidate', 'strategy_id': 'A', 'category': 'observed_fact',
            'text': 'B is not A current measurement', 'refs': [ref],
            'predicate': {'ref': ref, 'path': ['candidate_id'], 'value': 'B-new'}}]
        result = Analyzer(manifest).advance({'final': answer})
        self.assertEqual(result['analysis']['observations'][0]['category'], 'historical_claim')

    async def test_selected_run_benchmark_cannot_use_a_different_run_receipt(self):
        self.candidate('B-new', 'B')
        receipt = self.store.artifacts.put(canonical_json({'schema_version': 1, 'backend_id': 'colab.v1',
            'run_id': 'A-run', 'candidate_id': 'A-candidate', 'context_id': 'context', 'stage': 'benchmark',
            'outputs': {'samples_ms': [4.0, 4.0]}}).encode())
        self.append(Evidence('wrong-run-timing', 'B-new-run', 'measured_timing', 'available', 'completed',
            'supported', 4.0, 'ms', (receipt,), (), {'provenance': 'synthetic'}))
        self.append(EvaluationBundle('wrong-run-evaluation', 'B-new-run', 'passed', 'valid', 4.0, True,
            ('wrong-run-timing',)))
        manifest = await self.capture()
        history = manifest.to_dict()['references']['progress:all']['value']
        latest = next(route for route in history['routes'] if route['strategy_id'] == 'B')['latest_attempt']
        self.assertIsNone(latest['latency']['value'])
        result = self.call(manifest, 'B-new', 'benchmark')
        wrong = next(row for row in result['benchmarks'] if row['evaluation_id'] == 'wrong-run-evaluation')
        self.assertEqual(wrong['availability'], 'unavailable')
        self.assertEqual(wrong['evidence'][0]['availability'], 'unavailable')

    async def test_missing_nonparent_source_does_not_block_other_candidates(self):
        self.candidate()
        missing = 'f' * 64
        self.append(Candidate('A-missing', 'A-new-plan', 'A-parent', CandidateIdentity(missing, missing, missing)))
        manifest = await self.capture()
        result = self.call(manifest, 'A-missing', 'source')
        self.assertEqual(result['availability'], 'unavailable')
        self.assertEqual(self.call(manifest, 'A-new', 'source')['availability'], 'available')

    async def test_restored_optional_metric_and_source_do_not_rewrite_frozen_input(self):
        from rsi_kernel_agent.contracts import ArtifactRef
        self.candidate('B-new', 'B')
        source = self.store.artifacts.put(b'def saved_candidate(): return 1\n')
        self.append(Candidate('B-unavailable-source', 'B-new-plan', 'B-parent',
            CandidateIdentity(source.digest, source.digest, source.digest)))
        source_path = self.store.artifacts.path_for(source)
        source_bytes = source_path.read_bytes()
        source_path.unlink()
        timing = self.store.get('B-new-run-timing')
        raw_ref = timing.artifacts[0]
        raw_path = self.store.artifacts.path_for(raw_ref)
        raw = raw_path.read_bytes()
        raw_path.unlink()
        manifest = await self.capture()
        before = manifest.to_dict()
        self.assertIsNone(before['references']['candidate:B-unavailable-source']['value']['source_refs']['source_ref'])
        source_path.write_bytes(source_bytes)
        raw_path.write_bytes(raw)
        reloaded = load_manifest(self.store, manifest.artifact_ref)
        self.assertEqual(reloaded.to_dict(), before)

    async def test_selected_source_bundle_must_match_frozen_build_identity(self):
        source, config, dependency = self.candidate()
        altered_bundle = self.store.artifacts.put(canonical_json({'schema_version': 1,
            'entry': {'path': 'kernel.py', 'symbol': 'A_new'}, 'files': [
                {'path': 'kernel.py', 'artifact_ref': asdict(source), 'role': 'source'},
                {'path': 'utils.py', 'artifact_ref': asdict(dependency), 'role': 'dependency'}]}).encode())
        build = self.store.artifacts.put(canonical_json({'schema_version': 1,
            'source_bundle_digest': '0' * 64, 'oracle_bundle_digest': '1' * 64,
            'runner_digest': '2' * 64, 'oracle_digest': '3' * 64}).encode())
        self.append(Candidate('A-bad-bundle', 'A-new-plan', 'A-parent',
                              CandidateIdentity(source.digest, config.digest, build.digest)))
        self.append(Decision('A-bad-bundle-source', 'task', 'note', 'A-bad-bundle', 'Wrong bundle',
            {'type': 'workflow_candidate.v1', 'source_ref': asdict(source), 'config_ref': asdict(config),
             'build_ref': asdict(build), 'source_bundle_ref': asdict(altered_bundle), 'provenance': 'real'}))
        result = self.call(await self.capture(), 'A-bad-bundle', 'source')
        self.assertEqual(result['availability'], 'unavailable')
        self.assertIn('build', result['reason'].lower())

    async def test_failed_measurement_dependencies_remain_unavailable(self):
        self.candidate()
        self.append(Evidence('failed-counter', 'A-new-run', 'measured_counter', 'unavailable', 'failed',
            'inconclusive', None, 'cycles', (), (), {'provenance': 'synthetic', 'reason': 'Counter unavailable'}))
        trace = self.store.get('A-new-run-trace')
        self.append(Evidence('unsupported-profile', 'A-new-run', 'trace_derived', 'available', 'completed',
            'supported', {'format': 'PallasKernels.read_profile'}, None, trace.artifacts, ('failed-counter',),
            {'provenance': 'synthetic'}))
        self.append(ProfileBundle('unsupported-profile-bundle', 'A-new-run', ('unsupported-profile',), 'available'))
        result = self.call(await self.capture(), 'A-new', 'profile')
        invalid = next(row for row in result['profiles'] if row['profile_id'] == 'unsupported-profile-bundle')
        self.assertEqual(invalid['availability'], 'unavailable')
        self.assertEqual(invalid['reports'], [])

    async def test_pre_catalog_metric_manifest_remains_replayable(self):
        from rsi_kernel_agent.profiler.inputs import _Capture, _capture_local, _capture_knowledge
        self.candidate()
        snapshot = self.store.create_snapshot('task', 'old-metrics')
        current = await self.capture(snapshot=snapshot)
        data = current.to_dict()
        capture = _Capture(self.store, format_revision=2)
        task, workload, strategies = _capture_local(self.store, data['request'], capture)
        knowledge = await _capture_knowledge(capture, self.f['knowledge'], data['request']['knowledge'])
        data.update(format_revision=2, task=task, workload=workload, strategies=strategies,
                    knowledge=knowledge, references=capture.references, record_refs=capture.records,
                    verified_artifacts=list(capture.artifacts.values()), issues=capture.issues,
                    route_context=capture.route_context)
        ref = self.store.artifacts.put(canonical_json(data).encode())
        replay = load_manifest(self.store, ref)
        self.assertEqual(replay.to_dict(), data)
        self.assertNotIn('candidate:A-new', replay.to_dict()['references'])

    async def test_forged_candidate_catalog_is_rejected_on_replay(self):
        self.candidate()
        manifest = await self.capture()
        data = manifest.to_dict()
        data['references']['candidate:A-new']['value']['strategy_id'] = 'B'
        ref = self.store.artifacts.put(canonical_json(data).encode())
        with self.assertRaises(ValueError):
            load_manifest(self.store, ref)


if __name__ == '__main__':
    unittest.main()
