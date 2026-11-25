"""Local protocol tests; no network or TPU claims."""
import json
import io
import logging
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from rsi_kernel_agent.workflow.colab_bridge import ColabBridge, BridgeUnavailable, extract_json_result
from rsi_kernel_agent.workflow import colab_bridge_call


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

    def test_bridge_process_ignores_python_environment_injection(self):
        import os
        completed = subprocess.CompletedProcess([], 0, '{"status":"ok","result":[]}', '')
        with patch.dict(os.environ, {'PYTHONPATH': 'untrusted-path', 'PYTHONHOME': 'untrusted-home'}), \
             patch('subprocess.run', return_value=completed) as run:
            self.bridge().execute('print(1)')
        args, kwargs = run.call_args
        self.assertIn('-I', args[0])
        self.assertNotIn('PYTHONPATH', kwargs['env'])
        self.assertNotIn('PYTHONHOME', kwargs['env'])

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


class BridgeKernelBindingTests(unittest.TestCase):
    def modules(self, *, saved_id='pinned-kernel', observed_id='pinned-kernel', client_name='KernelClient'):
        events = []
        sdk = ModuleType('jupyter_kernel_client')
        session = SimpleNamespace(endpoint='runtime-test', url='https://private.invalid', token='secret',
                                  kernel_id=saved_id, session_id='session-test')
        class Client:
            def __init__(self):
                self.id = observed_id
            def start(self):
                if self.id is None:
                    events.append('POST kernel')
                    self.id = 'new-kernel'
                events.append('connect ' + self.id)
        setattr(sdk, client_name, Client)
        class Runtime:
            def __init__(self, url, token, **kwargs):
                self.kwargs = kwargs
            def execute_code(self, code, **kwargs):
                client = getattr(sdk, client_name)()
                client.start()
                if not self.kwargs['kernel_id']:
                    self.kwargs['on_kernel_started'](client.id)
                events.append('execute')
                return []
            def stop(self, shutdown_kernel=False):
                events.append(('stop', shutdown_kernel))
        class Contents:
            def __init__(self, session):
                pass
            def upload(self, local, remote):
                events.append('upload')
            def download(self, remote, local):
                Path(local).write_bytes(b'verified transfer fixture')
                events.append('download')
        modules = {'jupyter_kernel_client': sdk,
                   'colab_cli.common': SimpleNamespace(state=SimpleNamespace(
                       get_session=lambda name: session,
                       store=SimpleNamespace(add=lambda value: events.append('save kernel ' + value.kernel_id)))),
                   'colab_cli.runtime': SimpleNamespace(ColabRuntime=Runtime),
                   'colab_cli.contents': SimpleNamespace(ContentsClient=Contents)}
        return modules, sdk, Client, events

    def request(self, action='execute', **extra):
        return {'action': action, 'session': 'dedicated-test', 'expected_runtime_id': 'runtime-test',
                'timeout': 60, 'code': 'pass', **extra}

    def test_missing_or_changed_bound_kernel_fails_before_creation_or_execution(self):
        for client_name in ('ColabKernelClient', 'KernelClient', 'JupyterKernelClient'):
            for observed_id in (None, 'other-kernel'):
                with self.subTest(client=client_name, observed=observed_id):
                    modules, sdk, original, events = self.modules(observed_id=observed_id, client_name=client_name)
                    with patch.dict(sys.modules, modules):
                        with self.assertRaises(RuntimeError):
                            colab_bridge_call.call(self.request())
                    self.assertEqual(events, [('stop', False)])
                    self.assertIs(getattr(sdk, client_name), original)

    def test_existing_kernel_connects_without_creation_and_restores_sdk(self):
        modules, sdk, original, events = self.modules()
        with patch.dict(sys.modules, modules):
            self.assertEqual(colab_bridge_call.call(self.request()), [])
        self.assertEqual(events, ['connect pinned-kernel', 'execute', ('stop', False)])
        self.assertIs(sdk.KernelClient, original)

    def test_new_session_can_create_and_save_its_initial_kernel(self):
        modules, _, _, events = self.modules(saved_id=None, observed_id=None)
        with patch.dict(sys.modules, modules):
            self.assertEqual(colab_bridge_call.call(self.request()), [])
        self.assertEqual(events, ['POST kernel', 'connect new-kernel', 'save kernel new-kernel',
                                  'execute', ('stop', False)])

    def test_file_transfers_do_not_require_a_live_kernel(self):
        modules, _, _, events = self.modules(observed_id=None)
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, modules):
            target = Path(directory) / 'download'
            colab_bridge_call.call(self.request('get', remote_path='safe/path', local_path=str(target)))
            self.assertEqual(target.read_bytes(), b'verified transfer fixture')
            colab_bridge_call.call(self.request('put', remote_path='safe/path', local_path=str(target)))
        self.assertEqual(events, ['download', 'upload'])

    def test_missing_bound_kernel_error_is_sanitized_by_child_protocol(self):
        modules, _, _, events = self.modules(observed_id=None)
        output = io.StringIO()
        previous_logging_disable = logging.root.manager.disable
        try:
            with patch.dict(sys.modules, modules), patch('sys.stdin', io.StringIO(json.dumps(self.request()))), \
                    patch('sys.stdout', output):
                status = colab_bridge_call.main()
        finally:
            logging.disable(previous_logging_disable)
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(output.getvalue()), {'status': 'error', 'error_type': 'PinnedKernelUnavailable'})
        self.assertNotIn('secret', output.getvalue())
        self.assertEqual(events, [('stop', False)])


if __name__ == '__main__':
    unittest.main()
