"""Manifest isolation, exact binding and replay behavior on real SQLite/artifacts."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest

from phase2c_fixtures import make_analysis_fixture
from rsi_kernel_agent.contracts import ArtifactRef, EvaluationBundle, Evidence, ParentCandidate, ProfileBundle, canonical_json
try:
    from rsi_kernel_agent.profiler.inputs import AnalysisRequest, capture_inputs, load_manifest
except ImportError:
    AnalysisRequest = None


class AnalysisInputTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assertIsNotNone(AnalysisRequest, 'Phase 2C input contract is not implemented')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fx = make_analysis_fixture(self.temp.name)
        self.store, self.raw, self.knowledge = self.fx['store'], self.fx['request'], self.fx['knowledge']

    async def capture(self, raw=None):
        return await capture_inputs(self.store, self.knowledge, AnalysisRequest.from_dict(raw or self.raw))

    async def test_exact_independent_complete_histories_and_detached_manifest(self):
        before = len(self.store.history('task'))
        manifest = await self.capture()
        data = manifest.to_dict()
        self.assertEqual(set(data['strategies']), {'A', 'B'})
        for strategy in 'AB':
            item = data['strategies'][strategy]
            self.assertTrue(item['history']['complete'])
            self.assertIn(strategy + '-run', item['history']['owned_ids'])
            self.assertNotIn(('B' if strategy == 'A' else 'A') + '-run', item['history']['owned_ids'])
            self.assertEqual(item['parent']['candidate_id'], strategy + '-candidate')
        self.assertFalse(data['consistency']['combined_atomic_snapshot'])
        self.assertFalse(data['consistency']['knowledge_corpus_snapshot_consistent'])
        data['strategies']['A']['source'] = 'changed'
        self.assertIn('def matmul', manifest.to_dict()['strategies']['A']['source'])
        self.assertEqual(len(self.store.history('task')), before)
        self.assertIsNone(self.store.best('task', 'context'))

    async def test_wrong_parent_run_and_cross_strategy_profile_are_rejected(self):
        for field, value in [('parent_id', 'B-parent'), ('run_id', 'B-run'), ('profile_ids', ['B-profile'])]:
            with self.subTest(field=field):
                raw = deepcopy(self.raw)
                raw['strategies'][0][field] = value
                with self.assertRaises(ValueError):
                    await self.capture(raw)

    async def test_transitive_cross_strategy_evidence_is_rejected(self):
        self.store.append(Evidence('laundered', 'B-run', 'derived_measurement', 'available', 'completed', 'supported',
                                   10, 'ms', input_ids=('A-timing',), source={'provenance': 'synthetic'}), 'laundered')
        self.store.append(ProfileBundle('laundered-profile', 'B-run', ('laundered',), 'available'), 'laundered-profile')
        snap = self.store.create_snapshot('task', 'laundered-snapshot')
        self.raw['strategies'][1]['profile_ids'] = ['laundered-profile']
        self.raw['strategies'][1]['snapshot_id'] = snap['id']
        with self.assertRaises(ValueError):
            await self.capture()

    async def test_wrong_source_and_config_digest_rejected(self):
        for field in ('source_artifact', 'config_artifact'):
            raw = deepcopy(self.raw)
            raw['strategies'][0][field] = asdict(self.store.artifacts.put(b'wrong'))
            with self.subTest(field=field), self.assertRaises(ValueError):
                await self.capture(raw)

    async def test_missing_required_source_is_rejected(self):
        self.store.artifacts.path_for(ArtifactRef(**self.raw['strategies'][0]['source_artifact'])).unlink()
        with self.assertRaises(ValueError):
            await self.capture()

    async def test_missing_profile_raw_bytes_mark_unavailable(self):
        ref = self.store.get('A-trace').artifacts[0]
        self.store.artifacts.path_for(ref).unlink()
        data = (await self.capture()).to_dict()
        self.assertEqual(data['references']['profile:A-profile']['availability'], 'unavailable')
        self.assertEqual(data['references']['evidence:A-trace']['availability'], 'unavailable')
        self.assertTrue(any(issue['code'] == 'artifact_unavailable' for issue in data['issues']))
        self.assertEqual(data['references']['profile:B-profile']['availability'], 'available')

    async def test_replay_uses_pinned_knowledge_after_live_edit(self):
        manifest = await self.capture()
        self.fx['documents'].clear()
        replay = load_manifest(self.store, manifest.artifact_ref)
        self.assertEqual(replay.to_dict(), manifest.to_dict())
        method_id = self.raw['knowledge'][0]['method_id']
        self.assertIn('Independent loads', replay.to_dict()['knowledge'][method_id]['method']['description'])

    async def test_stale_method_revision_is_explicit_not_silently_replaced(self):
        pin = self.raw['knowledge'][0]
        self.fx['documents'][pin['note_id']] += b'\nHuman edit\n'
        data = (await self.capture()).to_dict()
        self.assertEqual(data['knowledge'][pin['method_id']]['availability'], 'stale')
        self.assertIsNone(data['knowledge'][pin['method_id']]['method'])

    async def test_stale_source_preserves_pinned_excerpt_and_distinct_bom_digest(self):
        manifest = await self.capture()
        method_id = self.raw['knowledge'][0]['method_id']
        source = manifest.to_dict()['knowledge'][method_id]['sources'][0]
        self.assertNotEqual(source['revision'], source['artifact']['digest'])
        self.fx['documents']['Research/Pipelining.md'] += b'changed'
        data = (await self.capture()).to_dict()['knowledge'][method_id]
        self.assertEqual(data['sources'][0]['availability'], 'stale')
        self.assertIn('Independent loads', data['method']['description'])

    async def test_replay_fails_on_corrupted_referenced_artifact(self):
        manifest = await self.capture()
        ref = self.store.get('A-trace').artifacts[0]
        self.store.artifacts.path_for(ref).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            load_manifest(self.store, manifest.artifact_ref)

    async def test_replay_rejects_forged_parent_reference_and_knowledge_payload(self):
        original = (await self.capture()).to_dict()
        variants = []
        forged = deepcopy(original)
        forged['strategies']['A']['parent'] = original['strategies']['B']['parent']
        variants.append(forged)
        forged = deepcopy(original)
        forged['references']['source:A']['value'] = 'unrelated source'
        variants.append(forged)
        forged = deepcopy(original)
        mid = self.raw['knowledge'][0]['method_id']
        forged['knowledge'][mid]['method']['description'] = 'Invented source claim'
        variants.append(forged)
        forged = deepcopy(original)
        del forged['record_refs']['A-run']
        variants.append(forged)
        forged = deepcopy(original)
        forged['strategies']['A']['context']['environment'] = {'device': 'invented TPU'}
        variants.append(forged)
        forged = deepcopy(original)
        del forged['knowledge'][mid]['sources']
        variants.append(forged)
        for index, forged in enumerate(variants):
            ref = self.store.artifacts.put(canonical_json(forged).encode())
            with self.subTest(index=index), self.assertRaises(ValueError):
                load_manifest(self.store, ref)

    async def test_runless_imported_parent_retains_legal_exploration_inputs(self):
        existing = self.store.get('A-parent')
        self.store.append(ParentCandidate('unmeasured-parent', 'task', existing.identity), 'unmeasured-parent')
        snap = self.store.create_snapshot('task', 'unmeasured-snapshot')
        item = self.raw['strategies'][0]
        item.update(parent_id='unmeasured-parent', run_id=None, profile_ids=[], benchmark_ids=[], snapshot_id=snap['id'])
        result = await self.capture()
        self.assertIsNone(result.to_dict()['strategies']['A']['run'])
        self.assertTrue(any(x['code'] == 'not_supplied' for x in result.to_dict()['issues']))
        self.assertEqual(load_manifest(self.store, result.artifact_ref).to_dict(), result.to_dict())

    async def test_replay_rejects_equal_python_but_different_json_types(self):
        original = (await self.capture()).to_dict()
        variants = []
        forged = deepcopy(original)
        forged['workload']['spec']['M'] = 32.0
        variants.append(forged)
        forged = deepcopy(original)
        forged['consistency']['memory_snapshot_complete'] = 1
        variants.append(forged)
        for index, forged in enumerate(variants):
            ref = self.store.artifacts.put(canonical_json(forged).encode())
            with self.subTest(index=index), self.assertRaises(ValueError):
                load_manifest(self.store, ref)

    async def test_replay_rejects_contradictory_stale_source_states(self):
        self.fx['documents']['Research/Pipelining.md'] += b'changed'
        original = (await self.capture()).to_dict()
        mid = self.raw['knowledge'][0]['method_id']
        variants = []
        for field, value in [('current_revision', original['knowledge'][mid]['sources'][0]['revision']),
                             ('reason', None), ('artifact', self.raw['strategies'][0]['source_artifact'])]:
            forged = deepcopy(original)
            forged['knowledge'][mid]['sources'][0][field] = value
            forged['references']['knowledge:' + mid]['sources'][0][field] = value
            variants.append(forged)
        for index, forged in enumerate(variants):
            ref = self.store.artifacts.put(canonical_json(forged).encode())
            with self.subTest(index=index), self.assertRaises(ValueError):
                load_manifest(self.store, ref)

    async def test_replay_requires_complete_verified_and_available_artifact_catalogs(self):
        original = (await self.capture()).to_dict()
        variants = []
        forged = deepcopy(original)
        forged['verified_artifacts'] = []
        variants.append(forged)
        forged = deepcopy(original)
        forged['references']['evidence:A-trace']['artifacts'] = []
        variants.append(forged)
        for index, forged in enumerate(variants):
            ref = self.store.artifacts.put(canonical_json(forged).encode())
            with self.subTest(index=index), self.assertRaises(ValueError):
                load_manifest(self.store, ref)

    async def test_evidence_created_after_snapshot_cannot_appear_current(self):
        self.store.append(ProfileBundle('late-profile', 'A-run', ('A-trace',), 'available'), 'late-profile')
        self.raw['strategies'][0]['profile_ids'] = ['late-profile']
        with self.assertRaises(ValueError):
            await self.capture()

    async def test_failed_or_refuted_evidence_cannot_support_current_measurement(self):
        for status, claim, name in [('failed', 'supported', 'failed'), ('completed', 'refuted', 'refuted')]:
            raw = self.store.artifacts.put(b'synthetic unreliable profile data')
            evidence = Evidence(name, 'A-run', 'trace_derived', 'available', status, claim, {'count': 1},
                                artifacts=(raw,), source={'provenance': 'synthetic'})
            self.store.append(evidence, name)
            self.store.append(ProfileBundle(name + '-profile', 'A-run', (name,), 'available'), name + '-profile')
            snap = self.store.create_snapshot('task', name + '-snapshot')
            request = deepcopy(self.raw)
            request['strategies'][0].update(snapshot_id=snap['id'], profile_ids=[name + '-profile'])
            data = (await self.capture(request)).to_dict()
            with self.subTest(name=name):
                self.assertNotEqual(data['references']['evidence:' + name]['availability'], 'available')
                self.assertNotEqual(data['references']['profile:' + name + '-profile']['availability'], 'available')
                self.assertTrue(any(issue['code'] == 'evidence_not_supported' for issue in data['issues']))

    async def test_conflicting_benchmark_latency_retains_record_but_is_unavailable(self):
        self.store.append(EvaluationBundle('contradictory-benchmark', 'A-run', 'passed', 'valid', 999, True,
                                          ('A-timing',)), 'contradictory-benchmark')
        snapshot = self.store.create_snapshot('task', 'contradictory-snapshot')
        self.raw['strategies'][0].update(snapshot_id=snapshot['id'], benchmark_ids=['contradictory-benchmark'])
        manifest = await self.capture()
        data = manifest.to_dict()
        self.assertEqual(data['references']['benchmark:contradictory-benchmark']['availability'], 'unavailable')
        self.assertEqual(data['references']['benchmark:contradictory-benchmark']['value']['latency_ms'], 999)
        self.assertTrue(any(x['code'] == 'benchmark_not_verified' for x in data['issues']))
        self.assertEqual(load_manifest(self.store, manifest.artifact_ref).to_dict(), data)

    async def test_existing_derived_timing_kinds_remain_compatible(self):
        for kind in ('trace_derived','derived_measurement'):
            artifact=self.store.artifacts.put(b'synthetic derived timing')
            self.store.append(Evidence(kind,'A-run',kind,'available','completed','supported',10,'ms',(artifact,),source={'provenance':'synthetic'}),kind)
            self.store.append(EvaluationBundle(kind+'-eval','A-run','passed','valid',10,True,(kind,)),kind+'-eval')
            snapshot=self.store.create_snapshot('task',kind+'-snapshot')
            request=deepcopy(self.raw)
            request['strategies'][0].update(snapshot_id=snapshot['id'],benchmark_ids=[kind+'-eval'])
            data=(await self.capture(request)).to_dict()
            self.assertEqual(data['references']['benchmark:'+kind+'-eval']['availability'],'available')

    async def test_request_rejects_unknown_fields_boolean_version_and_duplicate_routes(self):
        variants = []
        for value in (True, 1.0, 2):
            raw = deepcopy(self.raw)
            raw['schema_version'] = value
            variants.append(raw)
        raw = deepcopy(self.raw)
        raw['winner'] = 'A'
        variants.append(raw)
        raw = deepcopy(self.raw)
        raw['strategies'].append(deepcopy(raw['strategies'][0]))
        variants.append(raw)
        for raw in variants:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                AnalysisRequest.from_dict(raw)


if __name__ == '__main__':
    unittest.main()
