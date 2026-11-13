"""Real MCP/HTTP Knowledge capture and offline manifest replay in a temporary site."""
import unittest

from phase2b_fixtures import TemporaryKnowledgeSite
from phase2c_fixtures import make_analysis_fixture
from rsi_kernel_agent.knowledge import extract_method
from rsi_kernel_agent.profiler.inputs import AnalysisRequest, capture_inputs, load_manifest


class Phase2CMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_mcp_capture_survives_source_revision_change_and_offline_replay(self):
        with TemporaryKnowledgeSite() as site:
            fixture = make_analysis_fixture(site.workspace / 'experiment')
            store, request = fixture['store'], fixture['request']
            history_before = store.history('task')
            async with site.connect() as knowledge:
                created = await knowledge.call_tool('create_note', {
                    'title': 'Pipeline assumptions', 'category': 'Research',
                    'content': '# Synthetic pipeline assumptions\n\n## Overlap\n'
                               'Independent transfers may overlap compute. Buffer lifetimes must not alias.\n',
                })
                document = await knowledge.fetch_document(created['id'], expected_revision=created['revision'], page_size=19)
                method = extract_method(document, 'Overlap', hardware=('synthetic CPU',), mechanism='overlap')
                saved = await knowledge.save_method(method)
                request['knowledge'] = [{'method_id': method.method_id, 'note_id': saved['id'], 'revision': saved['revision']}]
                pinned = await capture_inputs(store, knowledge, AnalysisRequest.from_dict(request))
                before = pinned.to_dict()
                captured = before['knowledge'][method.method_id]
                self.assertEqual(captured['sources'][0]['availability'], 'available')
                self.assertEqual(captured['sources'][0]['revision'], document.revision)
                self.assertEqual(captured['method']['description'], method.description)
                self.assertTrue(all(before['strategies'][sid]['history']['complete'] for sid in 'AB'))

                await knowledge.call_tool('update_note', {
                    'id': document.id, 'revision': document.revision,
                    'content': '# Revised source\n\n## Overlap\nRevised assumptions now require a distinct buffer.\n',
                })
                refreshed = await capture_inputs(store, knowledge, AnalysisRequest.from_dict(request))
                current = refreshed.to_dict()['knowledge'][method.method_id]
                self.assertEqual(current['sources'][0]['availability'], 'stale')
                self.assertNotEqual(current['sources'][0]['current_revision'], document.revision)
                self.assertEqual(current['method']['description'], method.description)
                self.assertIsNone(current['sources'][0]['artifact'])
                self.assertEqual(load_manifest(store, pinned.artifact_ref).to_dict(), before)

            # The stdio client is closed; replay has no Knowledge transport.
            replay = load_manifest(store, pinned.artifact_ref).to_dict()
            self.assertEqual(replay, before)
            self.assertEqual(store.history('task'), history_before)
            self.assertIsNone(store.best('task', 'context'))
            self.assertFalse((site.workspace / '.auth').exists())


if __name__ == '__main__':
    unittest.main()
