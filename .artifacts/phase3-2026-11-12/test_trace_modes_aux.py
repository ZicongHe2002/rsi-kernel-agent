"""CPU-only diagnostic guard/receipt tests; never imports JAX or contacts Colab."""
import ast
from hashlib import sha256
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests'))
from rsi_kernel_agent.workflow import colab_worker as worker
import test_phase3_worker as worker_fixture


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('trace_modes_aux_probe', HERE / 'trace_modes_remote.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.directory = self.root / 'diagnostic'
        self.directory.mkdir()
        self.guard = self.root / 'operations' / 'guard'
        self.spool = type('Spool', (), {'root': self.root, 'coordinator': self.root / 'coordinator'})()
        (self.root / 'operations').mkdir()
        self.request = {'request_digest': 'a' * 64, 'source_digest': 'b' * 64,
                        'remote_root': str(self.root), 'runtime_id': 'fixture', 'task_id': 'task'}
        worker._publish(self.directory / 'request.json', worker._json(self.request))
        self.binding = patch.object(self.module, 'bind', return_value=(worker, self.spool, self.directory, self.guard))
        self.binding.start()
        self.addCleanup(self.binding.stop)

    def test_persisted_dispatch_without_live_process_is_unknown_never_relaunched(self):
        worker._publish(self.directory / 'dispatch-intent.json', worker._json({'dispatched': True}))
        with patch.object(self.module.subprocess, 'Popen', side_effect=AssertionError('must not relaunch')):
            self.assertEqual(self.module.submit(self.request)['status'], 'unknown')

    def test_completed_receipt_can_finish_guard_cleanup_without_probe(self):
        worker._publish(self.directory / 'dispatch-intent.json', worker._json({'dispatched': True}))
        worker._publish(self.guard / 'intent.json', worker._json({
            'type': 'trace_mode_diagnostic_guard.v1', 'request_digest': self.request['request_digest']}))
        body = {'type': 'trace_mode_diagnostic_receipt.v1', 'request_digest': self.request['request_digest'],
                'source_digest': self.request['source_digest'], 'artifacts': []}
        worker._publish(self.directory / 'diagnostic-receipt.json', worker._json({
            **body, 'receipt_digest': worker._sha(worker._json(body))}))
        with patch.object(self.module, 'probe_modes', side_effect=AssertionError('must not probe')):
            result = self.module.poll(self.request)
        self.assertEqual(result['status'], 'completed')
        self.assertFalse((self.guard / 'intent.json').exists())

    def test_unknown_formal_worker_operation_blocks_before_jax(self):
        path = self.root / 'operations' / 'unknown'
        worker._publish(path / 'intent.json', worker._json({'unknown': True}))
        self.spool._completed = lambda unused: (_ for _ in ()).throw(ValueError('unknown prior operation'))
        self.spool._artifact = lambda path, allowed: {'path': str(path), 'ref': {
            'digest': sha256(path.read_bytes()).hexdigest(), 'size': path.stat().st_size}}
        with patch.object(self.module, 'probe_modes', side_effect=AssertionError('must not probe')):
            self.module.execute(self.request)
        receipt = worker._read(self.directory / 'diagnostic-receipt.json')
        self.assertEqual(receipt['status'], 'blocked_before_execution')
        self.assertFalse((self.directory / 'execution-intent.json').exists())

    def test_probe_holds_runtime_lock_and_guard_until_durable_receipt(self):
        self.spool._artifact = lambda path, allowed: {'path': str(path), 'ref': {
            'digest': sha256(path.read_bytes()).hexdigest(), 'size': path.stat().st_size}}
        observed = []
        def fake_probe(unused_worker, unused_request, unused_output):
            observed.append((self.guard / 'intent.json').exists())
            with worker._lock(self.spool.coordinator / 'runtime.lock') as held:
                self.assertFalse(held)
            return []
        with patch.object(worker, 'observe_device_owners', return_value={'status': 'clear_visible_processes'}):
            with patch.object(self.module, 'probe_modes', side_effect=fake_probe):
                self.module.execute(self.request)
        self.assertEqual(observed, [True])
        self.assertTrue((self.directory / 'diagnostic-receipt.json').exists())
        self.assertFalse((self.guard / 'intent.json').exists())

    def test_sources_parse_without_import_time_hardware_actions(self):
        for name in ('trace_modes_remote.py', 'trace_modes_diagnostic.py'):
            tree = ast.parse((HERE / name).read_text('utf-8'), filename=name)
            # All function invocations which might operate runtime are under
            # functions or the explicit __main__ guard.
            for node in tree.body:
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
                    self.assertNotIn(node.value.func.id, ('submit', 'poll', 'execute', 'prepare', 'collect', 'probe_modes'))


class RealWorkerGuardTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('trace_modes_actual_worker_probe', HERE / 'trace_modes_remote.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.spool = worker.WorkerSpool(self.root / 'spool', 'cpu-diagnostic-runtime', 'cpu-nonce')
        self.spool.coordinator = self.root / 'coordinator'  # Keep all CPU fixture state in temp.
        self.spool.bootstrap()
        self.request = {'request_digest': 'd' * 64, 'source_digest': 'e' * 64,
                        'remote_root': str(self.spool.root), 'runtime_id': 'cpu-diagnostic-runtime', 'task_id': 'task'}
        self.directory = self.spool.root / 'diagnostics' / self.request['request_digest']
        self.directory.mkdir(parents=True)
        self.guard = self.spool._operation('trace-mode-diagnostic:' + self.request['request_digest'])
        worker._publish(self.directory / 'request.json', worker._json(self.request))
        worker._publish(self.directory / 'dispatch-intent.json', worker._json({'dispatched': True}))
        worker._publish(self.directory / 'execution-intent.json', worker._json({'started': True}))
        worker._publish(self.guard / 'intent.json', worker._json({
            'type': 'trace_mode_diagnostic_guard.v1', 'request_digest': self.request['request_digest']}))
        binding = patch.object(self.module, 'bind', return_value=(worker, self.spool, self.directory, self.guard))
        binding.start()
        self.addCleanup(binding.stop)

    def receipt(self):
        body = {'type': 'trace_mode_diagnostic_receipt.v1', 'request_digest': self.request['request_digest'],
                'source_digest': self.request['source_digest'], 'artifacts': []}
        worker._publish(self.directory / 'diagnostic-receipt.json', worker._json({
            **body, 'receipt_digest': worker._sha(worker._json(body))}))

    def test_unknown_diagnostic_never_restarts_and_real_worker_is_blocked(self):
        with patch.object(self.module.subprocess, 'Popen', side_effect=AssertionError('must not relaunch')):
            with patch.object(self.module, 'probe_modes', side_effect=AssertionError('must not probe')):
                for _ in range(2):
                    self.assertEqual(self.module.submit(self.request)['status'], 'unknown')
                    self.assertEqual(self.module.poll(self.request)['status'], 'unknown')
                    self.module.execute(self.request)
        runner = self.root / 'runner.py'
        runner.write_text(worker_fixture.RUNNER, encoding='utf-8')
        counter = self.root / 'hardware-call-count.txt'
        fixture = SimpleNamespace(runtime='cpu-diagnostic-runtime', runner=runner, counter=counter)
        request, payload = worker_fixture.WorkerTests.make(fixture, name='formal-after-unknown-diagnostic')
        self.spool._validate(request, payload, runner)
        path = self.spool._operation(request['operation_id'])
        worker._publish(path / 'request.json', worker._json(request))
        worker._publish(path / 'payload.json', worker._json(payload))
        worker._publish(path / 'runner.py', runner.read_bytes())
        self.spool.execute(request['operation_id'])  # Real original worker, synchronous local CPU path.
        self.assertEqual(worker._read(path / 'blocked.json')['reason'], 'Unresolved previous operation')
        self.assertEqual(self.spool.lookup(request['operation_id'])['status'], 'unknown')
        self.assertFalse(counter.exists())
        self.assertTrue((self.guard / 'intent.json').exists())

    def test_receipt_poll_only_cleans_own_guard_and_never_reexecutes(self):
        other = self.spool._operation('another-task-diagnostic')
        worker._publish(other / 'intent.json', worker._json({'type': 'foreign_guard', 'request_digest': 'f' * 64}))
        before = (other / 'intent.json').read_bytes()
        self.receipt()
        with patch.object(self.module.subprocess, 'Popen', side_effect=AssertionError('must not launch')):
            with patch.object(self.module, 'probe_modes', side_effect=AssertionError('must not probe')):
                self.assertEqual(self.module.poll(self.request)['status'], 'completed')
                self.assertEqual(self.module.submit(self.request)['status'], 'completed')
        self.assertFalse((self.guard / 'intent.json').exists())
        self.assertEqual((other / 'intent.json').read_bytes(), before)
        self.assertTrue((self.directory / 'execution-intent.json').exists())

    def test_mismatched_guard_at_own_path_is_never_deleted(self):
        (self.guard / 'intent.json').unlink()
        worker._publish(self.guard / 'intent.json', worker._json({
            'type': 'trace_mode_diagnostic_guard.v1', 'request_digest': 'f' * 64}))
        before = (self.guard / 'intent.json').read_bytes()
        self.receipt()
        with self.assertRaisesRegex(ValueError, 'guard differs'):
            self.module.poll(self.request)
        self.assertEqual((self.guard / 'intent.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
