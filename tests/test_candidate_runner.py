from contextlib import ExitStack, nullcontext
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rsi_kernel_agent.workflow import mla_runner


class Array:
    def __init__(self, values):
        self.values = list(values)
        self.shape = (len(values),)
        self.dtype = 'bfloat16'
        self.deleted = False

    def tobytes(self, order='C'):
        return struct.pack('<' + 'd' * len(self.values), *self.values)

    def copy(self):
        return Array(self.values)

    def delete(self):
        self.deleted = True

    def astype(self, dtype):
        return self

    def ravel(self):
        return self

    def tolist(self):
        return list(self.values)


class CandidateRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'source'
        self.oracle = self.root / 'oracle'
        self.output = self.root / 'output'
        self.workspace.mkdir()
        self.oracle.mkdir()
        self.output.mkdir()
        self.source_bundle = self.bundle(self.workspace, 'build_mla_decode_contiguous')
        self.oracle_bundle = self.bundle(self.oracle, 'mla_decode_paged_reference')
        trusted = {}
        for relative in ('benchmarks/profiler.py', 'tools/tpu_duration_profile.py', 'tools/get_tpu_duration_info.py'):
            path = self.oracle / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = b'# Trusted local measurement fixture\n'
            path.write_bytes(raw)
            trusted[relative] = sha256(raw).hexdigest()
            self.oracle_bundle['files'].append({'path': relative, 'artifact_ref': {
                'digest': trusted[relative], 'size': len(raw)}, 'role': 'dependency'})
        config = {'schema_version': 1, 'kernel': 'mla_decode',
                  'case_overrides': {'small': {'tiling': {'block_size': 512}}}}
        self.config_path = self.root / 'config.json'
        self.config_path.write_text(mla_runner._json(config), encoding='utf-8')
        self.environment = {'runtime_session_id': 'fixture-v6e', 'device_count': 1, 'process_count': 1}
        self.device = type('Device', (), {'id': 0})()
        self.workload = {'kernel': 'mla_decode', 'interface': 'contiguous', 'cases': [
            {'id': name, 'config_name': 'mla_decode', 'overrides': {'tiling': {'block_size': 1024}},
             'outputs': [{'name': 'attention', 'shape': [1], 'dtype': 'bfloat16', 'comparison': 'allclose'},
                         {'name': 'cache', 'shape': [2], 'dtype': 'bfloat16', 'comparison': 'exact'}]}
            for name in ('small', 'boundary', 'default')]}
        protocol = {'execution_mode': 'candidate.v1', 'runner_digest': 'a' * 64,
                    'oracle_digest': self.oracle_bundle['files'][0]['artifact_ref']['digest'],
                    'oracle_bundle_ref': {'digest': 'b' * 64, 'size': 0},
                    'correctness': {'atol': .01, 'rtol': .01}, 'trusted_files': trusted,
                    'benchmark_case_id': 'small',
                    'benchmark': {'warmup': 2, 'measurements': 3},
                    'profile': {'trace_mode': 'default', 'parser_digest': 'c' * 64,
                                'profile_calls': 3, 'profile_warmup': 2}}
        job = {'run_id': 'run-1', 'candidate_id': 'candidate-1', 'context_id': 'context-1',
               'contract': {'workload': {'spec': self.workload},
                            'context': {'environment': self.environment, 'protocol': protocol}},
               'variant': {'source_bundle_ref': {'digest': 'd' * 64, 'size': 0}}}
        identity = {'source_digest': self.source_bundle['files'][0]['artifact_ref']['digest'],
                    'config_digest': sha256(self.config_path.read_bytes()).hexdigest(), 'build_digest': 'e' * 64}
        self.requests = []
        for stage in ('generate', 'compile', 'correctness', 'benchmark', 'profile'):
            request = {'schema_version': 1, 'backend_id': 'colab.v1', 'provenance': 'real',
                       'operation_id': 'operation:' + sha256(mla_runner._json(['run-1', stage]).encode()).hexdigest(),
                       'stage': stage, 'job': deepcopy(job), 'candidate_identity': deepcopy(identity),
                       'parent_identity': deepcopy(identity)}
            request['request_digest'] = sha256(mla_runner._json(request).encode()).hexdigest()
            self.requests.append(request)
        self.arrays = {}
        self.oracle_calls = []
        self.compiled = []
        self.placed = []
        self.executed = []
        self.configs = []
        self.capture_arguments = None
        self.mutate_case = None
        self.wrong_case = None
        self.compile_error = False
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.np = SimpleNamespace(asarray=lambda a: a, float64='float64',
                                  isfinite=lambda a: SimpleNamespace(all=lambda: True))
        self.jax = SimpleNamespace(default_device=lambda d: nullcontext(), process_count=lambda: 1,
            devices=lambda kind: [self.device], device_put=self.device_put,
            block_until_ready=lambda value: value, jit=self.jit)
        original_verify = mla_runner.verify_loaded_bundle
        self.stack.enter_context(patch.object(mla_runner, 'verify_loaded_bundle',
            side_effect=lambda root, bundle, modules=None: original_verify(root, bundle)))
        self.stack.enter_context(patch.object(mla_runner, '_run_oracle', side_effect=self.run_oracle))
        self.stack.enter_context(patch.object(mla_runner, '_load_arrays', side_effect=self.load_arrays))
        self.stack.enter_context(patch.object(mla_runner, '_save_arrays', side_effect=self.save_arrays))
        self.stack.enter_context(patch.object(mla_runner, 'load_trusted_tool', side_effect=self.load_tool))
        self.stack.enter_context(patch.dict(sys.modules, {'numpy': self.np}))

    def bundle(self, root, symbol):
        path = root / 'pallas_kernels' / 'kernels' / 'mla_decode.py'
        path.parent.mkdir(parents=True)
        raw = ('def ' + symbol + '():\n    return None\n').encode()
        path.write_bytes(raw)
        return {'schema_version': 1, 'entry': {'path': 'pallas_kernels/kernels/mla_decode.py', 'symbol': symbol},
                'files': [{'path': 'pallas_kernels/kernels/mla_decode.py',
                           'artifact_ref': {'digest': sha256(raw).hexdigest(), 'size': len(raw)}, 'role': 'source'}]}

    def initialize(self, run):
        run.jax, run.np, run.device = self.jax, self.np, self.device
        run.environment = deepcopy(self.environment)
        run.load_kernel_config = self.load_config
        run.get_kernel = lambda name, cfg: SimpleNamespace(case=cfg.case)
        run.kernel_module = ModuleType('candidate_fixture')
        path = self.workspace / self.source_bundle['entry']['path']
        run.kernel_module.__file__ = str(path)
        exec(compile(path.read_text(), str(path), 'exec'), run.kernel_module.__dict__)

    def load_config(self, name, overrides):
        self.configs.append(deepcopy(overrides))
        return SimpleNamespace(case=len(self.configs) - 1, shape={}, tiling=overrides['tiling'],
            params={'interface': 'contiguous'}, dtype='bfloat16', seed=0,
            get_tolerance=lambda dtype: {'atol': .01, 'rtol': .01})

    def run_oracle(self, case, root, bundle, digest, directory, tolerance):
        self.oracle_calls.append(case['id'])
        directory.mkdir(parents=True, exist_ok=True)
        inputs = (Array([1]), Array([2]), Array([3, 4]), Array([1]))
        self.save_arrays(directory / 'inputs.npz', inputs)
        self.save_arrays(directory / 'oracle_outputs.npz', (Array([5]), Array([3, 4])))
        metadata = {'input_arrays': [{'name': str(i)} for i in range(4)],
                    'oracle_identity': {'source_bundle_digest': digest, 'entry': bundle['entry'],
                                       'loaded_files': bundle['files']}}
        (directory / 'oracle_metadata.json').write_text(json.dumps(metadata))
        return metadata

    def load_arrays(self, path):
        return tuple(value.copy() for value in self.arrays[str(path)])

    def save_arrays(self, path, arrays):
        self.arrays[str(path)] = tuple(value.copy() for value in arrays)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b'fixture arrays: ' + b''.join(a.tobytes() for a in arrays))
        return [{'name': str(i)} for i in range(len(arrays))]

    def device_put(self, arrays, device=None):
        placed = tuple(a.copy() for a in arrays)
        self.placed.append(placed)
        return placed

    def jit(self, public):
        def compile_it():
            if self.compile_error:
                raise RuntimeError('CPU compiler fixture failure')
            index = len(self.compiled)
            fixture = self
            class Compiled:
                def runtime_executable(self):
                    return SimpleNamespace(local_devices=lambda: [fixture.device])
                def as_text(self):
                    return 'fixture HLO ' + str(index)
                def __call__(self, *inputs):
                    if any(a.deleted for a in inputs):
                        raise AssertionError('Deleted device inputs were reused')
                    fixture.executed.append((index, tuple(inputs[2].values)))
                    cache = inputs[2].copy()
                    if fixture.mutate_case == index:
                        inputs[2].values[0] = 99
                    return (Array([99 if fixture.wrong_case == index else 5]), cache)
            compiled = Compiled()
            self.compiled.append(compiled)
            return compiled
        return SimpleNamespace(lower=lambda *inputs: SimpleNamespace(compile=compile_it))

    def load_tool(self, root, relative, trusted, name):
        module = ModuleType(name)
        module.__file__ = str(root / relative)
        self.assertEqual(sha256(Path(module.__file__).read_bytes()).hexdigest(), trusted[relative])
        self.stack.enter_context(patch.dict(sys.modules, {name: module}))
        if relative == 'benchmarks/profiler.py':
            def warmup(compiled, *inputs):
                compiled(*inputs)
                return .1
            def measure(compiled, *inputs, repeats):
                for _ in range(repeats):
                    compiled(*inputs)
                return [1., 2., 3.]
            module.warmup, module.measure = warmup, measure
            return module
        if relative == 'tools/tpu_duration_profile.py':
            return module
        def capture(compiled, inputs, *, directory, tag, calls, warmup, device_ids):
            self.capture_arguments = {'compiled': compiled, 'inputs': inputs, 'tag': tag}
            compiled(*inputs)
            directory.mkdir(parents=True)
            (directory / 'fixture.xplane.pb').write_bytes(b'CPU protocol fixture; not a real XPlane')
            (directory / 'duration.json').write_text('{"errors":[]}')
            return {'errors': []}
        module._capture_entry = capture
        return module

    def make(self, requests=None):
        self.assertTrue(hasattr(mla_runner, 'CandidateRun'), 'CandidateRun must own reusable compiled state')
        run = mla_runner.CandidateRun(requests or self.requests, self.workspace, self.oracle, self.output,
            source_bundle=self.source_bundle, oracle_bundle=self.oracle_bundle,
            config_path=self.config_path, runtime_session_id=self.environment['runtime_session_id'])
        self.stack.enter_context(patch.object(run, '_initialize', side_effect=lambda: self.initialize(run)))
        return run

    def stage(self, run, name):
        request = next(r for r in self.requests if r['stage'] == name)
        return run.run_stage(request, self.output / name, lease={'operation_id': request['operation_id']})

    def test_three_cases_compile_and_generate_oracle_once_across_all_stages(self):
        run = self.make()
        results = {stage: self.stage(run, stage) for stage in ('compile', 'correctness', 'benchmark', 'profile')}
        self.assertEqual(self.oracle_calls, ['small', 'boundary', 'default'])
        self.assertEqual(len(self.compiled), 3)
        self.assertEqual(results['correctness']['correctness'], 'passed')
        self.assertEqual(results['benchmark']['samples_ms'], [1., 2., 3.])
        self.assertIs(self.capture_arguments['compiled'], self.compiled[0])
        self.assertEqual(self.configs[0]['tiling']['block_size'], 512)
        self.assertEqual(self.configs[1]['tiling']['block_size'], 1024)
        for result in results.values():
            self.assertEqual(result['identity']['process_scope'], 'candidate_run')
            self.assertEqual(result['identity']['process_run_id'], 'run-1')
            self.assertEqual(result['identity']['process_id'], os.getpid())
        for arrays in self.placed:
            self.assertTrue(all(a.deleted for a in arrays))

    def test_changed_request_or_wrong_stage_does_not_execute(self):
        run = self.make()
        with self.assertRaises(ValueError):
            self.stage(run, 'correctness')
        changed = deepcopy(self.requests[1])
        changed['job']['run_id'] = 'another-run'
        with self.assertRaises(ValueError):
            run.run_stage(changed, self.output / 'compile', lease={})
        self.assertEqual(self.compiled, [])
        self.stage(run, 'compile')
        with self.assertRaises(ValueError):
            self.stage(run, 'compile')
        self.assertEqual(len(self.compiled), 3)

    def test_constructor_rejects_cross_run_and_reordered_requests_before_import(self):
        for mutation in ('run', 'stage', 'digest', 'mode'):
            requests = deepcopy(self.requests)
            if mutation == 'run':
                requests[2]['job']['run_id'] = 'other'
            elif mutation == 'stage':
                requests[1], requests[2] = requests[2], requests[1]
            elif mutation == 'mode':
                for request in requests:
                    request['job']['contract']['context']['protocol'].pop('execution_mode')
            else:
                requests[1]['request_digest'] = 'f' * 64
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.make(requests)
        self.assertEqual(self.oracle_calls, [])

    def test_correctness_failure_closes_run_before_benchmark(self):
        run = self.make()
        self.stage(run, 'compile')
        self.wrong_case = 1
        result = self.stage(run, 'correctness')
        self.assertEqual(result['correctness'], 'failed')
        self.assertFalse(result['cases'][1]['passed'])
        with self.assertRaises(ValueError):
            self.stage(run, 'benchmark')
        self.assertEqual(len(self.executed), 3)

    def test_mutated_input_cache_is_archived_and_rejected(self):
        run = self.make()
        self.stage(run, 'compile')
        self.mutate_case = 0
        result = self.stage(run, 'correctness')
        self.assertEqual(result['correctness'], 'failed')
        self.assertFalse(result['cases'][0]['outputs'][1]['passed'])
        paths = [p for p in result['raw_artifact_paths'] if p.endswith('input_cache_after.npz')]
        self.assertEqual(len(paths), 3)
        self.assertEqual(self.load_arrays(paths[0])[0].values, [99, 4])

    def test_compile_exception_prevents_retry_and_releases_device_inputs(self):
        run = self.make()
        self.compile_error = True
        with self.assertRaises(RuntimeError):
            self.stage(run, 'compile')
        self.compile_error = False
        with self.assertRaises(ValueError):
            self.stage(run, 'compile')
        self.assertEqual(self.oracle_calls, ['small'])
        self.assertTrue(all(a.deleted for values in self.placed for a in values))

    def test_benchmark_and_profile_use_fresh_frozen_inputs_same_executable(self):
        run = self.make()
        self.stage(run, 'compile')
        self.stage(run, 'correctness')
        self.stage(run, 'benchmark')
        benchmark_inputs = self.placed[-1]
        self.stage(run, 'profile')
        self.assertIsNot(benchmark_inputs, self.capture_arguments['inputs'])
        self.assertEqual(self.executed[-1], (0, (3, 4)))
        self.assertIs(self.capture_arguments['compiled'], self.compiled[0])

    def test_only_verified_measurement_modules_may_be_outside_candidate_root(self):
        run = self.make()
        measurement = self.oracle / 'benchmarks' / 'profiler.py'
        measurement.parent.mkdir(exist_ok=True)
        measurement.write_text('def warmup(): return 0\n')
        trusted = {'benchmarks/profiler.py': sha256(measurement.read_bytes()).hexdigest()}
        module = ModuleType('trusted_measurement')
        module.__file__ = str(measurement)
        run.protocol['trusted_files'] = trusted
        run._measurement_modules = {'_rsi_benchmark': (module, 'benchmarks/profiler.py')}
        self.stack.close()
        verify = mla_runner.verify_loaded_bundle
        def verify_boundary(root, bundle, modules=None):
            return verify(root, bundle, {name: value for name, value in (modules or {}).items()
                                        if name.startswith('_rsi')})
        with patch.object(mla_runner, 'verify_loaded_bundle', side_effect=verify_boundary):
            with patch.dict(sys.modules, {'_rsi_benchmark': module}):
                self.assertEqual(run._identity()['process_scope'], 'candidate_run')
                measurement.write_text('def warmup(): return 99\n')
                with self.assertRaises(ValueError):
                    run._identity()

    def test_real_initialization_rejects_non_v6e_and_multiple_devices(self):
        import builtins
        run = self.make()
        self.stack.close()
        initialize = mla_runner.CandidateRun._initialize
        source = self.workspace / self.source_bundle['entry']['path']
        module = ModuleType('candidate_hardware_fixture')
        module.__file__ = str(source)
        exec(compile(source.read_text(), str(source), 'exec'), module.__dict__)
        original_import = builtins.__import__
        for kind, count, allowed in [('TPU v5 lite', 1, False), ('TPU v6 lite', 2, False),
                                     ('TPU v6 lite', 1, True), ('TPU v6e', 1, True), ('gpu', 1, False)]:
            devices = [SimpleNamespace(device_kind=kind, id=i) for i in range(count)]
            fake_jax = SimpleNamespace(process_count=lambda: 1, devices=lambda selected: devices)
            def fixture_import(name, *args, **kwargs):
                if name == 'jax': return fake_jax
                if name == 'numpy': return self.np
                if name == 'pallas_kernels.kernels': return SimpleNamespace(get_kernel=self.load_config)
                if name == 'pallas_kernels.kernels.config_loader': return SimpleNamespace(load_kernel_config=self.load_config)
                return original_import(name, *args, **kwargs)
            with self.subTest(devices=count, kind=kind):
                with patch.object(builtins, '__import__', side_effect=fixture_import), \
                     patch.object(mla_runner, 'collect_environment', return_value=self.environment), \
                     patch.object(mla_runner.importlib, 'import_module', return_value=module), \
                     patch.object(sys, 'path', list(sys.path)):
                    if allowed:
                        initialize(run)
                    else:
                        with self.assertRaises(ValueError):
                            initialize(run)

    def test_case_labels_are_data_not_output_paths(self):
        requests = deepcopy(self.requests)
        for request in requests:
            request['job']['contract']['workload']['spec']['cases'][1]['id'] = 'tiny/boundary'
            request['job']['contract']['workload']['spec']['cases'][2]['id'] = '../outside'
            request['request_digest'] = sha256(mla_runner._json({k: v for k, v in request.items()
                                               if k != 'request_digest'}).encode()).hexdigest()
        self.requests = requests
        run = self.make()
        self.stage(run, 'compile')
        result = self.stage(run, 'correctness')
        self.assertEqual(result['correctness'], 'passed')
        self.assertEqual([case['id'] for case in result['cases']], ['small', 'tiny/boundary', '../outside'])
        self.assertTrue(all(Path(path).resolve().is_relative_to(self.output) for path in result['raw_artifact_paths']))
        self.assertFalse((self.root / 'outside').exists())

    def test_output_path_escape_and_source_changes_are_rejected(self):
        run = self.make()
        with self.assertRaises(ValueError):
            run.run_stage(self.requests[1], self.root / 'outside', lease={})
        source = self.workspace / self.source_bundle['entry']['path']
        source.write_text('def build_mla_decode_contiguous(): return 99\n')
        with self.assertRaises(ValueError):
            self.stage(run, 'compile')
        self.assertEqual(self.oracle_calls, [])


if __name__ == '__main__':
    unittest.main()
