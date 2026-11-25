"""Pinned prior analysis and source coverage survive independent route rounds."""
import copy
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from collections import Counter
from unittest.mock import patch

from phase2c_fixtures import make_analysis_fixture
from test_analyzer import proposal
from test_knowledge import Notes
from rsi_kernel_agent.contracts import ArtifactRef, Strategy, canonical_json
from rsi_kernel_agent.discovery import archive_source
from rsi_kernel_agent.knowledge import KnowledgeClient, extract_method, method_note_id
from rsi_kernel_agent.profiler.analyzer import Analyzer
from rsi_kernel_agent.profiler.inputs import AnalysisRequest, capture_inputs, load_manifest
from rsi_kernel_agent.profiler.outputs import publish_result
from rsi_kernel_agent.workflow.design import (
    begin_design_round, complete_design_round, finish_route_design, route_manifest,
)


class InputHistoryProvenanceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fixture = make_analysis_fixture(temporary.name)
        self.store = self.fixture['store']

    def request(self, snapshot=None):
        raw = copy.deepcopy(self.fixture['request'])
        raw['schema_version'] = 2
        raw['strategies'] = raw['strategies'][:1]
        raw['environment'].update(design_round_id='round', design_batch_id='batch')
        if snapshot is not None:
            raw['strategies'][0]['snapshot_id'] = snapshot['id']
        return raw

    async def capture(self, raw=None, knowledge=None):
        return await capture_inputs(self.store, knowledge or self.fixture['knowledge'],
                                    AnalysisRequest.from_dict(raw or self.request()))

    async def test_next_design_round_reads_its_prior_hypothesis_and_plan_risk(self):
        assessments = {
            'A': {'status': 'hypothesis', 'reason': 'Inspect tile lifetime',
                  'hypothesis': 'Tile lifetime may help', 'refs': []},
            'B': {'status': 'deferred', 'reason': 'B is deferred', 'hypothesis': None, 'refs': []},
        }
        batch = await begin_design_round(self.store, self.fixture['knowledge'], 'task', 'context',
                                         'first', assessments, parents={'A': 'A-parent'})
        manifest = route_manifest(self.store, batch.id, 'A')
        answer = proposal()
        for field in ('code_understanding', 'observations', 'hypotheses', 'plans'):
            answer[field] = [row for row in answer[field] if row['strategy_id'] == 'A']
        answer['plans'] = answer['plans'][:1]
        answer['hypotheses'][0]['text'] = 'PRIOR_A_HYPOTHESIS'
        answer['plans'][0]['risks'] = ['PRIOR_A_PLAN_RISK']
        result = Analyzer(manifest).advance({'final': answer})
        marker = publish_result(self.store, manifest, result, 'prior-A')
        finish_route_design(self.store, batch.id, 'A', marker.id)
        complete_design_round(self.store, batch.id)
        second = await begin_design_round(self.store, self.fixture['knowledge'], 'task', 'context',
                                          'second', assessments, parents={'A': 'A-parent'})
        following = route_manifest(self.store, second.id, 'A')
        data = following.to_dict()
        self.assertIn(marker.id, data['record_refs'])
        self.assertIn(marker.details['result'], data['verified_artifacts'])
        history = Analyzer(following).tools.call('history', {'strategy_id': 'A'})
        self.assertIn('PRIOR_A_HYPOTHESIS', canonical_json(history))
        self.assertIn('PRIOR_A_PLAN_RISK', canonical_json(history))
        self.assertEqual(load_manifest(self.store, following.artifact_ref).to_dict(), data)

    async def legacy_publication(self):
        manifest = await self.capture(self.fixture['request'])
        answer = proposal()
        answer['hypotheses'][0]['text'] = 'LEGACY_A_HYPOTHESIS'
        answer['hypotheses'].append(dict(id='h2', strategy_id='B', text='PRIVATE_B_HYPOTHESIS',
                                        refs=['source:B'], alternatives=['PRIVATE_B_ALTERNATIVE']))
        answer['plans'][0]['risks'] = ['LEGACY_A_RISK']
        answer['plans'][2]['risks'] = ['PRIVATE_B_RISK']
        answer['unknowns'] = ['GLOBAL_PRIVATE_B_UNKNOWN']
        analyzer = Analyzer(manifest)
        analyzer.advance({'tool_calls': [{'id': 'private-B', 'name': 'source', 'arguments': {'strategy_id': 'B'}}]})
        result = analyzer.advance({'final': answer})
        return publish_result(self.store, manifest, result, 'legacy-multiple')

    async def test_legacy_multi_route_publication_projects_only_the_selected_route(self):
        marker = await self.legacy_publication()
        snapshot = self.store.create_snapshot('task', 'after-legacy')
        manifest = await self.capture(self.request(snapshot))
        data = manifest.to_dict()
        self.assertIn(marker.id, data['record_refs'])
        text = canonical_json(Analyzer(manifest).packet())
        self.assertIn('LEGACY_A_HYPOTHESIS', text)
        self.assertIn('LEGACY_A_RISK', text)
        for private in ('PRIVATE_B_HYPOTHESIS', 'PRIVATE_B_ALTERNATIVE', 'PRIVATE_B_RISK',
                        'GLOBAL_PRIVATE_B_UNKNOWN', 'private-B', 'Synthetic route B'):
            self.assertNotIn(private, text)
        self.assertEqual(load_manifest(self.store, manifest.artifact_ref).to_dict(), data)

    async def test_shared_parent_run_does_not_make_child_publication_parent_analysis(self):
        self.store.append(Strategy('D', 'task', 'Independent partial reduction'), 'D')
        snapshot = self.store.create_snapshot('task', 'child-before-publication')
        raw = self.request(snapshot)
        raw['strategies'][0]['strategy_id'] = 'D'
        child_manifest = await self.capture(raw)
        publications = []
        for planned in (True, False):
            answer = proposal()
            for field in ('code_understanding', 'observations', 'hypotheses', 'plans'):
                answer[field] = [row for row in answer[field] if row['strategy_id'] == 'A']
                for row in answer[field]:
                    row['strategy_id'] = 'D'
                    row['refs'] = ['source:D']
            answer['hypotheses'][0]['text'] = 'PRIVATE_D_HYPOTHESIS'
            answer['plans'] = answer['plans'][:1] if planned else []
            if not planned:
                answer['hypotheses'] = []
                answer['outcome'] = {'status': 'needs_research', 'reason': 'PRIVATE_D_OUTCOME', 'refs': ['source:D']}
            result = Analyzer(child_manifest).advance({'final': answer})
            publications.append(publish_result(self.store, child_manifest, result, 'child-analysis-' + str(planned)))
        snapshot = self.store.create_snapshot('task', 'child-after-publication')
        parent_manifest = await self.capture(self.request(snapshot))
        parent = parent_manifest.to_dict()
        for publication in publications:
            self.assertNotIn(publication.id, parent['record_refs'])
        self.assertIn('A-run', parent['record_refs'])
        self.assertNotIn('PRIVATE_D_', canonical_json(Analyzer(parent_manifest).packet()))
        self.assertEqual(load_manifest(self.store, parent_manifest.artifact_ref).to_dict(), parent)
        raw['strategies'][0]['snapshot_id'] = snapshot['id']
        child = await self.capture(raw)
        for publication in publications:
            self.assertIn(publication.id, child.to_dict()['record_refs'])
        text = canonical_json(Analyzer(child).packet())
        self.assertIn('PRIVATE_D_HYPOTHESIS', text)
        self.assertIn('PRIVATE_D_OUTCOME', text)
        self.assertEqual(load_manifest(self.store, child.artifact_ref).to_dict(), child.to_dict())

    async def test_missing_prior_result_artifact_cannot_claim_complete_history(self):
        marker = await self.legacy_publication()
        snapshot = self.store.create_snapshot('task', 'missing-legacy')
        self.store.artifacts.path_for(ArtifactRef(**marker.details['result'])).unlink()
        with self.assertRaises(ValueError):
            await self.capture(self.request(snapshot))

    async def publication_history(self, rounds=4):
        markers = []
        for number in range(rounds):
            snapshot = self.store.create_snapshot('task', 'history-round-' + str(number))
            manifest = await self.capture(self.request(snapshot))
            answer = proposal()
            for field in ('code_understanding', 'observations', 'hypotheses', 'plans'):
                answer[field] = [row for row in answer[field] if row['strategy_id'] == 'A']
            answer['plans'] = answer['plans'][:1]
            answer['hypotheses'][0]['text'] = 'ROUND_HYPOTHESIS_' + str(number)
            result = Analyzer(manifest).advance({'final': answer})
            markers.append(publish_result(self.store, manifest, result, 'history-publication-' + str(number)))
        return markers, self.store.create_snapshot('task', 'history-after-publications')

    async def test_prior_publication_validation_is_bounded_per_capture_and_replay(self):
        from rsi_kernel_agent.workflow import intake
        markers, snapshot = await self.publication_history()
        validate = intake._publication
        visited = []

        def observe(store, marker):
            visited.append(marker.id)
            return validate(store, marker)

        with patch.object(intake, '_publication', observe):
            manifest = await self.capture(self.request(snapshot))
        self.assertEqual(Counter(visited), {marker.id: 1 for marker in markers})
        for number in range(4):
            self.assertIn('ROUND_HYPOTHESIS_' + str(number), canonical_json(manifest.to_dict()))
        visited.clear()
        with patch.object(intake, '_publication', observe):
            replayed = load_manifest(self.store, manifest.artifact_ref)
        self.assertEqual(Counter(visited), {marker.id: 1 for marker in markers})
        self.assertEqual(replayed.to_dict(), manifest.to_dict())

    async def test_reused_publication_still_checks_raw_bytes_within_one_capture(self):
        from rsi_kernel_agent.workflow import intake
        markers, snapshot = await self.publication_history(rounds=2)
        validate = intake._publication
        corrupted = False
        artifact = ArtifactRef(**markers[0].details['result'])

        def corrupt_after_validation(store, marker):
            nonlocal corrupted
            result = validate(store, marker)
            if marker.id == markers[0].id and not corrupted:
                corrupted = True
                store.artifacts.path_for(artifact).write_bytes(b'corrupted during capture')
            return result

        with patch.object(intake, '_publication', corrupt_after_validation):
            with self.assertRaises(ValueError):
                await self.capture(self.request(snapshot))

    async def test_later_replay_revalidates_transitive_prior_source_bytes(self):
        markers, snapshot = await self.publication_history(rounds=2)
        raw = self.request(snapshot)
        raw['knowledge'] = []
        manifest = await self.capture(raw)
        load_manifest(self.store, manifest.artifact_ref)
        old = load_manifest(self.store, ArtifactRef(**markers[0].details['manifest'])).to_dict()
        source = next(iter(old['knowledge'].values()))['sources'][0]
        self.store.artifacts.path_for(ArtifactRef(**source['artifact'])).write_bytes(b'changed old source')
        with self.assertRaises(ValueError):
            load_manifest(self.store, manifest.artifact_ref)

    async def partial_source(self):
        notes = Notes()
        knowledge = KnowledgeClient(notes.call)
        content = '# Partial source\n\n## Mechanism\nReturned paragraph.\n\n## Caveat\nFULL_SAVED_SOURCE_CAVEAT\n'
        supplied = {'channel': 'external', 'url': 'https://research.example.org/partial',
                    'title': 'Partial source', 'content': content,
                    'content_sha256': sha256(content.encode()).hexdigest(),
                    'upstream_revision': 'v1', 'locator': 'paragraph:1',
                    'coverage': {'scope': 'document', 'expected_chars': len(content) + 2000,
                                 'read_chars': len(content), 'complete': False}}
        document = await archive_source(knowledge, supplied)
        method = extract_method(document, 'Mechanism', mechanism='partial source test')
        saved = await knowledge.save_method(method)
        raw = self.request()
        raw['knowledge'] = [{'method_id': method.method_id, 'note_id': method_note_id(method.method_id),
                             'revision': saved['revision']}]
        return await self.capture(raw, knowledge), method.method_id, document, supplied

    async def test_partial_source_retains_coverage_and_full_saved_text_offline(self):
        manifest, mid, document, supplied = await self.partial_source()
        data = manifest.to_dict()
        source = data['knowledge'][mid]['sources'][0]
        self.assertEqual(source['availability'], 'partial')
        self.assertFalse(source['coverage_complete'])
        self.assertEqual(source['provenance']['coverage'], supplied['coverage'])
        self.assertEqual(source['document']['content'], document.content)
        self.assertTrue(any(row['code'] == 'knowledge_source_partial' for row in data['issues']))
        replayed = load_manifest(self.store, manifest.artifact_ref)
        tool = Analyzer(replayed).tools.call('knowledge', {'method_id': mid})
        self.assertIn('FULL_SAVED_SOURCE_CAVEAT', canonical_json(tool))

    async def test_replay_rejects_forged_source_coverage_and_full_text(self):
        manifest, mid, _, _ = await self.partial_source()
        original = manifest.to_dict()
        self.assertIn('document', original['knowledge'][mid]['sources'][0])
        for change in ('coverage', 'text'):
            forged = copy.deepcopy(original)
            source = forged['knowledge'][mid]['sources'][0]
            if change == 'coverage':
                source['coverage_complete'] = True
            else:
                source['document']['content'] += ' fabricated text'
            forged['references']['knowledge:' + mid]['sources'] = copy.deepcopy(forged['knowledge'][mid]['sources'])
            artifact = self.store.artifacts.put(canonical_json(forged).encode())
            with self.subTest(change=change), self.assertRaises(ValueError):
                load_manifest(self.store, artifact)


if __name__ == '__main__':
    unittest.main()
