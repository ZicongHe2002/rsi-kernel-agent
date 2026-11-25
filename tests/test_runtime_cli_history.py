"""Historical runtimes do not veto a valid backend; operations stay pinned."""
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from phase3_fixtures import make_real_fixture
from test_phase3_backend import RemoteFixture
from rsi_kernel_agent.contracts import RunEvent
from rsi_kernel_agent.workflow.__main__ import make_backend
from rsi_kernel_agent.workflow.execution import make_request


class RuntimeHistoryCLITests(unittest.TestCase):
    def test_history_allows_current_runtime_and_preserves_unknown_foreign_operation(self):
        with tempfile.TemporaryDirectory() as root:
            data = make_real_fixture(root)
            store, job = data['store'], data['job']
            context = store.get('context')
            newer = replace(context, id='context-new', environment={**context.environment, 'runtime_session_id': 'new-runtime'})
            store.append(newer, 'new-context')
            remote = RemoteFixture()
            remote.runtime_id = context.environment['runtime_session_id']
            args = SimpleNamespace(colab_config='mock-config')
            with patch('rsi_kernel_agent.workflow.__main__.colab_remote', return_value=remote):
                try:
                    backend = make_backend(store, args, 'task')
                except ValueError as error:
                    self.fail('Historical context blocked the matching runtime: ' + str(error))
            store.transition(RunEvent('start', job['run_id'], 'running', 'active', 'CPU fixture'), None, 'start')
            request = make_request(store, job, 'generate')
            remote.unavailable = True
            self.assertEqual(backend.submit(request)['status'], 'unknown')
            original_calls = remote.calls
            new_remote = RemoteFixture()
            new_remote.runtime_id = 'new-runtime'
            with patch('rsi_kernel_agent.workflow.__main__.colab_remote', return_value=new_remote):
                recovered = make_backend(store, args, 'task')
            with self.assertRaisesRegex(ValueError, 'runtime'):
                recovered.lookup(request['operation_id'])
            with self.assertRaisesRegex(ValueError, 'runtime'):
                recovered.submit(request)
            self.assertEqual(new_remote.calls, 0)
            self.assertEqual(remote.calls, original_calls)

    def test_unrelated_runtime_is_still_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as root:
            data = make_real_fixture(root)
            remote = RemoteFixture()
            remote.runtime_id = 'unrelated-runtime'
            with patch('rsi_kernel_agent.workflow.__main__.colab_remote', return_value=remote):
                with self.assertRaisesRegex(ValueError, 'Runtime'):
                    make_backend(data['store'], SimpleNamespace(colab_config='mock'), 'task')
