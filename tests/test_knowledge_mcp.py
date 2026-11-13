"""Actual stdio MCP and website revision writes against isolated synthetic notes."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rsi_kernel_agent.knowledge import KnowledgeError, RevisionConflict, extract_method, method_to_dict
from phase2b_fixtures import TemporaryKnowledgeSite


class KnowledgeMCPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.site = TemporaryKnowledgeSite().__enter__()
        self.addCleanup(self.site.__exit__, None, None, None)
        raw = ('# Synthetic source\r\n\r\n## Transfer overlap\r\n' + 'Use independent buffers.  中文\n' * 2000).encode('utf-8')
        (self.site.root / 'source.md').write_bytes(b'\xef\xbb\xbf' + raw)

    async def test_actual_stdio_full_source_method_write_and_conflicting_human_edit(self):
        async with self.site.connect() as client:
            search = await client.search('independent')
            self.assertEqual(search['total'], 1)
            document = await client.fetch_document('source.md', expected_revision=search['results'][0]['revision'], page_size=1000)
            self.assertEqual(document.content.count('Use independent'), 2000)
            method = extract_method(document, 'Transfer overlap', mechanism='overlap')
            saved = await client.save_method(method)
            self.assertEqual(await client.get_method(method.method_id), method)
            self.assertEqual((await client.save_method(method))['revision'], saved['revision'])
            human = self.site.server.store.read(saved['id'])
            self.site.server.store.save({**human, 'content': human['content'] + '\nHuman addition\n'})
            with self.assertRaises(RevisionConflict):
                await client.save_method(replace(method, known_risks=('buffer ownership',)), expected_revision=saved['revision'])
            current = await client.fetch_document(saved['id'])
            await client.save_method(replace(method, known_risks=('buffer ownership',)), expected_revision=current.revision)
            self.assertIn('Human addition', (await client.fetch_document(saved['id'])).content)
            with self.assertRaises(KnowledgeError):
                await client.fetch_document('../outside.md')

    async def test_actual_write_requires_existing_auth_and_never_changes_auth(self):
        async with self.site.connect(authorized=False) as client:
            document = await client.fetch_document('source.md')
            method = extract_method(document, 'Transfer overlap')
            with self.assertRaises(KnowledgeError):
                await client.save_method(method)
            self.assertFalse((self.site.workspace / '.auth').exists())
            self.assertEqual(len(list(self.site.root.rglob('*.md'))), 1)

    async def test_malformed_numeric_method_note_does_not_hide_valid_methods(self):
        async with self.site.connect() as client:
            method = extract_method(await client.fetch_document('source.md'), 'Transfer overlap')
            await client.save_method(method)
            bad = replace(method, method_id='malformed-method')
            saved = await client.save_method(bad)
            payload = {'schema_version': 1, 'method': method_to_dict(bad)}
            payload['method']['source_refs'][0]['excerpt'] = 'BAD_NUMBER'
            for token in ('NaN', '1e309'):
                with self.subTest(token=token):
                    content = '```rsi-method-v1\n' + json.dumps(payload).replace('"BAD_NUMBER"', token) + '\n```\n'
                    human = self.site.server.store.read(saved['id'])
                    self.site.server.store.save({**human, 'content': content})
                    result = await client.find_methods()
                    self.assertEqual(result['methods'], [method])
                    self.assertFalse(result['complete'])
                    self.assertEqual([item['id'] for item in result['unavailable']], [saved['id']])
                    self.assertTrue(result['unavailable'][0]['reason'])
                    self.assertEqual(self.site.server.store.read(saved['id'])['content'], content)


if __name__ == '__main__':
    unittest.main()
