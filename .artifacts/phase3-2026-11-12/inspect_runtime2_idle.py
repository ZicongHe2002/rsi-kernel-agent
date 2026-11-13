"""Read-only post-TRACE idle observation; never bootstrap, launch work or kill.

Invoke only after diagnostic collect, before resuming formal experiments.
No import-time bridge calls. No JAX imports, proc cmdline or environment reads.
"""
import argparse
from hashlib import sha256
import inspect
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.workflow.colab_bridge import ColabBridge, BridgeUnavailable, extract_json_result


def inspect_remote(request):
    """Executed in a fresh isolated control interpreter, without JAX."""
    import fcntl
    import hashlib
    import importlib.util
    import json
    from pathlib import Path
    import sys
    import time

    def require(condition, message):
        if not condition:
            raise ValueError(message)

    def hash_file(path):
        digest = hashlib.sha256()
        size = 0
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                size += len(block)
                digest.update(block)
        return {'digest': digest.hexdigest(), 'size': size}

    worker_path = Path(request['worker_path'])
    require(hash_file(worker_path)['digest'] == request['worker_digest'], 'Pinned worker changed')
    require(Path(sys.executable).resolve() == Path(request['remote_python']).resolve(), 'Isolated interpreter mismatch')
    spec = importlib.util.spec_from_file_location('_idle_pinned_worker', worker_path)
    worker = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = worker
    spec.loader.exec_module(worker)
    spool = worker.WorkerSpool(request['remote_root'], request['runtime_id'], request['nonce'])
    require(spool._ready(), 'Pinned existing worker registration is unavailable')
    directory = worker._safe(spool.root / 'diagnostics' / request['request_digest'])
    guard = spool._operation('trace-mode-diagnostic:' + request['request_digest'])
    require(worker._read(directory / 'request.json') == request, 'Remote diagnostic request changed')
    script = worker._safe(spool.root / 'tools' / ('trace-modes-' + request['source_digest'] + '.py'))
    require(hash_file(script)['digest'] == request['source_digest'], 'Diagnostic script changed')

    report = {'type': 'runtime_idle_observation.v1', 'runtime_id': request['runtime_id'],
              'request_digest': request['request_digest'], 'worker_digest': request['worker_digest'],
              'scope': 'post-diagnostic visible Linux process/device ownership at one observation',
              'observed_ns': time.time_ns(), 'status': 'not_clear', 'reasons': [],
              'runtime_lock_available': False, 'diagnostic_lock_available': False,
              'guard_cleared': False, 'receipt_verified': False,
              'diagnostic_pid': None, 'diagnostic_pid_exited': False,
              'device_owners': None, 'jax_imported': False, 'remote_mutations': [],
              'formal_work_started': False}
    runtime_lock = worker._safe(spool.coordinator / 'runtime.lock')
    diagnostic_lock = worker._safe(directory / 'diagnostic.lock')
    if not runtime_lock.is_file() or not diagnostic_lock.is_file():
        report['reasons'].append('Expected existing lock file is missing; no lock file was created')
        return report
    # Linux flock is compatible with the original worker flock. Open existing
    # files read-only: this observation creates or modifies no remote files.
    with runtime_lock.open('rb') as runtime_stream, diagnostic_lock.open('rb') as diagnostic_stream:
        try:
            fcntl.flock(runtime_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            report['reasons'].append('Runtime lock is currently held')
            return report
        report['runtime_lock_available'] = True
        try:
            fcntl.flock(diagnostic_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            report['reasons'].append('Diagnostic lock is currently held')
            return report
        report['diagnostic_lock_available'] = True
        report['guard_cleared'] = not (guard / 'intent.json').exists()
        if not report['guard_cleared']:
            report['reasons'].append('Diagnostic guard remains; observation does not clean it')
        receipt = worker._read(directory / 'diagnostic-receipt.json')
        body = {k: v for k, v in receipt.items() if k != 'receipt_digest'}
        require(receipt['receipt_digest'] == worker._sha(worker._json(body)), 'Diagnostic receipt checksum mismatch')
        require(receipt['type'] == 'trace_mode_diagnostic_receipt.v1'
                and receipt['request_digest'] == request['request_digest']
                and receipt['source_digest'] == request['source_digest']
                and receipt['runtime_id'] == request['runtime_id']
                and receipt['task_id'] == request['task_id']
                and receipt['formal_run_id'] is None, 'Diagnostic receipt identity mismatch')
        before = None
        for artifact in receipt['artifacts']:
            path = worker._safe(artifact['path'])
            require(path.is_relative_to(spool.root / 'blobs'), 'Diagnostic blob escaped spool')
            require(hash_file(path) == artifact['ref'], 'Diagnostic artifact changed')
            if artifact['relative_path'] == 'device-before.json':
                require(before is None, 'Duplicate diagnostic device-before artifact')
                before = worker._read(path)
        report['receipt_verified'] = True
        report['receipt_digest'] = receipt['receipt_digest']
        report['diagnostic_status'] = receipt['status']
        if before is not None:
            pid = before.get('worker_pid')
            require(type(pid) is int and pid > 0, 'Diagnostic PID missing in pinned device-before evidence')
            report['diagnostic_pid'] = pid
            # Check only existence, no cmdline/environ. PID reuse remains
            # conservative: an existing PID never counts as observed exit.
            report['diagnostic_pid_exited'] = not Path('/proc', str(pid)).exists()
        if not report['diagnostic_pid_exited']:
            report['reasons'].append('Diagnostic process exit not observed from its pinned PID')
        for operation in (spool.root / 'operations').iterdir():
            if (operation / 'intent.json').exists():
                try:
                    spool._completed(operation)
                except (OSError, ValueError, KeyError, TypeError):
                    report['reasons'].append('Unresolved worker/diagnostic intent remains: ' + operation.name)
        report['device_owners'] = worker.observe_device_owners()
        if report['device_owners']['status'] != 'clear_visible_processes':
            report['reasons'].append('Visible device ownership is not clear')
        report['jax_imported'] = any(name == 'jax' or name.startswith('jax.') for name in sys.modules)
        require(not report['jax_imported'], 'Idle observer unexpectedly imported JAX')
        if not report['reasons']:
            report['status'] = 'clear_visible_processes_after_diagnostic_exit'
        report['finished_ns'] = time.time_ns()
        return report


def build_control_code(request):
    source = inspect.getsource(inspect_remote)
    child = source + '\nimport json,sys\ntry:\n    result=inspect_remote(json.load(sys.stdin))\nexcept Exception as error:\n    result={"status":"unknown","error_type":type(error).__name__,"reason":str(error)}\nprint(json.dumps(result,allow_nan=False))\n'
    return ('import subprocess,json\n'
            f'_r=subprocess.run([{request["remote_python"]!r},"-I","-B","-c",{child!r}],'
            f'input={json.dumps(request)!r},capture_output=True,text=True,timeout=45)\n'
            'assert _r.returncode==0, "read-only idle control failed"\n'
            'print("RSI_RESULT:"+json.dumps(json.loads(_r.stdout),allow_nan=False))\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path)
    args = parser.parse_args()
    work = Path(__file__).resolve().parent
    request = json.loads((work / 'trace-mode-diagnostic-request.json').read_bytes())
    body = {k: v for k, v in request.items() if k != 'request_digest'}
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if sha256(canonical).hexdigest() != request['request_digest']:
        raise ValueError('Local diagnostic request changed')
    config = json.loads((args.config or work / 'colab-config-runtime2.json').read_bytes())
    if any(config[key] != request[field] for key, field in
           (('runtime_id','runtime_id'),('nonce','nonce'),('remote_root','remote_root'),('remote_python','remote_python'))):
        raise ValueError('Idle observation cannot remap the pinned Runtime')
    bridge = ColabBridge(config['python'], config['session'], config['runtime_id'])
    try:
        result = extract_json_result(bridge.execute(build_control_code(request)))
    except BridgeUnavailable:
        result = {'status': 'unknown', 'reason': 'Bridge unavailable; no work was started'}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)


if __name__ == '__main__':
    main()
