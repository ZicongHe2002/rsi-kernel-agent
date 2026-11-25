from copy import deepcopy
from hashlib import sha256
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys


def _require(value, message):
    if not value:
        raise ValueError(message)


def _read(path, ref):
    path = Path(path).absolute()
    _require(not any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)()
                     for p in (path, *path.parents)), 'CPU artifact path contains a link')
    raw = path.read_bytes()
    _require(len(raw) == ref['size'] and sha256(raw).hexdigest() == ref['digest'], 'CPU artifact identity changed')
    return raw


def run_candidate(payload):
    subject = payload['subject']
    _read(__file__, subject['checker_ref'])
    runner = payload['runner_path']
    _read(runner, subject['runner_ref'])
    spec = importlib.util.spec_from_file_location('_rsi_frozen_cpu_runner', runner)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    workspace = Path(payload['workspace']).resolve()
    source_bundle = payload['source_bundle']
    bundle_raw = json.dumps(source_bundle, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    _require({'digest': sha256(bundle_raw).hexdigest(), 'size': len(bundle_raw)} == subject['source_bundle_ref'],
             'CPU source bundle differs from subject')
    workload = module.validate_workload(payload['workload'])
    config_raw = _read(payload['config_path'], subject['config_ref'])
    config = module.validate_candidate_config(json.loads(config_raw), workload)
    expected_entry = {'path': 'pallas_kernels/kernels/mla_decode.py', 'symbol': 'build_mla_decode_contiguous'}
    _require(source_bundle['entry'] == expected_entry, 'Unsupported CPU candidate entry')
    module.verify_loaded_bundle(workspace, source_bundle, sys.modules)
    _require(not any(name == 'pallas_kernels' or name.startswith('pallas_kernels.')
                     or name == 'jax_ops' or name.startswith('jax_ops.') for name in sys.modules),
             'CPU candidate requires a fresh process')
    sys.path.insert(0, str(workspace))
    import jax
    import numpy as np
    from pallas_kernels.kernels import get_kernel
    from pallas_kernels.kernels.config_loader import load_kernel_config
    _require(jax.default_backend() == 'cpu' and all(device.platform == 'cpu' for device in jax.devices()),
             'Candidate check must be CPU-only')
    kernel_module = importlib.import_module('pallas_kernels.kernels.mla_decode')
    module.verify_entry(workspace, source_bundle['entry'], kernel_module)
    tolerance = payload['tolerance']
    _require(tolerance == {'atol': .01, 'rtol': .01}, 'Frozen CPU tolerance differs')
    _require([row['case_id'] for row in payload['cases']] == [case['id'] for case in workload['cases']],
             'CPU case coverage differs')
    rows = []
    for case, saved in zip(workload['cases'], payload['cases']):
        for key in ('inputs', 'oracle_outputs', 'oracle_metadata'):
            _read(saved[key + '_path'], saved[key + '_ref'])
        oracle_metadata = json.loads(Path(saved['oracle_metadata_path']).read_bytes())
        _require(oracle_metadata['oracle_identity'] == payload['oracle_identity'], 'CPU Oracle binding differs')
        overrides = deepcopy(case.get('overrides', {}))
        for key, values in config['case_overrides'].get(case['id'], {}).items():
            overrides.setdefault(key, {}).update(values)
        overrides.setdefault('params', {})['interpret_mode'] = True
        cfg = load_kernel_config(case['config_name'], overrides=overrides)
        _require(cfg.get_tolerance('bfloat16') == tolerance and cfg.dtype == 'bfloat16'
                 and cfg.params.get('interface') == 'contiguous' and cfg.params.get('interpret_mode') is True,
                 'CPU candidate changed the frozen numerical contract')
        candidate = get_kernel('mla_decode', cfg)
        original = module._load_arrays(saved['inputs_path'])
        reference = module._load_arrays(saved['oracle_outputs_path'])
        inputs = jax.device_put(original)
        before = [np.asarray(value).tobytes(order='C') for value in inputs]
        actual = jax.block_until_ready(candidate(*inputs))
        _require(type(actual) is tuple and len(actual) == len(reference) == 2,
                 'CPU candidate must return attention and complete cache')
        outputs = [module.compare_frame(module._frame(a, exact=out['comparison'] == 'exact'),
                    module._frame(b, exact=out['comparison'] == 'exact'), out, **tolerance)
                   for a, b, out in zip(actual, reference, case['outputs'])]
        unchanged = all(np.asarray(value).tobytes(order='C') == raw for value, raw in zip(inputs, before))
        rows.append({'case_id': case['id'], 'outputs': outputs, 'input_unchanged': unchanged,
                     'passed': all(row['passed'] for row in outputs) and unchanged,
                     'inputs_ref': saved['inputs_ref'], 'oracle_outputs_ref': saved['oracle_outputs_ref'],
                     'oracle_metadata_ref': saved['oracle_metadata_ref']})
        del original, reference, inputs, actual, candidate
    loaded = module.verify_loaded_bundle(workspace, source_bundle, sys.modules)
    _read(__file__, subject['checker_ref'])
    _read(runner, subject['runner_ref'])
    _read(payload['config_path'], subject['config_ref'])
    return {'schema_version': 1, 'type': 'cpu_candidate_result.v1', 'subject': subject,
            'status': 'passed' if all(row['passed'] for row in rows) else 'failed',
            'backend': 'cpu', 'interpret_mode': True, 'hardware_executed': False,
            'process_id': os.getpid(), 'environment': {'python': sys.version.split()[0],
                'jax': jax.__version__, 'numpy': np.__version__},
            'identity': {'entry': source_bundle['entry'], 'loaded_files': loaded,
                         'source_bundle_ref': subject['source_bundle_ref'], 'config_ref': subject['config_ref']},
            'oracle_identity': payload['oracle_identity'], 'cases': rows}


if __name__ == '__main__':
    payload = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    try:
        result = run_candidate(payload)
    except (ImportError, NotImplementedError) as error:
        result = {'schema_version': 1, 'type': 'cpu_candidate_result.v1', 'subject': payload['subject'],
                  'status': 'unavailable', 'error_type': type(error).__name__, 'reason': str(error)}
    except Exception as error:
        result = {'schema_version': 1, 'type': 'cpu_candidate_result.v1', 'subject': payload['subject'],
                  'status': 'failed', 'error_type': type(error).__name__, 'reason': str(error)}
    Path(payload['result_path']).write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False), encoding='utf-8')
    raise SystemExit(0 if result['status'] == 'passed' else 2 if result['status'] == 'unavailable' else 1)
