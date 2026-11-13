"""Local protocol tests; no network or TPU claims."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from rsi_kernel_agent.workflow.colab_bridge import ColabBridge, BridgeUnavailable, extract_json_result


class BridgeTests(unittest.TestCase):
    def bridge(self):
        return ColabBridge(sys.executable, 'dedicated-test', 'runtime-test', timeout=2)

    def test_calls_installed_bridge_with_bound_runtime_and_no_shell(self):
        completed = subprocess.CompletedProcess([], 0, json.dumps({'status': 'ok', 'result': []}), '')
        with patch('subprocess.run', return_value=completed) as run:
            self.assertEqual(self.bridge().execute('print(1)'), [])
        args, kwargs = run.call_args
        self.assertEqual(args[0][0], sys.executable)
        self.assertFalse(kwargs.get('shell', False))
        request = json.loads(kwargs['input'])
        self.assertEqual(request['expected_runtime_id'], 'runtime-test')
        self.assertEqual(request['session'], 'dedicated-test')
        self.assertEqual(request['action'], 'execute')

    def test_local_timeout_is_unknown_and_does_not_retry(self):
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('bridge', 2, output='secret')) as run:
            with self.assertRaisesRegex(BridgeUnavailable, 'unknown') as error:
                self.bridge().execute('pass')
        self.assertNotIn('secret', str(error.exception))
        self.assertEqual(run.call_count, 1)

    def test_invalid_or_failed_bridge_response_never_exposes_raw_logs(self):
        for code, output in [(1, 'token-secret'), (0, 'not-json-secret'),
                              (0, json.dumps({'status': 'error', 'error_type': 'RuntimeChanged'}))]:
            with self.subTest(code=code, output=output), patch('subprocess.run', return_value=
                    subprocess.CompletedProcess([], code, output, 'stderr-secret')):
                with self.assertRaises(BridgeUnavailable) as error:
                    self.bridge().execute('pass')
                self.assertNotIn('secret', str(error.exception))

    def test_error_output_cannot_be_mistaken_for_success(self):
        outputs = [{'output_type': 'stream', 'text': 'RSI_RESULT:{"ok":true}\n'},
                   {'output_type': 'error', 'ename': 'RuntimeError', 'evalue': 'private'}]
        with self.assertRaises(BridgeUnavailable):
            extract_json_result(outputs)

    def test_exactly_one_complete_marked_json_is_required(self):
        good = {'output_type': 'stream', 'text': 'noise\nRSI_RESULT:{"ok":true}\n'}
        self.assertEqual(extract_json_result([good]), {'ok': True})
        for bad in [[], [good, good], [{'output_type': 'stream', 'text': 'RSI_RESULT:{'}]]:
            with self.assertRaises(BridgeUnavailable):
                extract_json_result(bad)

    def test_transfer_paths_are_json_arguments(self):
        completed = subprocess.CompletedProcess([], 0, '{"status":"ok","result":null}', '')
        with patch('subprocess.run', return_value=completed) as run:
            self.bridge().put(Path('a quote " file'), 'rsi/upload.json')
        payload = json.loads(run.call_args.kwargs['input'])
        self.assertEqual(payload['remote_path'], 'rsi/upload.json')
        self.assertIn('a quote " file', payload['local_path'])


if __name__ == '__main__':
    unittest.main()
