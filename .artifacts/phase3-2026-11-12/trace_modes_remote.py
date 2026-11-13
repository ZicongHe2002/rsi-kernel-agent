"""One task-owned trace capability diagnostic, never an MLA Run/receipt.

Run only through trace_modes_diagnostic.py after the existing worker bootstrap.
An unknown dispatched operation is never launched again. A diagnostic guard in
the worker operations directory keeps its runtime quarantined after a crash.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback

MODES = ('TRACE_ONLY_HOST', 'TRACE_ONLY_XLA', 'TRACE_COMPUTE', 'TRACE_COMPUTE_AND_SYNC')
SCOPE = 'minimal JAX trace-mode capability only; not MLA, benchmark, or optimization'


def load_module(path, expected_digest, name):
    path = Path(path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected_digest:
        raise ValueError('Pinned diagnostic dependency differs: ' + name)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def bind(request):
    if request['scope'] != SCOPE or request['modes'] != list(MODES):
        raise ValueError('Unsupported diagnostic scope/options')
    digest = request['request_digest']
    body = {k: v for k, v in request.items() if k != 'request_digest'}
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if hashlib.sha256(encoded).hexdigest() != digest:
        raise ValueError('Diagnostic request changed')
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != request['source_digest']:
        raise ValueError('Actual diagnostic script differs from request')
    if Path(sys.executable).resolve() != Path(request['remote_python']).resolve():
        raise ValueError('Diagnostic is not in the pinned isolated interpreter')
    worker = load_module(request['worker_path'], request['worker_digest'], '_trace_pinned_worker')
    spool = worker.WorkerSpool(request['remote_root'], request['runtime_id'], request['nonce'])
    if not spool._ready():
        raise ValueError('Existing runtime worker must be bootstrapped and bound first')
    directory = worker._safe(spool.root / 'diagnostics' / digest)
    guard = spool._operation('trace-mode-diagnostic:' + digest)
    return worker, spool, directory, guard


def completed(worker, directory, request):
    receipt = worker._read(directory / 'diagnostic-receipt.json')
    body = {k: v for k, v in receipt.items() if k != 'receipt_digest'}
    if (receipt['receipt_digest'] != worker._sha(worker._json(body))
            or receipt['request_digest'] != request['request_digest']
            or receipt['type'] != 'trace_mode_diagnostic_receipt.v1'
            or receipt['source_digest'] != request['source_digest']):
        raise ValueError('Diagnostic receipt binding mismatch')
    for item in receipt['artifacts']:
        path = worker._safe(item['path'])
        if not path.is_relative_to(Path(request['remote_root']) / 'blobs'):
            raise ValueError('Diagnostic artifact escapes worker blobs')
        raw = path.read_bytes()
        if item['ref'] != {'digest': worker._sha(raw), 'size': len(raw)}:
            raise ValueError('Diagnostic artifact changed')
    return receipt


def clear_guard(worker, guard, request):
    active = guard / 'intent.json'
    if active.exists():
        expected = {'type': 'trace_mode_diagnostic_guard.v1', 'request_digest': request['request_digest']}
        if worker._read(active) != expected:
            raise ValueError('Diagnostic guard differs; leave runtime blocked')
        active.unlink()  # Only our marker, after durable receipt; historical intent remains in diagnostics.
        if os.name != 'nt':
            descriptor = os.open(guard, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)


def poll(request):
    worker, spool, directory, guard = bind(request)
    if not directory.exists() or not (directory / 'dispatch-intent.json').exists():
        return {'status': 'definitely_not_started', 'receipt': None}
    if worker._read(directory / 'request.json') != request:
        raise ValueError('Persisted diagnostic request differs')
    if (directory / 'diagnostic-receipt.json').exists():
        receipt = completed(worker, directory, request)
        # Repair only the post-receipt cleanup prefix; never rerun computation.
        with worker._lock(directory / 'diagnostic.lock') as held:
            if held:
                with worker._lock(spool.coordinator / 'runtime.lock') as runtime_held:
                    if runtime_held:
                        clear_guard(worker, guard, request)
        return {'status': 'completed', 'receipt': receipt,
                'guard_cleanup_pending': (guard / 'intent.json').exists()}
    with worker._lock(directory / 'diagnostic.lock') as held:
        return {'status': 'unknown' if held else 'running', 'receipt': None}


def submit(request):
    worker, spool, directory, guard = bind(request)
    directory.mkdir(parents=True, exist_ok=True)
    with worker._lock(directory / 'submission.lock', blocking=True):
        worker._publish(directory / 'request.json', worker._json(request))
        if (directory / 'dispatch-intent.json').exists():
            return poll(request)  # Lost launch acknowledgement stays unknown, never relaunches.
        worker._publish(directory / 'dispatch-intent.json', worker._json({
            'type': 'trace_mode_diagnostic_dispatch.v1', 'request_digest': request['request_digest'],
            'created_ns': time.time_ns()}))
        options = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       close_fds=True, env={k: v for k, v in os.environ.items() if k != 'PYTHONPATH'})
        if os.name == 'nt':
            options['creationflags'] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options['start_new_session'] = True
        child = subprocess.Popen([sys.executable, '-I', '-B', str(Path(__file__).resolve()),
                                  '--execute', str(directory / 'request.json')], **options)
        threading.Thread(target=child.wait, daemon=True).start()
        return {'status': 'submitted', 'receipt': None}


def inventory_xplane(path):
    """Inventory actual events; no timing summation or counter interpretation."""
    from tensorflow.tsl.profiler.protobuf import xplane_pb2
    space = xplane_pb2.XSpace.FromString(path.read_bytes())
    planes = []
    for plane in space.planes:
        counts = {}
        for line in plane.lines:
            for event in line.events:
                name = plane.event_metadata[event.metadata_id].name
                counts[name] = counts.get(name, 0) + 1
        planes.append({'name': plane.name, 'line_count': len(plane.lines),
                       'event_count': sum(counts.values()), 'event_names': counts})
    return {'path': str(path), 'planes': planes,
            'interpretation': 'event inventory only; no DMA/counter/critical-path inference'}


def probe_modes(worker, request, output):
    runner = load_module(request['runner_path'], request['runner_digest'], '_trace_environment_reader')
    import jax
    import jax.numpy as jnp
    environment = runner.collect_environment(request['runtime_id'])
    worker._publish(output / 'environment.json', worker._json(environment))
    if environment != request['expected_environment']:
        raise ValueError('Observed environment differs from the pinned new-runtime probe')
    worker._publish(output / 'options.json', worker._json({
        'scope': SCOPE, 'advanced_configuration': [{'tpu_trace_mode': mode} for mode in MODES],
        'shape': [128, 128], 'dtype': 'float32', 'warmup_calls': 2, 'traced_calls': 5,
        'computation': 'jit((x @ x) + x), fixed full inputs, block_until_ready on each result',
        'timer': None, 'formal_benchmark': False}))
    device = jax.devices('tpu')[0]
    x = jax.device_put(jnp.ones((128, 128), dtype=jnp.float32), device)
    jax.block_until_ready(x)
    compiled = jax.jit(lambda value: (value @ value) + value).lower(x).compile()
    for _ in range(2):
        jax.block_until_ready(compiled(x))
    rows = []
    for mode in MODES:
        trace = output / mode
        trace.mkdir()
        row = {'mode': mode, 'options': {'advanced_configuration': {'tpu_trace_mode': mode}},
               'api_status': 'failed', 'synchronized': False, 'xplane_files': [],
               'event_inventory': [], 'scope': SCOPE, 'started_ns': time.time_ns()}
        started, stop_failed = False, False
        errors = []
        try:
            options = jax.profiler.ProfileOptions()
            options.advanced_configuration = {'tpu_trace_mode': mode}
            jax.profiler.start_trace(str(trace), profiler_options=options)
            started = True
            for _ in range(5):
                result = compiled(x)
                jax.block_until_ready(result)
            row['synchronized'] = True
            row['result_0_0'] = float(jax.device_get(result[0, 0]))
        except BaseException:
            errors.append(traceback.format_exc())
        finally:
            if started:
                try:
                    jax.profiler.stop_trace()
                except BaseException:
                    errors.append(traceback.format_exc())
                    stop_failed = True
        row['api_status'] = 'succeeded' if started and not errors else 'failed'
        for pb in sorted(trace.rglob('*.xplane.pb')):
            row['xplane_files'].append(str(pb.relative_to(output)))
            try:
                row['event_inventory'].append(inventory_xplane(pb))
            except BaseException:
                errors.append('Inventory decode failed; raw protobuf retained:\n' + traceback.format_exc())
        row['errors'], row['finished_ns'] = errors, time.time_ns()
        worker._publish(trace / 'mode-result.json', worker._json(row))
        rows.append(row)
        if stop_failed:
            # The profiler's process state is uncertain. Do not start another
            # mode on it, and retain an explicit not-attempted result for each.
            for unattempted in MODES[len(rows):]:
                skipped = {'mode': unattempted, 'options': {'advanced_configuration': {'tpu_trace_mode': unattempted}},
                           'api_status': 'not_attempted', 'synchronized': False, 'xplane_files': [],
                           'event_inventory': [], 'scope': SCOPE,
                           'errors': ['Previous mode stop_trace failed; no further trace session was started']}
                worker._publish(output / (unattempted + '-not-attempted.json'), worker._json(skipped))
                rows.append(skipped)
            break
    return rows


def execute(request):
    worker, spool, directory, guard = bind(request)
    with worker._lock(directory / 'diagnostic.lock', blocking=True):
        if ((directory / 'execution-intent.json').exists() or (directory / 'diagnostic-receipt.json').exists()
                or (guard / 'intent.json').exists()):
            return
        with worker._lock(spool.coordinator / 'runtime.lock', blocking=True):
            output = worker._safe(directory / 'output')
            output.mkdir(exist_ok=True)
            rows, status, error, guarded = [], 'failed_before_execution', None, False
            try:
                # Scan under exactly the same global lock used by formal worker stages.
                for operation in (spool.root / 'operations').iterdir():
                    if (operation / 'intent.json').exists():
                        spool._completed(operation)  # Any unknown/unverified previous operation blocks.
                # Guard first: every later crash is visible to the unchanged
                # formal worker even when this process loses its OS lock.
                worker._publish(guard / 'intent.json', worker._json({
                    'type': 'trace_mode_diagnostic_guard.v1', 'request_digest': request['request_digest']}))
                guarded = True
                worker._publish(directory / 'execution-intent.json', worker._json({
                    'type': 'trace_mode_diagnostic_intent.v1', 'request_digest': request['request_digest'],
                    'source_digest': request['source_digest'], 'started_ns': time.time_ns()}))
                before = worker.observe_device_owners()
                worker._publish(output / 'device-before.json', worker._json(before))
                if before['status'] != 'clear_visible_processes':
                    raise RuntimeError('Visible TPU ownership is not clear; no diagnostic computation started')
                with worker._capture(output):
                    rows = probe_modes(worker, request, output)
                status = 'completed_modes'
            except BaseException:
                error = traceback.format_exc()
                worker._publish(output / 'diagnostic-error.txt', error.encode('utf-8'))
                status = 'failed' if guarded else 'blocked_before_execution'
            worker._publish(output / 'device-after.json', worker._json(worker.observe_device_owners()))
            artifacts = []
            for path in sorted(output.rglob('*')):
                if path.is_file():
                    artifact = spool._artifact(path, output)
                    artifacts.append({**artifact, 'relative_path': path.relative_to(output).as_posix()})
            body = {'type': 'trace_mode_diagnostic_receipt.v1', 'scope': SCOPE,
                    'request_digest': request['request_digest'], 'source_digest': request['source_digest'],
                    'runtime_id': request['runtime_id'], 'task_id': request['task_id'],
                    'status': status, 'modes': rows, 'error': error, 'artifacts': artifacts,
                    'finished_ns': time.time_ns(), 'formal_run_id': None, 'promotion': None}
            receipt = {**body, 'receipt_digest': worker._sha(worker._json(body))}
            worker._publish(directory / 'diagnostic-receipt.json', worker._json(receipt))
            clear_guard(worker, guard, request)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--execute':
        execute(json.loads(Path(sys.argv[2]).read_bytes()))
    else:
        command = json.load(sys.stdin)
        action = command['action']
        if action not in ('submit', 'poll'):
            raise ValueError('Unsupported diagnostic action')
        try:
            result = submit(command['request']) if action == 'submit' else poll(command['request'])
        except (OSError, ValueError, KeyError, TypeError):
            result = {'status': 'unknown', 'receipt': None, 'reason': 'Diagnostic state/binding unavailable'}
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
