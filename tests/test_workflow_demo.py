"""Standalone demo uses the original MCP, production round/session/controller APIs."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


class WorkflowDemoTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_rounds_mixed_failures_repair_and_knowledge_are_one_loop(self):
        self.assertTrue((Path(__file__).resolve().parents[1] / 'src/rsi_kernel_agent/workflow/demo.py').exists(),
                        'Standalone production-API synthetic demo must exist')
        from rsi_kernel_agent.workflow.demo import run_demo
        with tempfile.TemporaryDirectory() as tmp:
            result = await run_demo(tmp)
            self.assertEqual(result['provenance'], 'synthetic')
            self.assertEqual(len(result['rounds']), 2)
            first, second = result['rounds']
            self.assertEqual(len(first['intake']['items']), 4)
            self.assertEqual([len(x['jobs']) for x in first['intake']['items']], [2, 1, 1, 2])
            self.assertEqual(len(second['intake']['items']), 4)
            self.assertEqual([c['strategy_id'] for c in second['cards']], list('ABCDE'))
            self.assertTrue(all(h['complete'] for h in second['histories'].values()))
            self.assertGreater(second['histories']['A']['total'], first['histories']['A']['total'])
            classifications = {row['classification'] for row in result['results']}
            self.assertTrue({'compile_failed', 'incorrect', 'incomplete', 'invalid_measurement',
                             'correct_faster'} <= classifications)
            self.assertEqual(len(result['repairs']), 1)
            self.assertEqual(result['best']['task']['latency_ms'], 7)
            self.assertEqual(result['best']['B']['latency_ms'], 9)
            self.assertTrue(result['frontier']['B'])
            self.assertTrue(all(j['state'] in ('completed', 'failed') for j in result['workflow']['jobs'].values()))
            self.assertFalse(any(Path(tmp).rglob('.auth')))


if __name__ == '__main__':
    unittest.main()
