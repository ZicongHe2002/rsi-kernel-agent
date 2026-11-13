"""Host entry for one trace-mode capability probe; no official MLA Run.

prepare is local. submit/poll/collect use the existing bridge and isolated
runtime only after explicit host launch; this file has no import-time effects.
"""
import argparse
import base64
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.contracts import ArtifactRef, Decision, canonical_json
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.workflow.__main__ import colab_remote
from rsi_kernel_agent.workflow.colab_bridge import BridgeUnavailable, extract_json_result
from rsi_kernel_agent.workflow.colab_worker import _publish, _json
from rsi_kernel_agent.workflow.execution import stable_id
from rsi_kernel_agent.workflow.journal import WorkflowJournal

SCOPE = 'minimal JAX trace-mode capability only; not MLA, benchmark, or optimization'
MODES = ['TRACE_ONLY_HOST', 'TRACE_ONLY_XLA', 'TRACE_COMPUTE', 'TRACE_COMPUTE_AND_SYNC']


def prepare(work, config_path, environment_path, task_id):
    store = ExperimentStore(work / 'experiments.sqlite3', work / 'artifacts')
    store.get(task_id)
    remote = colab_remote(config_path)  # Constructs transport, does not connect.
    if not remote.remote_python:
        raise ValueError('Diagnostic requires the explicitly pinned isolated interpreter')
    environment = json.loads(environment_path.read_bytes())
    if environment['runtime_session_id'] != remote.runtime_id:
        raise ValueError('Observed environment differs from configured runtime')
    source = (work / 'trace_modes_remote.py').read_bytes()
    source_ref = asdict(store.artifacts.put(source))
    body = {'schema_version': 1, 'scope': SCOPE, 'task_id': task_id,
            'runtime_id': remote.runtime_id, 'nonce': remote.nonce, 'remote_root': remote.root,
            'remote_python': remote.remote_python, 'source_digest': source_ref['digest'],
            'source_ref': source_ref, 'modes': MODES, 'expected_environment': environment,
            'worker_path': remote.worker_remote_path, 'worker_digest': remote.worker_digest,
            'runner_path': remote.runner_remote_path, 'runner_digest': remote.runner_digest}
    request = {**body, 'request_digest': sha256(_json(body)).hexdigest()}
    path = work / 'trace-mode-diagnostic-request.json'
    _publish(path, _json(request))
    marker = Decision(stable_id('trace-mode-diagnostic-intent', request['request_digest']), task_id,
        'note', task_id, 'Pinned minimal JAX trace-mode diagnostic, separate from MLA execution',
        {'type': 'trace_mode_diagnostic_host_intent.v1', 'request': request,
         'remote_calls': 0, 'is_formal_run': False})
    store.append(marker, marker.id)
    return {'status': 'prepared_locally', 'request_digest': request['request_digest'],
            'source_ref': source_ref, 'scope': SCOPE}


def load(work, config_path):
    store = ExperimentStore(work / 'experiments.sqlite3', work / 'artifacts')
    request = json.loads((work / 'trace-mode-diagnostic-request.json').read_bytes())
    body = {k: v for k, v in request.items() if k != 'request_digest'}
    if sha256(_json(body)).hexdigest() != request['request_digest'] or request['scope'] != SCOPE:
        raise ValueError('Diagnostic request differs from its pinned identity')
    marker = store.get(stable_id('trace-mode-diagnostic-intent', request['request_digest']))
    if marker.details.get('request') != request:
        raise ValueError('Diagnostic request differs from durable host intent')
    remote = colab_remote(config_path)
    if any((remote.runtime_id != request['runtime_id'], remote.nonce != request['nonce'],
            remote.root != request['remote_root'], remote.remote_python != request['remote_python'])):
        raise ValueError('Runtime/nonce/spool/interpreter differs; never remap an old diagnostic')
    return store, remote, request


def control(remote, request, action, source=None):
    script = request['remote_root'] + '/tools/trace-modes-' + request['source_digest'] + '.py'
    code = 'import subprocess, hashlib, json\nfrom pathlib import Path\n'
    if action == 'submit':
        if source is None or sha256(source).hexdigest() != request['source_digest']:
            raise ValueError('Diagnostic source bytes differ')
        # Install only task-owned content, using the unchanged worker's safe
        # fsync+exclusive publisher. This does not bootstrap the runtime spool.
        code += ('import importlib.util, base64\n'
                 f'_w=Path({request["worker_path"]!r})\n'
                 f'assert hashlib.sha256(_w.read_bytes()).hexdigest()=={request["worker_digest"]!r}\n'
                 '_s=importlib.util.spec_from_file_location("_trace_installer",_w)\n'
                 '_m=importlib.util.module_from_spec(_s); _s.loader.exec_module(_m)\n'
                 f'_m._publish(Path({script!r}),base64.b64decode({base64.b64encode(source).decode()!r}))\n')
    command = {'action': action, 'request': request}
    code += (f'_p=Path({script!r})\n'
             f'assert hashlib.sha256(_p.read_bytes()).hexdigest()=={request["source_digest"]!r}\n'
             f'_r=subprocess.run([{request["remote_python"]!r},"-I","-B",str(_p)],'
             f'input={json.dumps(command)!r},capture_output=True,text=True,timeout=45)\n'
             'assert _r.returncode==0, "diagnostic control failed; outcome unknown"\n'
             'print("RSI_RESULT:"+json.dumps(json.loads(_r.stdout),allow_nan=False))\n')
    try:
        return extract_json_result(remote.bridge.execute(code))
    except BridgeUnavailable:
        return {'status': 'unknown', 'receipt': None, 'reason': 'Bridge unavailable; no automatic relaunch'}


def collect(store, remote, request, state, work):
    if state.get('status') != 'completed':
        return state
    receipt = state['receipt']
    body = {k: v for k, v in receipt.items() if k != 'receipt_digest'}
    if (receipt.get('receipt_digest') != sha256(_json(body)).hexdigest()
            or receipt.get('request_digest') != request['request_digest']
            or receipt.get('source_digest') != request['source_digest']
            or receipt.get('type') != 'trace_mode_diagnostic_receipt.v1'
            or receipt.get('runtime_id') != request['runtime_id'] or receipt.get('task_id') != request['task_id']
            or receipt.get('scope') != SCOPE or receipt.get('formal_run_id') is not None):
        raise ValueError('Diagnostic receipt identity mismatch')
    inventory = []
    for item in receipt['artifacts']:
        ref = ArtifactRef(**item['ref'])
        try:
            store.artifacts.verify(ref)
        except (ValueError, OSError):
            raw = remote.read_artifact({'path': item['path'], 'ref': item['ref']})
            if store.artifacts.put(raw) != ref:
                raise ValueError('Diagnostic artifact download changed identity')
        inventory.append({'relative_path': item['relative_path'], 'artifact_ref': item['ref']})
    receipt_ref = asdict(store.artifacts.put(_json(receipt)))
    report = {'type': 'trace_mode_diagnostic_collection.v1', 'scope': SCOPE,
              'request_digest': request['request_digest'], 'receipt_ref': receipt_ref,
              'artifacts': inventory, 'status': receipt['status'], 'is_formal_run': False,
              'formal_run_id': None, 'promotion': None}
    marker = Decision(stable_id('trace-mode-diagnostic-complete', request['request_digest']),
                      request['task_id'], 'note', request['task_id'],
                      'Collected standalone JAX trace capability evidence; no MLA performance claim', report)
    store.append(marker, marker.id)
    _publish(work / 'trace-mode-diagnostic-collected.json', _json(report))
    return {**report, 'guard_cleanup_pending': state.get('guard_cleanup_pending', False)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'submit', 'poll', 'collect'))
    parser.add_argument('--config', type=Path)
    parser.add_argument('--environment', type=Path)
    parser.add_argument('--task', default='mla-decode-v1')
    args = parser.parse_args()
    work = Path(__file__).resolve().parent
    config = args.config or work / 'colab-config-runtime2.json'
    environment = args.environment or ROOT / 'knowledge-site/manifests/phase3-2026-11-12/environment-isolated-runtime2.json'
    if args.action == 'prepare':
        result = prepare(work, config, environment, args.task)
    else:
        store, remote, request = load(work, config)
        with WorkflowJournal(store, request['task_id']).lock():
            source = store.artifacts.read(ArtifactRef(**request['source_ref'])) if args.action == 'submit' else None
            state = control(remote, request, 'submit' if args.action == 'submit' else 'poll', source)
            result = collect(store, remote, request, state, work) if args.action == 'collect' else state
    print(canonical_json(result), flush=True)


if __name__ == '__main__':
    main()
