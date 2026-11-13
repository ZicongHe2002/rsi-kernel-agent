"""Archive only the four pinned Runtime3 R2 jobs proven never backend-submitted.

No bridge, Controller.step, retry, runtime allocation, or core changes. A lost
lookup alone is insufficient: every durable submission/receipt must be absent.
The started reservation is evaluated as explicitly cancelled/incomplete; three
unstarted Runs stay not_run with no fabricated result. The task remains open.
"""
import argparse
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.contracts import Decision, canonical_json, record_to_dict
from rsi_kernel_agent.store import ExperimentStore, StoreError
from rsi_kernel_agent.workflow.controller import stable
from rsi_kernel_agent.workflow.evaluation import integrate_result
from rsi_kernel_agent.workflow.execution import make_request, stable_id
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from rsi_kernel_agent.workflow.scheduling import validate_limits
from real_round_aux import LIMITS, read_json, write_once

EXPECTED = {
    'task_id': 'mla-decode-v1', 'round_id': 'real-runtime2-round:2',
    'context_id': 'runtime-context:559e042239cb327b2d894d6535b09b9a7b406dbfff2dc55e5852311c430ccab3',
    'runtime_id': 'tpu-v6e1-s-euw4a1-39sjw4nm2h4b9', 'initial_revision': 145,
    'strategy_ids': {
        'A': 'mla-strategy:deee0989771642ee8e066be07adb0370a6c7a3bca6be04ebc8ab50e145c5b05e',
        'B': 'mla-strategy:ad3416851dfcc72142373541d6dc1f47121b390cce460b7fa479e9436b97e3cd'},
    'job_ids': {
        'length-row': 'wf-job:ccdca258e9823aa6b76598fcff1b35f5c118e22c',
        'length-smem': 'wf-job:f61380e692e57f79cde9196d8bbc9d374c996408',
        'query-in-attention': 'wf-job:7568a0c923807a22e88a8929d30b580434444fa3',
        'b1-fused-prepare': 'wf-job:31a9eba3c778f5acf5591ddbfe820e8c103f192d'},
}
PLANS = {'R2-A-length-row': 'length-row', 'R2-A-length-smem': 'length-smem',
         'R2-B-query': 'query-in-attention', 'R2-B-b1-fusion': 'b1-fused-prepare'}
SPENT = {'executions': 10, 'tokens': 0, 'time_s': 18000.0}
PRIOR_SPENT = {'executions': 9, 'tokens': 0, 'time_s': 16200.0}
COST = {'executions': 1, 'tokens': 0, 'time_s': 1800.0}
REASON = 'Runtime3 allocation lost before durable backend submission; no execution verdict'
INTENT_TYPE = 'p35_unsubmitted_runtime_loss_intent.v1'
RESULT_TYPE = 'p35_unsubmitted_runtime_loss_result.v1'
OUTPUT_NAME = 'runtime-loss-archive.json'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value):
    return sha256(canonical_json(value).encode()).hexdigest()


def maybe(store, identity):
    try:
        return store.get(identity)
    except StoreError as exc:
        if str(exc) == 'Missing record: ' + identity:
            return None
        raise


def load(path):
    try:
        return read_json(path)
    except (OSError, ValueError) as exc:
        raise ValueError('Missing or invalid frozen input: ' + str(path)) from exc


def identities():
    intent = stable_id('p35-loss-intent', EXPECTED['task_id'], EXPECTED['round_id'], EXPECTED['context_id'])
    result = stable_id('p35-loss-result', intent)
    return intent, result


def validate_observation(observation):
    require(type(observation) is dict and set(observation) == {
        'schema_version', 'runtime_id', 'observed_at_utc', 'control_plane_reachable',
        'assignments', 'driver_processes_stopped', 'reason'}, 'Invalid loss observation fields')
    require(observation['schema_version'] == 1 and observation['runtime_id'] == EXPECTED['runtime_id']
        and observation['control_plane_reachable'] is True
        and observation['driver_processes_stopped'] is True
        and type(observation['reason']) is str and bool(observation['reason'].strip()),
        'Loss observation must identify the absent old Runtime and stopped drivers')
    rows = observation['assignments']
    require(type(rows) is list, 'Discovery assignments must be a sanitized list')
    for row in rows:
        require(type(row) is dict and set(row) == {'runtime_id', 'session_names', 'accelerator', 'variant'}
            and all(type(row[key]) is str and bool(row[key].strip()) for key in ('runtime_id', 'accelerator', 'variant'))
            and type(row['session_names']) is list
            and all(type(name) is str and bool(name.strip()) for name in row['session_names']),
            'Discovery assignments must contain sanitized runtime/session/accelerator/variant rows')
        require(row['runtime_id'] != EXPECTED['runtime_id'], 'Old Runtime is still present in discovery assignments')
    try:
        timestamp = datetime.fromisoformat(observation['observed_at_utc'].replace('Z', '+00:00'))
        require(timestamp.utcoffset() is not None, 'Observation needs an explicit timezone')
    except (TypeError, AttributeError, ValueError) as exc:
        raise ValueError('Invalid loss observation timestamp') from exc


def load_scope(store, directory):
    directory = Path(directory)
    target = directory / 'real-round-2'
    require(load(target / 'runtime-binding.json') == {'generation': 'runtime3'}, 'Old Runtime binding changed')
    require(load(directory / 'colab-config-runtime3.json')['runtime_id'] == EXPECTED['runtime_id'],
            'Old Runtime config changed')
    intake, variants = load(target / 'intake.json'), load(target / 'variants.json')
    inputs = load(target / 'execution-input.json')
    prior = load(directory / 'confirmation-f686274a57bb7ebb' / 'controller-status.json')
    require(inputs['round_id'] == EXPECTED['round_id'] and inputs['variants'] == variants,
            'Execution inputs differ from frozen variants')
    require(set(variants) == set(PLANS) and all(len(v) == 1 for v in variants.values()),
            'Expected exactly the four original Plan variants')
    require(intake['task_id'] == EXPECTED['task_id'] and intake['round_id'] == EXPECTED['round_id']
        and len(intake['items']) == 4 and all(item['status'] == 'accepted' and len(item['jobs']) == 1
        for item in intake['items']), 'Expected four accepted original R2 jobs')
    jobs = {job['id']: job for item in intake['items'] for job in item['jobs']}
    require(len(jobs) == 4 and set(jobs) == set(EXPECTED['job_ids'].values()), 'Original R2 job identities changed')
    for job in jobs.values():
        label = job['variant']['label']
        pid = next((pid for pid, value in PLANS.items() if value == label), None)
        require(pid is not None and job['id'] == EXPECTED['job_ids'][label]
            and job['variant'] == variants[pid][0] and job['variant']['cost'] == COST
            and job['context_id'] == EXPECTED['context_id']
            and job['strategy_id'] == EXPECTED['strategy_ids']['A' if '-A-' in pid else 'B']
            and job['contract']['context']['provenance'] == 'real'
            and job['contract']['context']['environment']['runtime_session_id'] == EXPECTED['runtime_id']
            and job['variant']['device'] == EXPECTED['runtime_id'], 'Frozen R2 identity, runtime or cost changed')
    record = maybe(store, intake['id'])
    require(type(record) is Decision and record.kind == 'note' and record.task_id == record.target_id == EXPECTED['task_id']
        and record.details.get('type') == 'workflow_intake.v1' and record.details.get('result') == intake,
        'Original persisted intake differs')
    require(prior['spent'] == PRIOR_SPENT and len(prior['jobs']) == 9
        and all(j['state'] in ('completed', 'failed', 'cancelled') for j in prior['jobs'].values()),
        'Expected original nine completed prior jobs and budget')
    return {'intake': intake, 'jobs': jobs, 'prior_jobs': prior['jobs'],
            'input_digests': {'intake': digest(intake), 'variants': digest(variants),
                             'execution_input': digest(inputs), 'prior_checkpoint': digest(prior)}}


def no_backend_submission(store, original_jobs):
    runs = {job['run_id'] for job in original_jobs.values()}
    operations = {stable_id('operation', run, stage) for run in runs
                  for stage in ('generate', 'compile', 'correctness', 'benchmark', 'profile')}
    for record in store.history(EXPECTED['task_id']):
        if type(record) is not Decision:
            continue
        details = record.details
        if details.get('type') not in ('colab_execution_request.v1', 'execution_request.v1', 'execution_receipt.v1'):
            continue
        request = details.get('request', {})
        related = (record.target_id in runs or details.get('operation_id') in operations
                   or request.get('operation_id') in operations or request.get('job', {}).get('run_id') in runs)
        require(not related, 'R2 has a durable backend submission or receipt; this archival path is forbidden')


def validate_original(store, scope, state, *, recovering=False):
    require(state is not None and state['task_id'] == EXPECTED['task_id']
        and state['revision'] == EXPECTED['initial_revision']
        and state['limits'] == validate_limits(LIMITS) and state['spent'] == SPENT
        and state['cancelled'] is False, 'Original journal revision, limits, budget or cancellation changed')
    require(state['intakes'].get(scope['intake']['id']) == digest(scope['intake']), 'Journal intake binding differs')
    other = {key: job for key, job in state['jobs'].items() if job['round_id'] != EXPECTED['round_id']}
    require(other == scope['prior_jobs'] and len(state['jobs']) == 13, 'Prior jobs or R2 coverage changed')
    jobs = {key: job for key, job in state['jobs'].items() if job['round_id'] == EXPECTED['round_id']}
    require(set(jobs) == set(scope['jobs']), 'Original R2 jobs changed')
    for jid, job in jobs.items():
        require(all(job.get(key) == value for key, value in scope['jobs'][jid].items())
            and job['candidate_id'] == stable('wf-c', jid) and job['run_id'] == stable('wf-r', jid + ':0')
            and job['attempt'] == 0 and job['repair_depth'] == 0
            and not job.get('retry_of') and not job.get('repair_of')
            and job['stage'] == 'generate' and job['receipts'] == {} and job['result'] is None
            and not job.get('cancel_requested'), 'Original R2 attempt identity or receipt state changed')
        require(job['task_id'] == EXPECTED['task_id'] and job['round_id'] == EXPECTED['round_id'],
                'Original R2 task or round changed')
        make_request(store, job, 'generate')
        run = store.run_state(job['run_id'])
        if job['variant']['label'] == 'length-row':
            require(job['state'] == 'blocked' and job['reserved'] is True
                and job.get('operation') == make_request(store, job, 'generate'), 'Only original length-row may be reserved and blocked')
            require(recovering or run == {'execution_status': 'running', 'scheduling_status': 'active',
                                        'event_id': job['run_id'] + ':start'}, 'Original started Run state changed')
        else:
            require(job['state'] == 'queued' and job['reserved'] is False and 'operation' not in job
                and run == {'execution_status': 'not_run', 'scheduling_status': 'ready', 'event_id': None},
                'Queued R2 job acquired an operation or execution event')
        if not recovering:
            require(maybe(store, stable_id('workflow-evaluation-intent', job['run_id'])) is None
                and maybe(store, stable_id('workflow-result', job['run_id'])) is None,
                'R2 already has an evaluation intent or result')
    no_backend_submission(store, jobs)
    return jobs


def load_intent(store, scope):
    identity, _ = identities()
    note = maybe(store, identity)
    if note is None:
        return None
    require(type(note) is Decision and note.task_id == note.target_id == EXPECTED['task_id']
        and note.kind == 'note' and set(note.details) == {'type', 'scope', 'input_digests', 'observation', 'original_state'}
        and note.details['type'] == INTENT_TYPE and note.details['scope'] == EXPECTED
        and note.details['input_digests'] == scope['input_digests'], 'Immutable archival intent differs')
    validate_observation(note.details['observation'])
    validate_original(store, scope, note.details['original_state'], recovering=True)
    return note


def final_state(original, intent_id, result):
    state = deepcopy(original)
    for job in state['jobs'].values():
        if job['round_id'] != EXPECTED['round_id']:
            continue
        job.update(state='cancelled', reason=REASON, runtime_loss_archive_id=intent_id)
        if job['variant']['label'] == 'length-row':
            # Keep the original operation verbatim as an archived journal intent.
            job.update(stage='evaluate', result=deepcopy(result), cancel_requested=True)
    return state


def verified_cancel_result(store, original_jobs, *, optional=False):
    job = original_jobs[EXPECTED['job_ids']['length-row']]
    run_id = job['run_id']
    result_note = maybe(store, stable_id('workflow-result', run_id))
    intent = maybe(store, stable_id('workflow-evaluation-intent', run_id))
    if intent is not None:
        require(type(intent) is Decision and intent.task_id == EXPECTED['task_id'] and intent.target_id == run_id
            and intent.details == {'type': 'workflow_evaluation_intent.v1', 'job_id': job['id'],
                'request_digest': make_request(store, job, 'generate')['request_digest'],
                'receipt_refs': {}, 'cancelled': True}, 'Recovery evaluation intent differs')
    if result_note is None and optional:
        run = store.run_state(run_id)
        require(run['execution_status'] in ('running', 'incomplete'), 'Started Run has another terminal state')
        require(run['execution_status'] != 'incomplete' or intent is not None,
                'Incomplete Run lacks pinned cancellation intent')
        return None
    require(intent is not None and type(result_note) is Decision and result_note.task_id == EXPECTED['task_id']
        and result_note.target_id == run_id and result_note.details.get('type') == 'workflow_result.v1'
        and result_note.details.get('job_id') == job['id'] and result_note.details.get('cancelled') is True,
        'Started R2 lacks its exact cancelled workflow result')
    result = result_note.details['result']
    require(result['classification'] == 'cancelled' and result['run_id'] == run_id
        and result['candidate_id'] == job['candidate_id'] and result['receipt_refs'] == {}
        and result['correctness'] == result['timing_validity'] == 'unknown'
        and result['latency_ms'] is None and result['confirmed'] is False
        and result['cost'] == {'executions': 0, 'tokens': 0, 'time_s': 0}
        and store.run_state(run_id)['execution_status'] == 'incomplete', 'Archived cancellation fabricated execution evidence')
    return result


def summary_for(intent, original_jobs, result):
    started = original_jobs[EXPECTED['job_ids']['length-row']]
    return {'task_id': EXPECTED['task_id'], 'round_id': EXPECTED['round_id'],
        'context_id': EXPECTED['context_id'], 'runtime_id': EXPECTED['runtime_id'],
        'old_job_ids': sorted(original_jobs),
        'started_run_id': started['run_id'], 'started_classification': result['classification'],
        'started_execution_status': 'incomplete',
        'not_started_run_ids': sorted(job['run_id'] for jid, job in original_jobs.items() if jid != started['id']),
        'original_operation': deepcopy(started['operation']), 'original_revision': intent.details['original_state']['revision'],
        'spent_before': deepcopy(SPENT), 'spent_after': deepcopy(SPENT), 'task_cancelled': False,
        'durable_backend_requests': 0, 'durable_backend_receipts': 0,
        'old_context_preserved': True, 'round_execution_completed': False,
        'interpretation': 'Lookup was unknown, but no durable backend submission exists. Started reservation is not refunded; three queued Runs were never started.'}


def archive_unsubmitted_runtime_loss(store, directory, observation):
    """Perform this one guarded archival transaction sequence; no remote calls."""
    validate_observation(observation)
    journal = WorkflowJournal(store, EXPECTED['task_id'])
    with journal.lock():
        scope = load_scope(store, directory)
        intent = load_intent(store, scope)
        state = journal.load()
        if intent is None:
            validate_original(store, scope, state)
            intent_id, _ = identities()
            intent = Decision(intent_id, EXPECTED['task_id'], 'note', EXPECTED['task_id'], REASON,
                {'type': INTENT_TYPE, 'scope': deepcopy(EXPECTED), 'input_digests': scope['input_digests'],
                 'observation': deepcopy(observation), 'original_state': deepcopy(state)})
            store.append(intent, intent.id)
        require(intent.details['observation'] == observation, 'Loss observation differs from immutable intent')
        original = intent.details['original_state']
        jobs = {jid: job for jid, job in original['jobs'].items() if job['round_id'] == EXPECTED['round_id']}
        result = verified_cancel_result(store, jobs, optional=True)
        terminal = final_state(original, intent.id, result) if result else None
        if terminal:
            terminal['revision'] += 1
        require(state == original or state == terminal, 'Journal changed outside this archival sequence')
        no_backend_submission(store, jobs)
        active = jobs[EXPECTED['job_ids']['length-row']]
        # The original Evaluator provides all terminal records and idempotency.
        result = integrate_result(store, active, {}, cancelled=True)
        require(verified_cancel_result(store, jobs) == result, 'Evaluator cancellation differs')
        terminal = final_state(original, intent.id, result)
        if state == original:
            state = journal.save(terminal, REASON)
        else:
            terminal['revision'] += 1
            require(state == terminal, 'Archived terminal journal differs')
        _, result_id = identities()
        summary = summary_for(intent, jobs, result)
        note = Decision(result_id, EXPECTED['task_id'], 'note', EXPECTED['task_id'],
            'Archived unsubmitted Runtime3 R2 work without claiming experiment completion',
            {'type': RESULT_TYPE, 'intent_id': intent.id, 'summary': summary})
        store.append(note, note.id)
        snapshot = store.create_snapshot(EXPECTED['task_id'], stable_id('p35-loss-snapshot', intent.id))
        output = {'schema_version': 1, 'decision_id': note.id, 'intent_id': intent.id,
                  'summary': summary, 'snapshot': snapshot}
        write_once(Path(directory) / 'real-round-2' / OUTPUT_NAME, output)
        return require_archived_unsubmitted_runtime_loss(store, directory)


def require_archived_unsubmitted_runtime_loss(store, directory):
    """Read-only prerequisite; caller may already hold WorkflowJournal.lock.

    Raises ValueError on missing/mismatched archival evidence. New unrelated
    jobs may exist after archival, but may not refund or mutate its old budget.
    """
    scope = load_scope(store, directory)
    intent = load_intent(store, scope)
    require(intent is not None, 'Runtime3 unsubmitted loss has not been archived')
    original = intent.details['original_state']
    jobs = {jid: job for jid, job in original['jobs'].items() if job['round_id'] == EXPECTED['round_id']}
    result = verified_cancel_result(store, jobs)
    terminal = final_state(original, intent.id, result)
    terminal['revision'] += 1
    current = WorkflowJournal(store, EXPECTED['task_id']).load()
    require(current['limits'] == original['limits'] and current['cancelled'] is False
        and all(current['jobs'].get(jid) == job for jid, job in terminal['jobs'].items()),
        'Archived jobs, prior results, task limits or cancellation changed')
    added = [job for jid, job in current['jobs'].items() if jid not in terminal['jobs']]
    require(current['spent'] == {key: SPENT[key] + sum(job['variant']['cost'][key]
        for job in added if job['reserved']) for key in SPENT}, 'Archived reservation was refunded or spending changed')
    _, result_id = identities()
    note = maybe(store, result_id)
    summary = summary_for(intent, jobs, result)
    require(type(note) is Decision and note.kind == 'note' and note.task_id == note.target_id == EXPECTED['task_id']
        and note.details == {'type': RESULT_TYPE, 'intent_id': intent.id, 'summary': summary},
        'Archival completion marker differs')
    output = load(Path(directory) / 'real-round-2' / OUTPUT_NAME)
    require(set(output) == {'schema_version', 'decision_id', 'intent_id', 'summary', 'snapshot'}
        and output['schema_version'] == 1 and output['decision_id'] == note.id
        and output['intent_id'] == intent.id and output['summary'] == summary, 'Archival output differs')
    offset, found = 0, {}
    wanted = {note.id, intent.id, WorkflowJournal(store, EXPECTED['task_id']).prefix + str(terminal['revision'])}
    while True:
        page = store.snapshot_page(output['snapshot']['id'], offset=offset, limit=200)
        require(page['snapshot'] == output['snapshot'], 'Archive snapshot metadata differs')
        for row in page['records']:
            payload = record_to_dict(row)
            if payload['id'] in wanted:
                found[payload['id']] = payload
        if page['next_offset'] is None:
            break
        offset = page['next_offset']
    require(set(found) == wanted, 'Fresh archive snapshot omits its intent, journal or result')
    require(found[WorkflowJournal(store, EXPECTED['task_id']).prefix + str(terminal['revision'])]['details']['state'] == terminal,
            'Archive snapshot journal differs')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('archive', 'verify'))
    parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--observation', type=Path)
    args = parser.parse_args()
    if args.action == 'archive':
        require(args.observation is not None, 'Archive requires a frozen observation JSON')
        require((args.directory / 'experiments.sqlite3').is_file(), 'Original Store is required')
        store = ExperimentStore(args.directory / 'experiments.sqlite3', args.directory / 'artifacts')
        result = archive_unsubmitted_runtime_loss(store, args.directory, load(args.observation))
    else:
        from verify_real_round import ReadOnlyStore
        store = ReadOnlyStore(args.directory / 'experiments.sqlite3', args.directory / 'artifacts')
        result = require_archived_unsubmitted_runtime_loss(store, args.directory)
    print(canonical_json({'decision_id': result['decision_id'], 'snapshot': result['snapshot'],
                         'spent': result['summary']['spent_after'], 'round_execution_completed': False}))
