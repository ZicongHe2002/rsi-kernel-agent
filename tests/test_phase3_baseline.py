"""Baseline preparation tests; source preparation only, no TPU claim."""
from pathlib import Path
import importlib
import importlib.util
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rsi_kernel_agent.store import ExperimentStore


class BaselineTests(unittest.TestCase):
    def setUp(self):
        name = 'rsi_kernel_agent.workflow.mla_baseline'
        self.assertIsNotNone(importlib.util.find_spec(name), 'Baseline preparation has not been implemented')
        self.module = importlib.import_module(name)

    def sources(self, root):
        names = ('pallas_kernels/kernels/mla_decode.py', 'jax_ops/mla_decode.py',
                 'pallas_kernels/configs/mla_decode.yaml', 'pallas_kernels/configs/mla_decode_vcs.yaml',
                 'benchmarks/profiler.py', 'benchmarks/runner.py', 'benchmarks/metrics.py',
                 'tools/get_tpu_duration_info.py', 'tools/tpu_duration_runtime.py', 'tools/tpu_duration_profile.py')
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'# Source audit fixture, never executed\n')
        (root / '.env').write_bytes(b'NOT-A-REAL-SECRET\n')
        (root / 'pallas_kernels' / '.auth').write_bytes(b'NOT-A-REAL-SECRET\n')

    def test_matrix_contains_small_boundary_and_default_with_explicit_outputs(self):
        matrix = self.module.workload_matrix()
        self.assertEqual([case['id'] for case in matrix], ['decode-small', 'decode-boundary', 'decode-default'])
        for case in matrix:
            self.assertEqual([output['name'] for output in case['outputs']], ['attention', 'cache'])
            self.assertEqual([output['comparison'] for output in case['outputs']], ['allclose', 'exact'])

    def test_source_audit_rejects_wrong_root_and_records_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.sources(root)
            result = self.module.audit_source_tree(root)
            self.assertEqual(result['root'], str(root.resolve()))
            self.assertTrue(result['files'])
            self.assertEqual(result['entry']['path'], 'pallas_kernels/kernels/mla_decode.py')
            self.assertFalse(any(row['path'].endswith(('.auth', '.env')) for row in result['files']))
            with self.assertRaises(ValueError):
                self.module.audit_source_tree(root / 'other')

    def test_create_baseline_requires_observed_real_environment_and_separates_task(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.sources(root)
            store = ExperimentStore(root / 'db.sqlite', root / 'artifacts')
            with self.assertRaises(ValueError):
                self.module.create_baseline(store, None, source_root=root)
            environment = {'runtime_session_id': 'cpu-fixture-runtime', 'tpu_model': 'TPU v6 lite', 'device_count': 1,
                           'python': '3.11.15', 'jax': '0.8.1', 'jaxlib': '0.8.1', 'libtpu': '0.0.30',
                           'devices': [{'id': 0, 'platform': 'tpu', 'device_kind': 'TPU v6 lite'}], 'process_count': 1}
            created = self.module.create_baseline(store, environment, source_root=root)
            self.assertEqual(created['task_id'], 'mla-decode-v1')
            self.assertEqual(store.get(created['context_id']).provenance, 'real')
            self.assertIsNone(store.get(created['parent_id']).candidate_id)
            self.assertEqual(created['variant']['source_ref']['digest'], store.get(created['candidate_id']).identity.source_digest)
            self.assertIn('decode-default', [case['id'] for case in created['contract']['workload']['spec']['cases']])
            self.assertEqual(store.run_state(created['run_id'])['execution_status'], 'not_run')
            self.assertIsNone(store.best(created['task_id'], created['context_id']))
            self.assertEqual(self.module.create_baseline(store, environment, source_root=root), created)
            retry = self.module.create_baseline(store, environment, source_root=root, key='baseline:2')
            self.assertEqual(retry['candidate_id'], created['candidate_id'])
            self.assertNotEqual(retry['run_id'], created['run_id'])
            other = self.module.create_baseline(store, environment, source_root=root, benchmark_case_id='decode-default')
            self.assertNotEqual(other['context_id'], created['context_id'])


if __name__ == '__main__':
    unittest.main()
