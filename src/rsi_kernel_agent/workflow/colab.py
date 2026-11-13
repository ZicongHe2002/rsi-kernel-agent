"""Real execution adapter sharing the original request/receipt and Store APIs."""
import base64
from dataclasses import asdict
import json

from ..contracts import Decision, canonical_json
from .colab_bridge import BridgeUnavailable
from .execution import (_validate_request, _require, _maybe, artifact_ref, make_request,
                        stable_id, stages_for, verify_receipt)
from .real_protocol import load_bundle, validate_stage


class ColabBackend:
    """The remote transport must provide a pinned runtime and durable worker.

    ``lookup`` never guesses that an unreachable worker did not execute. Remote
    artifacts are verified and imported before the original receipt publication.
    A local completed receipt remains recoverable after the Runtime disappears.
    """
    def __init__(self, store, remote):
        self.store, self.remote = store, remote

    def _operation(self, operation_id):
        record = _maybe(self.store, stable_id('execution-request', operation_id))
        if record is None:
            return None
        _require(type(record) is Decision and record.kind == 'note'
                 and set(record.details) == {'type', 'request', 'runtime_id', 'nonce', 'remote_root'}
                 and record.details['type'] == 'colab_execution_request.v1', 'Invalid Colab operation record')
        request = _validate_request(self.store, record.details['request'])
        _require(record.target_id == request['job']['run_id'] and record.task_id == request['job']['task_id']
                 and request['operation_id'] == operation_id, 'Durable Colab operation binding mismatch')
        return record

    def _check_remote(self, request, operation=None):
        environment = request['job']['contract']['context']['environment']
        _require(request['backend_id'] == 'colab.v1' and request['provenance'] == 'real', 'Colab requires a real request')
        _require(self.remote.runtime_id == environment['runtime_session_id'], 'Colab runtime differs from fixed context')
        if operation is not None:
            expected = {'runtime_id': self.remote.runtime_id, 'nonce': self.remote.nonce, 'remote_root': self.remote.root}
            _require(all(operation.details[k] == v for k, v in expected.items()), 'Colab runtime ledger mapping changed')

    def payload(self, request):
        request = _validate_request(self.store, request)
        variant = request['job']['variant']
        oracle_ref = request['job']['contract']['context']['protocol']['oracle_bundle_ref']
        bundle = load_bundle(self.store, variant['source_bundle_ref'])
        oracle = load_bundle(self.store, oracle_ref)
        refs = [variant[name] for name in ('source_ref', 'config_ref', 'build_ref', 'source_bundle_ref')]
        refs.append(oracle_ref)
        refs.extend(item['artifact_ref'] for source in (bundle, oracle) for item in source['files'])
        blobs = {item['digest']: base64.b64encode(self.store.artifacts.read(artifact_ref(item))).decode('ascii') for item in refs}
        return {'source_bundle': bundle, 'oracle_bundle': oracle, 'blobs': blobs}

    def _local_receipt(self, request):
        record = _maybe(self.store, stable_id('execution-receipt', request['operation_id']))
        if record is None:
            return None
        ref = artifact_ref(record.details['artifact_ref'])
        body = json.loads(self.store.artifacts.read(ref))
        return verify_receipt(self.store, request, {**body, 'artifact_ref': asdict(ref)})

    def _import(self, request, state):
        _require(type(state) is dict and state.get('status') in
                 {'completed', 'running', 'definitely_not_started', 'unknown'}, 'Invalid remote operation state')
        if state['status'] != 'completed':
            _require(state.get('receipt') is None, 'Unfinished operation cannot supply a receipt')
            return {'status': state['status'], 'receipt': None}
        body = state.get('receipt')
        _require(type(body) is dict, 'Completed operation has no receipt')
        fields = {'schema_version', 'backend_id', 'operation_id', 'request_digest', 'stage', 'candidate_identity',
                  'parent_identity', 'contract_digest', 'run_id', 'candidate_id', 'parent_id', 'context_id',
                  'status', 'outputs', 'cost', 'provenance', 'artifact_refs'}
        _require(set(body) == fields, 'Invalid remote receipt fields')
        for key in ('schema_version', 'backend_id', 'operation_id', 'request_digest', 'stage',
                    'candidate_identity', 'parent_identity', 'provenance'):
            _require(canonical_json(body[key]) == canonical_json(request[key]), 'Remote receipt ' + key + ' mismatch')
        for key in ('contract_digest', 'run_id', 'candidate_id', 'parent_id', 'context_id'):
            _require(body[key] == request['job'][key], 'Remote receipt ' + key + ' mismatch')
        expected_cost = request['job']['variant']['cost'] if request['stage'] == 'generate' else {'executions': 0, 'tokens': 0, 'time_s': 0}
        _require(canonical_json(body['cost']) == canonical_json(expected_cost), 'Remote cost differs from reserved budget')
        artifacts = state.get('artifacts')
        _require(type(artifacts) is list and type(body['artifact_refs']) is list
                 and [a['ref'] for a in artifacts] == body['artifact_refs'],
                 'Remote artifact inventory differs from the durable receipt')
        for artifact in artifacts:
            ref = artifact_ref(artifact['ref'])
            try:
                self.store.artifacts.verify(ref)
            except (ValueError, OSError):
                raw = self.remote.read_artifact(artifact)
                # Verify before put: a wrong download must not be accepted by its own new digest.
                from hashlib import sha256
                _require(type(raw) is bytes and len(raw) == ref.size and sha256(raw).hexdigest() == ref.digest,
                         'Downloaded artifact digest or size mismatch')
                _require(self.store.artifacts.put(raw) == ref, 'Artifact import changed identity')
        validate_stage(self.store, request, body)
        ref = self.store.artifacts.put(canonical_json(body).encode('utf-8'))
        self.store.append(Decision(stable_id('execution-receipt', request['operation_id']), request['job']['task_id'],
            'note', request['job']['run_id'], 'Durable Colab execution receipt',
            {'type': 'execution_receipt.v1', 'operation_id': request['operation_id'],
             'request_digest': request['request_digest'], 'artifact_ref': asdict(ref)}),
            stable_id('execution-complete', request['operation_id']))
        return {'status': 'completed', 'receipt': verify_receipt(self.store, request, {**body, 'artifact_ref': asdict(ref)})}

    def lookup(self, operation_id):
        operation = self._operation(operation_id)
        if operation is not None:
            request = operation.details['request']
            receipt = self._local_receipt(request)
            if receipt is not None:
                return {'status': 'completed', 'receipt': receipt}
            self._check_remote(request, operation)
        try:
            state = self.remote.lookup(operation_id)
            if operation is None:
                # Without a durable local intent we cannot safely import an orphan result.
                if state.get('status') == 'definitely_not_started':
                    return {'status': 'definitely_not_started', 'receipt': None}
                return {'status': 'unknown', 'receipt': None}
            return self._import(operation.details['request'], state)
        except BridgeUnavailable:
            return {'status': 'unknown', 'receipt': None}

    def submit(self, request):
        request = _validate_request(self.store, request)
        op = request['operation_id']
        operation = self._operation(op)
        self._check_remote(request, operation)
        if operation is None:
            _require(self.store.run_state(request['job']['run_id'])['execution_status'] == 'running',
                     'Execution requires an active scheduled Run')
            stages = stages_for('real')
            if request['stage'] != 'generate':
                previous = stages[stages.index(request['stage']) - 1]
                result = self.lookup(stable_id('operation', request['job']['run_id'], previous))
                _require(result['status'] == 'completed' and result['receipt']['status'] == 'succeeded',
                         'Previous real execution stage has not succeeded')
                verify_receipt(self.store, make_request(self.store, request['job'], previous), result['receipt'])
            self.store.append(Decision(stable_id('execution-request', op), request['job']['task_id'], 'note',
                request['job']['run_id'], 'Pinned Colab operation before remote submission',
                {'type': 'colab_execution_request.v1', 'request': request, 'runtime_id': self.remote.runtime_id,
                 'nonce': self.remote.nonce, 'remote_root': self.remote.root}), stable_id('execution-submit', op))
        else:
            _require(canonical_json(operation.details['request']) == canonical_json(request), 'Operation reused with different request')
        state = self.lookup(op)
        if state['status'] != 'definitely_not_started':
            return state
        try:
            return self._import(request, self.remote.submit(request, self.payload(request)))
        except BridgeUnavailable:
            return {'status': 'unknown', 'receipt': None}

    def poll(self, operation_id):
        return self.lookup(operation_id)
