"""Standalone standard-library remote worker with durable operation receipts.

Upload this file unchanged. The spool is execution transport state, not another
experiment database. Unknown outcomes quarantine this Runtime until reconciled.
Only cooperative worker operations are serialized; process visibility is also
checked before accepting a real benchmark/profile as controlled measurement.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager, redirect_stdout, redirect_stderr
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _safe(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        _require(not part.is_symlink() and not (hasattr(part, 'is_junction') and part.is_junction()),
                 'Worker path may not contain links or junctions')
    return path


def _read(path):
    raw = _safe(path).read_bytes()
    value = json.loads(raw)
    _require(raw == _json(value), 'Noncanonical persisted worker JSON')
    return value


def _publish(path, data):
    """Publish once; identical retries succeed, a different value never wins."""
    path = _safe(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        try:
            os.link(name, path)
        except FileExistsError:
            _require(path.read_bytes() == data, 'Conflicting durable worker value')
        if os.name != 'nt':
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def _capture(output_dir):
    """Retain Python and native-library stdout/stderr inside this operation."""
    with (output_dir / 'stdout.txt').open('w', encoding='utf-8', newline='\n') as out, \
            (output_dir / 'stderr.txt').open('w', encoding='utf-8', newline='\n') as err:
        sys.stdout.flush(); sys.stderr.flush()
        saved = os.dup(1), os.dup(2)
        try:
            os.dup2(out.fileno(), 1); os.dup2(err.fileno(), 2)
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    yield
                except BaseException:
                    traceback.print_exc()
                    raise
        finally:
            out.flush(); err.flush(); os.fsync(out.fileno()); os.fsync(err.fileno())
            os.dup2(saved[0], 1); os.dup2(saved[1], 2)
            os.close(saved[0]); os.close(saved[1])


@contextmanager
def _lock(path, *, blocking=False):
    path = _safe(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        # Byte-range locks may extend past EOF. Writing an initializer before
        # locking races with another process already locking that first byte.
        while True:
            stream.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if not blocking:
                    yield False
                    return
                time.sleep(.05)
        try:
            yield True
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def observe_device_owners():
    """Inspect visible process device handles; never terminate another process.

    This is evidence about visible processes in one host namespace, not a claim
    that a remote multi-host TPU or hidden container cannot interfere.
    """
    report = {'scope': 'visible Linux process device handles', 'observed_ns': time.time_ns(),
              'worker_pid': os.getpid(), 'scanned_pids': [], 'unreadable_pids': [],
              'device_nodes': [], 'other_device_owners': [], 'status': 'unknown'}
    if os.name != 'posix' or not Path('/proc').is_dir():
        report['reason'] = 'Linux procfs visibility unavailable'
        return report
    nodes = sorted({str(p) for pattern in ('accel*', 'accel/*', 'vfio/[0-9]*') for p in Path('/dev').glob(pattern)})
    report['device_nodes'] = nodes
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        pid = int(proc.name)
        try:
            descriptors = list((proc / 'fd').iterdir())
        except FileNotFoundError:
            continue
        except PermissionError:
            report['unreadable_pids'].append(pid)
            continue
        report['scanned_pids'].append(pid)
        for descriptor in descriptors:
            try:
                target = os.readlink(descriptor)
            except FileNotFoundError:
                continue
            except PermissionError:
                report['unreadable_pids'].append(pid)
                break
            if target in nodes or target.startswith('/dev/accel') or re.fullmatch('/dev/vfio/[0-9]+', target):
                report['other_device_owners'].append({'pid': pid, 'device': target})
    if report['other_device_owners']:
        report['status'] = 'interference'
    elif nodes and not report['unreadable_pids']:
        report['status'] = 'clear_visible_processes'
    else:
        report['reason'] = 'TPU device nodes or process visibility incomplete'
    return report


def _process_identity(pid):
    if os.name == 'posix':
        try:
            fields = Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()
            if fields[0] in ('Z', 'X'):
                return 'dead', None
            return 'alive', {'pid': pid, 'created': fields[19]}
        except FileNotFoundError:
            return 'dead', None
        except (OSError, IndexError):
            return 'unknown', None
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4))
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ('dead' if ctypes.get_last_error() == 87 else 'unknown'), None
        try:
            code = wintypes.DWORD()
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                return 'unknown', None
            if code.value != 259:
                return 'dead', None
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
                return 'unknown', None
            return 'alive', {'pid': pid, 'created': str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)}
        finally:
            kernel.CloseHandle(handle)
    return 'unknown', None


def _process_exited(identity):
    _require(type(identity) is dict and set(identity) == {'pid', 'created'}
             and type(identity['pid']) is int and identity['pid'] > 0
             and type(identity['created']) is str and identity['created'].isdigit(), 'Invalid worker process identity')
    state, current = _process_identity(identity['pid'])
    return None if state == 'unknown' else state == 'dead' or current != identity


class WorkerSpool:
    def __init__(self, root, runtime_id, nonce):
        _require(all(type(v) is str and v.strip() for v in (runtime_id, nonce)), 'Missing runtime binding')
        self.root = _safe(root)
        self.runtime_id, self.nonce = runtime_id, nonce
        self.marker = {'schema_version': 1, 'runtime_id': runtime_id, 'nonce': nonce}
        # Colab is Linux with /content. The fixed temporary root is solely the
        # non-Colab CPU host equivalent, never a caller-supplied coordination root.
        coordinator_base = (Path('/content/rsi-kernel-agent/runtime-locks')
                            if os.name == 'posix' and Path('/content').is_dir()
                            else Path(tempfile.gettempdir()) / 'rsi-kernel-agent-runtime-locks')
        self.coordinator = _safe(coordinator_base / _sha(runtime_id.encode()))
        self.registration = {**self.marker, 'spool_root': str(self.root.resolve())}

    def bootstrap(self):
        self.coordinator.mkdir(parents=True, exist_ok=True)
        with _lock(self.coordinator / 'registration.lock', blocking=True):
            registered = self.coordinator / 'registration.json'
            if registered.exists():
                _require(_read(registered) == self.registration, 'Runtime is already registered to another spool or nonce')
            else:
                _require(not (self.root / 'runtime.json').exists() and not (self.coordinator / 'initialized').exists(),
                         'Runtime registration was lost; refusing to replace it')
                _publish(self.coordinator / 'initialized', _json(self.registration))
                _publish(registered, _json(self.registration))
            return self._bootstrap_spool()

    def _bootstrap_spool(self):
        self.root.mkdir(parents=True, exist_ok=True)
        marker = self.root / 'runtime.json'
        if marker.exists():
            _require(_read(marker) == self.marker, 'Runtime or nonce does not match initialized spool')
            return {'status': 'ready', 'runtime_id': self.runtime_id}
        for name in ('operations', 'workspaces', 'oracles', 'blobs'):
            old = self.root / name
            _require(not old.exists() or old.is_dir() and not any(old.iterdir()),
                     'Previous worker state exists without a marker')
        # Separate permanent sentinel makes lost/partial marker initialization fail closed.
        try:
            descriptor = os.open(_safe(self.root / 'initialized'), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise ValueError('Spool marker was lost; refusing to reinitialize') from None
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(_json(self.marker)); stream.flush(); os.fsync(stream.fileno())
        _publish(marker, _json(self.marker))
        (self.root / 'operations').mkdir(exist_ok=True)
        return {'status': 'ready', 'runtime_id': self.runtime_id}

    def _ready(self):
        try:
            return (_read(self.root / 'runtime.json') == self.marker and _read(self.root / 'initialized') == self.marker
                    and _read(self.coordinator / 'registration.json') == self.registration
                    and _read(self.coordinator / 'initialized') == self.registration)
        except (OSError, ValueError, TypeError):
            return False

    def _operation(self, operation_id):
        _require(type(operation_id) is str and operation_id.strip(), 'Invalid operation ID')
        return _safe(self.root / 'operations' / _sha(operation_id.encode()))

    def _unknown(self):
        return {'status': 'unknown', 'receipt': None}

    def _completed(self, path):
        record = _read(path / 'completed.json')
        _require(set(record) == {'receipt', 'artifacts', 'completion_digest'}, 'Malformed completion record')
        value = {k: record[k] for k in ('receipt', 'artifacts')}
        _require(record['completion_digest'] == _sha(_json(value)), 'Completion record checksum mismatch')
        for item in record['artifacts']:
            target = _safe(item['path'])
            _require(target.is_relative_to(self.root / 'blobs'), 'Result artifact escaped spool')
            raw = target.read_bytes()
            _require(item['ref'] == {'digest': _sha(raw), 'size': len(raw)}, 'Result artifact changed')
        request = _read(path / 'request.json')
        _require(record['receipt']['request_digest'] == request['request_digest']
                 and record['receipt']['operation_id'] == request['operation_id'], 'Receipt/request binding mismatch')
        return {'status': 'completed', **value}

    def lookup(self, operation_id):
        if not self._ready():
            return self._unknown()
        path = self._operation(operation_id)
        if not path.exists():
            return {'status': 'definitely_not_started', 'receipt': None}
        try:
            request = _read(path / 'request.json')
            _require(request['operation_id'] == operation_id, 'Operation ID differs from spool record')
            _require(request['request_digest'] == _sha(_json({k: v for k, v in request.items() if k != 'request_digest'})),
                     'Persisted request checksum mismatch')
            if (path / 'completed.json').exists():
                return self._completed(path)
            if (path / 'candidate.json').exists():
                owner = _read(path / 'candidate.json')
                candidate = self.lookup_candidate(owner['operation_id'])
                return {'status': 'running', 'receipt': None} if candidate['status'] == 'running' else self._unknown()
            if (path / 'blocked.json').exists():
                return self._unknown()
            with _lock(path / 'operation.lock') as acquired:
                if not acquired:
                    return {'status': 'running', 'receipt': None}
                if (path / 'intent.json').exists():
                    return self._unknown()
                return {'status': 'definitely_not_started', 'receipt': None}
        except (OSError, ValueError, KeyError, TypeError):
            return self._unknown()

    def _validate(self, request, payload, runner):
        _require(self._ready(), 'Runtime spool binding is unavailable')
        _require(type(request) is dict and request.get('backend_id') == 'colab.v1'
                 and request.get('provenance') == 'real' and request.get('schema_version') == 1, 'Invalid real request')
        body = {k: v for k, v in request.items() if k != 'request_digest'}
        _require(request.get('request_digest') == _sha(_json(body)), 'Request digest mismatch')
        _require(request['stage'] in ('generate', 'compile', 'correctness', 'benchmark', 'profile'), 'Unknown execution stage')
        job = request['job']; variant = job['variant']; protocol = job['contract']['context']['protocol']
        _require(job['contract']['context']['environment']['runtime_session_id'] == self.runtime_id, 'Request Runtime changed')
        _require(_sha(_safe(runner).read_bytes()) == protocol['runner_digest'], 'Pinned runner bytes changed')
        _require(type(payload) is dict and set(payload) == {'source_bundle', 'oracle_bundle', 'blobs'}, 'Invalid payload')
        decoded = {}
        for sha, value in payload['blobs'].items():
            _require(re.fullmatch('[0-9a-f]{64}', sha) is not None, 'Invalid blob digest')
            raw = base64.b64decode(value, validate=True)
            _require(_sha(raw) == sha, 'Uploaded blob digest mismatch')
            decoded[sha] = raw
        def blob(ref):
            _require(type(ref) is dict and set(ref) == {'digest', 'size'}, 'Invalid uploaded artifact reference')
            raw = decoded[ref['digest']]
            _require(type(ref['size']) is int and ref['size'] == len(raw), 'Uploaded blob length mismatch')
            return raw
        for name, bundle_ref in (('source_bundle', variant['source_bundle_ref']), ('oracle_bundle', protocol['oracle_bundle_ref'])):
            bundle = payload[name]
            _require(blob(bundle_ref) == _json(bundle), 'Uploaded source bundle mismatch')
            _require(set(bundle) == {'schema_version', 'entry', 'files'} and bundle['schema_version'] == 1, 'Invalid bundle')
            seen = set()
            for entry in bundle['files']:
                rel = entry['path']
                _require(type(rel) is str and rel and '\\' not in rel and ':' not in rel
                         and not rel.startswith('/') and all(p not in ('', '.', '..') for p in rel.split('/')),
                         'Bundle path escapes workspace')
                _require(rel.casefold() not in seen, 'Duplicate bundle path')
                seen.add(rel.casefold()); blob(entry['artifact_ref'])
                expected = protocol.get('trusted_files', {}).get(rel)
                _require(expected is None or expected == entry['artifact_ref']['digest'], 'Trusted file changed')
            match = [x for x in bundle['files'] if x['path'] == bundle['entry']['path']]
            _require(len(match) == 1, 'Bundle entry is missing')
            expected = variant['source_ref']['digest'] if name == 'source_bundle' else protocol['oracle_digest']
            _require(match[0]['artifact_ref']['digest'] == expected, 'Entry source differs from declared identity')
        config, build = blob(variant['config_ref']), blob(variant['build_ref'])
        _require(type(json.loads(config)) is dict and config == _json(json.loads(config)), 'Noncanonical config')
        expected_build = {'schema_version': 1, 'source_bundle_digest': variant['source_bundle_ref']['digest'],
                          'oracle_bundle_digest': protocol['oracle_bundle_ref']['digest'],
                          'runner_digest': protocol['runner_digest'], 'oracle_digest': protocol['oracle_digest']}
        _require(build == _json(expected_build), 'Build identity differs from fixed execution recipe')
        _require(request['candidate_identity'] == {k + '_digest': variant[k + '_ref']['digest'] for k in ('source', 'config', 'build')},
                 'Candidate identity differs from uploaded artifacts')
        return decoded

    def submit(self, request, payload, runner_path):
        decoded = self._validate(request, payload, runner_path)
        path = self._operation(request['operation_id'])
        path.mkdir(parents=True, exist_ok=True)
        with _lock(self.root / 'submission.lock', blocking=True):
            _require(not (path / 'candidate.json').exists(), 'Stage belongs to a candidate execution')
            _publish(path / 'request.json', _json(request))
            _publish(path / 'payload.json', _json(payload))
            _publish(path / 'runner.py', _safe(runner_path).read_bytes())
            for sha, data in decoded.items():
                _publish(self.root / 'blobs' / sha, data)
            result = self.lookup(request['operation_id'])
            if result['status'] != 'definitely_not_started':
                return result
            options = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL,
                       'close_fds': True, 'env': {key: value for key, value in os.environ.items() if key != 'PYTHONPATH'}}
            if os.name == 'nt':
                options['creationflags'] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                options['start_new_session'] = True
            process = subprocess.Popen([sys.executable, '-I', '-B', str(Path(__file__).absolute()), '--execute',
                str(self.root), self.runtime_id, self.nonce, request['operation_id']], **options)
            threading.Thread(target=process.wait, daemon=True).start()
            # Keep Popen alive until child has taken its lock; only a bounded handshake.
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                result = self.lookup(request['operation_id'])
                if result['status'] != 'definitely_not_started' or process.poll() is not None:
                    break
                time.sleep(.01)
            return result if result['status'] != 'definitely_not_started' else {'status': 'running', 'receipt': None}

    def _candidate(self, operation_id):
        _require(type(operation_id) is str and re.fullmatch('candidate-operation:[0-9a-f]{64}', operation_id),
                 'Invalid candidate operation ID')
        return _safe(self.root / 'candidates' / _sha(operation_id.encode()))

    def _candidate_shape(self, request):
        _require(type(request) is dict and set(request) == {'schema_version', 'type', 'operation_id', 'requests', 'request_digest'}
                 and type(request['schema_version']) is int and request['schema_version'] == 1
                 and request['type'] == 'candidate_execution.v1', 'Invalid candidate request')
        _require(request['request_digest'] == _sha(_json({k: v for k, v in request.items() if k != 'request_digest'})),
                 'Candidate request digest mismatch')
        rows = request['requests']
        kinds = ('generate', 'compile', 'correctness', 'benchmark', 'profile')
        _require(type(rows) is list and len(rows) == len(kinds), 'Candidate requires five ordered stages')
        first = rows[0]
        _require(request['operation_id'] == 'candidate-operation:' + _sha(_json([first['job']['run_id']])),
                 'Candidate operation differs from Run')
        for row, kind in zip(rows, kinds):
            _require(type(row) is dict and row['stage'] == kind
                     and row['operation_id'] == 'operation:' + _sha(_json([first['job']['run_id'], kind])),
                     'Candidate stage order or operation identity differs')
            _require(row['request_digest'] == _sha(_json({k: v for k, v in row.items() if k != 'request_digest'})),
                     'Candidate stage checksum differs')
            _require(all(row[key] == first[key] for key in
                         ('schema_version', 'backend_id', 'job', 'candidate_identity', 'parent_identity', 'provenance')),
                     'Candidate stages mix Run, source or contract identities')
        return rows

    def _candidate_state(self, status, stages=None, cancelled=False):
        return {'status': status, 'stages': stages or [], 'cancelled': cancelled}

    def _candidate_prefix(self, request):
        stages, ended = [], False
        owner = {'operation_id': request['operation_id'], 'request_digest': request['request_digest']}
        for row in request['requests']:
            path = self._operation(row['operation_id'])
            _require(_read(path / 'request.json') == row and _read(path / 'candidate.json') == owner,
                     'Candidate stage ownership differs')
            if (path / 'completed.json').exists():
                _require(not ended, 'Candidate completion is not an ordered prefix')
                completed = self._completed(path)
                stages.append(completed)
                ended = completed['receipt']['status'] != 'succeeded'
            else:
                ended = True
        return stages

    def lookup_candidate(self, operation_id):
        if not self._ready():
            return self._candidate_state('unknown')
        path = self._candidate(operation_id)
        if not path.exists():
            return self._candidate_state('definitely_not_started')
        try:
            request = _read(path / 'request.json')
            self._candidate_shape(request)
            _require(request['operation_id'] == operation_id, 'Candidate spool identity differs')
            stages = self._candidate_prefix(request)
            dispatch = _read(path / 'dispatch.json')
            _require(set(dispatch) == {'request_digest', 'sequence'}
                     and dispatch['request_digest'] == request['request_digest']
                     and type(dispatch['sequence']) is int and dispatch['sequence'] > 0,
                     'Candidate dispatch identity differs')
            if (path / 'blocked.json').exists():
                return self._candidate_state('unknown')
            with _lock(path / 'candidate.lock') as acquired:
                if not acquired:
                    return self._candidate_state('running', stages)
                if not (path / 'started.json').exists():
                    return self._candidate_state('unknown')
                started = _read(path / 'started.json')
                _require(set(started) == {'request_digest', 'process'}
                         and started['request_digest'] == request['request_digest'], 'Candidate start identity differs')
                exited = _process_exited(started['process'])
                if exited is None:
                    return self._candidate_state('unknown')
                if not exited:
                    return self._candidate_state('running', stages)
                terminal = _read(path / 'terminal.json')
                _require(set(terminal) == {'request_digest', 'completed_operations', 'cancelled'}
                         and terminal['request_digest'] == request['request_digest']
                         and terminal['completed_operations'] == [r['receipt']['operation_id'] for r in stages]
                         and type(terminal['cancelled']) is bool, 'Candidate terminal differs from receipts')
                if terminal['cancelled']:
                    _require(_read(path / 'cancel.json') == {'request_digest': request['request_digest']},
                             'Candidate cancellation has no durable request')
                else:
                    _require(stages and (len(stages) == 5 or stages[-1]['receipt']['status'] != 'succeeded'),
                             'Candidate terminal lacks a complete or failed prefix')
                return self._candidate_state('completed', stages, terminal['cancelled'])
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            return self._candidate_state('unknown')

    def cancel_candidate(self, operation_id):
        value = self.lookup_candidate(operation_id)
        if value['status'] in ('definitely_not_started', 'completed', 'unknown'):
            return value
        path = self._candidate(operation_id)
        request = _read(path / 'request.json')
        _publish(path / 'cancel.json', _json({'request_digest': request['request_digest']}))
        return self.lookup_candidate(operation_id)

    def submit_candidate(self, request, payload, runner_path):
        rows = self._candidate_shape(request)
        decoded = self._validate(rows[0], payload, runner_path)
        path = self._candidate(request['operation_id'])
        with _lock(self.root / 'submission.lock', blocking=True):
            if path.exists():
                _require(_read(path / 'request.json') == request, 'Candidate request reused with changed identity')
                return self.lookup_candidate(request['operation_id'])
            _require(all(not self._operation(row['operation_id']).exists() for row in rows),
                     'Candidate stage already belongs to another execution')
            path.mkdir(parents=True)
            _publish(path / 'request.json', _json(request))
            _publish(path / 'payload.json', _json(payload))
            _publish(path / 'runner.py', _safe(runner_path).read_bytes())
            owner = {'operation_id': request['operation_id'], 'request_digest': request['request_digest']}
            for row in rows:
                stage_path = self._operation(row['operation_id'])
                _publish(stage_path / 'candidate.json', _json(owner))
                _publish(stage_path / 'request.json', _json(row))
            for sha, data in decoded.items():
                _publish(self.root / 'blobs' / sha, data)
            _publish(path / 'dispatch.json', _json({'request_digest': request['request_digest'],
                'sequence': len(list(path.parent.iterdir()))}))
            options = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL,
                       'close_fds': True, 'env': {key: value for key, value in os.environ.items() if key != 'PYTHONPATH'}}
            if os.name == 'nt':
                options['creationflags'] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                options['start_new_session'] = True
            process = subprocess.Popen([sys.executable, '-I', '-B', str(Path(__file__).absolute()), '--execute-candidate',
                str(self.root), self.runtime_id, self.nonce, request['operation_id']], **options)
            threading.Thread(target=process.wait, daemon=True).start()
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                state = self.lookup_candidate(request['operation_id'])
                if state['status'] != 'unknown' or process.poll() is not None:
                    return state
                time.sleep(.01)
            return self._candidate_state('running') if process.poll() is None else self.lookup_candidate(request['operation_id'])

    def _candidate_barrier(self, current=None):
        try:
            # Publication exposes directories before request/dispatch. Snapshot
            # under its lock, but never hold it across liveness/terminal waits.
            with _lock(self.root / 'submission.lock', blocking=True):
                root = self.root / 'candidates'
                if not root.exists():
                    return False
                candidates, current_sequence = [], None
                for path in root.iterdir():
                    request = _read(path / 'request.json')
                    self._candidate_shape(request)
                    _require(self._candidate(request['operation_id']) == path, 'Candidate spool identity differs')
                    dispatch = _read(path / 'dispatch.json')
                    _require(set(dispatch) == {'request_digest', 'sequence'}
                             and dispatch['request_digest'] == request['request_digest']
                             and type(dispatch['sequence']) is int and dispatch['sequence'] > 0,
                             'Candidate dispatch identity differs')
                    if path == current:
                        current_sequence = dispatch['sequence']
                    else:
                        candidates.append((path, request, dispatch['sequence']))
                _require(current is None or current_sequence is not None, 'Missing current candidate dispatch')
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            return True
        for path, request, sequence in candidates:
            try:
                if not (path / 'runtime-started.json').exists():
                    if current is not None and sequence > current_sequence:
                        continue
                    if self.lookup_candidate(request['operation_id'])['status'] != 'running':
                        return True
                    continue
                while True:
                    status = self.lookup_candidate(request['operation_id'])['status']
                    if status == 'completed':
                        break
                    if status != 'running' or not (path / 'terminal.json').exists():
                        return True
                    time.sleep(.05)
            except (OSError, ValueError, KeyError, TypeError):
                return True
        return False

    def _candidate_stage(self, request, payload, output_dir, output_root, runner_factory, run):
        path = self._operation(request['operation_id'])
        _publish(path / 'intent.json', _json({'request_digest': request['request_digest'], 'started_ns': time.time_ns()}))
        output_dir.mkdir(parents=True, exist_ok=True)
        before = observe_device_owners()
        _publish(output_dir / 'device-before.json', _json(before))
        lease = {'runtime_session_id': self.runtime_id, 'operation_id': request['operation_id'],
                 'owner_run_id': request['job']['run_id'], 'exclusive_worker': True,
                 'interference_observed': before['status'] != 'clear_visible_processes',
                 'started_ns': time.time_ns(), 'finished_ns': 0}
        artifacts, status = [], 'succeeded'
        try:
            if request['stage'] == 'generate':
                workspace, _, _ = self._materialize(request, payload)
                outputs = {'workspace': str(workspace), **request['candidate_identity'],
                           'source_bundle_digest': request['job']['variant']['source_bundle_ref']['digest']}
            else:
                if before['status'] == 'interference' or (request['stage'] in ('benchmark', 'profile')
                                                         and before['status'] != 'clear_visible_processes'):
                    raise RuntimeError('TPU process ownership cannot establish a controlled measurement')
                with _capture(output_dir):
                    if run is None:
                        run = runner_factory()
                    outputs = run.run_stage(request, output_dir, lease=lease)
                _require(type(outputs) is dict, 'Runner returned no result object')
                for paths_key, refs_key in (('raw_artifact_paths', 'raw_artifact_refs'), ('raw_xplane_paths', 'raw_xplane_refs')):
                    if paths_key in outputs:
                        entries = [self._artifact(p, output_root) for p in outputs.pop(paths_key)]
                        artifacts.extend(entries)
                        outputs[refs_key] = [entry['ref'] for entry in entries]
                if 'report_path' in outputs:
                    entry = self._artifact(outputs.pop('report_path'), output_root)
                    artifacts.append(entry)
                    outputs['report_ref'] = entry['ref']
                if request['stage'] == 'profile':
                    outputs['capture_operation_id'] = request['operation_id']
                    outputs['benchmark_operation_id'] = 'operation:' + _sha(_json([request['job']['run_id'], 'benchmark']))
                if request['stage'] == 'correctness' and outputs.get('correctness') == 'failed':
                    status = 'failed'
            after = observe_device_owners()
            _publish(output_dir / 'device-after.json', _json(after))
            lease['finished_ns'] = time.time_ns()
            lease['interference_observed'] |= after['status'] != 'clear_visible_processes'
            if 'lease' in outputs:
                outputs['lease'] = lease
            if request['stage'] in ('benchmark', 'profile') and lease['interference_observed']:
                status, outputs = 'invalid', {'error_type': 'InterferenceUnknown',
                    'message': 'TPU process visibility/ownership was not clear before and after measurement'}
        except Exception as exc:
            status = 'invalid' if before['status'] != 'clear_visible_processes' and request['stage'] in ('benchmark', 'profile') else 'failed'
            outputs = {'error_type': 'InterferenceUnknown' if status == 'invalid' else type(exc).__name__,
                       'message': str(exc)[:2000] or 'Execution failed'}
        for raw in sorted(output_root.rglob('*')):
            try:
                _safe(raw)
                if raw.is_file():
                    artifacts.append(self._artifact(raw, output_root))
            except (OSError, ValueError) as exc:
                status, outputs = 'invalid', {'error_type': type(exc).__name__,
                                            'message': 'Unable to safely archive a runner output'}
        artifacts = list({entry['ref']['digest']: entry for entry in artifacts}.values())
        receipt = {key: request[key] for key in ('schema_version', 'backend_id', 'operation_id', 'request_digest',
                                               'stage', 'candidate_identity', 'parent_identity', 'provenance')}
        receipt.update({key: request['job'][key] for key in ('contract_digest', 'run_id', 'candidate_id', 'parent_id', 'context_id')})
        receipt.update(status=status, outputs=outputs,
                       cost=request['job']['variant']['cost'] if request['stage'] == 'generate'
                       else {'executions': 0, 'tokens': 0, 'time_s': 0}, artifact_refs=[entry['ref'] for entry in artifacts])
        completed = {'receipt': receipt, 'artifacts': artifacts}
        _publish(path / 'completed.json', _json({**completed, 'completion_digest': _sha(_json(completed))}))
        return status, run

    def execute_candidate(self, operation_id):
        path = self._candidate(operation_id)
        if not self._ready():
            return
        with _lock(path / 'candidate.lock', blocking=True):
            if (path / 'started.json').exists() or (path / 'terminal.json').exists():
                return
            request, payload = _read(path / 'request.json'), _read(path / 'payload.json')
            rows = self._candidate_shape(request)
            self._validate(rows[0], payload, path / 'runner.py')
            state, identity = _process_identity(os.getpid())
            _require(state == 'alive', 'Cannot pin worker process identity')
            _publish(path / 'started.json', _json({'request_digest': request['request_digest'], 'process': identity}))
            with _lock(self.coordinator / 'runtime.lock', blocking=True):
                if self._candidate_barrier(path):
                    _publish(path / 'blocked.json', _json({'reason': 'Unresolved candidate execution'}))
                    return
                owned = {self._operation(row['operation_id']) for row in rows}
                for other in (self.root / 'operations').iterdir():
                    if other in owned or not (other / 'intent.json').exists():
                        continue
                    try:
                        self._completed(other)
                    except (OSError, ValueError, KeyError, TypeError):
                        _publish(path / 'blocked.json', _json({'reason': 'Unresolved previous operation'}))
                        return
                _publish(path / 'runtime-started.json', _json({'request_digest': request['request_digest']}))
                output_root = _safe(path / 'output')
                output_root.mkdir(exist_ok=True)
                workspace, oracle, config = self._materialize(rows[0], payload)
                def factory():
                    spec = importlib.util.spec_from_file_location('_rsi_candidate_runner', path / 'runner.py')
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[spec.name] = module
                    spec.loader.exec_module(module)
                    return module.CandidateRun(rows, workspace, oracle, output_root,
                        source_bundle=payload['source_bundle'], oracle_bundle=payload['oracle_bundle'],
                        config_path=config, runtime_session_id=self.runtime_id)
                run, completed, cancelled = None, [], False
                for row in rows:
                    if (path / 'cancel.json').exists():
                        _require(_read(path / 'cancel.json') == {'request_digest': request['request_digest']},
                                 'Candidate cancel identity differs')
                        cancelled = True
                        break
                    status, run = self._candidate_stage(row, payload, output_root / row['stage'], output_root, factory, run)
                    completed.append(row['operation_id'])
                    if status != 'succeeded':
                        break
                run = None
                _publish(path / 'terminal.json', _json({'request_digest': request['request_digest'],
                    'completed_operations': completed, 'cancelled': cancelled}))

    def _materialize(self, request, payload):
        variant = request['job']['variant']
        candidate_key = _sha(_json([request['job']['task_id'], request['job']['candidate_id'], variant['source_bundle_ref'], variant['config_ref']]))
        workspace = _safe(self.root / 'workspaces' / candidate_key / 'source')
        oracle = _safe(self.root / 'oracles' / request['job']['contract']['context']['protocol']['oracle_bundle_ref']['digest'])
        for base, bundle in ((workspace, payload['source_bundle']), (oracle, payload['oracle_bundle'])):
            base.mkdir(parents=True, exist_ok=True)
            for item in bundle['files']:
                # Copy bytes, never hard-link a writable candidate to a trusted blob.
                raw = (self.root / 'blobs' / item['artifact_ref']['digest']).read_bytes()
                _require(_sha(raw) == item['artifact_ref']['digest'], 'Input blob changed')
                _publish(base / item['path'], raw)
        config = workspace.parent / 'config.json'
        _publish(config, (self.root / 'blobs' / variant['config_ref']['digest']).read_bytes())
        return workspace, oracle, config

    def _artifact(self, path, allowed):
        path = _safe(path)
        _require(path.is_relative_to(allowed), 'Runner artifact escaped operation output')
        raw = path.read_bytes(); identity = {'digest': _sha(raw), 'size': len(raw)}
        target = self.root / 'blobs' / identity['digest']
        _publish(target, raw)
        return {'ref': identity, 'path': str(target)}

    def execute(self, operation_id):
        path = self._operation(operation_id)
        if not self._ready():
            return
        # lookup briefly takes this same lock to determine liveness. A new child
        # must wait for that observation instead of mistaking it for a worker.
        with _lock(path / 'operation.lock', blocking=True) as acquired:
            if not acquired or (path / 'completed.json').exists() or (path / 'intent.json').exists():
                return
            with _lock(self.coordinator / 'runtime.lock', blocking=True):
                if self._candidate_barrier():
                    _publish(path / 'blocked.json', _json({'reason': 'Unresolved candidate execution'}))
                    return
                # An earlier started operation without a valid receipt may still have
                # external work. Never free that uncertainty by merely taking a lock.
                for other in (self.root / 'operations').iterdir():
                    if other == path or not (other / 'intent.json').exists():
                        continue
                    try:
                        self._completed(other)
                    except (OSError, ValueError, KeyError, TypeError):
                        _publish(path / 'blocked.json', _json({'reason': 'Unresolved previous operation'}))
                        return
                request = _read(path / 'request.json'); payload = _read(path / 'payload.json')
                self._validate(request, payload, path / 'runner.py')
                _publish(path / 'intent.json', _json({'request_digest': request['request_digest'], 'started_ns': time.time_ns()}))
                output_dir = _safe(path / 'output'); output_dir.mkdir(exist_ok=True)
                before = observe_device_owners()
                _publish(output_dir / 'device-before.json', _json(before))
                lease = {'runtime_session_id': self.runtime_id, 'operation_id': operation_id,
                         'owner_run_id': request['job']['run_id'], 'exclusive_worker': True,
                         'interference_observed': before['status'] != 'clear_visible_processes',
                         'started_ns': time.time_ns(), 'finished_ns': 0}
                artifacts = []
                status = 'succeeded'
                try:
                    workspace, oracle, config = self._materialize(request, payload)
                    if request['stage'] == 'generate':
                        outputs = {'workspace': str(workspace), **request['candidate_identity'],
                                   'source_bundle_digest': request['job']['variant']['source_bundle_ref']['digest']}
                    else:
                        if before['status'] == 'interference' or (request['stage'] in ('benchmark', 'profile') and before['status'] != 'clear_visible_processes'):
                            raise RuntimeError('TPU process ownership cannot establish a controlled measurement')
                        with _capture(output_dir):
                            spec = importlib.util.spec_from_file_location('_rsi_pinned_runner', path / 'runner.py')
                            module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module
                            spec.loader.exec_module(module)
                            outputs = module.run_mla_stage(request, workspace, oracle, output_dir,
                                source_bundle=payload['source_bundle'], oracle_bundle=payload['oracle_bundle'],
                                config_path=config, runtime_session_id=self.runtime_id, lease=lease, environment=None)
                        _require(type(outputs) is dict, 'Runner returned no result object')
                        for paths_key, refs_key in (('raw_artifact_paths', 'raw_artifact_refs'), ('raw_xplane_paths', 'raw_xplane_refs')):
                            if paths_key in outputs:
                                entries = [self._artifact(p, output_dir) for p in outputs.pop(paths_key)]
                                artifacts.extend(entries); outputs[refs_key] = [e['ref'] for e in entries]
                        if 'report_path' in outputs:
                            entry = self._artifact(outputs.pop('report_path'), output_dir)
                            artifacts.append(entry); outputs['report_ref'] = entry['ref']
                        if request['stage'] == 'profile':
                            outputs['capture_operation_id'] = operation_id
                            outputs['benchmark_operation_id'] = 'operation:' + _sha(_json([request['job']['run_id'], 'benchmark']))
                        if request['stage'] == 'correctness' and outputs.get('correctness') == 'failed':
                            status = 'failed'
                    after = observe_device_owners()
                    _publish(output_dir / 'device-after.json', _json(after))
                    lease['finished_ns'] = time.time_ns()
                    lease['interference_observed'] |= after['status'] != 'clear_visible_processes'
                    if 'lease' in outputs:
                        outputs['lease'] = lease
                    if request['stage'] in ('benchmark', 'profile') and lease['interference_observed']:
                        status, outputs = 'invalid', {'error_type': 'InterferenceUnknown', 'message': 'TPU process visibility/ownership was not clear before and after measurement'}
                except Exception as exc:
                    status = 'invalid' if before['status'] != 'clear_visible_processes' and request['stage'] in ('benchmark', 'profile') else 'failed'
                    outputs = {'error_type': 'InterferenceUnknown' if status == 'invalid' else type(exc).__name__,
                               'message': str(exc)[:2000] or 'Execution failed'}
                # Retain failure traces, IR, timing details, inputs and logs even
                # when the stage never produced a successful typed output.
                for raw_output in sorted(output_dir.rglob('*')):
                    try:
                        _safe(raw_output)
                        if raw_output.is_file():
                            artifacts.append(self._artifact(raw_output, output_dir))
                    except (OSError, ValueError) as exc:
                        status, outputs = 'invalid', {'error_type': type(exc).__name__, 'message': 'Unable to safely archive a runner output'}
                # Unique artifact descriptors, preserving raw output order.
                artifacts = list({item['ref']['digest']: item for item in artifacts}.values())
                job = request['job']
                receipt = {name: request[name] for name in ('schema_version', 'backend_id', 'operation_id', 'request_digest', 'stage', 'candidate_identity', 'parent_identity', 'provenance')}
                receipt.update({name: job[name] for name in ('contract_digest', 'run_id', 'candidate_id', 'parent_id', 'context_id')})
                receipt.update(status=status, outputs=outputs, cost=job['variant']['cost'] if request['stage'] == 'generate' else {'executions': 0, 'tokens': 0, 'time_s': 0})
                receipt['artifact_refs'] = [item['ref'] for item in artifacts]
                completion = {'receipt': receipt, 'artifacts': artifacts}
                _publish(path / 'completed.json', _json({**completion, 'completion_digest': _sha(_json(completion))}))


def run_command(command):
    worker = WorkerSpool(command['root'], command['runtime_id'], command['nonce'])
    if command['action'] == 'bootstrap':
        return worker.bootstrap()
    if command['action'] == 'lookup':
        return worker.lookup(command['operation_id'])
    if command['action'] == 'submit':
        payload = json.loads(_safe(command['payload_path']).read_bytes())
        return worker.submit(command['request'], payload, command['runner_path'])
    if command['action'] == 'submit_candidate':
        payload = json.loads(_safe(command['payload_path']).read_bytes())
        return worker.submit_candidate(command['request'], payload, command['runner_path'])
    if command['action'] == 'lookup_candidate':
        return worker.lookup_candidate(command['operation_id'])
    if command['action'] == 'cancel_candidate':
        return worker.cancel_candidate(command['operation_id'])
    raise ValueError('Unsupported worker action')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--execute':
        WorkerSpool(*sys.argv[2:5]).execute(sys.argv[5])
    elif len(sys.argv) > 1 and sys.argv[1] == '--execute-candidate':
        WorkerSpool(*sys.argv[2:5]).execute_candidate(sys.argv[5])
    else:
        print(_json(run_command(json.load(sys.stdin))).decode('utf-8'))
