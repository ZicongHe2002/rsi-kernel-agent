"""Durable synthetic execution protocol; never executes supplied source code.

Operation receipts live in the original ExperimentStore. A future real backend
must implement durable lookup/idempotent submission itself; an unknown external
operation must never be interpreted as permission to execute it again.
"""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Protocol

from ..contracts import (ArtifactRef, Candidate, Decision, EvaluationContext,
                         ParentCandidate, Plan, Run, Strategy, Task, Workload,
                         canonical_json, record_to_dict, validate_record)
from ..store import StoreError

STAGES = ('generate', 'compile', 'correctness', 'benchmark')
SCENARIOS = frozenset(('success', 'generation_failure', 'compile_failure', 'correctness_failure',
                      'execution_failure', 'timeout', 'invalid_measurement', 'deferred',
                      'incomplete', 'inconclusive'))
POLICY = {'correctness': 'fixed-contract-v1', 'timing': 'confirmed-positive-finite-ms',
          'promotion': 'experiment-store-consider', 'provenance': 'synthetic'}


def backend_id(provenance):
    _require(provenance in ('synthetic', 'real'), 'Only synthetic and explicitly configured real execution are supported')
    return 'synthetic.v1' if provenance == 'synthetic' else 'colab.v1'


def stages_for(provenance):
    backend_id(provenance)
    return STAGES if provenance == 'synthetic' else (*STAGES, 'profile')


def policy_for(provenance):
    backend_id(provenance)
    return dict(POLICY) if provenance == 'synthetic' else {
        **POLICY, 'provenance': 'real', 'correctness': 'fixed-oracle-metrics-v1',
        'timing': 'raw-sample-median-synchronized-ms', 'profile': 'required-before-promotion'}


def stable_id(prefix, *parts):
    return prefix + ':' + sha256(canonical_json(parts).encode('utf-8')).hexdigest()


def _digest(value):
    return sha256(canonical_json(value).encode('utf-8')).hexdigest()


def _copy(value):
    return json.loads(canonical_json(value))


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _get(store, record_id, kind):
    record = store.get(record_id)
    _require(type(record) is kind, f'{record_id} must be {kind.__name__}')
    return record


def _maybe(store, record_id):
    try:
        return store.get(record_id)
    except StoreError as exc:
        if str(exc) == f'Missing record: {record_id}':
            return None
        raise


def execution_contract(store, context_id):
    """Return the fixed original records and deterministic evaluator policy."""
    context = _get(store, context_id, EvaluationContext)
    policy = policy_for(context.provenance)
    task = _get(store, context.task_id, Task)
    workload = _get(store, context.workload_id, Workload)
    _require(workload.task_id == task.id, 'Workload task mismatch')
    if context.provenance == 'real':
        from .real_protocol import validate_context
        validate_context(store, context, workload)
    body = {'schema_version': 1, 'task': record_to_dict(task), 'workload': record_to_dict(workload),
            'context': record_to_dict(context), 'policy': policy}
    return body, _digest(body)


def artifact_ref(value):
    _require(type(value) is dict and set(value) == {'digest', 'size'}, 'Invalid artifact reference')
    ref = ArtifactRef(**value)
    validate_record(ref)
    return ref


def validate_variant(store, variant, *, provenance='synthetic'):
    backend_id(provenance)
    fields = {'label', 'source_ref', 'config_ref', 'build_ref', 'scenario', 'latency_ms',
              'priority', 'device', 'cost'}
    if provenance == 'real':
        fields = fields - {'scenario', 'latency_ms'} | {'source_bundle_ref'}
    _require(type(variant) is dict and set(variant) == fields, 'Invalid execution variant fields')
    for name in ('label', 'device'):
        _require(type(variant[name]) is str and bool(variant[name].strip()), f'Invalid variant {name}')
    _require(type(variant['priority']) is int, 'Priority must be an integer')
    if provenance == 'synthetic':
        _require(type(variant['scenario']) is str and variant['scenario'] in SCENARIOS, 'Unknown synthetic scenario')
    for name in ('source_ref', 'config_ref', 'build_ref'):
        data = store.artifacts.read(artifact_ref(variant[name]))
        if name != 'build_ref':
            data.decode('utf-8')
    latency = variant.get('latency_ms')
    _require(latency is None or (type(latency) in (int, float) and math.isfinite(latency) and latency > 0),
             'Latency must be a finite positive number or null')
    if variant.get('scenario') in ('success', 'deferred'):
        _require(latency is not None, 'Successful synthetic execution needs latency')
    if provenance == 'real':
        from .real_protocol import validate_bundle_identity
        validate_bundle_identity(store, variant)
    cost = variant['cost']
    _require(type(cost) is dict and set(cost) == {'executions', 'tokens', 'time_s'}, 'Invalid cost fields')
    for name in ('executions', 'tokens'):
        _require(type(cost[name]) is int and cost[name] >= 0, f'Invalid {name} cost')
    _require(cost['executions'] >= 1, 'Every execution requires a positive execution budget')
    _require(type(cost['time_s']) in (int, float) and math.isfinite(cost['time_s']) and cost['time_s'] >= 0,
             'Invalid time cost')
    return _copy(variant)


def make_request(store, job, stage):
    """Bind an operation to immutable records, artifacts, and the exact contract."""
    names = ('id', 'task_id', 'round_id', 'strategy_id', 'plan_id', 'parent_id', 'context_id',
             'candidate_id', 'run_id', 'variant', 'contract', 'contract_digest')
    _require(type(job) is dict and all(name in job for name in names), 'Incomplete execution job')
    selected = {name: _copy(job[name]) for name in names}
    selected['parent_evaluation_id'] = job.get('parent_evaluation_id')
    for name in ('id', 'round_id'):
        _require(type(selected[name]) is str and bool(selected[name].strip()), f'Invalid job {name}')
    candidate = _get(store, job['candidate_id'], Candidate)
    parent = _get(store, job['parent_id'], ParentCandidate)
    plan = _get(store, job['plan_id'], Plan)
    strategy = _get(store, job['strategy_id'], Strategy)
    run = _get(store, job['run_id'], Run)
    contract, digest = execution_contract(store, job['context_id'])
    provenance = contract['context']['provenance']
    _require(stage in stages_for(provenance), 'Unknown execution stage')
    _require(canonical_json(job['contract']) == canonical_json(contract) and job['contract_digest'] == digest,
             'Execution contract differs from immutable Task/Workload/Context')
    _require(strategy.task_id == parent.task_id == contract['task']['id'] == job['task_id'], 'Job task mismatch')
    _require(plan.strategy_id == strategy.id and candidate.plan_id == plan.id and candidate.parent_id == parent.id,
             'Candidate Plan/Strategy/Parent mismatch')
    _require(run.candidate_id == candidate.id and run.context_id == job['context_id'], 'Run binding mismatch')
    if parent.candidate_id is not None:
        _require(_get(store, parent.candidate_id, Candidate).identity == parent.identity, 'Parent identity mismatch')
    variant = validate_variant(store, job['variant'], provenance=provenance)
    if provenance == 'real':
        from .real_protocol import validate_bundle_identity
        validate_bundle_identity(store, variant, contract['context']['protocol'])
        _require(variant['device'] == contract['context']['environment']['runtime_session_id'], 'Variant device differs from fixed Runtime')
    _require(candidate.identity.source_digest == variant['source_ref']['digest']
             and candidate.identity.config_digest == variant['config_ref']['digest']
             and candidate.identity.build_digest == variant['build_ref']['digest'], 'Candidate artifact identity mismatch')
    selected['variant'] = variant
    body = {'schema_version': 1, 'backend_id': backend_id(provenance),
            'operation_id': stable_id('operation', run.id, stage), 'stage': stage, 'job': selected,
            'candidate_identity': asdict(candidate.identity), 'parent_identity': asdict(parent.identity),
            'provenance': provenance}
    return {**body, 'request_digest': _digest(body)}


def _validate_request(store, request):
    _require(type(request) is dict, 'Execution request must be an object')
    try:
        expected = make_request(store, request['job'], request['stage'])
    except KeyError as exc:
        raise ValueError('Incomplete execution request') from exc
    _require(canonical_json(request) == canonical_json(expected), 'Execution request fingerprint or binding mismatch')
    return expected


def verify_receipt(store, request, receipt):
    """Verify raw bytes, durable publication, and binding before trusting outputs."""
    request = _validate_request(store, request)
    _require(type(receipt) is dict and 'artifact_ref' in receipt, 'Missing execution receipt')
    ref = artifact_ref(receipt['artifact_ref'])
    raw = store.artifacts.read(ref)
    body = json.loads(raw)
    expected_fields = {'schema_version', 'backend_id', 'operation_id', 'request_digest', 'stage',
                       'candidate_identity', 'parent_identity', 'contract_digest', 'run_id',
                       'candidate_id', 'parent_id', 'context_id', 'status', 'outputs', 'cost', 'provenance'}
    if request['provenance'] == 'real':
        expected_fields.add('artifact_refs')
    _require(type(body) is dict and set(body) == expected_fields, 'Invalid receipt fields')
    _require(raw == canonical_json(body).encode('utf-8'), 'Receipt bytes are not canonical')
    _require(canonical_json(receipt) == canonical_json({**body, 'artifact_ref': asdict(ref)}),
             'Receipt differs from its raw artifact')
    for name in ('schema_version', 'backend_id', 'operation_id', 'request_digest', 'stage',
                 'candidate_identity', 'parent_identity', 'provenance'):
        _require(canonical_json(body[name]) == canonical_json(request[name]), f'Receipt {name} mismatch')
    for name in ('contract_digest', 'run_id', 'candidate_id', 'parent_id', 'context_id'):
        _require(body[name] == request['job'][name], f'Receipt {name} mismatch')
    _require(body['status'] in ('succeeded', 'failed', 'invalid', 'incomplete', 'deferred'), 'Invalid receipt status')
    _require(type(body['outputs']) is dict, 'Invalid receipt outputs')
    if request['provenance'] == 'real':
        _require(type(body['artifact_refs']) is list, 'Missing complete real artifact inventory')
        seen = set()
        for item in body['artifact_refs']:
            bound = artifact_ref(item)
            _require(bound.digest not in seen, 'Duplicate real receipt artifact')
            seen.add(bound.digest)
            store.artifacts.verify(bound)
    published = _get(store, stable_id('execution-receipt', request['operation_id']), Decision)
    _require(published.task_id == request['job']['task_id'] and published.target_id == request['job']['run_id']
             and published.kind == 'note' and published.details == {
                 'type': 'execution_receipt.v1', 'operation_id': request['operation_id'],
                 'request_digest': request['request_digest'], 'artifact_ref': asdict(ref)},
             'Receipt does not match durable backend publication')
    return _copy(receipt)


class ExecutionBackend(Protocol):
    def lookup(self, operation_id): ...
    def submit(self, request): ...
    def poll(self, operation_id): ...


class SyntheticBackend:
    """A local durable test producer. Costs/timing/correctness are fixtures only.

    Pending polls are persisted when an operation is accepted, so rebuilding
    this Python object cannot erase progress or create a second execution.
    """
    def __init__(self, store, workspace_root, *, polls_before_completion=0):
        _require(type(polls_before_completion) is int and polls_before_completion >= 0, 'Invalid pending poll count')
        self.store = store
        supplied = Path(workspace_root).absolute()
        self._reject_links(supplied)
        supplied.mkdir(parents=True, exist_ok=True)
        self.workspace_root = supplied.resolve()
        self.polls_before_completion = polls_before_completion

    @staticmethod
    def _reject_links(path):
        for part in (path, *path.parents):
            _require(not part.is_symlink() and not (hasattr(part, 'is_junction') and part.is_junction()),
                     'Execution workspace paths cannot contain links')

    def _operation(self, operation_id):
        record = _maybe(self.store, stable_id('execution-request', operation_id))
        if record is None:
            return None
        _require(type(record) is Decision and record.kind == 'note'
                 and record.details.get('type') == 'execution_request.v1', 'Invalid durable operation record')
        _require(set(record.details) == {'type', 'request', 'polls_required', 'workspace_root'}, 'Invalid operation fields')
        _require(record.details['workspace_root'] == str(self.workspace_root), 'Backend workspace root changed')
        request = _validate_request(self.store, record.details['request'])
        _require(request['operation_id'] == operation_id and record.target_id == request['job']['run_id']
                 and record.task_id == request['job']['task_id'], 'Durable operation binding mismatch')
        return record

    def lookup(self, operation_id):
        operation = self._operation(operation_id)
        if operation is None:
            return {'status': 'definitely_not_started', 'receipt': None}
        receipt = _maybe(self.store, stable_id('execution-receipt', operation_id))
        if receipt is None:
            return {'status': 'running', 'receipt': None}
        ref = artifact_ref(receipt.details['artifact_ref'])
        body = json.loads(self.store.artifacts.read(ref))
        return {'status': 'completed', 'receipt': verify_receipt(self.store, operation.details['request'],
                                                                {**body, 'artifact_ref': asdict(ref)})}

    def submit(self, request):
        request = _validate_request(self.store, request)
        _require(request['provenance'] == 'synthetic', 'Synthetic backend cannot execute a real request')
        op = request['operation_id']
        prior = self._operation(op)
        if prior is not None:
            _require(canonical_json(prior.details['request']) == canonical_json(request), 'Operation ID reused with different request')
            return self.lookup(op)
        _require(self.store.run_state(request['job']['run_id'])['execution_status'] == 'running',
                 'Execution requires an active scheduled Run')
        stage = request['stage']
        if stage != 'generate':
            previous = STAGES[STAGES.index(stage) - 1]
            result = self.lookup(stable_id('operation', request['job']['run_id'], previous))
            _require(result['status'] == 'completed' and result['receipt']['status'] == 'succeeded',
                     'Previous execution stage has not succeeded')
            verify_receipt(self.store, make_request(self.store, request['job'], previous), result['receipt'])
            self._workspace(request, create=False)
        details = {'type': 'execution_request.v1', 'request': request,
                   'polls_required': self.polls_before_completion, 'workspace_root': str(self.workspace_root)}
        self.store.append(Decision(stable_id('execution-request', op), request['job']['task_id'], 'note',
                                   request['job']['run_id'], 'Accepted synthetic operation', details),
                          stable_id('execution-submit', op))
        if self.polls_before_completion == 0:
            return self._complete(request)
        return self.lookup(op)

    def poll(self, operation_id):
        state = self.lookup(operation_id)
        if state['status'] != 'running':
            return state
        operation = self._operation(operation_id)
        required = operation.details['polls_required']
        count = 0
        while _maybe(self.store, stable_id('execution-poll', operation_id, count + 1)) is not None:
            count += 1
        if count < required:
            count += 1
            self.store.append(Decision(stable_id('execution-poll', operation_id, count), operation.task_id, 'note',
                                       operation.target_id, 'Synthetic operation progress',
                                       {'type': 'execution_poll.v1', 'operation_id': operation_id, 'poll': count}),
                              stable_id('execution-poll-request', operation_id, count))
        if count >= required:
            return self._complete(operation.details['request'])
        return self.lookup(operation_id)

    def _workspace(self, request, *, create):
        path = self.workspace_root / sha256(request['job']['candidate_id'].encode('utf-8')).hexdigest()
        self._reject_links(path)
        if create:
            path.mkdir(parents=True, exist_ok=True)
        _require(path.is_dir(), 'Candidate workspace is missing')
        for name, field in (('source.txt', 'source_ref'), ('config.json', 'config_ref'), ('build.txt', 'build_ref')):
            target = path / name
            self._reject_links(target)
            raw = self.store.artifacts.read(artifact_ref(request['job']['variant'][field]))
            if not target.exists() and create:
                descriptor, temporary = tempfile.mkstemp(prefix='.pending-', dir=path)
                try:
                    with os.fdopen(descriptor, 'wb') as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    try:
                        os.link(temporary, target)
                    except FileExistsError:
                        pass
                finally:
                    Path(temporary).unlink(missing_ok=True)
            _require(target.is_file() and target.read_bytes() == raw, 'Candidate workspace identity changed')
        return path

    def _complete(self, request):
        if self.lookup(request['operation_id'])['status'] == 'completed':
            return self.lookup(request['operation_id'])
        stage, variant = request['stage'], request['job']['variant']
        scenario = variant['scenario']
        status, outputs = 'succeeded', {}
        if stage == 'generate':
            if scenario == 'generation_failure':
                status, outputs = 'failed', {'reason': 'Synthetic generation failure'}
            else:
                path = self._workspace(request, create=True)
                outputs = {'workspace': str(path), **request['candidate_identity']}
        elif stage == 'compile':
            self._workspace(request, create=False)
            if scenario == 'compile_failure':
                status, outputs = 'failed', {'reason': 'Synthetic compile failure'}
            else:
                outputs = {'compiled_identity': request['candidate_identity'], 'compiler': 'synthetic-v1', 'executed': False}
        elif stage == 'correctness':
            self._workspace(request, create=False)
            status = 'failed' if scenario == 'correctness_failure' else 'succeeded'
            outputs = {'correctness': 'failed' if status == 'failed' else 'passed',
                       'contract_digest': request['job']['contract_digest'], 'cases': 1, 'synthetic': True}
        else:
            self._workspace(request, create=False)
            if scenario in ('execution_failure', 'timeout', 'incomplete'):
                status = 'failed' if scenario == 'execution_failure' else 'incomplete'
                outputs = {'reason': 'Synthetic ' + scenario}
            else:
                validity = 'invalid' if scenario == 'invalid_measurement' else 'unknown' if scenario == 'inconclusive' else 'valid'
                status = 'invalid' if validity == 'invalid' else 'succeeded'
                latency = variant['latency_ms'] if validity == 'valid' else None
                outputs = {'latency_ms': latency, 'unit': 'ms', 'timing_validity': validity,
                           'confirmed': validity == 'valid', 'reason': 'Synthetic ' + scenario,
                           'profile_ref': None}
                if validity == 'valid':
                    _require(latency is not None, 'Valid benchmark requires synthetic latency')
                    profile = {'provenance': 'synthetic', 'run_id': request['job']['run_id'],
                               'candidate_id': request['job']['candidate_id'], 'context_id': request['job']['context_id'],
                               'candidate_identity': request['candidate_identity'], 'contract_digest': request['job']['contract_digest'],
                               'time_source': 'TPU XPlane device_duration_ps', 'unit': 'us',
                               'profile': 'synthetic-fixture-no-hardware.xplane.pb', 'calls': 1,
                               'devices': [variant['device']], 'longest_device_per_call': [],
                               'multi_device_span': {'status': 'unavailable', 'reason': 'Synthetic single device fixture'},
                               'errors': [], 'warnings': [],
                               'groups': [{'kind': 'program', 'name': 'synthetic_kernel', 'plane': variant['device'],
                                           'line': 'XLA Modules', 'line_id': 1, 'program_id': 1, 'count': 1,
                                           'mean_us': latency * 1000, 'median_us': latency * 1000,
                                           'p95_us': latency * 1000, 'min_us': latency * 1000, 'max_us': latency * 1000,
                                           'events_per_measured_call': 1, 'invalid_samples': 0,
                                           'samples': [{'duration_ps': latency * 1_000_000_000,
                                                        'device_offset_ps': 0, 'step_num': 0, 'run_id': 1}]}]}
                    outputs['profile_ref'] = asdict(self.store.artifacts.put(canonical_json(profile).encode('utf-8')))
        # Generation consumes the predeclared logical operation cost once.
        # Later stage receipts have zero additional fixture cost. These are not
        # provider token statistics or measured hardware utilization.
        cost = variant['cost'] if stage == 'generate' else {'executions': 0, 'tokens': 0, 'time_s': 0}
        job = request['job']
        body = {name: request[name] for name in ('schema_version', 'backend_id', 'operation_id',
                                                'request_digest', 'stage', 'candidate_identity',
                                                'parent_identity', 'provenance')}
        body.update({name: job[name] for name in ('contract_digest', 'run_id', 'candidate_id', 'parent_id', 'context_id')})
        body.update(status=status, outputs=outputs, cost=cost)
        ref = self.store.artifacts.put(canonical_json(body).encode('utf-8'))
        self.store.append(Decision(stable_id('execution-receipt', request['operation_id']), job['task_id'], 'note',
                                   job['run_id'], 'Durable synthetic execution receipt',
                                   {'type': 'execution_receipt.v1', 'operation_id': request['operation_id'],
                                    'request_digest': request['request_digest'], 'artifact_ref': asdict(ref)}),
                          stable_id('execution-complete', request['operation_id']))
        return self.lookup(request['operation_id'])
