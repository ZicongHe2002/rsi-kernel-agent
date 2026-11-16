"""Resume only the original admitted Runtime N >= 6 two-role diagnostic pair.

Original publication/intake and Journal acceptance must already exist. Large
manifest/result artifacts are hashed once, without replaying Analyzer history.
No publication, intake, retry, repair, allocation, or budget grant is performed.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.contracts import (ArtifactRef, Candidate, Decision, EvaluationBundle,
    ParentCandidate, Plan, Run, canonical_json)
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.profiler.outputs import require
from rsi_kernel_agent.profiler.session import _write
from rsi_kernel_agent.workflow.controller import Controller
from rsi_kernel_agent.workflow.colab import ColabBackend
from rsi_kernel_agent.workflow.__main__ import colab_remote
from rsi_kernel_agent.workflow.execution import make_request, stable_id
from rsi_kernel_agent.workflow.intake import _digest, _id
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from rsi_kernel_agent.workflow.scheduling import validate_limits
from real_round_aux import LIMITS, read_json, runtime_config_path, write_once
from p35_prepare_diagnostic_round import (COST, PLAN_ROLES, build_packet, runtime_binding,
    scope_path, target_for, validate_selection)
from p35_recovery_round import two_candidate_recovery
from p35_run_diagnostic_round import check_scope, driver_lock

TERMINAL = ('completed', 'failed', 'cancelled')
PRIOR_SPENT = {'executions': 13, 'tokens': 0, 'time_s': 23400}


def file_ref(path):
    digest, size = sha256(), 0
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
            size += len(block)
    return {'digest': digest.hexdigest(), 'size': size}


def checked_artifact(store, raw):
    ref = ArtifactRef(**raw)
    require(file_ref(store.artifacts.path_for(ref)) == raw, 'Frozen artifact bytes differ')


def result_marker(store, job, *, compare_job=True):
    marker = store.get(stable_id('workflow-result', job['run_id']))
    require(type(marker) is Decision and marker.kind == 'note'
            and marker.task_id == job['task_id'] and marker.target_id == job['run_id']
            and marker.details.get('type') == 'workflow_result.v1'
            and marker.details.get('job_id') == job['id']
            and (not compare_job or marker.details.get('result') == job['result']),
            'Run result differs from original integrated result marker')
    return marker.details['result']


def admitted_pair(store, directory, target, selection, state):
    """Authenticate the existing admission without preparing or replaying it."""
    require(state is not None, 'Existing admitted Controller journal required')
    task, round_id, sid = (selection[key] for key in ('task_id', 'round_id', 'strategy_id'))
    binding = runtime_binding(selection)
    require(read_json(scope_path(directory, selection)) == read_json(target / 'selection.json') == selection
            and read_json(target / 'runtime-binding.json') == binding
            and read_json(directory / 'active-runtime-binding.json') == binding,
            'Frozen diagnostic selection or active Runtime binding differs')
    config_path = runtime_config_path(directory, binding)
    config = read_json(config_path)
    packet = read_json(target / 'diagnostic-packet.json')
    variants = read_json(target / 'variants.json')
    manifest = read_json(target / 'manifest-ref.json')
    session = read_json(target / 'session.json')
    inputs = read_json(target / 'execution-input.json')
    intake = read_json(target / 'intake.json')
    checkpoint = read_json(target / 'budget-checkpoint.json')
    context_id = packet['context']['id']
    require(set(checkpoint) == {'task_id', 'round_id', 'revision', 'spent', 'limits', 'prior_jobs'}
            and checkpoint['task_id'] == task and checkpoint['round_id'] == round_id
            and checkpoint['spent'] == PRIOR_SPENT and checkpoint['limits'] == validate_limits(LIMITS),
            'Original thirteen-execution diagnostic checkpoint required')
    journal = WorkflowJournal(store, task)
    prior_note = store.get(journal.prefix + str(checkpoint['revision']))
    require(type(prior_note) is Decision and prior_note.kind == 'note' and prior_note.task_id == task
            and prior_note.details.get('type') == 'workflow_state.v1',
            'Diagnostic checkpoint has no original Journal revision')
    prior = prior_note.details['state']
    require(prior['task_id'] == task and prior['revision'] == checkpoint['revision']
            and prior['jobs'] == checkpoint['prior_jobs'] and prior['spent'] == checkpoint['spent']
            and prior['limits'] == checkpoint['limits'] and not prior['cancelled']
            and all(job['round_id'] != round_id and job['state'] in TERMINAL for job in prior['jobs'].values()),
            'Diagnostic checkpoint differs from original pre-admission Journal')

    key = round_id + ':' + manifest['digest']
    publication_id = 'analysis:' + sha256(key.encode()).hexdigest()[:24] + ':complete'
    require(intake['task_id'] == task and intake['round_id'] == round_id
            and intake['publication_id'] == publication_id
            and intake['id'] == _id('wf-intake', round_id, publication_id),
            'Diagnostic intake identity differs')
    intake_note = store.get(intake['id'])
    fingerprint = sha256(json.dumps({'publication_id': publication_id, 'round_id': round_id,
        'variants': variants}, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    require(type(intake_note) is Decision and intake_note.kind == 'note'
            and intake_note.task_id == intake_note.target_id == task
            and intake_note.details == {'type': 'workflow_intake.v1', 'request_digest': fingerprint, 'result': intake}
            and state['intakes'].get(intake['id']) == _digest(intake),
            'Diagnostic intake differs from original accepted request and Journal digest')
    publication = store.get(publication_id)
    require(type(publication) is Decision and publication.kind == 'note'
            and publication.task_id == publication.target_id == task
            and publication.details.get('type') == 'analysis_result.v1'
            and publication.details['manifest'] == manifest
            and session['schema_version'] == 1 and session['manifest'] == manifest
            and session['result_digest'] == publication.details['result']['digest'],
            'Diagnostic frozen publication, manifest, or session differs')
    require(inputs == {'selection': selection, 'packet': packet, 'variants': variants,
                       'publication_key': key, 'result_digest': session['result_digest']},
            'Diagnostic frozen execution input differs')
    checked_artifact(store, manifest)
    checked_artifact(store, publication.details['result'])
    require(file_ref(target / 'result.json') == publication.details['result']
            and read_json(target / 'packet.json') == {
                'status': 'complete', 'result': 'result.json', 'digest': session['result_digest']},
            'Completed Analyzer result bytes or completion packet differ')

    baseline = read_json(directory / ('baseline-repair-' + binding['generation'] + '-job.json'))
    singles = [job for job in checkpoint['prior_jobs'].values()
        if job['round_id'] == 'real-runtime2-round:1' and job['strategy_id'] == sid
        and job['variant']['label'] == 'single-tile']
    require(len(singles) == 1, 'Original unique single-tile source attempt required')
    origins = {'baseline': baseline, 'single_tile': singles[0]}
    for role, origin in origins.items():
        require(origin['task_id'] == task and origin['strategy_id'] == sid
                and (role == 'baseline' or origin['state'] == 'completed'),
                'Diagnostic source origin belongs to another task, strategy, or incomplete Run')
        candidate, run = store.get(origin['candidate_id']), store.get(origin['run_id'])
        require(type(candidate) is Candidate and type(run) is Run
                and run.candidate_id == candidate.id and run.context_id == origin['context_id']
                and candidate.identity.source_digest == origin['variant']['source_ref']['digest']
                and candidate.identity.config_digest == origin['variant']['config_ref']['digest']
                and candidate.identity.build_digest == origin['variant']['build_ref']['digest']
                and store.get(candidate.plan_id).strategy_id == sid,
                'Diagnostic source Candidate or original Context identity differs')
        make_request(store, origin, 'generate')
        result = result_marker(store, origin, compare_job=role != 'baseline')
        evaluation = store.get(result['evaluation_id'])
        require(result['run_id'] == origin['run_id'] and result['candidate_id'] == origin['candidate_id']
                and result['provenance'] == 'real' and result['correctness'] == 'passed'
                and result['timing_validity'] == 'valid' and result['confirmed'] is True
                and type(evaluation) is EvaluationBundle and evaluation.run_id == origin['run_id']
                and evaluation.correctness == 'passed' and evaluation.timing_validity == 'valid'
                and evaluation.confirmed is True and evaluation.latency_ms == result['latency_ms']
                and store.run_state(origin['run_id'])['execution_status'] == 'succeeded',
                'Diagnostic original source lacks an integrated successful real result')
    require(baseline['context_id'] == selection['context_id'] == packet['original_context_id']
            and baseline['contract']['context']['id'] == selection['context_id']
            and baseline['contract']['context']['environment']['runtime_session_id'] == config['runtime_id'],
            'Diagnostic formal parent Context or Runtime config differs')
    expected_packet, expected_variants = build_packet(store, selection, origins, persist_config=False)
    require(packet == expected_packet and variants == expected_variants,
            'Frozen diagnostic source, config, workload, or environment differs')
    parent_id = stable_id('p35-diagnostic-parent', selection)
    parent = store.get(parent_id)
    require(type(parent) is ParentCandidate and parent.task_id == task
            and parent.candidate_id == singles[0]['candidate_id']
            and parent.identity == store.get(parent.candidate_id).identity,
            'Diagnostic parent differs from original single-tile Candidate')
    round_note = store.get(_id('wf-round', task, round_id))
    request_digest = _digest({'task_id': task, 'context_id': context_id, 'strategies': [sid],
        'round_id': round_id, 'knowledge': selection['knowledge'],
        'parents': {sid: parent_id}, 'parent_policy': 'explicit'})
    require(type(round_note) is Decision and round_note.kind == 'note'
            and round_note.task_id == round_note.target_id == task
            and round_note.details.get('type') == 'workflow_round.v1'
            and round_note.details['manifest'] == manifest and round_note.details['request_digest'] == request_digest
            and round_note.details['round_id'] == round_id and round_note.details['context_id'] == context_id
            and round_note.details['strategies'] == [sid], 'Original diagnostic preparation binding differs')
    require(len(intake['items']) == 2 and {item['proposal_id'] for item in intake['items']} == set(PLAN_ROLES.values())
            and all(item['status'] == 'accepted' and len(item['jobs']) == 1 for item in intake['items'])
            and set(variants) == set(PLAN_ROLES.values()), 'Exactly two accepted diagnostic roles required')
    mapped = {row['proposal_id']: row['record_id'] for row in publication.details['plans']}
    require(len(publication.details['plans']) == 2 and set(mapped) == set(PLAN_ROLES.values()),
            'Published diagnostic Plan scope differs')
    expected = {}
    for item in intake['items']:
        job, pid = item['jobs'][0], item['proposal_id']
        require(job['task_id'] == task and job['round_id'] == round_id and job['strategy_id'] == sid
                and job['parent_id'] == parent_id and item['parent_id'] == parent_id
                and item['strategy_id'] == sid and item['context_id'] == context_id
                and job['plan_id'] == item['plan_id'] == mapped[pid]
                and type(store.get(job['plan_id'])) is Plan and store.get(job['plan_id']).strategy_id == sid
                and job['contract']['context'] == packet['context']
                and job['contract']['workload'] == packet['workload']
                and job['variant']['device'] == config['runtime_id']
                and job['variant']['label'] == pid and variants[pid] == [job['variant']],
                'Admitted diagnostic role, parent, contract, or variant differs')
        expected[job['id']] = job
    require(len(expected) == 2, 'Distinct original diagnostic jobs required')
    rows = check_scope(state, selection, checkpoint, expected, context_id)
    for job in rows:
        make_request(store, job, 'generate')  # Original immutable records and bundle validation; no submission.
    return {'intake': intake, 'checkpoint': checkpoint, 'expected': expected,
            'context_id': context_id, 'config_path': config_path, 'rows': rows}


def complete(store, target, selection, frozen, state, rows):
    require(all(job['state'] in TERMINAL and job.get('result') is not None for job in rows),
            'Terminal diagnostic lacks original integrated results')
    for job in rows:
        result_marker(store, job)
    snapshot = store.create_snapshot(selection['task_id'], selection['round_id'] + ':completed-snapshot')
    intake = frozen['intake']
    write_once(target / 'completed.json', {'round_id': selection['round_id'], 'context_id': frozen['context_id'],
        'snapshot': snapshot, 'publication_id': intake['publication_id'], 'intake_id': intake['id'],
        'results': {job['id']: job['result'] for job in rows},
        'charged_cost': {key: state['spent'][key] - frozen['checkpoint']['spent'][key] for key in COST},
        'limitations': ['Separate zero-length diagnostic; timings cannot replace original Workload or ABBA results.',
                        'LSE is not exposed by the frozen interface and was not tested.']})


def resume(selection_path, *, directory=None, steps=100, interval=15):
    require(type(steps) is int and steps > 0 and type(interval) is int and 1 <= interval <= 60,
            'Positive steps and 1..60 second polling interval required')
    directory = Path(directory) if directory is not None else Path(__file__).resolve().parent
    require((directory / 'experiments.sqlite3').is_file(), 'Original existing Store required')
    selection = validate_selection(read_json(selection_path))
    require(selection['schema_version'] == 2 and two_candidate_recovery(selection.get('runtime_generation')),
            'Only the admitted Runtime N >= 6 diagnostic scope is supported')
    target = target_for(directory, selection)
    store = ExperimentStore(directory / 'experiments.sqlite3', directory / 'artifacts')
    with driver_lock(store, selection['task_id']):
        journal = WorkflowJournal(store, selection['task_id'])
        state = journal.load()
        # Capture small mutable host files before validation, so a replacement
        # during the large artifact hashes cannot become a new trusted pin.
        paths = [target / name for name in ('selection.json', 'runtime-binding.json', 'diagnostic-packet.json',
            'variants.json', 'manifest-ref.json', 'execution-input.json', 'session.json', 'packet.json',
            'budget-checkpoint.json', 'intake.json')]
        paths += [Path(selection_path), scope_path(directory, selection),
                  runtime_config_path(directory, runtime_binding(selection)),
                  directory / 'active-runtime-binding.json',
                  directory / ('baseline-repair-' + selection['runtime_generation'] + '-job.json')]
        pins = {path: file_ref(path) for path in paths}
        frozen = admitted_pair(store, directory, target, selection, state)
        require(read_json(selection_path) == selection
                and all(file_ref(path) == ref for path, ref in pins.items()),
                'Diagnostic frozen input or Runtime config changed during validation')
        rows, controller = frozen['rows'], None
        for index in range(steps):
            require(all(file_ref(path) == ref for path, ref in pins.items()),
                    'Diagnostic frozen input or Runtime config changed during resume')
            state = journal.load()
            require(state['intakes'].get(frozen['intake']['id']) == _digest(frozen['intake']),
                    'Diagnostic accepted intake digest changed')
            rows = check_scope(state, selection, frozen['checkpoint'], frozen['expected'], frozen['context_id'])
            if all(job['state'] in TERMINAL for job in rows):
                state = Controller(store, selection['task_id'], None).status()
                rows = check_scope(state, selection, frozen['checkpoint'], frozen['expected'], frozen['context_id'])
                _write(target / 'controller-status.json', state)
                complete(store, target, selection, frozen, state, rows)
                break
            if any(job['strategy_id'] in state['paused'] for job in rows if job['state'] not in TERMINAL):
                break
            if controller is None:
                controller = Controller(store, selection['task_id'],
                    ColabBackend(store, colab_remote(frozen['config_path'])))
            controller.step()
            state = controller.status()
            rows = check_scope(state, selection, frozen['checkpoint'], frozen['expected'], frozen['context_id'])
            _write(target / 'controller-status.json', state)
            print(canonical_json({'revision': state['revision'], 'round_id': selection['round_id'],
                'jobs': [{key: job.get(key) for key in ('id', 'run_id', 'state', 'stage')} for job in rows]}), flush=True)
            if all(job['state'] in TERMINAL for job in rows):
                complete(store, target, selection, frozen, state, rows)
                break
            if not any(job['state'] in ('queued', 'active', 'evaluating', 'blocked') for job in rows):
                break
            if index + 1 < steps:
                time.sleep(interval)
        return {'round_id': selection['round_id'], 'directory': str(target),
                'completed': all(job['state'] in TERMINAL for job in rows),
                'revision': state['revision'], 'spent': state['spent']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('selection', type=Path)
    parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--steps', type=int, default=100)
    parser.add_argument('--interval', type=int, default=15)
    args = parser.parse_args()
    print(canonical_json(resume(args.selection, directory=args.directory, steps=args.steps, interval=args.interval)))
