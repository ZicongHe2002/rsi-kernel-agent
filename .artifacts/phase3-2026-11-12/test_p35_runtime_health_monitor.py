"""No-network tests of original operation observation; fake remote only has lookup."""
from contextlib import redirect_stdout
import importlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / 'src')]
OP = 'operation:' + 'a' * 64


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((HERE / 'p35_runtime_health_monitor.py').exists(), 'Monitor implementation missing')
        self.api = importlib.import_module('p35_runtime_health_monitor')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.config = self.directory / 'colab-config-runtime6.json'
        self.config.write_text(json.dumps({'runtime_id': 'runtime-original', 'nonce': 'fixture-nonce'}))
        self.calls = []
        owner = self
        class LookupOnly:
            runtime_id = 'runtime-original'
            def lookup(self, operation_id):
                owner.calls.append(operation_id)
                response = owner.responses.pop(0)
                if isinstance(response, BaseException):
                    raise response
                return response
        self.remote = LookupOnly()
        self.responses = []

    def run_monitor(self, **kwargs):
        output = io.StringIO()
        with patch.object(self.api, 'colab_remote', return_value=self.remote) as factory, \
             patch.object(self.api.time, 'sleep') as sleep, redirect_stdout(output):
            result = self.api.monitor('runtime6', OP, directory=self.directory, **kwargs)
        return result, [json.loads(line) for line in output.getvalue().splitlines()], factory, sleep

    def test_same_operation_status_only_and_no_submit_surface(self):
        self.responses = [{'status': 'running', 'secret': 'DO-NOT-LOG'},
                          {'status': 'completed', 'receipt': {'operation_id': OP, 'outputs': 'DO-NOT-LOG'}}]
        result, rows, factory, sleep = self.run_monitor(max_polls=2)
        self.assertEqual(self.calls, [OP, OP])
        self.assertEqual([row['status'] for row in rows], ['running', 'completed'])
        self.assertTrue(all(row['runtime_id'] == 'runtime-original' for row in rows))
        self.assertNotIn('DO-NOT-LOG', json.dumps(rows))
        self.assertEqual(result['polls'], 2)
        factory.assert_called_once_with(self.config)
        sleep.assert_called_once_with(60)

    def test_errors_and_unknown_only_record_and_continue(self):
        self.responses = [RuntimeError('https://private.invalid/?token=DO-NOT-LOG'),
                          {'status': 'unknown'}, {'status': 'definitely_not_started'}]
        _, rows, _, _ = self.run_monitor(max_polls=3)
        self.assertEqual([row['consecutive_unknown'] for row in rows], [1, 2, 0])
        self.assertEqual(rows[0]['error_type'], 'RuntimeError')
        self.assertEqual(rows[2]['status'], 'definitely_not_started')
        self.assertNotIn('DO-NOT-LOG', json.dumps(rows))
        self.assertEqual(self.calls, [OP] * 3)

    def test_invalid_parameters_reject_before_remote(self):
        with patch.object(self.api, 'colab_remote') as factory:
            for generation, operation, interval, maximum in [
                ('../runtime6', OP, 60, 1), ('runtime1', OP, 60, 1),
                ('runtime6', 'other', 60, 1), ('runtime6', OP, 59, 1),
                ('runtime6', OP, 61, 1), ('runtime6', OP, 60, -1),
                ('runtime6', OP, 60, True), ('runtime6', OP, True, 1)]:
                with self.subTest(generation=generation, interval=interval, maximum=maximum), self.assertRaises(ValueError):
                    self.api.monitor(generation, operation, interval=interval, max_polls=maximum, directory=self.directory)
            factory.assert_not_called()

    def test_default_is_one_poll_and_explicit_unlimited_remains_interruptible(self):
        self.responses = [{'status': 'running'}]
        result, _, _, sleep = self.run_monitor()
        self.assertEqual(result['polls'], 1)
        sleep.assert_not_called()
        self.responses = [{'status': 'unknown'}, KeyboardInterrupt()]
        with self.assertRaises(KeyboardInterrupt):
            self.run_monitor(max_polls=0)
        self.assertEqual(self.calls, [OP] * 3)

    def test_changed_configuration_stops_without_following_new_runtime(self):
        def lookup(operation_id):
            self.calls.append(operation_id)
            self.config.write_text(json.dumps({'runtime_id': 'replacement', 'nonce': 'changed'}))
            return {'status': 'running'}
        self.remote.lookup = lookup
        result, rows, _, _ = self.run_monitor(max_polls=3)
        self.assertEqual(self.calls, [OP])
        self.assertEqual(rows[-1]['error_type'], 'ConfigChanged')
        self.assertEqual(rows[-1]['runtime_id'], 'runtime-original')
        self.assertEqual(result['stop_reason'], 'config_changed')

    def test_malformed_or_mismatched_receipt_is_unknown(self):
        self.responses = [{'status': 'completed', 'receipt': {'operation_id': 'operation:' + 'b' * 64}},
                          {'status': 'TOKEN=DO-NOT-LOG'}, None]
        _, rows, _, _ = self.run_monitor(max_polls=3)
        self.assertEqual([row['status'] for row in rows], ['unknown'] * 3)
        self.assertEqual([row['error_type'] for row in rows], ['InvalidLookupReply'] * 3)
        self.assertNotIn('DO-NOT-LOG', json.dumps(rows))


if __name__ == '__main__':
    unittest.main()
