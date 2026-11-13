"""Knowledge retrieval and provenance; all content is synthetic."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rsi_kernel_agent.knowledge import (
    KnowledgeClient, KnowledgeError, RevisionConflict, SourceDocument,
    extract_method, method_to_dict, method_from_dict, method_note_id,
)


class Notes:
    def __init__(self):
        self.notes = {}
        self.writes = 0

    def put(self, note_id, content):
        category, _, title = note_id.rpartition('/')
        self.notes[note_id] = dict(id=note_id, title=title[:-3], category=category,
                                  revision=sha256(content.encode()).hexdigest(), content=content)

    async def call(self, tool, args):
        if tool == 'search':
            items = [dict(x) for x in self.notes.values() if args['query'].casefold() in (x['id'] + x['content']).casefold()]
            start, end = args['offset'], min(args['offset'] + args['limit'], len(items))
            return {'results': items[start:end], 'total': len(items), 'next_offset': end if end < len(items) else None}
        if tool == 'fetch':
            if args['id'] not in self.notes:
                raise KnowledgeError('Document unavailable')
            note = dict(self.notes[args['id']])
            content = note.pop('content')
            start, end = args['offset'], min(args['offset'] + args['limit'], len(content))
            return {**note, 'content': content[start:end], 'offset': start, 'total_chars': len(content), 'next_offset': end if end < len(content) else None}
        self.writes += 1
        if tool == 'create_note':
            note_id = args['category'] + '/' + args['title'] + '.md'
            if note_id in self.notes:
                raise RevisionConflict('HTTP 409 duplicate')
        else:
            note_id = args['id']
            if args['revision'] != self.notes[note_id]['revision']:
                raise RevisionConflict('HTTP 409 revision conflict')
        self.put(note_id, args['content'])
        return {key: value for key, value in self.notes[note_id].items() if key != 'content'}


class KnowledgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.notes = Notes()
        self.notes.put('Research/pipeline.md', '# Synthetic research\n\n## Pipelining\nOverlap independent transfers.\n### Constraint\nNeeds independent buffers.\n## Other\nUnrelated.\n')
        self.client = KnowledgeClient(self.notes.call)

    async def method(self):
        doc = await self.client.fetch_document('Research/pipeline.md', page_size=9)
        return extract_method(doc, 'Pipelining', optimization_category='data movement',
                              hardware=('synthetic TPU',), workloads=('decode',), mechanism='overlap')

    async def test_complete_fetch_and_search_preserve_source_revision(self):
        found = await self.client.search('transfers', page_size=1)
        self.assertEqual(found['total'], 1)
        self.assertTrue(found['complete'])
        self.assertFalse(found['snapshot_consistent'])
        note = self.notes.notes['Research/pipeline.md']
        doc = await self.client.fetch_document(note['id'], expected_revision=note['revision'], page_size=7)
        self.assertEqual(doc.content, note['content'])
        self.assertEqual(doc.revision, note['revision'])
        self.assertEqual(self.notes.writes, 0)

    async def test_changing_document_revision_is_never_assembled_as_complete(self):
        async def changing(tool, args):
            if tool == 'fetch' and args['offset']:
                self.notes.put(args['id'], self.notes.notes[args['id']]['content'] + 'edited')
            return await self.notes.call(tool, args)
        with self.assertRaises(RevisionConflict):
            await KnowledgeClient(changing).fetch_document('Research/pipeline.md', page_size=10)
        with self.assertRaises(RevisionConflict):
            await self.client.fetch_document('Research/pipeline.md', expected_revision='0' * 64)

    async def test_invalid_pagination_and_search_mutation_fail_explicitly(self):
        self.notes.put('Research/second.md', 'second')
        async def bad_search(tool, args):
            result = await self.notes.call(tool, args)
            if tool == 'search' and args['offset']:
                result['results'][0]['id'] = 'Research/pipeline.md'
            return result
        with self.assertRaises(KnowledgeError):
            await KnowledgeClient(bad_search).search(page_size=1)
        async def bad_fetch(tool, args):
            result = await self.notes.call(tool, args)
            result['next_offset'] = 0
            return result
        with self.assertRaises(KnowledgeError):
            await KnowledgeClient(bad_fetch).fetch_document('Research/pipeline.md', page_size=10)

    async def test_extraction_quotes_source_and_does_not_invent_local_validation(self):
        method = await self.method()
        self.assertEqual(method.validation_status, 'source_supported')
        self.assertEqual(method.source_revision, {'Research/pipeline.md': self.notes.notes['Research/pipeline.md']['revision']})
        self.assertIn('Needs independent buffers.', method.source_refs[0].excerpt)
        self.assertNotIn('Unrelated.', method.description)
        self.assertEqual(method.local_experiment_refs, ())
        self.assertEqual(method_from_dict(method_to_dict(method)), method)
        self.assertEqual((await self.method()).method_id, method.method_id)
        with self.assertRaises(KnowledgeError):
            method_to_dict(replace(method, validation_status='locally_verified'))

    async def test_heading_extraction_ignores_code_and_rejects_ambiguous_heading(self):
        doc = SourceDocument('x.md', 'x', '', '0' * 64, '# Doc\n```python\n## Example\n```\n## Real\nText\n')
        with self.assertRaises(KnowledgeError):
            extract_method(doc, 'Example')
        with self.assertRaises(KnowledgeError):
            extract_method(replace(doc, content='## Same\none\n## Same\ntwo\n'), 'Same')

    async def test_methods_persist_without_duplicates_and_retrieval_never_writes(self):
        method = await self.method()
        first = await self.client.save_method(method)
        second = await self.client.save_method(method)
        self.assertEqual(first['id'], method_note_id(method.method_id))
        self.assertEqual(first['revision'], second['revision'])
        self.assertEqual(self.notes.writes, 1)
        self.assertEqual(await self.client.get_method(method.method_id), method)
        methods = await self.client.find_methods(keyword='transfers', category='data movement',
            hardware='TPU', mechanism='overlap', applicability='decode')
        self.assertEqual(methods['methods'], [method])
        self.assertTrue(methods['complete'])
        self.assertEqual(self.notes.writes, 1)
        self.assertEqual((await self.client.find_methods(hardware='GPU'))['methods'], [])

    async def test_method_cas_preserves_human_text_and_conflicts(self):
        method = await self.method()
        saved = await self.client.save_method(method)
        note_id = saved['id']
        self.notes.put(note_id, self.notes.notes[note_id]['content'] + '\nHuman context survives.\n')
        current = self.notes.notes[note_id]['revision']
        updated = replace(method, known_risks=('synthetic risk',))
        with self.assertRaises(RevisionConflict):
            await self.client.save_method(updated, expected_revision=saved['revision'])
        await self.client.save_method(updated, expected_revision=current)
        self.assertIn('Human context survives.', self.notes.notes[note_id]['content'])
        self.assertEqual(await self.client.get_method(method.method_id), updated)
        with self.assertRaises(RevisionConflict):
            await self.client.save_method(method)

    async def test_human_broken_method_json_is_not_overwritten(self):
        method = await self.method()
        saved = await self.client.save_method(method)
        self.notes.put(saved['id'], '# Human replaced structured block\nKeep me')
        revision = self.notes.notes[saved['id']]['revision']
        with self.assertRaises(KnowledgeError):
            await self.client.save_method(method, expected_revision=revision)
        self.assertIn('Keep me', self.notes.notes[saved['id']]['content'])

    async def test_method_schema_version_rejects_boolean_and_float(self):
        method = await self.method()
        saved = await self.client.save_method(method)
        content = self.notes.notes[saved['id']]['content']
        for version in ('true', '1.0'):
            with self.subTest(version=version):
                edited = content.replace('"schema_version":1', '"schema_version":' + version)
                self.notes.put(saved['id'], edited)
                with self.assertRaises(KnowledgeError):
                    await self.client.get_method(method.method_id)
                self.assertEqual(self.notes.notes[saved['id']]['content'], edited)

    async def test_missing_and_changed_sources_keep_captured_provenance(self):
        method = await self.method()
        await self.client.save_method(method)
        self.notes.put('Research/pipeline.md', '# New source revision')
        status = await self.client.source_status(method)
        self.assertEqual(status[0]['availability'], 'stale')
        del self.notes.notes['Research/pipeline.md']
        status = await self.client.source_status(method)
        self.assertEqual(status[0]['availability'], 'unavailable')
        self.assertEqual(await self.client.get_method(method.method_id), method)

    async def test_discovery_fetches_full_documents_without_saving_methods(self):
        discovered = await self.client.discover(('transfers', 'buffers'), page_size=1)
        self.assertEqual(len(discovered['documents']), 1)
        self.assertIn('Pipelining', discovered['sections']['Research/pipeline.md'])
        self.assertEqual(self.notes.writes, 0)

    async def test_stable_method_id_is_safe_for_windows_note_path(self):
        method = replace(await self.method(), method_id='method:colon')
        saved = await self.client.save_method(method)
        self.assertNotIn(':', saved['id'])
        self.assertEqual(await self.client.get_method('method:colon'), method)

    async def test_transient_source_read_failure_is_explicitly_unavailable(self):
        method = await self.method()
        async def disconnected(tool, arguments):
            raise ConnectionError('synthetic disconnect')
        status = await KnowledgeClient(disconnected).source_status(method)
        self.assertEqual(status[0]['availability'], 'unavailable')
        self.assertIn('ConnectionError', status[0]['reason'])


if __name__ == '__main__':
    unittest.main()
