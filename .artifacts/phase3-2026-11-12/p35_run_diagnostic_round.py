"""Run exactly two frozen diagnostic roles through the original task Controller.

Usage after completing the genuine conversation Analyzer session:
  python -B p35_run_diagnostic_round.py selection.json --steps 1

The additional OS lock excludes overlapping diagnostic drivers for this Task.
Other host drivers must remain stopped; Controller retains its original Task
lock and operation recovery. No retry, repair, or new budget is created here.
"""
import argparse
import asyncio
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.contracts import Decision, canonical_json
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.knowledge_mcp import StdioKnowledge
from rsi_kernel_agent.profiler.outputs import publish_result, require
from rsi_kernel_agent.profiler.session import _write
from rsi_kernel_agent.workflow.controller import Controller, stable
from rsi_kernel_agent.workflow.colab import ColabBackend
from rsi_kernel_agent.workflow.__main__ import colab_remote
from rsi_kernel_agent.workflow.intake import intake_publication
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from rsi_kernel_agent.workflow.scheduling import choose_jobs, validate_limits
from real_round_aux import (LIMITS, completed_session, load_prepared, read_json,
                            runtime_config_path, write_once)
from p35_prepare_diagnostic_round import (COST, PLAN_ROLES, build_packet,
    prerequisites, require_prepared_history, runtime_binding, scope_path, target_for, validate_selection)


@contextmanager
def driver_lock(store, task_id):
    journal = WorkflowJournal(store, task_id)
    journal.lock_path = journal.lock_path.with_name(journal.lock_path.name + '.p35-diagnostic-driver')
    with journal.lock():
        yield


def check_scope(state, selection, checkpoint, expected, context_id):
    """Reject extra attempts, unrelated scheduling and spending outside this pair."""
    require(state['limits'] == checkpoint['limits'] == validate_limits(LIMITS)
            and state['task_id'] == selection['task_id'] and not state['cancelled'],
            'Diagnostic task budget or cancellation changed')
    jobs = {key: job for key, job in state['jobs'].items() if job['round_id'] == selection['round_id']}
    other = {key: job for key, job in state['jobs'].items() if job['round_id'] != selection['round_id']}
    require(other == checkpoint['prior_jobs'], 'Other task jobs changed during diagnostic execution')
    require(all(job['state'] in ('completed', 'failed', 'cancelled') for job in other.values()),
            'Other work is unfinished')
    require(set(jobs) == set(expected), 'Diagnostic must retain exactly the two admitted jobs')
    for jid, job in jobs.items():
        admitted = expected[jid]
        require(all(job[key] == admitted[key] for key in admitted)
                and job['context_id'] == context_id and job['parent_evaluation_id'] is None
                and job['candidate_id'] == stable('wf-c', jid)
                and job['run_id'] == stable('wf-r', jid + ':0')
                and job['attempt'] == 0 and job['repair_depth'] == 0
                and not job.get('retry_of') and not job.get('repair_of')
                and job['variant']['cost'] == COST,
                'Diagnostic job identity, context, variant, or attempt changed')
    charged = {key: sum(job['variant']['cost'][key] for job in jobs.values() if job['reserved']) for key in COST}
    require(state['spent'] == {key: checkpoint['spent'][key] + charged[key] for key in COST},
            'Diagnostic spending differs from its two original reservations')
    require(charged['executions'] <= 2 and charged['time_s'] <= 3600 and charged['tokens'] == 0,
            'Diagnostic exceeds the two-execution budget')
    return list(jobs.values())


def preview_pair(state, jobs):
    temporary = deepcopy(state)
    temporary['jobs'].update({job['id']: dict(deepcopy(job), state='queued', reserved=False) for job in jobs})
    for label in (PLAN_ROLES['baseline'], PLAN_ROLES['single_tile']):
        chosen = choose_jobs(temporary)
        require(len(chosen) == 1 and temporary['jobs'][chosen[0]]['variant']['label'] == label,
                'Diagnostic pair is blocked by budget, pause, concurrency, or order')
        temporary['jobs'][chosen[0]]['state'] = 'completed'


async def run(selection_path, *, steps=1, interval=15, directory=None):
    require(type(steps) is int and steps > 0 and type(interval) is int and 1 <= interval <= 60,
            'Need positive steps and 1..60 second polling interval')
    directory = Path(directory) if directory is not None else Path(__file__).resolve().parent
    require((directory / 'experiments.sqlite3').is_file(), 'Original Store is required')
    selection = validate_selection(read_json(selection_path))
    target = target_for(directory, selection)
    require(read_json(scope_path(directory, selection)) == selection
            and read_json(target / 'selection.json') == selection, 'Frozen diagnostic selection differs')
    require(read_json(target / 'runtime-binding.json') == runtime_binding(selection),
            'Diagnostic Runtime binding differs')
    store = ExperimentStore(directory / 'experiments.sqlite3', directory / 'artifacts')
    with driver_lock(store, selection['task_id']):
        state, origins = prerequisites(store, directory, selection)
        packet, expected_variants = build_packet(store, selection, origins, persist_config=False)
        require(read_json(target / 'diagnostic-packet.json') == packet, 'Frozen diagnostic packet differs')
        variants = read_json(target / 'variants.json')
        require(variants == expected_variants, 'Frozen diagnostic variants differ from original role artifacts')
        context_id = packet['context']['id']
        prepared = load_prepared(store, selection['task_id'], context_id,
                                 [selection['strategy_id']], selection['round_id'])
        require(prepared is not None, 'Diagnostic round must be prepared first')
        require_prepared_history(store, directory, selection, prepared)
        result = completed_session(store, target, prepared['manifest'])
        plans = {plan['id']: plan for plan in result['plan_set']['plans']}
        require(len(result['plan_set']['plans']) == 2 and set(plans) == set(PLAN_ROLES.values()),
                'Need exactly the two diagnostic Plans')
        for pid, plan in plans.items():
            require(plan['strategy_id'] == selection['strategy_id'] and plan['variants'] == [pid]
                    and len(variants[pid]) == 1, 'Diagnostic Plan differs from one frozen role')
        key = selection['round_id'] + ':' + prepared['manifest'].artifact_ref.digest
        execution = {'selection': selection, 'packet': packet, 'variants': variants, 'publication_key': key,
                     'result_digest': sha256(canonical_json(result).encode()).hexdigest()}
        write_once(target / 'execution-input.json', execution)
        checkpoint_path = target / 'budget-checkpoint.json'
        if checkpoint_path.exists():
            checkpoint = read_json(checkpoint_path)
        else:
            require(not any(job['round_id'] == selection['round_id'] for job in state['jobs'].values()),
                    'Existing diagnostic jobs have no original budget checkpoint')
            checkpoint = {'task_id': selection['task_id'], 'round_id': selection['round_id'],
                          'revision': state['revision'], 'spent': state['spent'], 'limits': state['limits'],
                          'prior_jobs': state['jobs']}
            write_once(checkpoint_path, checkpoint)
        require(checkpoint['task_id'] == selection['task_id'] and checkpoint['round_id'] == selection['round_id'],
                'Diagnostic budget checkpoint identity differs')
        journal = WorkflowJournal(store, selection['task_id'])
        prior_note = store.get(journal.prefix + str(checkpoint['revision']))
        require(type(prior_note) is Decision and prior_note.details.get('type') == 'workflow_state.v1',
                'Diagnostic budget checkpoint has no original journal revision')
        prior_state = prior_note.details['state']
        require(prior_state['jobs'] == checkpoint['prior_jobs'] and prior_state['spent'] == checkpoint['spent']
                and prior_state['limits'] == checkpoint['limits'] and not any(
                    job['round_id'] == selection['round_id'] for job in prior_state['jobs'].values()),
                'Diagnostic budget checkpoint differs from the original pre-admission journal')
        marker = publish_result(store, prepared['manifest'], result, key)
        async with StdioKnowledge(ROOT / 'knowledge-site') as knowledge:
            intake = await intake_publication(store, marker.id, selection['round_id'], variants, knowledge)
        require(len(intake['items']) == 2 and all(item['status'] == 'accepted' for item in intake['items']),
                'Both diagnostic Plans must be accepted')
        jobs = [job for item in intake['items'] for job in item['jobs']]
        require(len(jobs) == 2 and len({job['id'] for job in jobs}) == 2
                and {job['variant']['label'] for job in jobs} == set(PLAN_ROLES.values())
                and all(job['context_id'] == context_id and job['parent_evaluation_id'] is None for job in jobs),
                'Intake differs from the two independent diagnostic Runs')
        expected = {job['id']: job for job in jobs}
        existing = {jid for jid, job in state['jobs'].items() if job['round_id'] == selection['round_id']}
        if existing:
            check_scope(state, selection, checkpoint, expected, context_id)
        else:
            require(state['jobs'] == checkpoint['prior_jobs'] and state['spent'] == checkpoint['spent'],
                    'Task changed after diagnostic budget checkpoint')
            preview_pair(state, jobs)
        write_once(target / 'intake.json', intake)
        controller = Controller(store, selection['task_id'],
            ColabBackend(store, colab_remote(runtime_config_path(directory, runtime_binding(selection)))), limits=LIMITS)
        controller.accept_intake(intake)
        for index in range(steps):
            state = controller.status()
            rows = check_scope(state, selection, checkpoint, expected, context_id)
            if not all(job['state'] in ('completed', 'failed', 'cancelled') for job in rows):
                controller.step()
            state = controller.status()
            rows = check_scope(state, selection, checkpoint, expected, context_id)
            _write(target / 'controller-status.json', state)
            print(canonical_json({'revision': state['revision'], 'jobs': [
                {key: job.get(key) for key in ('id', 'run_id', 'state', 'stage', 'reason')} for job in rows]}), flush=True)
            if all(job['state'] in ('completed', 'failed', 'cancelled') for job in rows):
                snapshot = store.create_snapshot(selection['task_id'], selection['round_id'] + ':completed-snapshot')
                write_once(target / 'completed.json', {'round_id': selection['round_id'], 'context_id': context_id,
                    'snapshot': snapshot, 'publication_id': marker.id, 'intake_id': intake['id'],
                    'results': {job['id']: job['result'] for job in rows},
                    'charged_cost': {key: state['spent'][key] - checkpoint['spent'][key] for key in COST},
                    'limitations': ['Separate zero-length diagnostic; timings cannot replace original Workload or ABBA results.',
                                    'LSE is not exposed by the frozen interface and was not tested.']})
                break
            if any(job['state'] == 'blocked' for job in rows) or not any(
                    job['state'] in ('queued', 'active', 'evaluating') for job in rows):
                break
            if index + 1 < steps:
                time.sleep(interval)
        return {'directory': str(target), 'round_id': selection['round_id'], 'workflow_state': state['workflow_state']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('selection', type=Path)
    parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--steps', type=int, default=1)
    parser.add_argument('--interval', type=int, default=15)
    args = parser.parse_args()
    print(canonical_json(asyncio.run(run(args.selection, directory=args.directory, steps=args.steps, interval=args.interval))))
