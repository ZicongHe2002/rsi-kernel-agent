"""Archive the pinned Runtime5 R2 before durable submission; retain all costs.

Reuse the original zero-submission guard, observation contract and Evaluator.
No bridge calls, budget grants, retries, Task cancellation or replacement Run.
"""
import argparse
from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.contracts import Decision, canonical_json
from rsi_kernel_agent.workflow.controller import stable
from rsi_kernel_agent.workflow.evaluation import integrate_result
from rsi_kernel_agent.workflow.execution import make_request, stable_id
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from rsi_kernel_agent.workflow.scheduling import validate_limits
from p35_archive_unsubmitted_runtime_loss import (
    COST, PLANS, digest, maybe, require, load, no_backend_submission, validate_observation)
from real_round_aux import LIMITS, write_once

EXPECTED = {
    'task_id': 'mla-decode-v1', 'round_id': 'real-runtime5-recovery-round:2',
    'context_id': 'runtime-context:bac61391584c500438dd688dd00965977b0232b5994dbf9449f8a0b39ac23f72',
    'runtime_id': 'tpu-v6e1-s-euw4a0-2u7r1pvl8i4i8', 'initial_revision': 150, 'prior_job_count': 13,
    'strategy_ids': {
        'A': 'mla-strategy:deee0989771642ee8e066be07adb0370a6c7a3bca6be04ebc8ab50e145c5b05e',
        'B': 'mla-strategy:ad3416851dfcc72142373541d6dc1f47121b390cce460b7fa479e9436b97e3cd'},
    'job_ids': {'length-row': 'wf-job:4f3d35da2bcc46075fc533480e0d681ae166d91c',
        'length-smem': 'wf-job:0f63155d744268f6855180bfa191efc4a1af0eec',
        'query-in-attention': 'wf-job:ad787a2f4581d3705902e7891dc3cde89f5a6f0d',
        'b1-fused-prepare': 'wf-job:f99bbe529f0e2de8417729f1cbf0f0bca3b4a1b8'}}
SPENT = {'executions': 11, 'tokens': 0, 'time_s': 19800.0}
PRIOR_SPENT = {'executions': 10, 'tokens': 0, 'time_s': 18000.0}
REASON = 'Runtime5 lost before durable backend submission; reserved cost retained, no execution verdict'


def identities():
    intent = stable_id('p35-r5-loss-intent', EXPECTED['task_id'], EXPECTED['round_id'], EXPECTED['context_id'])
    return intent, stable_id('p35-r5-loss-result', intent)


def load_scope(store, directory):
    target = Path(directory) / 'real-round-2-runtime5'
    require(load(target / 'runtime-binding.json') == {'generation': 'runtime5'}
            and load(Path(directory) / 'colab-config-runtime5.json')['runtime_id'] == EXPECTED['runtime_id'],
            'Original Runtime5 binding changed')
    intake, variants, inputs = (load(target / name) for name in ('intake.json', 'variants.json', 'execution-input.json'))
    require(intake['task_id'] == EXPECTED['task_id'] and intake['round_id'] == EXPECTED['round_id']
            and inputs['round_id'] == EXPECTED['round_id'] and inputs['variants'] == variants
            and set(variants) == set(PLANS) and all(len(v) == 1 for v in variants.values())
            and len(intake['items']) == 4 and all(i['status'] == 'accepted' and len(i['jobs']) == 1
                                               for i in intake['items']), 'Runtime5 frozen intake differs')
    note = store.get(intake['id'])
    require(type(note) is Decision and note.kind == 'note' and note.task_id == note.target_id == EXPECTED['task_id']
            and note.details.get('type') == 'workflow_intake.v1' and note.details.get('result') == intake,
            'Runtime5 persisted intake differs')
    jobs = {j['id']: j for i in intake['items'] for j in i['jobs']}
    require(set(jobs) == set(EXPECTED['job_ids'].values()), 'Runtime5 four job identities changed')
    for pid, label in PLANS.items():
        job = jobs[EXPECTED['job_ids'][label]]
        require(job['variant'] == variants[pid][0] and job['variant']['label'] == label
                and job['variant']['cost'] == COST and job['task_id'] == EXPECTED['task_id']
                and job['round_id'] == EXPECTED['round_id'] and job['context_id'] == EXPECTED['context_id']
                and job['strategy_id'] == EXPECTED['strategy_ids']['A' if '-A-' in pid else 'B']
                and job['contract']['context']['provenance'] == 'real'
                and job['contract']['context']['environment']['runtime_session_id'] == EXPECTED['runtime_id']
                and job['variant']['device'] == EXPECTED['runtime_id'], 'Runtime5 candidate identity changed')
    prior = [r.details['state'] for r in store.history(EXPECTED['task_id']) if type(r) is Decision
             and r.details.get('type') == 'workflow_state.v1'
             and r.details['state']['revision'] < EXPECTED['initial_revision']
             and not any(j['round_id'] == EXPECTED['round_id'] for j in r.details['state']['jobs'].values())]
    require(prior and len(prior[-1]['jobs']) == EXPECTED['prior_job_count']
            and prior[-1]['spent'] == PRIOR_SPENT and prior[-1]['limits'] == validate_limits(LIMITS)
            and all(j['state'] in ('completed', 'failed', 'cancelled') for j in prior[-1]['jobs'].values()),
            'Runtime5 prior original Journal checkpoint differs')
    return {'jobs': jobs, 'prior_jobs': prior[-1]['jobs'], 'intake': intake,
            'digests': {name: digest(value) for name, value in
                [('intake', intake), ('variants', variants), ('execution_input', inputs), ('prior_state', prior[-1])]}}


def validate_original(store, scope, state, recovering=False):
    require(state['revision'] == EXPECTED['initial_revision'] and state['spent'] == SPENT
            and state['limits'] == validate_limits(LIMITS) and state['cancelled'] is False
            and state['task_id'] == EXPECTED['task_id']
            and state['intakes'].get(scope['intake']['id']) == digest(scope['intake']),
            'Runtime5 original Journal or budget changed')
    jobs = {k: j for k, j in state['jobs'].items() if j['round_id'] == EXPECTED['round_id']}
    require(set(jobs) == set(scope['jobs']) and
            {k: j for k, j in state['jobs'].items() if k not in jobs} == scope['prior_jobs'], 'Prior jobs changed')
    for key, job in jobs.items():
        require(all(job.get(k) == v for k, v in scope['jobs'][key].items())
                and job['candidate_id'] == stable('wf-c', key) and job['run_id'] == stable('wf-r', key + ':0')
                and job['stage'] == 'generate' and job['receipts'] == {} and job['result'] is None
                and job['attempt'] == job['repair_depth'] == 0 and not job.get('retry_of')
                and not job.get('repair_of') and not job.get('cancel_requested'), 'Runtime5 job state changed')
        request = make_request(store, job, 'generate')
        run = store.run_state(job['run_id'])
        if key == EXPECTED['job_ids']['length-row']:
            require(job['state'] == 'blocked' and job['reserved'] is True and job.get('operation') == request,
                    'Runtime5 reserved operation changed')
            require(recovering or run == {'execution_status': 'running', 'scheduling_status': 'active',
                                          'event_id': job['run_id'] + ':start'}, 'Runtime5 Run changed')
        else:
            require(job['state'] == 'queued' and job['reserved'] is False and 'operation' not in job
                    and run == {'execution_status': 'not_run', 'scheduling_status': 'ready', 'event_id': None},
                    'Unstarted Runtime5 Run acquired execution state')
        if not recovering or key != EXPECTED['job_ids']['length-row']:
            require(maybe(store, stable_id('workflow-evaluation-intent', job['run_id'])) is None
                    and maybe(store, stable_id('workflow-result', job['run_id'])) is None,
                    'Runtime5 unsubmitted Run already has evaluation evidence')
    no_backend_submission(store, jobs, task_id=EXPECTED['task_id'])
    return jobs


def terminal(original, intent_id, result):
    state = deepcopy(original)
    for key, job in state['jobs'].items():
        if job['round_id'] != EXPECTED['round_id']: continue
        job.update(state='cancelled', reason=REASON, runtime_loss_archive_id=intent_id)
        if key == EXPECTED['job_ids']['length-row']:
            job.update(stage='evaluate', result=deepcopy(result), cancel_requested=True)
    return state


def load_intent(store, scope):
    intent_id, _ = identities()
    intent = maybe(store, intent_id)
    if intent is None:
        return None
    require(type(intent) is Decision and intent.kind == 'note'
            and intent.task_id == intent.target_id == EXPECTED['task_id']
            and set(intent.details) == {'type', 'scope', 'digests', 'observation', 'original_state'}
            and intent.details['type'] == 'p35_runtime5_unsubmitted_loss_intent.v1'
            and intent.details['scope'] == EXPECTED and intent.details['digests'] == scope['digests'],
            'Runtime5 archival intent differs')
    validate_observation(intent.details['observation'], expected_runtime_id=EXPECTED['runtime_id'])
    validate_original(store, scope, intent.details['original_state'], recovering=True)
    return intent


def verified_cancel_result(store, active, *, optional=False):
    run_id = active['run_id']
    intent = maybe(store, stable_id('workflow-evaluation-intent', run_id))
    marker = maybe(store, stable_id('workflow-result', run_id))
    if intent is not None:
        require(type(intent) is Decision and intent.kind == 'note'
                and intent.task_id == EXPECTED['task_id'] and intent.target_id == run_id
                and intent.details == {'type': 'workflow_evaluation_intent.v1', 'job_id': active['id'],
                    'request_digest': active['operation']['request_digest'], 'receipt_refs': {}, 'cancelled': True},
                'Runtime5 cancellation intent differs')
    if marker is None and optional:
        run = store.run_state(run_id)
        require(run['execution_status'] in ('running', 'incomplete')
                and (run['execution_status'] != 'incomplete' or intent is not None),
                'Runtime5 started Run has another state or lacks cancellation intent')
        return None
    require(intent is not None and type(marker) is Decision and marker.kind == 'note'
            and marker.task_id == EXPECTED['task_id'] and marker.target_id == run_id
            and set(marker.details) == {'type', 'job_id', 'result', 'cancelled'}
            and marker.details['type'] == 'workflow_result.v1'
            and marker.details['job_id'] == active['id'] and marker.details['cancelled'] is True,
            'Runtime5 cancelled workflow result differs')
    result = marker.details['result']
    require(result['classification'] == 'cancelled' and result['receipt_refs'] == {}
            and result['run_id'] == run_id and result['candidate_id'] == active['candidate_id']
            and result['correctness'] == result['timing_validity'] == 'unknown'
            and result['confirmed'] is False and result['latency_ms'] is None
            and result['cost'] == {'executions': 0, 'tokens': 0, 'time_s': 0}
            and store.run_state(run_id)['execution_status'] == 'incomplete',
            'Runtime5 cancellation fabricated execution evidence')
    return result


def summary_for(intent, jobs, result):
    active = jobs[EXPECTED['job_ids']['length-row']]
    return {'task_id': EXPECTED['task_id'], 'round_id': EXPECTED['round_id'],
        'runtime_id': EXPECTED['runtime_id'], 'context_id': EXPECTED['context_id'],
        'original_revision': intent.details['original_state']['revision'],
        'started_run_id': active['run_id'], 'started_classification': result['classification'],
        'started_execution_status': 'incomplete',
        'not_started_run_ids': sorted(j['run_id'] for k, j in jobs.items() if k != active['id']),
        'original_operation': deepcopy(active['operation']),
        'spent_before': deepcopy(SPENT), 'spent_after': deepcopy(SPENT),
        'task_cancelled': False, 'round_execution_completed': False,
        'durable_backend_requests': 0, 'durable_backend_receipts': 0}


def require_archived_runtime5_loss(store, directory):
    scope = load_scope(store, directory)
    intent_id, result_id = identities()
    intent, output = load_intent(store, scope), maybe(store, result_id)
    require(intent is not None, 'Runtime5 unsubmitted loss has not been archived')
    original = intent.details['original_state']
    jobs = validate_original(store, scope, original, recovering=True)
    active = jobs[EXPECTED['job_ids']['length-row']]
    result = verified_cancel_result(store, active)
    journal = WorkflowJournal(store, EXPECTED['task_id'])
    current = journal.load()
    old = terminal(original, intent_id, result)
    old['revision'] += 1
    require(current['limits'] == old['limits'] and current['cancelled'] is False
            and all(current['jobs'].get(key) == job for key, job in old['jobs'].items()),
            'Archived Runtime5 jobs or task limits changed')
    added = [j for k, j in current['jobs'].items() if k not in old['jobs']]
    require(current['spent'] == {k: SPENT[k] + sum(j['variant']['cost'][k] for j in added if j['reserved'])
                                 for k in SPENT}, 'Runtime5 reservation was refunded or spending changed')
    summary = summary_for(intent, jobs, result)
    require(type(output) is Decision and output.kind == 'note'
            and output.task_id == output.target_id == EXPECTED['task_id']
            and output.details == {'type': 'p35_runtime5_unsubmitted_loss_result.v1',
                'intent_id': intent_id, 'summary': summary}, 'Runtime5 loss completion marker differs')
    saved = load(Path(directory) / 'real-round-2-runtime5/runtime-loss-archive.json')
    require(set(saved) == {'schema_version', 'intent_id', 'decision_id', 'summary', 'snapshot'}
            and saved['schema_version'] == 1 and saved['intent_id'] == intent_id
            and saved['decision_id'] == result_id and saved['summary'] == summary,
            'Runtime5 archive output changed')
    wanted = {intent_id, result_id, stable_id('workflow-result', active['run_id']),
              journal.prefix + str(old['revision'])}
    offset, found = 0, {}
    while True:
        page = store.snapshot_page(saved['snapshot']['id'], offset=offset, limit=200)
        require(page['snapshot'] == saved['snapshot'], 'Runtime5 archive Snapshot metadata differs')
        found.update({r.id: r for r in page['records'] if r.id in wanted})
        if page['next_offset'] is None:
            break
        offset = page['next_offset']
    require(set(found) == wanted and found[journal.prefix + str(old['revision'])].details['state'] == old,
            'Runtime5 archive Snapshot omits exact terminal Journal or markers')
    return saved


def archive_runtime5_loss(store, directory, observation):
    validate_observation(observation, expected_runtime_id=EXPECTED['runtime_id'])
    journal = WorkflowJournal(store, EXPECTED['task_id'])
    with journal.lock():
        scope = load_scope(store, directory)
        intent_id, result_id = identities()
        intent = load_intent(store, scope)
        state = journal.load()
        if intent is None:
            validate_original(store, scope, state)
            intent = Decision(intent_id, EXPECTED['task_id'], 'note', EXPECTED['task_id'], REASON,
                {'type': 'p35_runtime5_unsubmitted_loss_intent.v1', 'scope': deepcopy(EXPECTED),
                 'digests': scope['digests'], 'observation': deepcopy(observation), 'original_state': deepcopy(state)})
            store.append(intent, intent.id)
        require(intent.details['observation'] == observation, 'Runtime5 immutable archival input changed')
        original = intent.details['original_state']
        jobs = validate_original(store, scope, original, recovering=True)
        active = jobs[EXPECTED['job_ids']['length-row']]
        existing = verified_cancel_result(store, active, optional=True)
        final = terminal(original, intent_id, existing) if existing is not None else None
        if final is not None:
            final['revision'] += 1
        require(state == original or state == final, 'Journal changed outside Runtime5 archival recovery')
        result = integrate_result(store, active, {}, cancelled=True)
        require(verified_cancel_result(store, active) == result, 'Evaluator cancellation differs')
        final = terminal(original, intent_id, result)
        if state == original:
            journal.save(final, REASON)
        else:
            final['revision'] += 1
            require(state == final, 'Journal changed outside Runtime5 archival recovery')
        summary = summary_for(intent, jobs, result)
        note = Decision(result_id, EXPECTED['task_id'], 'note', EXPECTED['task_id'], REASON,
            {'type': 'p35_runtime5_unsubmitted_loss_result.v1', 'intent_id': intent_id, 'summary': summary})
        store.append(note, note.id)
        snapshot = store.create_snapshot(EXPECTED['task_id'], stable_id('p35-r5-loss-snapshot', intent_id))
        output = {'schema_version': 1, 'intent_id': intent_id, 'decision_id': result_id, 'summary': summary, 'snapshot': snapshot}
        write_once(Path(directory) / 'real-round-2-runtime5/runtime-loss-archive.json', output)
        return require_archived_runtime5_loss(store, directory)


if __name__ == '__main__':
    from rsi_kernel_agent.store import ExperimentStore
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--observation', type=Path, required=True)
    args = parser.parse_args()
    require((args.directory / 'experiments.sqlite3').is_file(), 'Original Store is required')
    store = ExperimentStore(args.directory / 'experiments.sqlite3', args.directory / 'artifacts')
    output = archive_runtime5_loss(store, args.directory, load(args.observation))
    print(canonical_json({'decision_id': output['decision_id'], 'snapshot': output['snapshot'],
        'revision': output['summary']['original_revision'] + 1,
        'spent': output['summary']['spent_after'], 'durable_backend_requests': 0,
        'durable_backend_receipts': 0, 'incomplete_runs': 1, 'not_run': 3,
        'round_execution_completed': False}))
