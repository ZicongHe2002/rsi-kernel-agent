"""Resume an admitted round using original Controller operation identities.

No publication, intake, new attempt, retry, allocation, or budget initialization.
Unknown remains blocked; Controller queries the original operation before acting.
"""
import argparse
from hashlib import sha256
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.contracts import Decision, EvaluationBundle, ParentCandidate, canonical_json
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.workflow.controller import Controller, stable
from rsi_kernel_agent.workflow.colab import ColabBackend
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from rsi_kernel_agent.workflow.execution import stable_id
from rsi_kernel_agent.workflow.__main__ import colab_remote
from rsi_kernel_agent.profiler.session import _write
from rsi_kernel_agent.profiler.outputs import require
from rsi_kernel_agent.workflow.scheduling import validate_limits
from real_round_aux import LIMITS, read_json, runtime_binding, runtime_config_path, write_once
from p35_recovery_round import RUNTIME6_PLANS, recovery_identity, two_candidate_recovery
from p35_run_diagnostic_round import driver_lock

TERMINAL = ('completed', 'failed', 'cancelled')
COST = {'executions': 1, 'tokens': 0, 'time_s': 1800}


def admitted_jobs(store, target, intake, state, round_id, *, generation, runtime_id=None, baseline=None):
    require(state is not None and not state['cancelled'] and intake['id'] in state['intakes'],
            'Existing admitted Controller journal required')
    require(state['task_id'] == intake['task_id'] and intake['round_id'] == round_id,
            'Admitted round identity differs')
    require(state['limits'] == validate_limits(LIMITS), 'Original task budget differs')
    marker = store.get(intake['id'])
    require(type(marker) is Decision and marker.kind == 'note'
            and marker.task_id == marker.target_id == intake['task_id']
            and marker.details.get('type') == 'workflow_intake.v1'
            and marker.details['result'] == intake
            and state['intakes'][intake['id']] == sha256(canonical_json(intake).encode()).hexdigest(),
            'Intake differs from original admitted publication')
    require(all(job['round_id'] == round_id or job['state'] in TERMINAL for job in state['jobs'].values()),
            'Other round unfinished')
    jobs = {jid: job for jid, job in state['jobs'].items() if job['round_id'] == round_id}
    expected = {job['id']: job for item in intake['items'] if item['status'] == 'accepted' for job in item['jobs']}
    require(bool(expected) and set(expected) <= set(jobs), 'Admitted jobs are missing')
    if not two_candidate_recovery(generation):
        return jobs
    require(len(intake['items']) == len(expected) == len(jobs) == 2 and set(jobs) == set(expected)
            and {item['proposal_id'] for item in intake['items']} == set(RUNTIME6_PLANS)
            and all(item['status'] == 'accepted' and len(item['jobs']) == 1 for item in intake['items']),
            'Recovery resume requires exactly the two admitted A jobs')
    publication = store.get(intake['publication_id'])
    inputs = read_json(target / 'execution-input.json')
    manifest = read_json(target / 'manifest-ref.json')
    require(type(publication) is Decision and publication.kind == 'note'
            and publication.task_id == publication.target_id == intake['task_id']
            and publication.details.get('type') == 'analysis_result.v1'
            and inputs['round_id'] == round_id
            and inputs['manifest'] == manifest == publication.details['manifest']
            and inputs['result_digest'] == publication.details['result']['digest']
            and inputs['publication_key'] == round_id + ':' + manifest['digest'],
            'Recovery frozen publication binding differs')
    require(set(inputs['variants']) == set(RUNTIME6_PLANS), 'Recovery frozen variant scope differs')
    require(baseline['task_id'] == intake['task_id']
            and baseline['contract']['context']['environment']['runtime_session_id'] == runtime_id,
            'Recovery admitted baseline binding differs')
    mapped = {row['proposal_id']: row['record_id'] for row in publication.details['plans']}
    require(set(mapped) == set(RUNTIME6_PLANS), 'Recovery publication Plan scope differs')
    for item in intake['items']:
        pid = item['proposal_id']
        raw = item['jobs'][0]
        job = jobs[raw['id']]
        require(all(job[key] == value for key, value in raw.items())
                and item['plan_id'] == job['plan_id'] == mapped[pid]
                and job['strategy_id'] == baseline['strategy_id']
                and job['context_id'] == baseline['context_id']
                and job['contract']['context'] == baseline['contract']['context']
                and job['contract']['context']['provenance'] == 'real'
                and job['variant']['device'] == runtime_id
                and job['variant']['label'] == RUNTIME6_PLANS[pid]
                and inputs['variants'][pid] == [job['variant']] and job['variant']['cost'] == COST
                and job['candidate_id'] == stable('wf-c', job['id'])
                and job['run_id'] == stable('wf-r', job['id'] + ':0')
                and job['attempt'] == job['repair_depth'] == 0
                and not job.get('retry_of') and not job.get('repair_of'),
                'Recovery admitted job, binding, variant, or attempt differs')
        parent = store.get(job['parent_id'])
        evaluation = store.get(job['parent_evaluation_id'])
        require(type(parent) is ParentCandidate and parent.candidate_id == baseline['candidate_id']
                and type(evaluation) is EvaluationBundle and evaluation.run_id == baseline['run_id'],
                'Recovery resume parent is not the admitted current-Context A baseline')
    return jobs


def complete(store, target, intake, round_id, jobs):
    require(jobs and all(job['state'] in TERMINAL and job.get('result') is not None for job in jobs.values()),
            'Terminal round lacks original integrated results')
    for job in jobs.values():
        marker = store.get(stable_id('workflow-result', job['run_id']))
        require(type(marker) is Decision and marker.kind == 'note'
                and marker.task_id == intake['task_id'] and marker.target_id == job['run_id']
                and marker.details.get('type') == 'workflow_result.v1'
                and marker.details.get('job_id') == job['id'] and marker.details.get('result') == job['result'],
                'Terminal round differs from original result marker')
    snapshot = store.create_snapshot(intake['task_id'], round_id + ':completed-snapshot')
    write_once(target / 'completed.json', {'round_id': round_id, 'snapshot': snapshot,
        'publication_id': intake['publication_id'], 'intake_id': intake['id'],
        'results': {jid: job['result'] for jid, job in jobs.items()}})


def resume(number, *, steps=100, interval=15, directory=None, generation=None):
    require(type(number) is int and number in (1, 2), 'Round must be 1 or 2')
    require(type(steps) is int and steps > 0 and type(interval) is int and 1 <= interval <= 60,
            'Positive steps and 1..60 second polling interval required')
    require(generation is None or (two_candidate_recovery(generation) and number == 2),
            'Only round 2 supports explicit runtimeN >= 6 resume')
    directory = Path(directory) if directory is not None else Path(__file__).resolve().parent
    target = directory / ('real-round-' + str(number))
    round_id = 'real-runtime2-round:' + str(number)
    if generation is not None:
        target, round_id = recovery_identity(directory, generation)
    require((directory / 'experiments.sqlite3').is_file(), 'Existing experiment database required')
    binding = runtime_binding(directory, target)
    require(generation is None or binding == {'generation': generation}, 'Recovery frozen binding differs')
    intake = read_json(target / 'intake.json')
    store = ExperimentStore(directory / 'experiments.sqlite3', directory / 'artifacts')
    with driver_lock(store, intake['task_id']):
        baseline = read_json(directory / ('baseline-repair-' + generation + '-job.json')) if generation is not None else None
        config_path = runtime_config_path(directory, binding)
        runtime_id = read_json(config_path)['runtime_id'] if generation is not None else None
        state = WorkflowJournal(store, intake['task_id']).load()
        jobs = admitted_jobs(store, target, intake, state, round_id, generation=generation,
                             runtime_id=runtime_id, baseline=baseline)
        other = {jid: job for jid, job in state['jobs'].items() if jid not in jobs}
        budget_anchor = {key: state['spent'][key] - sum(job['variant']['cost'][key]
                         for job in jobs.values() if job['reserved']) for key in COST}
        if generation is not None:
            require(budget_anchor == {'executions': 11, 'tokens': 0, 'time_s': 19800},
                    'Recovery resume changed the preserved prior spending')
        controller = None
        for index in range(steps):
            if all(job['state'] in TERMINAL for job in jobs.values()):
                complete(store, target, intake, round_id, jobs)
                break
            if any(job['strategy_id'] in state['paused'] for job in jobs.values() if job['state'] not in TERMINAL):
                break
            if controller is None:
                require(runtime_binding(directory, target) == binding, 'Runtime binding changed before resume')
                if generation is not None:
                    require(read_json(config_path)['runtime_id'] == runtime_id, 'Runtime config changed before resume')
                controller = Controller(store, intake['task_id'], ColabBackend(store, colab_remote(config_path)))
            controller.step()
            state = controller.status()
            jobs = admitted_jobs(store, target, intake, state, round_id, generation=generation,
                                 runtime_id=runtime_id, baseline=baseline)
            if generation is not None:
                require({jid: job for jid, job in state['jobs'].items() if jid not in jobs} == other,
                        'Other task jobs changed during recovery resume')
                require(state['spent'] == {key: budget_anchor[key] + sum(job['variant']['cost'][key]
                    for job in jobs.values() if job['reserved']) for key in COST},
                    'Recovery resume spending differs from original reservations')
            _write(target / 'controller-status.json', state)
            print(canonical_json({'revision': state['revision'], 'round_id': round_id,
                'jobs': [{key: job[key] for key in ('id', 'run_id', 'state', 'stage')} for job in jobs.values()]}), flush=True)
            if all(job['state'] in TERMINAL for job in jobs.values()):
                complete(store, target, intake, round_id, jobs)
                break
            if not any(job['state'] in ('queued', 'active', 'evaluating', 'blocked') for job in jobs.values()):
                break
            if index + 1 < steps:
                time.sleep(interval)
        return {'round_id': round_id, 'state_file': str(target / 'controller-status.json'),
                'completed': all(job['state'] in TERMINAL for job in jobs.values()),
                'revision': state['revision'], 'spent': state['spent']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('round', type=int, choices=(1, 2))
    parser.add_argument('--steps', type=int, default=100)
    parser.add_argument('--interval', type=int, default=15)
    parser.add_argument('--generation', default=None, help='runtimeN, N >= 6; omitted preserves original round lookup')
    args = parser.parse_args()
    print(canonical_json(resume(args.round, steps=args.steps, interval=args.interval, generation=args.generation)))
