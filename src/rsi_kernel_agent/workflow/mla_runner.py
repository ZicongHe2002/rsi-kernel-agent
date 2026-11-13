"""Fixed MLA execution harness, reusing the audited PallasKernels entry points.

Importing this module requires only the standard library. JAX/NumPy are loaded
inside a fresh remote process; this module does not allocate or restart runtimes.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import importlib
import importlib.metadata
import importlib.util
import inspect
import gc
import json
import math
import os
from pathlib import Path, PurePosixPath
import site
import statistics
import subprocess
import sys
import sysconfig
import time


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def validate_workload(spec):
    """Accept the currently audited interface with explicit two-output cases."""
    _require(type(spec) is dict and spec.get('kernel') == 'mla_decode'
             and spec.get('interface') == 'contiguous', 'Only audited contiguous MLA Decode is supported')
    cases = spec.get('cases')
    _require(type(cases) is list and bool(cases), 'Explicit correctness cases are required')
    seen = set()
    for case in cases:
        _require(type(case) is dict and isinstance(case.get('id'), str) and case['id']
                 and case['id'] not in seen, 'Case IDs must be nonempty and unique')
        seen.add(case['id'])
        _require(case.get('config_name') in ('mla_decode', 'mla_decode_vcs'), 'Unknown baseline configuration')
        overrides = case.get('overrides', {})
        _require(type(overrides) is dict and set(overrides) <= {'shape', 'tiling', 'params'},
                 'Workload cannot change reference tolerances or dtype')
        _require(overrides.get('params', {}).get('interface', 'contiguous') == 'contiguous'
                 and overrides.get('params', {}).get('interpret_mode', False) is False,
                 'Real workload cannot switch interface or interpretation')
        outputs = case.get('outputs')
        _require(type(outputs) is list and len(outputs) == 2, 'Attention and complete cache outputs are required')
        for out, name, comparison in zip(outputs, ('attention', 'cache'), ('allclose', 'exact')):
            _require(out.get('name') == name and out.get('comparison') == comparison
                     and out.get('dtype') == 'bfloat16', 'Invalid fixed output contract')
            shape = out.get('shape')
            _require(type(shape) is list and shape and all(type(n) is int and n > 0 for n in shape),
                     'Every output needs an explicit positive shape')
        if 'lengths' in case:
            _require(type(case['lengths']) is list and all(type(n) is int for n in case['lengths']),
                     'Lengths must be integer vector data')
    return deepcopy(spec)


def compare_frame(actual, expected, spec, *, atol, rtol):
    """Compare numeric attention or opaque BF16 cache without masking NaNs.

    Numeric tolerance preserves the original assert_allclose(reference, actual)
    direction. Relative-error diagnostics use max(abs(reference), 1e-30).
    """
    _require(all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in (atol, rtol)),
             'Invalid fixed tolerances')
    compatible = (actual['shape'] == expected['shape'] == spec['shape']
                  and actual['dtype'] == expected['dtype'] == spec['dtype'])
    finite = bool(actual['finite'] and expected['finite'])
    result = {'name': spec['name'], 'shape': actual['shape'], 'dtype': actual['dtype'],
              'finite': finite, 'max_abs': None, 'max_rel': None, 'rms': None,
              'metric_reason': None, 'tolerance_violations': 0, 'passed': False}
    if spec['comparison'] == 'exact':
        same = type(actual['bytes']) is bytes and actual['bytes'] == expected['bytes']
        result.update(tolerance_violations=0 if compatible and same else 1,
                      passed=bool(compatible and same),
                      metric_reason='Opaque cache compared byte-for-byte; numeric error is not its contract')
        return result
    _require(spec['comparison'] == 'allclose', 'Unknown output comparison')
    values, reference = actual['values'], expected['values']
    if not compatible or not finite or len(values) != len(reference) or not values:
        result.update(tolerance_violations=max(1, len(values)),
                      metric_reason='Shape/dtype mismatch or nonfinite/empty numeric output')
        return result
    absolute = [abs(float(a) - float(b)) for a, b in zip(values, reference)]
    violations = sum(error > atol + rtol * abs(float(value)) for error, value in zip(absolute, values))
    result.update(max_abs=max(absolute),
                  max_rel=max(error / max(abs(float(ref)), 1e-30) for error, ref in zip(absolute, reference)),
                  rms=math.sqrt(statistics.fmean(error * error for error in absolute)),
                  tolerance_violations=violations, passed=violations == 0)
    return result


def sample_statistics(samples):
    _require(type(samples) is list and bool(samples)
             and all(type(x) in (float, int) and math.isfinite(x) and x > 0 for x in samples),
             'Timing samples must be finite positive numbers')
    ordered = sorted(samples)

    def percentile(fraction):
        position = (len(ordered) - 1) * fraction
        lower, upper = math.floor(position), math.ceil(position)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

    return {'count': len(samples), 'mean_ms': statistics.fmean(samples),
            'median_ms': statistics.median(samples), 'std_ms': statistics.pstdev(samples),
            'min_ms': min(samples), 'max_ms': max(samples), 'p25_ms': percentile(.25),
            'p75_ms': percentile(.75)}


def verify_loaded_bundle(workspace, bundle, modules=None):
    """Check every uploaded file and reject package modules from another root."""
    workspace = Path(workspace).absolute()
    _require(not any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction())
                     for p in (workspace, *workspace.parents)), 'Workspace contains a link')
    root = workspace.resolve()
    expected = {}
    for row in bundle['files']:
        name = row['path']
        path = PurePosixPath(name)
        _require(type(name) is str and '\\' not in name and not path.is_absolute()
                 and '..' not in path.parts and ':' not in name and name == path.as_posix()
                 and name not in expected, 'Bundle path escape or duplicate')
        actual = root.joinpath(*path.parts)
        _require(not any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction())
                         for p in (actual, *actual.parents)), 'Bundle file contains a link')
        _require(actual.is_file() and actual.resolve().is_relative_to(root), 'Bundle file missing or outside workspace')
        data = actual.read_bytes()
        ref = row['artifact_ref']
        digest = sha256(data).hexdigest()
        _require(len(data) == ref['size'] and digest == ref['digest'], 'Uploaded source identity mismatch: ' + name)
        expected[name] = digest
    _require(bundle['entry']['path'] in expected, 'Entry is absent from bundle')
    if modules is not None:
        runtime_roots = {Path(p).resolve() for key, p in sysconfig.get_paths().items()
                         if key in ('stdlib', 'platstdlib', 'purelib', 'platlib')}
        runtime_roots.update(Path(p).resolve() for p in site.getsitepackages())
        extension_root = sysconfig.get_config_var('DESTSHARED')
        if extension_root:
            runtime_roots.add(Path(extension_root).resolve())
        if os.name == 'nt':
            runtime_roots.add((Path(sys.base_prefix) / 'DLLs').resolve())
        # Generic package basenames (__init__, utils, typing, ...) legitimately
        # occur in unrelated installed libraries; compare logical source roots.
        candidate_suffixes = [PurePosixPath(path).parts for path in expected if path.endswith('.py')]
        candidate_roots = {PurePosixPath(path).parts[0].removesuffix('.py') for path in expected if path.endswith('.py')}
        worker_main = getattr(sys.modules.get('__main__'), '__file__', None)
        trusted_launchers = {Path(__file__).resolve()}
        if worker_main:
            trusted_launchers.add(Path(worker_main).resolve())
        # Debian ships this exact startup hook outside its stdlib directory.
        # Accept only its system-owned, non-writable canonical location.
        startup_hook = Path('/etc/python%d.%d/sitecustomize.py' % sys.version_info[:2])
        if os.name == 'posix' and startup_hook.is_file() and not startup_hook.is_symlink():
            attributes = startup_hook.stat()
            if attributes.st_uid == 0 and attributes.st_mode & 0o022 == 0:
                trusted_launchers.add(startup_hook.resolve())
        for name, module in tuple(modules.items()):
            filename = getattr(module, '__file__', None)
            if filename is None:
                continue
            path = Path(filename).resolve()
            if path.is_relative_to(root):
                relative = path.relative_to(root).as_posix()
                _require(relative in expected, 'Loaded dependency was not in source manifest: ' + relative)
            elif path not in trusted_launchers:
                _require(name.split('.')[0] not in candidate_roots
                         and not any(tuple(path.parts[-len(parts):]) == parts for parts in candidate_suffixes),
                         'Loaded candidate/helper module is outside its declared workspace: ' + name)
                _require(any(path.is_relative_to(base) for base in runtime_roots),
                         'Loaded external helper is not a declared runtime dependency: ' + name)
    return [{'path': name, 'digest': digest} for name, digest in sorted(expected.items())]


def measure_compiled(compile_fn, inputs, *, synchronize, warmup, measure, warmup_count, measurements):
    """Keep compilation and input placement outside original synchronized timing."""
    _require(type(warmup_count) is int and warmup_count >= 1
             and type(measurements) is int and measurements >= 2, 'Invalid benchmark counts')
    synchronize(inputs)
    started = time.perf_counter_ns()
    compiled = compile_fn(inputs)
    compile_ms = (time.perf_counter_ns() - started) / 1e6
    warmup_ms = [warmup(compiled, *inputs) for _ in range(warmup_count)]
    samples = measure(compiled, *inputs, repeats=measurements)
    _require(len(samples) == measurements, 'Original benchmark returned incomplete samples')
    stats = sample_statistics(samples)
    return {'compile_time_ms': compile_ms, 'warmup_count': warmup_count,
            'warmup_ms': warmup_ms, 'samples_ms': samples, 'latency_ms': stats['median_ms'],
            'statistics': stats, 'synchronized': True}


def validate_candidate_config(config, workload):
    _require(type(config) is dict and set(config) == {'schema_version', 'kernel', 'case_overrides'}
             and config['schema_version'] == 1 and config['kernel'] == 'mla_decode', 'Invalid candidate config')
    overrides = config['case_overrides']
    _require(type(overrides) is dict and set(overrides) <= {c['id'] for c in workload['cases']},
             'Candidate config names an unknown case')
    for case in overrides.values():
        _require(type(case) is dict and set(case) <= {'tiling', 'params'}, 'Candidate cannot change workload semantics')
        for name, values in case.items():
            allowed = {'block_size'} if name == 'tiling' else {'vmem_limit_bytes'}
            _require(type(values) is dict and set(values) <= allowed, 'Candidate cannot change oracle or semantic parameters')
            for field, value in values.items():
                _require(type(value) is int and value > 0 and (field != 'block_size' or value % 128 == 0),
                         'Invalid candidate resource configuration')
    return deepcopy(config)


def load_trusted_tool(root, relative, trusted_files, module_name):
    """Load the frozen original measurement/parser source after digest checking."""
    root = Path(root).resolve()
    target = (root / relative).resolve()
    _require(target.is_relative_to(root) and relative in trusted_files, 'Tool is outside frozen protocol')
    digest = sha256(target.read_bytes()).hexdigest()
    _require(digest == trusted_files[relative], 'Trusted tool digest mismatch: ' + relative)
    spec = importlib.util.spec_from_file_location(module_name, target)
    _require(spec is not None and spec.loader is not None, 'Cannot load trusted tool')
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def verify_entry(root, entry, module):
    """Verify the declared function resolves to code in the imported source file."""
    expected = (Path(root) / entry['path']).resolve()
    _require(Path(module.__file__).resolve() == expected, 'Entry module path differs from declaration')
    value = module
    for part in entry['symbol'].split('.'):
        value = getattr(value, part)
    value = inspect.unwrap(value)
    source = inspect.getsourcefile(value)
    _require(source is not None and Path(source).resolve() == expected, 'Actual entry callable uses another source file')
    return str(expected)


def collect_environment(runtime_session_id):
    """Read the real process/runtime; absent optional versions remain null."""
    import jax
    devices = jax.devices('tpu')
    _require(bool(devices), 'No TPU device is available')

    def version(name):
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            return None

    rows = []
    for device in devices:
        coords = getattr(device, 'coords', None)
        rows.append({'id': device.id, 'platform': device.platform, 'device_kind': device.device_kind,
                     'process_index': device.process_index, 'coords': list(coords) if coords is not None else None,
                     'core_on_chip': getattr(device, 'core_on_chip', None)})
    client = getattr(devices[0], 'client', None)
    return {'runtime_session_id': runtime_session_id, 'python': sys.version.split()[0],
            'jax': jax.__version__, 'jaxlib': version('jaxlib'), 'libtpu': version('libtpu'),
            'tensorflow': version('tensorflow'), 'xprof': version('xprof'),
            'tpu_model': ','.join(sorted({d.device_kind for d in devices})),
            'xla': {'jaxlib_version': version('jaxlib'),
                    'backend_platform_version': getattr(client, 'platform_version', None)},
            'devices': rows, 'device_count': len(devices), 'process_count': jax.process_count()}


def _write_json(path, value):
    Path(path).write_text(_json(value), encoding='utf-8')


def _save_arrays(path, arrays):
    """NPZ contains raw BF16 words, never lossy float casts or pickles."""
    import numpy as np
    payload, metadata = {}, []
    for index, array in enumerate(arrays):
        value = np.asarray(array)
        dtype = str(value.dtype)
        raw = value.view(np.uint16) if dtype == 'bfloat16' else value
        payload['array_' + str(index)] = raw
        metadata.append({'name': 'array_' + str(index), 'shape': list(value.shape), 'dtype': dtype,
                         'storage_dtype': str(raw.dtype), 'sha256': sha256(raw.tobytes(order='C')).hexdigest()})
    payload['metadata'] = np.frombuffer(_json(metadata).encode('utf-8'), dtype=np.uint8)
    np.savez(path, **payload)
    return metadata


def _load_arrays(path):
    import jax.numpy as jnp
    import numpy as np
    result = []
    with np.load(path, allow_pickle=False) as payload:
        metadata = json.loads(payload['metadata'].tobytes())
        for row in metadata:
            raw = payload[row['name']]
            _require(sha256(raw.tobytes(order='C')).hexdigest() == row['sha256'], 'Tensor bytes differ from manifest')
            value = raw.view(jnp.bfloat16) if row['dtype'] == 'bfloat16' else raw
            _require(list(value.shape) == row['shape'] and str(value.dtype) == row['dtype'], 'Tensor schema mismatch')
            result.append(value.copy())
    return tuple(result)


def _frame(array, *, exact=False):
    import numpy as np
    value = np.asarray(array)
    return {'shape': list(value.shape), 'dtype': str(value.dtype),
            'finite': bool(np.isfinite(value).all()), 'bytes': value.tobytes(order='C') if exact else None,
            'values': [] if exact else value.astype(np.float64).ravel().tolist()}


def _oracle_process(payload):
    """CPU subprocess entry: generate frozen inputs and independent reference."""
    root = Path(payload['workspace']).resolve()
    bundle = payload['bundle']
    verify_loaded_bundle(root, bundle, sys.modules)
    sys.path.insert(0, str(root))
    import jax
    import jax.numpy as jnp
    import numpy as np
    from pallas_kernels.kernels import get_kernel
    from pallas_kernels.kernels.config_loader import load_kernel_config
    from jax_ops import run_reference_op
    _require(all(d.platform == 'cpu' for d in jax.devices()), 'Oracle subprocess must be CPU-only')
    case = payload['case']
    cfg = load_kernel_config(case['config_name'], overrides=case.get('overrides', {}))
    _require(cfg.params.get('interface') == 'contiguous' and cfg.dtype == 'bfloat16', 'Frozen oracle interface mismatch')
    _require(cfg.get_tolerance('bfloat16') == payload['tolerance'], 'Original oracle tolerance differs from fixed contract')
    runner = get_kernel('mla_decode', cfg)
    oracle_module = importlib.import_module('pallas_kernels.kernels.mla_decode')
    _require(bundle['entry'] == {'path': 'pallas_kernels/kernels/mla_decode.py',
                                 'symbol': 'mla_decode_paged_reference'}, 'Unexpected frozen oracle entry')
    verify_entry(root, bundle['entry'], oracle_module)
    (inputs,) = runner.make_inputs(jax.random.key(cfg.seed))
    if 'lengths' in case:
        _require(len(case['lengths']) == cfg.shape['batch'], 'Lengths batch mismatch')
        inputs = (*inputs[:3], jnp.asarray(case['lengths'], jnp.int32))
    if case.get('opaque_tail_nan', False):
        q, new, cache, lengths = inputs
        copied = np.array(cache, copy=True)
        copied[:, :, q.shape[-1]:] = np.nan
        for index, stop in enumerate(np.clip(np.asarray(lengths), 0, copied.shape[1])):
            copied[index, int(stop):, :] = np.nan
        inputs = (q, new, jnp.asarray(copied), lengths)
    jax.block_until_ready(inputs)
    input_meta = _save_arrays(payload['inputs_path'], inputs)
    outputs = run_reference_op('mla_decode', runner.reference_functions()[0], *inputs)
    _save_arrays(payload['outputs_path'], outputs)
    loaded = verify_loaded_bundle(root, bundle, sys.modules)
    _write_json(payload['metadata_path'], {'input_arrays': input_meta, 'oracle_identity': {
        'source_bundle_digest': payload['bundle_digest'], 'entry': bundle['entry'], 'loaded_files': loaded}})


def _run_oracle(case, root, bundle, bundle_digest, directory, tolerance):
    directory.mkdir(parents=True, exist_ok=True)
    payload = {'workspace': str(root), 'bundle': bundle, 'bundle_digest': bundle_digest,
               'case': case, 'tolerance': tolerance, 'inputs_path': str(directory / 'inputs.npz'),
               'outputs_path': str(directory / 'oracle_outputs.npz'),
               'metadata_path': str(directory / 'oracle_metadata.json')}
    payload_path = directory / 'oracle_request.json'
    _write_json(payload_path, payload)
    env = dict(os.environ, JAX_PLATFORMS='cpu', PYTHONDONTWRITEBYTECODE='1')
    env.pop('PYTHONPATH', None)
    script = Path(__file__).resolve()
    with (directory / 'oracle.stdout.txt').open('wb') as stdout, (directory / 'oracle.stderr.txt').open('wb') as stderr:
        process = subprocess.run([sys.executable, '-B', '-I', str(script), '--oracle', str(payload_path)],
                                 env=env, cwd=directory, stdout=stdout, stderr=stderr, check=False)
    _require(process.returncode == 0, 'Frozen CPU oracle failed; see saved oracle.stderr.txt')
    return json.loads(Path(payload['metadata_path']).read_text(encoding='utf-8'))


def run_mla_stage(request, workspace, oracle_workspace, output_dir, *, source_bundle, oracle_bundle,
                  config_path, runtime_session_id, lease, environment=None):
    """Execute one real stage inside the worker's fresh isolated subprocess.

    File paths in the result are converted to ArtifactRefs by the durable worker.
    This runner neither fabricates receipts nor changes Store/Best state.
    """
    job = request['job']
    contract = job['contract']
    workload = validate_workload(contract['workload']['spec'])
    protocol = contract['context']['protocol']
    workspace, oracle_workspace = Path(workspace).resolve(), Path(oracle_workspace).resolve()
    _require(workspace != oracle_workspace, 'Oracle and Candidate workspaces must be separate')
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    config_bytes = Path(config_path).read_bytes()
    _require(sha256(config_bytes).hexdigest() == request['candidate_identity']['config_digest'], 'Candidate config bytes mismatch')
    config = validate_candidate_config(json.loads(config_bytes), workload)
    verify_loaded_bundle(workspace, source_bundle, sys.modules)
    verify_loaded_bundle(oracle_workspace, oracle_bundle)
    _require(source_bundle['entry'] == {'path': 'pallas_kernels/kernels/mla_decode.py',
                                        'symbol': 'build_mla_decode_contiguous'}, 'Unexpected Candidate entry')
    _require(not any(name == 'pallas_kernels' or name.startswith('pallas_kernels.')
                     or name == 'jax_ops' or name.startswith('jax_ops.') for name in sys.modules),
             'Fresh process required before any Kernel package import')
    sys.path.insert(0, str(workspace))
    import jax
    import numpy as np
    from pallas_kernels.kernels import get_kernel
    from pallas_kernels.kernels.config_loader import load_kernel_config
    actual_environment = collect_environment(runtime_session_id)
    if environment is not None:
        _require(actual_environment == environment, 'Observed runtime environment differs from submitted environment')
    _require(actual_environment == contract['context']['environment'], 'Actual runtime is not the fixed evaluation context')
    _require(jax.process_count() == 1, 'Only single-process TPU runtime execution is supported')
    device = jax.devices('tpu')[0]
    tolerance = protocol['correctness']
    _require(tolerance == {'atol': .01, 'rtol': .01}, 'This runner preserves the original MLA BF16 tolerance')
    raw_paths, input_rows, results, compiled_cases = [], [], [], []
    oracle_identity = None
    compile_ms_total = 0.0
    benchmark_case = protocol.get('benchmark_case_id', workload['cases'][0]['id'])
    selected = workload['cases'] if request['stage'] in ('compile', 'correctness') else [
        c for c in workload['cases'] if c['id'] == benchmark_case]
    _require(bool(selected), 'Benchmark case is absent from frozen workload')
    for index, case in enumerate(selected):
        directory = output_dir / ('case_' + str(index))
        metadata = _run_oracle(case, oracle_workspace, oracle_bundle,
                               protocol['oracle_bundle_ref']['digest'], directory, tolerance)
        oracle_identity = metadata['oracle_identity']
        host_inputs = _load_arrays(directory / 'inputs.npz')
        overrides = deepcopy(case.get('overrides', {}))
        for key, values in config['case_overrides'].get(case['id'], {}).items():
            overrides.setdefault(key, {}).update(values)
        cfg = load_kernel_config(case['config_name'], overrides=overrides)
        _require(cfg.get_tolerance('bfloat16') == tolerance and cfg.params.get('interface') == 'contiguous'
                 and not cfg.params.get('interpret_mode', False), 'Candidate runner changed fixed validation rules')
        runner = get_kernel('mla_decode', cfg)
        kernel_module = importlib.import_module('pallas_kernels.kernels.mla_decode')
        verify_entry(workspace, source_bundle['entry'], kernel_module)
        tag = 'rsi_mla_' + sha256(request['operation_id'].encode()).hexdigest()[:16] + '_' + str(index)

        def public(*values, _runner=runner):
            return _runner(*values)

        public.__name__ = tag
        with jax.default_device(device):
            inputs = jax.device_put(host_inputs, device)
            jax.block_until_ready(inputs)
            start = time.perf_counter_ns()
            compiled = jax.jit(public).lower(*inputs).compile()
            compile_ms = (time.perf_counter_ns() - start) / 1e6
        compile_ms_total += compile_ms
        executable = compiled.runtime_executable()
        _require(executable is not None and set(executable.local_devices()) == {device}, 'Compiled executable uses unexpected devices')
        hlo_path = directory / 'compiled-hlo.txt'
        hlo_path.write_text(compiled.as_text(), encoding='utf-8')
        specialization = {'case_id': case['id'], 'shape': cfg.shape, 'tiling': cfg.tiling, 'params': cfg.params,
                          'dtype': cfg.dtype, 'seed': cfg.seed, 'input_arrays': metadata['input_arrays'],
                          'entry': source_bundle['entry'], 'device_ids': [d.id for d in executable.local_devices()]}
        _write_json(directory / 'specialization.json', specialization)
        input_rows.append({'case_id': case['id'], 'arrays': metadata['input_arrays'],
                           'npz_digest': sha256((directory / 'inputs.npz').read_bytes()).hexdigest()})
        raw_paths.extend(str(directory / name) for name in
                         ('inputs.npz', 'oracle_outputs.npz', 'oracle_metadata.json', 'specialization.json', 'compiled-hlo.txt'))
        if request['stage'] in ('benchmark', 'profile'):
            compiled_cases.append((case, compiled, inputs, tag, directory, compile_ms))
        if request['stage'] == 'correctness':
            actual = jax.block_until_ready(compiled(*inputs))
            _require(type(actual) is tuple and len(actual) == 2, 'Kernel must return attention and full cache')
            expected = _load_arrays(directory / 'oracle_outputs.npz')
            _save_arrays(directory / 'candidate_outputs.npz', actual)
            raw_paths.append(str(directory / 'candidate_outputs.npz'))
            outputs = [compare_frame(_frame(a, exact=s['comparison'] == 'exact'),
                                     _frame(e, exact=s['comparison'] == 'exact'), s, **tolerance)
                       for a, e, s in zip(actual, expected, case['outputs'])]
            before_cache = np.asarray(host_inputs[2]).tobytes()
            if np.asarray(inputs[2]).tobytes() != before_cache:
                outputs[1].update(passed=False, tolerance_violations=max(1, outputs[1]['tolerance_violations']),
                                  metric_reason='Candidate mutated the input cache')
            results.append({'id': case['id'], 'outputs': outputs, 'passed': all(o['passed'] for o in outputs)})
            del actual, expected
        if request['stage'] in ('compile', 'correctness'):
            # Large full-cache fixtures must not accumulate across the matrix.
            del compiled, inputs, host_inputs, executable, runner, public
            gc.collect()
    loaded = verify_loaded_bundle(workspace, source_bundle, sys.modules)
    identity = {'runtime_session_id': runtime_session_id, 'workspace': str(workspace),
                'module_path': str(workspace / source_bundle['entry']['path']), 'entry': source_bundle['entry'],
                'loaded_files': loaded, 'source_bundle_digest': job['variant']['source_bundle_ref']['digest'],
                'config_digest': request['candidate_identity']['config_digest'], 'fresh_process': True,
                'specialization_digest': request['candidate_identity']['config_digest']}
    base = {'environment': actual_environment, 'identity': identity}
    if request['stage'] == 'compile':
        _write_json(output_dir / 'compile-details.json', {'compile_time_ms': compile_ms_total, 'raw_paths': raw_paths})
        return {**base, 'compile_time_ms': compile_ms_total, 'executed': True}
    if request['stage'] == 'correctness':
        manifest_path = output_dir / 'input_manifest.json'
        _write_json(manifest_path, {'schema_version': 1, 'cases': input_rows})
        raw_paths.append(str(manifest_path))
        return {**base, 'oracle_digest': protocol['oracle_digest'], 'oracle_identity': oracle_identity,
                'input_digest': sha256(manifest_path.read_bytes()).hexdigest(), 'cases': results,
                'correctness': 'passed' if all(c['passed'] for c in results) else 'failed',
                'raw_artifact_paths': raw_paths}
    case, compiled, inputs, tag, directory, compile_ms = compiled_cases[0]
    trusted = protocol['trusted_files']
    if request['stage'] == 'benchmark':
        helpers = load_trusted_tool(oracle_workspace, 'benchmarks/profiler.py', trusted, '_rsi_benchmark')
        bench = protocol['benchmark']
        timing = measure_compiled(lambda unused: compiled, inputs, synchronize=jax.block_until_ready,
                                  warmup=helpers.warmup, measure=helpers.measure,
                                  warmup_count=bench['warmup'], measurements=bench['measurements'])
        timing['compile_time_ms'] = compile_ms
        _write_json(output_dir / 'benchmark-details.json', {**timing, 'case_id': case['id'], 'raw_paths': raw_paths})
        return {**base, 'protocol': bench, 'samples_ms': timing['samples_ms'], 'latency_ms': timing['latency_ms'],
                'compile_time_ms': compile_ms, 'warmup_count': timing['warmup_count'], 'synchronized': True, 'lease': lease}
    _require(request['stage'] == 'profile', 'Unknown real MLA stage')
    profile = protocol['profile']
    _require(profile['trace_mode'] in ('default', 'default_profile_options'), 'Requested trace mode has not been verified')
    if 'tools' not in sys.modules:
        import types
        package = types.ModuleType('tools')
        package.__path__ = [str(oracle_workspace / 'tools')]
        sys.modules['tools'] = package
    load_trusted_tool(oracle_workspace, 'tools/tpu_duration_profile.py', trusted, 'tools.tpu_duration_profile')
    capture = load_trusted_tool(oracle_workspace, 'tools/get_tpu_duration_info.py', trusted, '_rsi_capture')
    trace_dir = output_dir / 'trace'
    report = capture._capture_entry(compiled, inputs, directory=trace_dir, tag=tag,
                                    calls=profile.get('profile_calls', 20), warmup=profile.get('profile_warmup', 5),
                                    device_ids=[device.id])
    _require(not report['errors'], 'Profile parser rejected capture; raw XPlane retained')
    paths = sorted(trace_dir.rglob('*.xplane.pb'))
    _require(len(paths) == 1, 'Profile must contain exactly one raw XPlane')
    return {**base, 'parser_digest': profile['parser_digest'], 'trace_mode': profile['trace_mode'],
            'raw_xplane_paths': [str(p) for p in paths], 'report_path': str(trace_dir / 'duration.json'),
            'capture_operation_id': request['operation_id'],
            'benchmark_operation_id': 'operation:' + sha256(_json([job['run_id'], 'benchmark']).encode('utf-8')).hexdigest(),
            'synchronized': True, 'lease': lease}


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--oracle':
        _oracle_process(json.loads(Path(sys.argv[2]).read_text(encoding='utf-8')))
    else:
        raise SystemExit('The fixed runner is invoked by the durable Colab worker')
