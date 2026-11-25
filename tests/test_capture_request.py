from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from test_analyzer import manifest, proposal
from rsi_kernel_agent.profiler.analyzer import Analyzer


class CaptureRequestTests(unittest.TestCase):
    def test_capture_request_is_recorded_without_execution_or_new_evidence(self):
        analyzer = Analyzer(manifest())
        before = analyzer.manifest.copy()
        analyzer.advance({'tool_calls': [{'id': 'need-trace', 'name': 'capture_request', 'arguments': {
            'strategy_id': 'A', 'kind': 'profile', 'reason': 'Need a matched device trace',
            'refs': ['source:A'], 'phase': 'post_design_diagnostic'}}]})
        request = analyzer.packet()['tool_results'][0]
        self.assertNotIn('error', request)
        self.assertEqual(request['result']['status'], 'registered_not_executed')
        self.assertFalse(request['result']['evidence_available'])
        self.assertEqual(analyzer.manifest, before)
        final = analyzer.advance({'final': proposal()})
        self.assertEqual(final['tool_calls'][0]['result']['phase'], 'post_design_diagnostic')
        self.assertEqual(final['calculations'], {})

    def test_inline_tpu_capture_and_cross_route_inputs_are_rejected(self):
        for phase, refs in [('execute_now', ['source:A']), ('next_input_preparation', ['source:B'])]:
            with self.subTest(phase=phase):
                analyzer = Analyzer(manifest())
                analyzer.advance({'tool_calls': [{'id': 'bad', 'name': 'capture_request', 'arguments': {
                    'strategy_id': 'A', 'kind': 'profile', 'reason': 'Capture requested',
                    'refs': refs, 'phase': phase}}]})
                self.assertIn('error', analyzer.packet()['tool_results'][0])


if __name__ == '__main__':
    unittest.main()
