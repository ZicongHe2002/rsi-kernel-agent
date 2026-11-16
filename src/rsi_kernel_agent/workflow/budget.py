"""Explicit cumulative grants in the existing append-only workflow journal.

Authorization is an archived user assertion, not cryptographic authentication.
Milestones recheck fixed evaluator records and small bound receipts; this does
not rerun hardware or independently recompute NPZ/XPlane measurements.
"""
from hashlib import sha256
import json

from ..contracts import (ArtifactRef, Decision, EvaluationBundle, EvaluationContext,
                         Evidence, ProfileBundle, Run, canonical_json)
from .scheduling import remaining_budget, validate_limits


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value):
    return sha256(canonical_json(value).encode('utf-8')).hexdigest()


def grants(state):
    commands = state.get('commands', {})
    require(type(commands) is dict, 'Invalid workflow commands')
    return {key: value for key, value in commands.items() if key.startswith('budget:')}


def _text(value):
    return type(value) is str and bool(value.strip())


def _grant_shape(grant, before):
    fields = {'schema_version', 'task_id', 'expected_revision', 'expected_state_digest',
              'old_limits', 'new_limits', 'spent', 'next_batch', 'authorization', 'milestone'}
    require(type(grant) is dict and set(grant) == fields and type(grant['schema_version']) is int
            and grant['schema_version'] == 1, 'Invalid budget grant fields')
    require(grant['task_id'] == before['task_id'] and type(grant['expected_revision']) is int
            and grant['expected_revision'] == before['revision']
            and grant['expected_state_digest'] == digest(before), 'Stale budget grant state')
    if 'limits' in before:
        validate_limits(before['limits'])
    old, new = validate_limits(grant['old_limits']), validate_limits(grant['new_limits'])
    require(old == grant['old_limits'] == before['limits'] and new == grant['new_limits']
            and canonical_json(grant['spent']) == canonical_json(before['spent']),
            'Budget grant changes prior limits or spending')
    require(all(new[k] == old[k] for k in old if k not in ('executions', 'time_s'))
            and new['executions'] > old['executions'] and new['time_s'] > old['time_s'],
            'Only positive execution and time additions are permitted')
    remaining_budget(before)
    batch = grant['next_batch']
    require(type(batch) is dict and set(batch) == {'round_id', 'purpose', 'cost'}
            and _text(batch['round_id']) and _text(batch['purpose']), 'Invalid next budget batch')
    expected = {'executions': new['executions'] - old['executions'], 'tokens': 0,
                'time_s': new['time_s'] - old['time_s']}
    require(type(batch['cost']) is dict and canonical_json(batch['cost']) == canonical_json(expected),
            'Budget addition must equal the declared next batch cost')
    auth = grant['authorization']
    require(type(auth) is dict and set(auth) == {'source', 'quote', 'artifact_ref'}
            and _text(auth['source']) and _text(auth['quote']), 'Budget authorization source is required')
    milestone = grant['milestone']
    require(type(milestone) is dict and set(milestone) == {'snapshot', 'rounds', 'successful_round_id'}
            and type(milestone['rounds']) is list and milestone['rounds'], 'Invalid budget milestone')
    ids = []
    for row in milestone['rounds']:
        require(type(row) is dict and set(row) == {'round_id', 'context_id', 'run_ids'}
                and _text(row['round_id']) and _text(row['context_id'])
                and type(row['run_ids']) is list and row['run_ids']
                and all(_text(rid) for rid in row['run_ids'])
                and len(set(row['run_ids'])) == len(row['run_ids']), 'Invalid milestone round')
        ids.append(row['round_id'])
    require(len(set(ids)) == len(ids) and milestone['successful_round_id'] in ids
            and batch['round_id'] not in ids, 'Duplicate or missing milestone round')
    old_grants = grants(before)
    if old_grants:
        last_entry = max(old_grants.values(), key=lambda item: item['sequence'])
        require(last_entry['grant']['next_batch']['round_id'] in ids
                and milestone['successful_round_id'] == last_entry['grant']['next_batch']['round_id'],
                'Each grant must close the immediately preceding declared batch')
    for entry in old_grants.values():
        prior = entry['grant']
        used = {r['round_id'] for r in prior['milestone']['rounds']}
        require(not used.intersection(ids)
                and prior['next_batch']['round_id'] != batch['round_id'],
                'Completed milestone or next batch was already used by a budget grant')
    return new


class _SnapshotRecords:
    """Read-only dependencies pinned to one verified original Store snapshot."""
    def __init__(self, store, records):
        self.artifacts = store.artifacts
        self.records = {record.id: record for record in records}

    def get(self, identity):
        require(identity in self.records, 'Budget milestone dependency is outside its Snapshot')
        return self.records[identity]


def _milestone(store, before, grant):
    from .evaluation import _verified_prefix
    from .execution import make_request, stable_id
    auth = grant['authorization']
    require(auth['quote'] in store.artifacts.read(ArtifactRef(**auth['artifact_ref'])).decode('utf-8'),
            'Budget authorization quote is absent from its archived source')
    require(before.get('cancelled') is False and not any(
        job['state'] in ('active', 'evaluating', 'blocked') for job in before['jobs'].values()),
        'Budget grant requires an uncancelled Task without unresolved operations')
    milestone = grant['milestone']
    metadata = milestone['snapshot']
    require(type(metadata) is dict and metadata.get('task_id') == before['task_id']
            and type(metadata.get('record_count')) is int and metadata['record_count'] > 0,
            'Invalid milestone Snapshot metadata')
    page = store.snapshot_page(metadata['id'], limit=metadata['record_count'])
    require(page['snapshot'] == metadata and page['complete']
            and page['total'] == metadata['record_count'], 'Milestone Snapshot identity or coverage changed')
    frozen = _SnapshotRecords(store, page['records'])
    states = [r for r in page['records'] if type(r) is Decision
              and r.details.get('type') == 'workflow_state.v1']
    require(states and states[-1].details['state'] == before
            and states[-1].details['state_digest'] == digest(before),
            'Milestone Snapshot must contain the exact prior Controller state')
    succeeded = False
    for row in milestone['rounds']:
        jobs = [job for job in before['jobs'].values() if job['round_id'] == row['round_id']]
        require(jobs and {job['run_id'] for job in jobs} == set(row['run_ids'])
                and len(jobs) == len(row['run_ids']), 'Milestone does not bind every round attempt')
        context = frozen.get(row['context_id'])
        require(type(context) is EvaluationContext and context.task_id == before['task_id']
                and context.provenance == 'real', 'Budget milestone requires a real Context')
        for job in jobs:
            require(job['state'] in ('completed', 'failed') and not job.get('operation')
                    and job['context_id'] == context.id and job['task_id'] == before['task_id']
                    and job['reserved'] is True, 'Milestone attempt is unfinished or has a different Context')
            run = frozen.get(job['run_id'])
            require(type(run) is Run and run.context_id == context.id
                    and run.candidate_id == job['candidate_id'], 'Milestone Run binding changed')
            marker = frozen.get(stable_id('workflow-result', run.id))
            result = job['result']
            require(type(marker) is Decision and marker.kind == 'note'
                    and marker.task_id == before['task_id'] and marker.target_id == run.id
                    and marker.details == {'type': 'workflow_result.v1', 'job_id': job['id'],
                                           'result': result, 'cancelled': False}
                    and result['provenance'] == 'real' and result['run_id'] == run.id
                    and result['candidate_id'] == run.candidate_id,
                    'Milestone lacks an exact noncancelled fixed Evaluator result')
            receipts = {}
            for stage, ref in result['receipt_refs'].items():
                raw = frozen.artifacts.read(ArtifactRef(**ref))
                receipt = json.loads(raw)
                require(raw == canonical_json(receipt).encode(), 'Noncanonical milestone receipt')
                receipts[stage] = {**receipt, 'artifact_ref': ref}
                request = make_request(frozen, job, stage)
                published = frozen.get(stable_id('execution-receipt', request['operation_id']))
                require(type(published) is Decision and published.kind == 'note'
                        and published.task_id == before['task_id'] and published.target_id == run.id
                        and published.details == {'type': 'execution_receipt.v1',
                            'operation_id': request['operation_id'], 'request_digest': request['request_digest'],
                            'artifact_ref': ref}, 'Milestone receipt publication changed')
                for key in ('operation_id', 'request_digest', 'candidate_identity', 'parent_identity', 'provenance'):
                    require(receipt[key] == request[key], 'Milestone receipt identity changed')
                require(receipt['stage'] == stage and all(receipt[k] == job[k] for k in
                    ('run_id', 'candidate_id', 'parent_id', 'context_id', 'contract_digest')),
                    'Milestone receipt execution binding changed')
                evidence = frozen.get(stable_id('stage-evidence', run.id, stage))
                require(type(evidence) is Evidence and evidence.run_id == run.id
                        and evidence.value == {'stage': stage, 'status': receipt['status'],
                            'cost': receipt['cost'], 'outputs': receipt['outputs']}
                        and evidence.artifacts and evidence.artifacts[0] == ArtifactRef(**ref),
                        'Milestone receipt lacks matching fixed stage evidence')
            require('compile' in receipts and receipts['compile']['status'] in ('succeeded', 'failed'),
                    'Milestone has no real compile feedback')
            _verified_prefix(frozen, job, {s: receipts[s] for s in ('generate', 'compile')}, True)
            intent = frozen.get(stable_id('workflow-evaluation-intent', run.id))
            require(type(intent) is Decision and intent.kind == 'note' and intent.target_id == run.id
                    and intent.task_id == before['task_id'] and intent.details == {
                        'type': 'workflow_evaluation_intent.v1', 'job_id': job['id'],
                        'request_digest': make_request(frozen, job, 'generate')['request_digest'],
                        'receipt_refs': result['receipt_refs'], 'cancelled': False},
                    'Milestone differs from original Evaluator intent')
            evaluation = frozen.get(result['evaluation_id'])
            require(type(evaluation) is EvaluationBundle and evaluation.run_id == run.id
                    and all(getattr(evaluation, k) == result[k] for k in
                            ('correctness', 'timing_validity', 'latency_ms', 'confirmed')),
                    'Milestone Evaluation differs from its result')
            require(all(stable_id('stage-evidence', run.id, stage) in evaluation.evidence_ids
                        for stage in receipts), 'Milestone Evaluation omits stage evidence')
            for identity in evaluation.evidence_ids:
                evidence = frozen.get(identity)
                require(type(evidence) is Evidence and evidence.run_id == run.id,
                        'Milestone Evaluation evidence belongs to another Run')
            if (row['round_id'] == milestone['successful_round_id'] and result['confirmed'] is True
                    and result['correctness'] == 'passed' and result['timing_validity'] == 'valid'
                    and job['state'] == 'completed'):
                require(set(receipts) == {'generate', 'compile', 'correctness', 'benchmark', 'profile'}
                        and all(r['status'] == 'succeeded' for r in receipts.values()),
                        'Successful milestone needs every fixed execution stage')
                profile = frozen.get(result['profile_id'])
                require(type(profile) is ProfileBundle and profile.run_id == run.id
                        and profile.availability == 'available', 'Successful milestone needs a bound Profile')
                succeeded = True
    require(succeeded, 'Budget continuation needs a fully correct confirmed Run in its successful round')


def validate_transition(store, before, after):
    """Validate every limits transition, including callers bypassing Controller."""
    for state in (before, after):
        if state is None:
            continue
        if 'limits' in state:
            validate_limits(state['limits'])
            require('spent' in state, 'Workflow limits require spending fields')
            remaining_budget(state)
    previous, current = grants(before or {}), grants(after)
    require(all(k in current and canonical_json(current[k]) == canonical_json(v)
                for k, v in previous.items()), 'Budget grant history changed')
    added = set(current) - set(previous)
    if before is None:
        require(not current, 'Initial workflow cannot contain budget grants')
        return
    if not added:
        require(canonical_json(after.get('limits')) == canonical_json(before.get('limits')),
                'Workflow limits changed without a budget grant')
        return
    require(len(added) == 1 and 'limits' in before, 'One explicit budget grant per journal revision is required')
    command = added.pop()
    entry = current[command]
    require(type(entry) is dict and set(entry) == {'type', 'key', 'digest', 'grant',
            'sequence', 'previous_key', 'applied_revision'} and entry['type'] == 'workflow_budget_grant.v1'
            and _text(entry['key']) and command == 'budget:' + entry['key']
            and entry['digest'] == digest(entry['grant']), 'Invalid budget grant audit record')
    last = max(previous.values(), key=lambda item: item['sequence']) if previous else None
    require(type(entry['sequence']) is int and entry['sequence'] == len(previous) + 1
            and entry['previous_key'] == (last['key'] if last else None)
            and entry['applied_revision'] == before['revision'] + 1 == after['revision'],
            'Broken budget grant chain')
    new = _grant_shape(entry['grant'], before)
    require(canonical_json(after['limits']) == canonical_json(new),
            'Budget grant new limits differ from persisted state')
    expected = json.loads(canonical_json(before))
    expected.update(limits=new, revision=after['revision'])
    expected['commands'][command] = entry
    require(canonical_json(expected) == canonical_json(after), 'Budget grant modified unrelated workflow state')
    _milestone(store, before, entry['grant'])


def apply_grant(store, state, grant, key):
    """Build one auditable transition; the caller owns the original Journal lock."""
    require(_text(key), 'Budget grant key required')
    command = 'budget:' + key
    fingerprint = digest(grant)
    prior = state['commands'].get(command)
    if prior is not None:
        require(prior['digest'] == fingerprint, 'Budget grant key reused with a different request')
        return prior, False
    _grant_shape(grant, state)
    previous = grants(state)
    last = max(previous.values(), key=lambda item: item['sequence']) if previous else None
    entry = {'type': 'workflow_budget_grant.v1', 'key': key, 'digest': fingerprint,
             'grant': json.loads(canonical_json(grant)), 'sequence': len(previous) + 1,
             'previous_key': last['key'] if last else None, 'applied_revision': state['revision'] + 1}
    # Journal.save validates the same transition before appending any effect.
    state['limits'] = grant['new_limits']
    state['commands'][command] = entry
    return entry, True
