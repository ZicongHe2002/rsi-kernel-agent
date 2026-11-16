"""Archive a submitted Runtime4 prerequisite whose remote outcome is unknown.

Preserve the complete requests and verified generate/compile receipts. This is
an administrative Run closure, never a correctness evaluation or cancellation
of the remote operation. No backend calls, budget refund, or consider occurs.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.contracts import Decision, RunEvent
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.workflow.colab import ColabBackend
from rsi_kernel_agent.workflow.evaluation import _verified_prefix
from rsi_kernel_agent.workflow.execution import make_request, stable_id
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from rsi_kernel_agent.workflow.scheduling import validate_limits
from p35_archive_unsubmitted_runtime_loss import digest, maybe, require, SPENT, LIMITS
from real_round_aux import read_json, write_once

NAMES = ('oracle-negative-attention', 'oracle-negative-cache', 'baseline-repair', 'baseline-b')
OUTPUT = 'runtime4-prerequisite-loss-archive.json'
INTENT_TYPE = 'p35_runtime4_unknown_loss_intent.v1'
RESULT_TYPE = 'p35_runtime4_unknown_loss_result.v1'


def require_prior_archive(store, directory):
    from admit_runtime4 import require_runtime4_archive
    return require_runtime4_archive(store, directory)


def validate_observation(observation, runtime):
    require(type(observation) is dict and set(observation) == {
        'schema_version', 'runtime_id', 'observed_at_utc', 'control_plane_reachable',
        'assignments', 'driver_processes_stopped', 'reason'}, 'Invalid loss observation fields')
    require(observation['schema_version'] == 1 and observation['runtime_id'] == runtime
        and observation['control_plane_reachable'] is True and observation['driver_processes_stopped'] is True
        and type(observation['reason']) is str and bool(observation['reason'].strip()),
        'Loss observation must bind absent Runtime4 and stopped drivers')
    require(type(observation['assignments']) is list, 'Invalid discovery list')
    for row in observation['assignments']:
        require(type(row) is dict and set(row) == {'runtime_id', 'session_names', 'accelerator', 'variant'}
            and all(type(row[k]) is str and bool(row[k].strip()) for k in ('runtime_id', 'accelerator', 'variant'))
            and type(row['session_names']) is list
            and all(type(n) is str and bool(n.strip()) for n in row['session_names'])
            and row['runtime_id'] != runtime, 'Old Runtime remains present or discovery row is invalid')
    try:
        timestamp = datetime.fromisoformat(observation['observed_at_utc'].replace('Z', '+00:00'))
        require(timestamp.utcoffset() is not None, 'Observation timezone missing')
    except (TypeError, AttributeError, ValueError) as error:
        raise ValueError('Invalid loss observation timestamp') from error


def scope(store, directory):
    """Verify the exact durable prefix; callers hold the original task lock."""
    directory = Path(directory)
    config = read_json(directory / 'colab-config-runtime4.json')
    jobs = {name: read_json(directory / (name + '-runtime4-job.json')) for name in NAMES}
    active = jobs[NAMES[0]]
    runtime, task = config['runtime_id'], active['task_id']
    require(len({j['run_id'] for j in jobs.values()}) == 4, 'Repeated prerequisite Run')
    remote = SimpleNamespace(runtime_id=runtime, nonce=config['nonce'], root=config['remote_root'])
    backend = ColabBackend(store, remote)
    ledger_ids, operations, requests, receipts = set(), set(), {}, {}
    for name, job in jobs.items():
        require(job['task_id'] == task and job['contract']['context']['provenance'] == 'real'
            and job['contract']['context']['environment']['runtime_session_id'] == runtime,
            'Runtime4 prerequisite identity changed')
        for stage in ('generate', 'compile', 'correctness', 'benchmark', 'profile'):
            request = make_request(store, job, stage)
            op = request['operation_id']
            operations.add(op)
            request_record = backend._operation(op)
            receipt = backend._local_receipt(request)
            if name == NAMES[0] and stage in ('generate', 'compile', 'correctness'):
                require(request_record is not None and request_record.details['request'] == request,
                        'Missing or changed durable submitted request')
                backend._check_remote(request, request_record)
                requests[stage] = request_record
                ledger_ids.add(request_record.id)
                if stage != 'correctness':
                    require(receipt is not None and receipt['status'] == 'succeeded',
                            'Missing successful generate/compile receipt')
                    receipts[stage] = receipt
                    ledger_ids.add(stable_id('execution-receipt', op))
                else:
                    require(receipt is None, 'Correctness has completed; recover its actual result')
            else:
                require(request_record is None and receipt is None,
                        'Later or other prerequisite execution must be reconciled')
        if name != NAMES[0]:
            require(store.run_state(job['run_id'])['execution_status'] == 'not_run',
                    'Other Runtime4 prerequisite was started')
    _verified_prefix(store, active, receipts, cancelled=True)
    runs = {j['run_id'] for j in jobs.values()}
    for record in store.history(task):
        if type(record) is not Decision:
            continue
        details = record.details
        request = details.get('request', {})
        related = (record.target_id in runs or details.get('operation_id') in operations
                   or request.get('operation_id') in operations or request.get('job', {}).get('run_id') in runs)
        if related and details.get('type') in ('execution_request.v1', 'colab_execution_request.v1', 'execution_receipt.v1'):
            require(record.id in ledger_ids, 'Unexpected or orphan operation ledger')
        if related:
            require(details.get('type') not in ('workflow_evaluation_intent.v1', 'workflow_result.v1', 'oracle_gate_failure.v1'),
                    'Runtime4 already has a terminal evaluator result')
    summary = {'task_id': task, 'runtime_id': runtime, 'jobs': jobs,
        'remote_correctness_outcome': 'unknown', 'durable_request_stages': list(requests),
        'request_records': {stage: {'id': row.id, 'digest': digest(row.details)} for stage, row in requests.items()},
        'receipt_stages': list(receipts), 'receipt_refs': {stage: row['artifact_ref'] for stage, row in receipts.items()},
        'original_operation_id': requests['correctness'].details['request']['operation_id'],
        'not_submitted': False, 'remote_cancelled': False, 'evaluation_created': False,
        'task_cancelled': False, 'budget_refunded': False}
    return active, summary


def identity(job):
    intent = stable_id('p35-runtime4-unknown-loss-intent', job['run_id'])
    return intent, stable_id('p35-runtime4-unknown-loss-result', intent), stable_id('p35-runtime4-lost-run', job['run_id'])


def archive_runtime4_loss(store, directory, observation):
    directory = Path(directory)
    base = read_json(directory / 'oracle-negative-attention-runtime4-job.json')
    with WorkflowJournal(store, base['task_id']).lock():
        prior_state = require_prior_archive(store, directory)
        job, summary = scope(store, directory)
        validate_observation(observation, summary['runtime_id'])
        state = WorkflowJournal(store, job['task_id']).load()
        require(state == prior_state and state is not None and not state['cancelled']
            and state['spent'] == SPENT and state['limits'] == validate_limits(LIMITS)
            and all(j['state'] in ('completed', 'failed', 'cancelled') for j in state['jobs'].values()),
            'Original Controller state, spending or limits changed')
        intent_id, decision_id, event_id = identity(job)
        intent = maybe(store, intent_id)
        current = store.run_state(job['run_id'])
        if intent is None:
            require(current['execution_status'] == 'running', 'Expected original running prerequisite')
            details = {'type': INTENT_TYPE, 'summary': summary, 'observation': observation,
                       'journal_digest': digest(state), 'expected_event_id': current['event_id']}
            intent = Decision(intent_id, job['task_id'], 'note', job['run_id'],
                              'Preserve submitted correctness with unknown remote outcome before closing lost Runtime', details)
        else:
            require(type(intent) is Decision and intent.kind == 'note' and intent.task_id == job['task_id'] and intent.target_id == job['run_id']
                and intent.details.get('type') == INTENT_TYPE and intent.details.get('summary') == summary
                and intent.details.get('observation') == observation and intent.details.get('journal_digest') == digest(state),
                'Existing Runtime4 archive intent changed')
            require(current['execution_status'] == 'running' or
                (current['execution_status'] == 'incomplete' and current['event_id'] == event_id),
                'Prior Run changed after archive intent')
        store.append(intent, intent.id)
        store.transition(RunEvent(event_id, job['run_id'], 'incomplete', 'finished',
            'Runtime unavailable after correctness submission; remote outcome unknown; requests and receipts preserved'),
            intent.details['expected_event_id'], event_id)
        result = {'schema_version': 1, 'intent_id': intent_id, 'decision_id': decision_id,
                  'summary': summary, 'observation': observation, 'journal_digest': digest(state), 'event_id': event_id}
        marker = Decision(decision_id, job['task_id'], 'note', job['run_id'],
                          'Archived unknown Runtime4 prerequisite without correctness verdict or budget change',
                          {'type': RESULT_TYPE, 'result': result})
        store.append(marker, marker.id)
        require(WorkflowJournal(store, job['task_id']).load() == state, 'Archive changed Controller state')
        write_once(directory / OUTPUT, result)
        return result


def require_archived_runtime4_loss(store, directory):
    directory = Path(directory)
    result = read_json(directory / OUTPUT)
    job, summary = scope(store, directory)
    intent_id, decision_id, event_id = identity(job)
    require(result['intent_id'] == intent_id and result['decision_id'] == decision_id and result['event_id'] == event_id
        and result['summary'] == summary, 'Runtime4 archive output changed')
    validate_observation(result['observation'], summary['runtime_id'])
    intent, marker = store.get(intent_id), store.get(decision_id)
    require(type(intent) is Decision and intent.kind == 'note' and intent.details.get('type') == INTENT_TYPE
        and intent.task_id == job['task_id'] and intent.target_id == job['run_id']
        and intent.details['summary'] == summary and intent.details['observation'] == result['observation']
        and intent.details['journal_digest'] == result['journal_digest']
        and type(marker) is Decision and marker.kind == 'note' and marker.task_id == job['task_id'] and marker.target_id == job['run_id']
        and marker.details == {'type': RESULT_TYPE, 'result': result},
        'Persisted Runtime4 archive differs')
    current = store.run_state(job['run_id'])
    require(current['execution_status'] == 'incomplete' and current['event_id'] == event_id,
            'Archived Runtime4 Run was changed')
    state = WorkflowJournal(store, job['task_id']).load()
    require(state is not None and not state['cancelled'] and state['limits'] == validate_limits(LIMITS)
        and all(state['spent'][key] >= value for key, value in SPENT.items()),
        'Original limits changed or spending refunded')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--observation', required=True, type=Path)
    args = parser.parse_args()
    store = ExperimentStore(args.directory / 'experiments.sqlite3', args.directory / 'artifacts')
    result = archive_runtime4_loss(store, args.directory, read_json(args.observation))
    print(json.dumps({'decision_id': result['decision_id'], 'operation_id': result['summary']['original_operation_id'],
                      'remote_correctness_outcome': 'unknown', 'state': 'incomplete'}))
