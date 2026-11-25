import copy
from dataclasses import replace
from hashlib import sha256
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from phase2c_fixtures import make_analysis_fixture
from test_knowledge import Notes
from rsi_kernel_agent.associations import MemoryWriter
from rsi_kernel_agent.contracts import Decision, EvaluationContext, Strategy, Task, Workload, canonical_json
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.knowledge import (
    KnowledgeClient, KnowledgeError, RevisionConflict, SourceDocument,
    extract_method, method_from_dict, method_note_id,
)
from rsi_kernel_agent.memory import MemoryReader


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('rsi_kernel_agent.discovery'),
                             'Open discovery and mechanism review are not implemented')
        self.api = importlib.import_module('rsi_kernel_agent.discovery')
        self.notes = Notes()
        self.notes.put('Research/local.md', '# Local\n## Buffers\nRetain independent buffers.\n')
        self.knowledge = KnowledgeClient(self.notes.call)

    async def test_external_unavailable_keeps_local_results_without_fake_discovery(self):
        result = await self.api.search_sources(self.knowledge, ['buffers'])
        self.assertEqual(result['availability']['external'], 'unavailable')
        self.assertEqual(result['documents'][0]['id'], 'Research/local.md')
        self.assertIn('Retain independent buffers.', result['documents'][0]['content'])
        self.assertTrue(result['unknowns'])
        self.assertFalse(result['knowledge_corpus_snapshot_consistent'])
        self.assertEqual(self.notes.writes, 0)

    async def test_open_search_reads_returned_nonseed_url_and_records_actual_identity(self):
        calls = []
        content = '# Synthetic external source\n\n## Decomposition\nPartial results can be merged.\n'

        async def external(tool, args):
            calls.append((tool, args))
            if tool == 'search':
                return {'status': 'available', 'results': [
                    {'url': 'https://research.example.org/decomposition', 'title': 'A discovered source'}]}
            return {'status': 'available', 'url': args['url'], 'title': 'A discovered source',
                    'content': content, 'revision': 'source-v7', 'locator': 'section:Decomposition'}

        result = await self.api.search_sources(self.knowledge, ['decomposition'], external=external)
        captured = result['documents'][0]
        self.assertEqual(captured['url'], 'https://research.example.org/decomposition')
        self.assertEqual(captured['upstream_revision'], 'source-v7')
        self.assertEqual(captured['content_sha256'], sha256(content.encode()).hexdigest())
        self.assertEqual(captured['locator'], 'section:Decomposition')
        self.assertEqual(calls[0], ('search', {'query': 'decomposition', 'limit': 8}))
        self.assertEqual(calls[1], ('read', {'url': captured['url']}))
        self.assertEqual(result['availability']['external'], 'available')
        self.assertEqual(len([x for x in result['request_log'] if x['channel'] == 'external']), 2)
        self.assertNotIn('real_tpu', json.dumps(result))

    async def test_search_and_read_are_bounded_and_unread_sources_are_explicit(self):
        for i in range(60):
            self.notes.put(f'Research/many-{i}.md', '# Shared public mechanism\n')
        calls = []

        async def external(tool, args):
            calls.append((tool, args))
            if tool == 'search':
                return {'status': 'available', 'results': [
                    {'url': f'https://research.example.org/{i}', 'title': str(i)} for i in range(2)]}
            return {'status': 'available', 'url': args['url'], 'title': 'Source', 'content': 'Source text'}

        result = await self.api.search_sources(self.knowledge, ['Shared'], external=external, max_reads=2)
        self.assertEqual(len(result['documents']), 2)
        self.assertEqual({d['channel'] for d in result['documents']}, {'knowledge', 'external'})
        self.assertTrue(result['deferred'])
        self.assertTrue(any('limit' in item['reason'].lower() for item in result['deferred']))
        self.assertLessEqual(len([c for c in calls if c[0] == 'read']), 2)
        with self.assertRaises(KnowledgeError):
            await self.api.search_sources(self.knowledge, ['x'] * 9)

    async def test_missing_external_content_is_unavailable_and_never_a_source(self):
        async def external(tool, args):
            if tool == 'search':
                return {'status': 'available', 'results': [
                    {'url': 'https://research.example.org/missing', 'title': 'Missing'}]}
            return {'status': 'unavailable', 'reason': 'Synthetic transport outage'}

        result = await self.api.search_sources(self.knowledge, ['missing'], external=external)
        self.assertEqual(result['documents'], [])
        self.assertTrue(result['unknowns'])
        self.assertEqual(result['request_log'][-1]['status'], 'unavailable')

    async def test_search_cannot_turn_private_or_file_urls_into_read_requests(self):
        reads = []

        async def external(tool, args):
            if tool == 'search':
                return {'status': 'available', 'results': [
                    {'url': url, 'title': 'Untrusted result'} for url in (
                        'file:///C:/private.txt', 'http://127.0.0.1/token', 'http://127.1/',
                        'http://0x7f.0.0.1/', 'http://0177.0.0.1/',
                        'http://192.168.1.2/admin', 'https://user:password@example.org/x')]}
            reads.append(args)
            self.fail('Unsafe URL reached external read boundary')

        result = await self.api.search_sources(self.knowledge, ['safety'], external=external)
        self.assertEqual(reads, [])
        self.assertEqual(result['documents'], [])
        self.assertTrue(result['unknowns'])

    async def test_empty_search_is_a_valid_result_not_invented_methods(self):
        async def external(tool, args):
            return {'status': 'available', 'results': []}

        result = await self.api.search_sources(self.knowledge, ['no match'], external=external)
        self.assertEqual(result['documents'], [])
        self.assertEqual(result['availability'], {'knowledge': 'available', 'external': 'available'})
        self.assertEqual(self.notes.writes, 0)

    async def test_archive_source_uses_note_revision_and_does_not_overwrite_human_edits(self):
        capture = {'channel': 'external', 'url': 'https://research.example.org/source',
                   'title': 'A source', 'content': '# Source\n\n## Idea\nA reusable idea.\n',
                   'upstream_revision': 'v1', 'locator': 'section:Idea', 'read_at': '2026-11-16T10:00:00Z'}
        capture['content_sha256'] = sha256(capture['content'].encode()).hexdigest()
        first = await self.api.archive_source(self.knowledge, capture)
        second = await self.api.archive_source(self.knowledge, capture)
        self.assertEqual(first, second)
        self.assertTrue(first.id.startswith('RSI/Sources/'))
        self.assertEqual(first.revision, self.notes.notes[first.id]['revision'])
        self.assertIn(capture['content'], first.content)
        self.assertIn(capture['content_sha256'], first.content)
        self.assertEqual(self.notes.writes, 1)
        self.notes.put(first.id, self.notes.notes[first.id]['content'] + '\nHuman note.\n')
        with self.assertRaises(RevisionConflict):
            await self.api.archive_source(self.knowledge, capture)
        self.assertIn('Human note.', self.notes.notes[first.id]['content'])
        capture['content'] += '\nDifferent source revision.\n'
        capture['content_sha256'] = sha256(capture['content'].encode()).hexdigest()
        changed = await self.api.archive_source(self.knowledge, capture)
        self.assertNotEqual(changed.id, first.id)

    async def test_archive_preserves_explicit_partial_read_count(self):
        content = 'A complete returned body'

        async def external(tool, args):
            if tool == 'search':
                return {'status': 'available', 'results': [
                    {'url': 'https://research.example.org/partial-count', 'title': 'Partial count'}]}
            return {'status': 'available', 'url': args['url'], 'title': 'Partial count', 'content': content,
                    'coverage': {'scope': 'document', 'expected_chars': len(content),
                                 'read_chars': 4, 'complete': True}}

        result = await self.api.search_sources(None, ['partial-count'], external=external)
        capture = result['documents'][0]
        self.assertFalse(capture['coverage']['complete'])
        self.assertEqual(capture['coverage']['read_chars'], 4)
        document = await self.api.archive_source(self.knowledge, capture)
        metadata = json.loads(document.content.split('```rsi-source-v1\n', 1)[1].split('\n```', 1)[0])
        self.assertFalse(metadata['coverage']['complete'])
        self.assertEqual(metadata['coverage']['read_chars'], 4)

    async def test_archive_rejects_tampered_content_identity(self):
        capture = {'channel': 'external', 'url': 'https://research.example.org/source',
                   'title': 'Source', 'content': 'changed', 'content_sha256': '0' * 64,
                   'upstream_revision': None, 'locator': 'returned-content', 'read_at': '2026-11-16T10:00:00Z'}
        with self.assertRaises(KnowledgeError):
            await self.api.archive_source(self.knowledge, capture)
        self.assertEqual(self.notes.writes, 0)


class MethodReviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNotNone(importlib.util.find_spec('rsi_kernel_agent.discovery'),
                             'Open discovery and mechanism review are not implemented')
        self.api = importlib.import_module('rsi_kernel_agent.discovery')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = make_analysis_fixture(self.temp.name)
        self.store = self.fixture['store']
        self.notes = Notes()
        for name, raw in self.fixture['documents'].items():
            self.notes.put(name, raw.decode('utf-8'))
        self.knowledge = KnowledgeClient(self.notes.call)
        self.old = await self.knowledge.get_method(self.fixture['request']['knowledge'][0]['method_id'])
        self.notes.put('Research/new-source.md', '# Another explanation\n## Async staging\nOverlap independent loads with arithmetic.\n')
        self.proposed = extract_method(await self.knowledge.fetch_document('Research/new-source.md'),
                                       'Async staging', mechanism='overlap')
        self.store.append(Strategy('paused', 'task', 'Historical route'), 'paused-route')
        self.store.append(Decision('pause', 'task', 'paused', 'paused', 'Await target conditions'), 'pause')

    async def inventory(self, key='review-snapshot'):
        view = MemoryReader(self.store).open_snapshot(self.store.create_snapshot('task', key)['id'])
        result = self.api.build_review_inventory(self.store, view, 'context', await self.knowledge.find_methods())
        return self.api.expand_review_inventory(self.store, result, ['source:A-candidate', 'source:B-candidate'])

    def review(self, inventory, **changes):
        result = {'inventory_digest': inventory['digest'], 'closest_method_id': self.old.method_id,
                  'global_relation': 'same', 'task_relation': 'new_to_task', 'route_action': 'reuse',
                  'strategy_id': 'A', 'mechanism_difference': 'Same independent load/compute overlap; different explanatory source.',
                  'structural_change': 'same', 'code_refs': ['source:A-candidate', 'source:B-candidate'],
                  'plan_refs': ['plan:A-initial', 'plan:B-initial'],
                  'reviewed_strategy_ids': ['A', 'B', 'paused'],
                  'reviewed_method_ids': [self.old.method_id], 'lineage': None,
                  'coverage_rationale': self.coverage_rationale()}
        result.update(changes)
        return result

    def coverage_rationale(self):
        return [
            {'strategy_id': 'A', 'mechanism_difference': 'A uses serial accumulation inside the tiled loop.',
             'refs': ['plan:A-initial', 'source:A-candidate']},
            {'strategy_id': 'B', 'mechanism_difference': 'B retains the same accumulation structure with a smaller tile.',
             'refs': ['plan:B-initial']},
            {'strategy_id': 'paused', 'mechanism_difference': 'The paused route has no recorded Plan or implementation to compare.',
             'refs': ['strategy:paused']},
        ]

    async def test_legacy_review_remains_readable_but_bare_strategy_ids_cannot_confirm_novelty(self):
        inventory = await self.inventory()
        review = self.review(inventory, global_relation='new', structural_change='reduction',
            mechanism_difference='Independent partial accumulators followed by a merge.',
            route_action='fork', strategy_id='new-D',
            lineage={'strategy_id': 'A', 'parent_candidate_id': 'A-candidate'})
        review.pop('coverage_rationale')
        checked = self.api.validate_method_review(self.proposed, review, inventory)
        self.assertEqual(checked['global_relation'], 'unknown')
        self.assertEqual(checked['task_relation'], 'unknown')
        self.assertEqual(checked['route_action'], 'unknown')
        self.assertFalse(checked['coverage']['complete'])

    async def test_route_comparison_can_use_indexed_plans_without_reading_every_source(self):
        view = MemoryReader(self.store).open_snapshot(self.store.create_snapshot('task', 'selective-review')['id'])
        inventory = self.api.build_review_inventory(self.store, view, 'context', await self.knowledge.find_methods())
        inventory = self.api.expand_review_inventory(self.store, inventory, ['source:A-candidate'])
        rationale = self.coverage_rationale()
        rationale[1]['refs'] = ['plan:B-initial']
        rationale[1]['mechanism_difference'] = 'The pinned B Plan describes the same serial fixture; no independent merge is proposed there.'
        review = self.review(inventory, global_relation='new', structural_change='reduction',
            mechanism_difference='Independent partial accumulators followed by a merge.',
            code_refs=['source:A-candidate'], coverage_rationale=rationale)
        checked = self.api.validate_method_review(self.proposed, review, inventory)
        self.assertEqual(checked['global_relation'], 'new')
        self.assertTrue(checked['coverage']['complete'])
        self.assertFalse(inventory['references']['source:B-candidate']['read'])

    async def test_status_only_comparison_cannot_skip_recorded_route_mechanisms(self):
        inventory = await self.inventory()
        rationale = self.coverage_rationale()
        rationale[1]['refs'] = ['strategy:B']
        checked = self.api.validate_method_review(self.proposed,
            self.review(inventory, coverage_rationale=rationale), inventory)
        self.assertEqual(checked['task_relation'], 'unknown')
        self.assertFalse(checked['coverage']['complete'])

    async def test_route_comparison_cannot_cite_unread_feedback_or_another_routes_source(self):
        inventory = await self.inventory()
        for refs in (['feedback:B-run'], ['source:A-candidate']):
            with self.subTest(refs=refs):
                rationale = self.coverage_rationale()
                rationale[1]['refs'] = refs
                with self.assertRaises(KnowledgeError):
                    self.api.validate_method_review(self.proposed,
                        self.review(inventory, coverage_rationale=rationale), inventory)

    async def test_empty_source_inventory_cannot_claim_newness(self):
        with tempfile.TemporaryDirectory() as root:
            store = ExperimentStore(Path(root) / 'empty.sqlite3', Path(root) / 'artifacts')
            for record in (Task('task', 'Empty task', 'v1'), Workload('w', 'task', {}),
                           EvaluationContext('context', 'task', 'w', {'device': 'synthetic CPU'}, {'timer': 'synthetic'}, 'synthetic'),
                           Strategy('empty', 'task', 'Not implemented')):
                store.append(record, record.id)
            view = MemoryReader(store).open_snapshot(store.create_snapshot('task', 'empty-snapshot')['id'])
            inventory = self.api.build_review_inventory(store, view, 'context', await self.knowledge.find_methods())
            review = self.review(inventory, global_relation='new', structural_change='reduction',
                mechanism_difference='A potentially new partial reduction, but there is no implementation input.',
                strategy_id='empty', code_refs=[], plan_refs=[], reviewed_strategy_ids=['empty'],
                coverage_rationale=[{'strategy_id': 'empty', 'mechanism_difference': 'No implementation is recorded.',
                                     'refs': ['strategy:empty']}])
            checked = self.api.validate_method_review(self.proposed, review, inventory)
            self.assertEqual(checked['global_relation'], 'unknown')
            self.assertEqual(checked['task_relation'], 'unknown')
            self.assertFalse(checked['coverage']['complete'])

    async def test_fork_cannot_reuse_an_admitted_route_without_plans(self):
        self.store.append(Decision('old-admission', 'task', 'note', 'paused', 'Prior protected structure',
            {'type': 'exploration_admission.v1', 'strategy_id': 'paused',
             'initial_exploration_rounds': 5, 'policy_revision': 'test-v1',
             'state': 'deferred', 'admission_queued': True,
             'mechanism_difference': 'Prior partial reduction'}), 'old-admission')
        inventory = await self.inventory()
        review = self.review(inventory, global_relation='new', structural_change='reduction',
            mechanism_difference='Independent groups with a different merge tree.',
            route_action='fork', strategy_id='paused',
            lineage={'strategy_id': 'A', 'parent_candidate_id': 'A-candidate'},
            admission_comparisons=[{'admission_id': 'old-admission', 'relation': 'different',
                'mechanism_difference': 'A different merge tree.', 'refs': ['old-admission']}])
        with self.assertRaises(KnowledgeError):
            self.api.validate_method_review(self.proposed, review, inventory)

    async def test_admission_comparison_requires_read_references(self):
        from rsi_kernel_agent.contracts import record_to_dict
        from rsi_kernel_agent.workflow.exploration import admit_strategy

        inventory = await self.inventory('before-admission')
        old_review = self.api.validate_method_review(self.proposed,
            self.review(inventory, global_relation='new', structural_change='reduction',
                mechanism_difference='Prior independent partial-reduction structure.',
                route_action='fork', strategy_id='paused',
                lineage={'strategy_id': 'A', 'parent_candidate_id': 'A-candidate'}), inventory)
        old_review_ref = self.store.artifacts.put(canonical_json(old_review).encode())
        admission = admit_strategy(self.store, 'paused', initial_exploration_rounds=5,
            policy_revision='test-v1', reason='Prior protected structure',
            mechanism_difference=old_review['mechanism_difference'], method_refs=[],
            parent_ids=['A-parent'], review_ref=record_to_dict(old_review_ref))
        admission_id = admission['id']
        view = MemoryReader(self.store).open_snapshot(self.store.create_snapshot('task', 'admission-read')['id'])
        inventory = self.api.build_review_inventory(self.store, view, 'context', await self.knowledge.find_methods())
        inventory = self.api.expand_review_inventory(self.store, inventory, ['source:A-candidate'])
        rationale = self.coverage_rationale()
        rationale[2]['refs'].append(admission_id)
        review = self.review(inventory, global_relation='new', structural_change='reduction',
            mechanism_difference='Independent groups with a different merge tree.', code_refs=['source:A-candidate'],
            route_action='fork', strategy_id='new-D', coverage_rationale=rationale,
            lineage={'strategy_id': 'A', 'parent_candidate_id': 'A-candidate'},
            admission_comparisons=[{'admission_id': admission_id, 'relation': 'different',
                'mechanism_difference': 'A different merge tree.', 'refs': [admission_id, 'source:B-candidate']}])
        with self.assertRaises(KnowledgeError):
            self.api.validate_method_review(self.proposed, review, inventory)

    async def test_index_covers_paused_routes_and_is_not_a_merged_history(self):
        inventory = await self.inventory()
        self.assertEqual(set(inventory['strategies']), {'A', 'B', 'paused'})
        self.assertTrue(inventory['strategies']['paused']['paused'])
        self.assertEqual(inventory['coverage']['strategy_count'], 3)
        self.assertTrue(inventory['coverage']['index_complete'])
        self.assertNotIn('histories', inventory)
        self.assertNotIn('records', inventory)
        self.assertEqual(inventory['context_kind'], 'cross_route_method_review')
        self.assertIn('def matmul', inventory['references']['source:A-candidate']['content'])

    async def test_same_mechanism_new_source_reuses_id_and_preserves_original_method(self):
        inventory = await self.inventory()
        result = await self.api.classify_method(self.knowledge, self.proposed, self.review(inventory), inventory=inventory)
        method = method_from_dict(result['method'])
        self.assertEqual(method.method_id, self.old.method_id)
        self.assertEqual(method.method_name, self.old.method_name)
        self.assertEqual(len(method.source_refs), 2)
        self.assertFalse(result['classification']['new_method'])
        self.assertTrue(result['classification']['new_source'])
        self.assertTrue(result['classification']['new_task_application'])
        self.assertEqual(await self.knowledge.get_method(self.old.method_id), method)
        self.assertNotIn(method_note_id(self.proposed.method_id), self.notes.notes)

    async def test_parameter_change_is_a_plan_on_existing_method_not_a_fork(self):
        inventory = await self.inventory()
        review = self.review(inventory, structural_change='parameter', task_relation='tried',
                             mechanism_difference='Keep overlap dependencies unchanged; adjust buffer count only.')
        result = await self.api.classify_method(self.knowledge, self.proposed, review, inventory=inventory)
        self.assertEqual(result['method']['method_id'], self.old.method_id)
        self.assertFalse(result['classification']['new_method'])
        review['route_action'] = 'fork'
        with self.assertRaises(KnowledgeError):
            self.api.validate_method_review(self.proposed, review, inventory)

    async def test_substantively_new_mechanism_is_registered_without_performance_claim(self):
        inventory = await self.inventory()
        proposed = replace(self.proposed, method_id='method-partial-reduction', mechanism='partial reduction and final merge')
        review = self.review(inventory, global_relation='new', structural_change='reduction',
            mechanism_difference='Existing matmul retains one serial accumulator; proposed independent partial accumulators feed a final merge.',
            route_action='fork', strategy_id='new-D',
            lineage={'strategy_id': 'A', 'parent_candidate_id': 'A-candidate'})
        result = await self.api.classify_method(self.knowledge, proposed, review, inventory=inventory)
        self.assertTrue(result['classification']['new_method'])
        self.assertEqual(result['method']['method_id'], 'method-partial-reduction')
        self.assertEqual(result['classification']['correctness'], 'unknown')
        self.assertEqual(result['classification']['performance'], 'unknown')
        self.assertEqual(result['review']['lineage']['parent_candidate_id'], 'A-candidate')

    async def test_actual_code_already_implements_mechanism_without_forced_new_route(self):
        inventory = await self.inventory()
        review = self.review(inventory, task_relation='already_implemented', route_action='none', strategy_id=None,
            mechanism_difference='Pinned matmul source already has an inner reduction accumulator; this source is not a new implementation.')
        result = await self.api.classify_method(self.knowledge, self.proposed, review, inventory=inventory)
        self.assertFalse(result['classification']['new_task_application'])
        self.assertEqual(result['classification']['route_action'], 'none')
        self.assertFalse(result['classification']['new_strategy'])

    async def test_unknown_is_saved_as_unverified_and_can_never_assert_novelty(self):
        inventory = await self.inventory()
        review = self.review(inventory, global_relation='unknown', task_relation='unknown',
                             route_action='unknown', strategy_id=None, structural_change='unknown',
                             mechanism_difference='Need to read the source implementation before resolving equivalence.')
        result = await self.api.classify_method(self.knowledge, self.proposed, review, inventory=inventory)
        self.assertIsNone(result['classification']['new_method'])
        self.assertEqual(result['method']['validation_status'], 'unverified')
        self.assertIsNone(result['classification']['new_task_application'])
        self.assertEqual(await self.knowledge.get_method(self.proposed.method_id), method_from_dict(result['method']))

    async def test_partial_global_index_downgrades_novelty_instead_of_claiming_absence(self):
        methods = await self.knowledge.find_methods()
        methods.update(complete=False, unavailable=[{'id': 'RSI/Methods/missing.md', 'reason': 'Source not readable'}])
        view = MemoryReader(self.store).open_snapshot(self.store.create_snapshot('task', 'partial')['id'])
        inventory = self.api.build_review_inventory(self.store, view, 'context', methods)
        inventory = self.api.expand_review_inventory(self.store, inventory, ['source:A-candidate'])
        review = self.review(inventory, global_relation='new', structural_change='reduction',
                             mechanism_difference='Separate partial accumulators replace the serial recurrence.',
                             code_refs=['source:A-candidate'])
        result = await self.api.classify_method(self.knowledge, self.proposed, review, inventory=inventory)
        self.assertEqual(result['classification']['global_relation'], 'unknown')
        self.assertTrue(result['classification']['limitations'])
        self.assertEqual(result['method']['validation_status'], 'unverified')

    async def test_method_links_are_many_to_many_and_block_false_new_to_task(self):
        saved = self.notes.notes[method_note_id(self.old.method_id)]
        writer = MemoryWriter(self.store)
        for sid in 'AB':
            writer.bind_plan(sid + '-initial', 'baseline', 'binding:' + sid)
            writer.link_method(self.old, saved['id'], saved['revision'], sid + '-initial', 'baseline',
                               'Synthetic source adoption', 'link:' + sid)
        inventory = await self.inventory()
        self.assertEqual(inventory['method_strategies'][self.old.method_id], ['A', 'B'])
        self.assertEqual(set(inventory['strategies']), {'A', 'B', 'paused'})
        with self.assertRaises(KnowledgeError):
            self.api.validate_method_review(self.proposed, self.review(inventory), inventory)
        retry = self.review(inventory, task_relation='tried', route_action='retry', strategy_id='A')
        result = await self.api.classify_method(self.knowledge, self.proposed, retry, inventory=inventory)
        self.assertEqual(result['classification']['route_action'], 'retry')
        self.assertFalse(result['classification']['new_strategy'])

    async def test_foreign_or_unread_code_refs_and_stale_inventory_are_rejected(self):
        inventory = await self.inventory()
        for changes in ({'code_refs': ['source:foreign']}, {'inventory_digest': '0' * 64},
                        {'lineage': {'strategy_id': 'B', 'parent_candidate_id': 'A-candidate'},
                         'route_action': 'fork', 'structural_change': 'reduction'}):
            with self.subTest(changes=changes), self.assertRaises(KnowledgeError):
                self.api.validate_method_review(self.proposed, self.review(inventory, **changes), inventory)
        unexpanded = self.api.build_review_inventory(self.store,
            MemoryReader(self.store).open_snapshot(self.store.create_snapshot('task', 'unexpanded')['id']),
            'context', await self.knowledge.find_methods())
        with self.assertRaises(KnowledgeError):
            self.api.validate_method_review(self.proposed, self.review(unexpanded), unexpanded)

    async def test_fork_is_bound_to_a_new_stable_strategy_identity(self):
        inventory = await self.inventory()
        review = self.review(inventory, global_relation='new', structural_change='reduction',
            mechanism_difference='Independent partial accumulators followed by merge replace the serial accumulator.',
            route_action='fork', strategy_id='new-D',
            lineage={'strategy_id': 'A', 'parent_candidate_id': 'A-candidate'})
        try:
            validated = self.api.validate_method_review(self.proposed, review, inventory)
        except KnowledgeError as error:
            self.fail('A genuinely new stable strategy ID must be accepted for a fork: ' + str(error))
        self.assertEqual(validated['strategy_id'], 'new-D')
        self.assertTrue(validated['coverage']['complete'])
        review['strategy_id'] = 'A'
        with self.assertRaises(KnowledgeError):
            self.api.validate_method_review(self.proposed, review, inventory)
        review['strategy_id'] = None
        with self.assertRaises(KnowledgeError):
            self.api.validate_method_review(self.proposed, review, inventory)

    async def test_source_read_incompleteness_cannot_establish_novelty_or_admission(self):
        inventory = await self.inventory()
        capture = {'channel': 'external', 'url': 'https://research.example.org/partial',
                   'title': 'Partial source', 'content': '## Partial\nIndependent partial sums.\n',
                   'upstream_revision': None, 'locator': 'lines:1:2',
                   'coverage': {'scope': 'document', 'expected_chars': 5000, 'complete': False}}
        capture['content_sha256'] = sha256(capture['content'].encode()).hexdigest()
        document = await self.api.archive_source(self.knowledge, capture)
        proposed = extract_method(document, 'Partial', mechanism='partial reductions')
        review = self.review(inventory, global_relation='new', structural_change='reduction',
            mechanism_difference='Independent partial accumulators followed by merge replace the serial accumulator.',
            route_action='fork', strategy_id='new-D',
            lineage={'strategy_id': 'A', 'parent_candidate_id': 'A-candidate'})
        result = await self.api.classify_method(self.knowledge, proposed, review, inventory=inventory)
        self.assertEqual(result['review']['global_relation'], 'unknown')
        self.assertEqual(result['review']['task_relation'], 'unknown')
        self.assertEqual(result['review']['route_action'], 'unknown')
        self.assertFalse(result['review']['coverage']['complete'])
        self.assertEqual(result['method']['validation_status'], 'unverified')

    async def test_unread_or_unavailable_proposed_source_downgrades_newness(self):
        inventory = await self.inventory()
        del self.notes.notes['Research/new-source.md']
        review = self.review(inventory, global_relation='new', structural_change='reduction',
            mechanism_difference='Independent partial accumulators followed by merge replace the serial accumulator.')
        result = await self.api.classify_method(self.knowledge, self.proposed, review, inventory=inventory)
        self.assertEqual(result['classification']['global_relation'], 'unknown')
        self.assertEqual(result['classification']['task_relation'], 'unknown')
        self.assertFalse(result['review']['coverage']['complete'])

    async def test_external_reader_truncation_is_retained_in_source_coverage(self):
        async def external(tool, args):
            if tool == 'search':
                return {'status': 'available', 'results': [
                    {'url': 'https://research.example.org/partial', 'title': 'Partial'}]}
            return {'status': 'available', 'url': args['url'], 'title': 'Partial',
                    'content': 'Partial body', 'truncated': True}
        result = await self.api.search_sources(self.knowledge, ['missing-query'], external=external)
        self.assertFalse(result['documents'][0]['coverage']['complete'])
        self.assertTrue(result['unknowns'])

    async def test_prior_admissions_need_explicit_mechanism_comparison_before_fork(self):
        self.store.append(Decision('old-admission', 'task', 'note', 'paused', 'Prior protected structure',
            {'type': 'exploration_admission.v1', 'strategy_id': 'paused',
             'initial_exploration_rounds': 5, 'policy_revision': 'test-v1',
             'state': 'deferred', 'admission_queued': True,
             'mechanism_difference': 'A prior partial-reduction structure'}), 'old-admission')
        inventory = await self.inventory()
        self.assertIn('old-admission', inventory.get('admissions', {}))
        review = self.review(inventory, global_relation='new', structural_change='reduction',
            mechanism_difference='Use a different reduction tree and independently laid out partial state.',
            route_action='fork', strategy_id='new-D',
            lineage={'strategy_id': 'A', 'parent_candidate_id': 'A-candidate'})
        without_comparison = self.api.validate_method_review(self.proposed, review, inventory)
        self.assertEqual(without_comparison['route_action'], 'unknown')
        review['admission_comparisons'] = [{'admission_id': 'old-admission', 'relation': 'different',
            'mechanism_difference': 'The old structure keeps one partial state; the new tree merges independent groups.',
            'refs': ['old-admission', 'source:A-candidate']}]
        review['coverage_rationale'][2]['refs'].append('old-admission')
        checked = self.api.validate_method_review(self.proposed, review, inventory)
        self.assertEqual(checked['route_action'], 'fork')
        self.assertTrue(checked['coverage']['complete'])
        for relation in ('same', 'unknown'):
            review['admission_comparisons'][0]['relation'] = relation
            checked = self.api.validate_method_review(self.proposed, review, inventory)
            self.assertEqual(checked['route_action'], 'unknown')
        review['admission_comparisons'][0]['admission_id'] = 'invented'
        with self.assertRaises(KnowledgeError):
            self.api.validate_method_review(self.proposed, review, inventory)

    async def test_false_complete_source_cannot_establish_novelty(self):
        body = '## Partial\nA short excerpt, not the full source.\n'
        metadata = {'schema_version': 1, 'content_sha256': sha256(body.encode()).hexdigest(),
                    'coverage': {'complete': True, 'read_chars': len(body), 'expected_chars': len(body) + 100}}
        self.notes.put('RSI/Sources/false-complete.md', '# Source\n```rsi-source-v1\n' +
                       json.dumps(metadata) + '\n```\n\n' + body)
        proposed = extract_method(await self.knowledge.fetch_document('RSI/Sources/false-complete.md'),
                                  'Partial', mechanism='reduction')
        inventory = await self.inventory()
        review = self.review(inventory, global_relation='new', structural_change='reduction',
                             mechanism_difference='Independent partial states and an explicit combine phase.')
        result = await self.api.classify_method(self.knowledge, proposed, review, inventory=inventory)
        self.assertEqual(result['classification']['global_relation'], 'unknown')
        self.assertFalse(result['review']['coverage']['complete'])

    async def test_unknown_source_with_pretty_printed_coverage_is_not_novelty_evidence(self):
        self.notes.put('RSI/Sources/partial.md', '# Archived source\n```rsi-source-v1\n' +
            json.dumps({'schema_version': 1, 'coverage': {'complete': False}}, indent=2) +
            '\n```\n\n## Unknown\nPartial evidence only.\n')
        proposed = extract_method(await self.knowledge.fetch_document('RSI/Sources/partial.md'),
                                  'Unknown', mechanism='reduction')
        inventory = await self.inventory()
        review = self.review(inventory, global_relation='new', structural_change='reduction',
                             mechanism_difference='New grouping of independent partial accumulators.')
        result = await self.api.classify_method(self.knowledge, proposed, review, inventory=inventory)
        self.assertEqual(result['classification']['global_relation'], 'unknown')
        self.assertFalse(result['review']['coverage']['complete'])

    async def test_filtered_method_index_cannot_establish_global_novelty(self):
        methods = await self.knowledge.find_methods(keyword='not-present')
        view = MemoryReader(self.store).open_snapshot(self.store.create_snapshot('task', 'filtered')['id'])
        inventory = self.api.build_review_inventory(self.store, view, 'context', methods)
        inventory = self.api.expand_review_inventory(self.store, inventory, ['source:A-candidate'])
        review = self.review(inventory, closest_method_id=None, reviewed_method_ids=[],
            global_relation='new', structural_change='reduction', code_refs=['source:A-candidate'],
            mechanism_difference='Use independent partial reductions instead of serial accumulation.')
        result = await self.api.classify_method(self.knowledge, self.proposed, review, inventory=inventory)
        self.assertEqual(result['classification']['global_relation'], 'unknown')
        self.assertFalse(result['review']['coverage']['complete'])

    async def test_oversized_local_read_has_explicit_incomplete_coverage(self):
        self.notes.put('Research/oversized.md', '# Oversized unique marker\n' + 'x' * 250000)
        result = await self.api.search_sources(self.knowledge, ['Oversized unique marker'])
        self.assertEqual(result['documents'], [])
        self.assertEqual(result['availability']['knowledge'], 'partial')
        self.assertTrue(any('coverage is incomplete' in text for text in result['unknowns']))

    async def test_unresolved_global_equivalence_cannot_create_a_protected_fork(self):
        inventory = await self.inventory()
        review = self.review(inventory, global_relation='unknown', structural_change='reduction',
            mechanism_difference='Potentially distinct grouping but nearest method equivalence is unresolved.',
            route_action='fork', strategy_id='new-D',
            lineage={'strategy_id': 'A', 'parent_candidate_id': 'A-candidate'})
        result = await self.api.classify_method(self.knowledge, self.proposed, review, inventory=inventory)
        self.assertEqual(result['review']['route_action'], 'unknown')
        self.assertEqual(result['review']['task_relation'], 'unknown')
        self.assertFalse(result['review']['coverage']['complete'])

    async def test_unavailable_indexed_source_cannot_establish_global_newness(self):
        del self.notes.notes['Research/Pipelining.md']
        inventory = await self.inventory()
        review = self.review(inventory, global_relation='new', structural_change='reduction',
                             mechanism_difference='Use independent partial reductions instead of serial accumulation.')
        result = await self.api.classify_method(self.knowledge, self.proposed, review, inventory=inventory)
        self.assertEqual(result['classification']['global_relation'], 'unknown')
        self.assertTrue(result['classification']['limitations'])

    async def test_partial_archived_source_stays_incomplete_after_method_indexing(self):
        capture = {'channel': 'external', 'url': 'https://research.example.org/old-partial',
                   'title': 'Old partial', 'content': '## Incomplete mechanism\nPartial state description.\n',
                   'upstream_revision': None, 'locator': 'excerpt',
                   'coverage': {'scope': 'document', 'expected_chars': 5000, 'complete': False}}
        capture['content_sha256'] = sha256(capture['content'].encode()).hexdigest()
        document = await self.api.archive_source(self.knowledge, capture)
        old_partial = replace(extract_method(document, 'Incomplete mechanism'), validation_status='unverified')
        await self.knowledge.save_method(old_partial)
        inventory = await self.inventory()
        review = self.review(inventory, global_relation='new', structural_change='reduction',
                             reviewed_method_ids=list(inventory['methods']),
                             mechanism_difference='Independent partial accumulators with a different merge.')
        result = await self.api.classify_method(self.knowledge, self.proposed, review, inventory=inventory)
        self.assertEqual(result['classification']['global_relation'], 'unknown')
        self.assertFalse(result['review']['coverage']['complete'])
        self.assertFalse(inventory['method_sources'][old_partial.method_id][0]['coverage_complete'])

    async def test_live_method_revision_change_preserves_human_changes(self):
        inventory = await self.inventory()
        note_id = method_note_id(self.old.method_id)
        self.notes.put(note_id, self.notes.notes[note_id]['content'] + '\nHuman update after freeze.\n')
        with self.assertRaises(RevisionConflict):
            await self.api.classify_method(self.knowledge, self.proposed, self.review(inventory), inventory=inventory)
        self.assertIn('Human update after freeze.', self.notes.notes[note_id]['content'])


if __name__ == '__main__':
    unittest.main()
