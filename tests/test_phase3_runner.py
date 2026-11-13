"""Local CPU validation for the fixed MLA runner; no JAX/TPU claim."""
from copy import deepcopy
from hashlib import sha256
import importlib
import importlib.util
import math
import os
from pathlib import Path
import sys
import sysconfig
import site
import tempfile
import types
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


class RunnerTests(unittest.TestCase):
    def setUp(self):
        name = 'rsi_kernel_agent.workflow.mla_runner'
        self.assertIsNotNone(importlib.util.find_spec(name), 'Fixed MLA runner is not implemented')
        self.runner = importlib.import_module(name)

    def frame(self, values, *, raw=None, shape=None, dtype='bfloat16'):
        return {'values': values, 'bytes': raw, 'shape': shape or [len(values)],
                'dtype': dtype, 'finite': all(math.isfinite(x) for x in values)}

    def test_metrics_and_original_tolerance_detect_wrong_attention(self):
        expected = self.frame([1., 4.])
        actual = self.frame([1., 2.])
        spec = {'name': 'attention', 'shape': [2], 'dtype': 'bfloat16', 'comparison': 'allclose'}
        result = self.runner.compare_frame(actual, expected, spec, atol=.01, rtol=.01)
        self.assertFalse(result['passed'])
        self.assertEqual(result['tolerance_violations'], 1)
        self.assertEqual(result['max_abs'], 2.)
        self.assertEqual(result['max_rel'], .5)
        self.assertAlmostEqual(result['rms'], math.sqrt(2))
        self.assertIsNone(result['metric_reason'])

    def test_wrong_shape_dtype_and_nonfinite_cannot_pass(self):
        good = self.frame([1., 4.])
        spec = {'name': 'attention', 'shape': [2], 'dtype': 'bfloat16', 'comparison': 'allclose'}
        for bad in (self.frame([1., 4.], shape=[1, 2]),
                    self.frame([1., 4.], dtype='float32'), self.frame([1., float('nan')])):
            with self.subTest(bad=bad):
                self.assertFalse(self.runner.compare_frame(bad, good, spec, atol=.01, rtol=.01)['passed'])

    def test_exact_opaque_cache_accepts_same_nan_bits_and_rejects_wrong_cache(self):
        spec = {'name': 'cache', 'shape': [2], 'dtype': 'bfloat16', 'comparison': 'exact'}
        expected = self.frame([float('nan'), 1], raw=b'\xc0\x7f\x80\x3f')
        same = self.runner.compare_frame(expected, expected, spec, atol=.01, rtol=.01)
        self.assertTrue(same['passed'])
        self.assertFalse(same['finite'])
        self.assertIsNone(same['max_abs'])
        self.assertTrue(same['metric_reason'])
        wrong = {**expected, 'bytes': b'\xc0\x7f\x00\x00'}
        self.assertFalse(self.runner.compare_frame(wrong, expected, spec, atol=.01, rtol=.01)['passed'])

    def test_statistics_keep_all_raw_samples_and_reject_invalid_measurements(self):
        result = self.runner.sample_statistics([1., 2., 3., 10.])
        self.assertEqual(result['median_ms'], 2.5)
        self.assertEqual(result['mean_ms'], 4.)
        self.assertEqual(result['p25_ms'], 1.75)
        self.assertEqual(result['p75_ms'], 4.75)
        self.assertEqual(result['count'], 4)
        for bad in ([], [0., 1.], [-1., 1.], [True, 1.], [float('nan'), 1.], [float('inf'), 1.]):
            with self.subTest(samples=bad):
                with self.assertRaises(ValueError):
                    self.runner.sample_statistics(bad)

    def test_source_identity_rejects_replaced_helper_and_old_module(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {'pallas_kernels/kernel.py': b'def entry(): return 1\n',
                     'pallas_kernels/helper.py': b'value = 2\n'}
            for name, data in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            bundle = {'schema_version': 1, 'entry': {'path': 'pallas_kernels/kernel.py', 'symbol': 'entry'},
                      'files': [{'path': name, 'artifact_ref': {'digest': sha256(data).hexdigest(), 'size': len(data)},
                                 'role': 'source'} for name, data in files.items()]}
            module = types.SimpleNamespace(__file__=str(root / 'pallas_kernels/kernel.py'))
            self.assertEqual(len(self.runner.verify_loaded_bundle(root, bundle, {'pallas_kernels.kernel': module})), 2)
            old = types.SimpleNamespace(__file__=str(root.parent / 'old-kernel.py'))
            with self.assertRaises(ValueError):
                self.runner.verify_loaded_bundle(root, bundle, {'pallas_kernels.kernel': old})
            (root / 'pallas_kernels/helper.py').write_bytes(b'value = 9\n')
            with self.assertRaises(ValueError):
                self.runner.verify_loaded_bundle(root, bundle, {'pallas_kernels.kernel': module})

    def test_source_bundle_path_escape_and_duplicate_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = b'x=1\n'
            (root / 'entry.py').write_bytes(data)
            row = {'path': 'entry.py', 'artifact_ref': {'digest': sha256(data).hexdigest(), 'size': len(data)}, 'role': 'source'}
            bundle = {'schema_version': 1, 'entry': {'path': 'entry.py', 'symbol': 'entry'}, 'files': [row, row]}
            with self.assertRaises(ValueError):
                self.runner.verify_loaded_bundle(root, bundle, {})
            bundle['files'] = [{**row, 'path': '../entry.py'}]
            with self.assertRaises(ValueError):
                self.runner.verify_loaded_bundle(root, bundle, {})

    def test_controlled_measurement_excludes_compile_and_synchronizes_inputs(self):
        pending = {'input': True}
        samples = [2., 4., 3.]

        def synchronize(tree):
            self.assertEqual(tree, ('input-array',))
            pending['input'] = False

        def compile_fn(inputs):
            self.assertFalse(pending['input'])
            self.assertEqual(inputs, ('input-array',))
            return lambda *args: ('attention', 'cache')

        def warmup(fn, *args):
            self.assertEqual(fn(*args), ('attention', 'cache'))
            return .3

        def measure(fn, *args, repeats):
            self.assertEqual(repeats, 3)
            self.assertEqual(fn(*args), ('attention', 'cache'))
            return list(samples)

        result = self.runner.measure_compiled(compile_fn, ('input-array',), synchronize=synchronize,
                                              warmup=warmup, measure=measure, warmup_count=2, measurements=3)
        self.assertEqual(result['samples_ms'], samples)
        self.assertEqual(result['latency_ms'], 3.)
        self.assertEqual(result['warmup_count'], 2)
        self.assertEqual(result['warmup_ms'], [.3, .3])
        self.assertGreaterEqual(result['compile_time_ms'], 0)

    def test_contiguous_matrix_requires_explicit_tuple_outputs(self):
        case = {'id': 'small', 'config_name': 'mla_decode',
                'overrides': {'shape': {'batch': 1, 'seq_len': 1024}},
                'outputs': [{'name': 'attention', 'shape': [1, 16, 512], 'dtype': 'bfloat16', 'comparison': 'allclose'},
                            {'name': 'cache', 'shape': [1, 1024, 640], 'dtype': 'bfloat16', 'comparison': 'exact'}]}
        spec = {'kernel': 'mla_decode', 'interface': 'contiguous', 'cases': [case]}
        self.assertEqual(self.runner.validate_workload(spec), spec)
        for mutate in ('missing-cache', 'paged', 'duplicate', 'bad-tolerance'):
            bad = deepcopy(spec)
            if mutate == 'missing-cache':
                bad['cases'][0]['outputs'].pop()
            elif mutate == 'paged':
                bad['interface'] = 'paged'
            elif mutate == 'duplicate':
                bad['cases'].append(deepcopy(case))
            else:
                bad['cases'][0]['overrides']['tolerance'] = {'atol': 99}
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                self.runner.validate_workload(bad)

    def test_candidate_configuration_cannot_change_workload_or_oracle(self):
        spec = {'cases': [{'id': 'small'}]}
        baseline = {'schema_version': 1, 'kernel': 'mla_decode', 'case_overrides': {}}
        self.assertTrue(hasattr(self.runner, 'validate_candidate_config'), 'Candidate config boundary is not implemented')
        self.assertEqual(self.runner.validate_candidate_config(baseline, spec), baseline)
        for field, value in (('shape', {'batch': 1}), ('tolerance', {'atol': 999}),
                             ('params', {'interface': 'paged'}), ('params', {'sm_scale': 0}),
                             ('tiling', {'block_size': 0})):
            bad = deepcopy(baseline)
            bad['case_overrides']['small'] = {field: value}
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.runner.validate_candidate_config(bad, spec)
        good = deepcopy(baseline)
        good['case_overrides']['small'] = {'tiling': {'block_size': 512}}
        self.assertEqual(self.runner.validate_candidate_config(good, spec), good)

    def test_untrusted_runner_inputs_fail_before_jax_import(self):
        self.assertTrue(hasattr(self.runner, 'run_mla_stage'), 'Remote MLA stage runner is not implemented')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = {'stage': 'correctness', 'job': {'contract': {'workload': {'spec': {'kernel': 'not-mla'}}}}}
            with self.assertRaises(ValueError):
                self.runner.run_mla_stage(request, root, root, root, source_bundle={}, oracle_bundle={},
                                          config_path=root / 'config.json', runtime_session_id='runtime', lease={})

    def test_frozen_tool_loader_rejects_changed_benchmark_before_import(self):
        self.assertTrue(hasattr(self.runner, 'load_trusted_tool'), 'Frozen benchmark loader is not implemented')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'benchmarks' / 'profiler.py'
            source.parent.mkdir()
            data = b'def measure(): return [1.0, 2.0]\n'
            source.write_bytes(data)
            trusted = {'benchmarks/profiler.py': sha256(data).hexdigest()}
            tool = self.runner.load_trusted_tool(root, 'benchmarks/profiler.py', trusted, 'phase3_test_bench')
            self.assertEqual(tool.measure(), [1., 2.])
            source.write_bytes(b'raise RuntimeError("untrusted code executed")\n')
            with self.assertRaises(ValueError):
                self.runner.load_trusted_tool(root, 'benchmarks/profiler.py', trusted, 'phase3_test_bad')

    def test_declared_entry_must_be_actual_loaded_source(self):
        self.assertTrue(hasattr(self.runner, 'verify_entry'), 'Loaded callable identity is not implemented')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / 'entry.py'
            file.write_text('def kernel():\n    return 1\n', encoding='utf-8')
            module = types.ModuleType('runner_identity_fixture')
            module.__file__ = str(file)
            exec(compile(file.read_text(), str(file), 'exec'), module.__dict__)
            entry = {'path': 'entry.py', 'symbol': 'kernel'}
            self.assertEqual(self.runner.verify_entry(root, entry, module), str(file))
            module.kernel = lambda: 1
            with self.assertRaises(ValueError):
                self.runner.verify_entry(root, entry, module)

    def test_aliased_top_level_helper_cannot_load_an_old_source_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'candidate'
            root.mkdir()
            files = {'entry.py': b'def entry(): return 1\n', 'new_helper.py': b'value=1\n'}
            rows = []
            for name, data in files.items():
                (root / name).write_bytes(data)
                rows.append({'path': name, 'artifact_ref': {'digest': sha256(data).hexdigest(), 'size': len(data)}, 'role': 'source'})
            bundle = {'schema_version': 1, 'entry': {'path': 'entry.py', 'symbol': 'entry'}, 'files': rows}
            old = Path(directory) / 'old' / 'new_helper.py'
            old.parent.mkdir()
            old.write_bytes(files['new_helper.py'])
            with self.assertRaises(ValueError):
                self.runner.verify_loaded_bundle(root, bundle, {'dynamic_alias': types.SimpleNamespace(__file__=str(old))})

    def test_runtime_package_init_is_not_confused_with_candidate_package_init(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'pallas_kernels' / '__init__.py'
            path.parent.mkdir()
            path.write_bytes(b'')
            bundle = {'entry': {'path': 'pallas_kernels/__init__.py', 'symbol': 'entry'},
                      'files': [{'path': 'pallas_kernels/__init__.py', 'artifact_ref': {'digest': sha256(b'').hexdigest(), 'size': 0}}]}
            installed = Path(sysconfig.get_paths()['purelib']) / 'numpy' / '__init__.py'
            self.assertEqual(len(self.runner.verify_loaded_bundle(root, bundle,
                {'numpy': types.SimpleNamespace(__file__=str(installed))})), 1)

    def test_system_dist_packages_remain_allowed_runtime_dependencies(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'candidate'
            root.mkdir()
            (root / 'entry.py').write_bytes(b'')
            bundle = {'entry': {'path': 'entry.py', 'symbol': 'entry'},
                      'files': [{'path': 'entry.py', 'artifact_ref': {'digest': sha256(b'').hexdigest(), 'size': 0}}]}
            system_packages = Path(directory) / 'system' / 'dist-packages'
            installed = system_packages / '_distutils_hack' / '__init__.py'
            with patch.object(site, 'getsitepackages', return_value=[str(system_packages)]):
                self.assertEqual(len(self.runner.verify_loaded_bundle(root, bundle,
                    {'_distutils_hack': types.SimpleNamespace(__file__=str(installed))})), 1)

    @unittest.skipUnless(os.name == 'posix' and Path('/etc/python%d.%d/sitecustomize.py' % sys.version_info[:2]).is_file(),
                         'System-owned Debian Python startup file is not present')
    def test_system_owned_startup_hook_is_allowed_without_allowing_other_etc_files(self):
        import sitecustomize
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'entry.py').write_bytes(b'')
            bundle = {'entry': {'path': 'entry.py', 'symbol': 'entry'},
                      'files': [{'path': 'entry.py', 'artifact_ref': {'digest': sha256(b'').hexdigest(), 'size': 0}}]}
            self.assertEqual(len(self.runner.verify_loaded_bundle(root, bundle, {'sitecustomize': sitecustomize})), 1)
            with self.assertRaises(ValueError):
                self.runner.verify_loaded_bundle(root, bundle,
                    {'sitecustomize': types.SimpleNamespace(__file__='/etc/old-helper.py')})


if __name__ == '__main__':
    unittest.main()
