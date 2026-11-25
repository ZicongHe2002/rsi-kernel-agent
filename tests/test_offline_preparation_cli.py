import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_offline_preparation import route_answer
import test_design_cli
from rsi_kernel_agent.contracts import Decision
from rsi_kernel_agent.profiler.context import decode_context


class OfflinePreparationCLITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = test_design_cli.DesignCLITests()
        await self.fixture.asyncSetUp()
        for function, args, kwargs in self.fixture._cleanups:
            self.addCleanup(function, *args, **kwargs)
        self.fixture._cleanups.clear()

    async def test_cli_drives_preparation_without_publishing_executable_plans(self):
        f = self.fixture
        before = f.store.history('task')
        assessments = {sid: {'status': 'hypothesis' if sid in 'AB' else 'deferred',
            'reason': 'Offline research' if sid in 'AB' else 'No exact parent',
            'hypothesis': 'Change tile' if sid in 'AB' else None, 'refs': []} for sid in 'ABCDE'}
        prep = f.cli('preparation-begin', '--task', 'task', '--historical-context', 'context',
            '--preparation-key', 'offline-cli', '--assessments', f.write('assessments.json', assessments),
            '--parents', f.write('parents.json', {'A': 'A-parent', 'B': 'B-parent'}), '--website', str(f.website))
        snapshots = []
        for sid in 'AB':
            session = f.root / ('offline-' + sid)
            opened = f.cli('preparation-route', '--preparation', prep['id'], '--strategy', sid,
                           '--session', str(session))
            packet = json.loads(Path(opened['packet']).read_text(encoding='utf-8'))
            manifest = decode_context(packet['manifest'])
            self.assertEqual(set(manifest['strategies']), {sid})
            snapshots.append(manifest['route_context']['snapshot_id'])
            self.assertIn('offline', packet['instructions'].lower())
            total = sum(len(json.dumps(node, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
                        for node in packet['manifest']['nodes'].values())
            offset = 0
            while offset < total:
                response = {'tool_calls': [{'id': 'page-' + str(offset), 'name': 'context_page',
                                            'arguments': {'offset': offset, 'limit': 24000}}]}
                f.cli('respond', '--session', str(session), '--response', f.write(sid + '-page.json', response))
                packet = json.loads((session / 'packet.json').read_text(encoding='utf-8'))
                offset = packet['tool_results'][0]['result']['next_offset']
                if offset is None:
                    break
            answer = {'final': route_answer(sid, no_plan=True)}
            response = f.cli('respond', '--session', str(session), '--response', f.write(sid + '-final.json', answer))
            self.assertEqual(response['status'], 'draft_complete')
            self.assertNotIn('publication_id', response)
            closed = f.cli('preparation-finish', '--preparation', prep['id'], '--strategy', sid,
                           '--session', str(session), '--drafts', f.write(sid + '-drafts.json', {}))
            self.assertEqual(closed['details']['outcome']['status'], 'needs_research')
        self.assertEqual(snapshots[0], snapshots[1])
        done = f.cli('preparation-complete', '--preparation', prep['id'])
        self.assertFalse(done['details']['executable'])
        added = f.store.history('task')[len(before):]
        self.assertTrue(all(type(row) is Decision for row in added))
        self.assertFalse(any(row.details.get('type') in ('analysis_result.v1', 'design_complete.v1', 'workflow_state.v1')
                             for row in added))
        error = f.cli('design-complete', '--batch', prep['id'], success=False)
        self.assertIn('design batch', error)


if __name__ == '__main__':
    unittest.main()
