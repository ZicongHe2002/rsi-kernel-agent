"""Record loss after all four prerequisites, before any exploration Run exists.

This path adds two notes and an immutable Snapshot only. It never creates or
cancels a Run, integrates results, changes Journal state, or contacts a Runtime.
Incomplete admission or any exploration submission requires another reviewed
recovery path and is deliberately rejected here.
"""
import argparse
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.contracts import ArtifactRef, Decision, EvaluationContext, Run, canonical_json
from rsi_kernel_agent.workflow.execution import stable_id
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from p35_archive_unsubmitted_runtime_loss import validate_observation, maybe
from p35_runtime_generations import generation_number
from real_round_aux import read_json, require, require_gates, write_once

NAMES = ('oracle-negative-attention', 'oracle-negative-cache', 'baseline-repair', 'baseline-b')
INTENT = 'p35_completed_prerequisite_loss_intent.v1'
RESULT = 'p35_completed_prerequisite_loss_result.v1'


def digest(value):
    return sha256(canonical_json(value).encode('utf-8')).hexdigest()


def output_path(directory, generation):
    generation_number(generation, minimum=6)
    return Path(directory) / (generation + '-completed-prerequisite-loss-archive.json')


def require_previous_archives(store, directory, generation):
    from admit_runtime4 import require_generation_archive
    return require_generation_archive(store, directory, generation)


def identities(task_id, generation, runtime_id):
    intent = stable_id('p35-completed-loss-intent', task_id, generation, runtime_id)
    return intent, stable_id('p35-completed-loss-result', intent)


def verify_report(store, directory, generation, report):
    """Bind a previously completed NPZ audit to current immutable small evidence."""
    generation_number(generation, minimum=6)
    directory = Path(directory)
    config = read_json(directory / ('colab-config-' + generation + '.json'))
    require(report.get('generation') == generation and report.get('runtime_id') == config['runtime_id']
        and report.get('status') == generation + '_prerequisites_verified'
        and report.get('counts') == {'verified': 4, 'pending': 0, 'failed_verification': 0}
        and report.get('completed_views') == 4
        and report.get('aggregate_gate_verification') == 'all_four_original_gates_verified',
        'Previous generation lacks complete four-prerequisite verification')
    rows = report.get('jobs', [])
    require(len(rows) == 4 and {r.get('name') for r in rows} == set(NAMES), 'Verification names differ')
    jobs, results = {}, {}
    for row in rows:
        name = row['name']
        raw = (directory / (name + '-' + generation + '-job.json')).read_bytes()
        job = json.loads(raw)
        marker = store.get(stable_id('workflow-result', job['run_id']))
        require(type(marker) is Decision and marker.kind == 'note' and marker.task_id == job['task_id']
            and marker.target_id == job['run_id'] and marker.details.get('type') == 'workflow_result.v1'
            and marker.details.get('job_id') == job['id'] and marker.details.get('cancelled') is False,
            'Previous generation completion marker differs')
        result = marker.details['result']
        require(row.get('verification') == 'verified' and row.get('job_file_sha256') == sha256(raw).hexdigest()
            and row.get('result_digest') == digest(result) and row.get('id') == job['id']
            and row.get('run_id') == job['run_id'] and row.get('context_id') == job['context_id']
            and row.get('runtime_id') == config['runtime_id']
            and job['contract']['context']['environment']['runtime_session_id'] == config['runtime_id']
            and result['run_id'] == job['run_id'] and result['candidate_id'] == job['candidate_id'],
            'Previous verification report does not bind exact job/result/runtime')
        run = store.get(job['run_id'])
        context = store.get(job['context_id'])
        require(type(run) is Run and run.context_id == job['context_id'] and run.candidate_id == job['candidate_id']
            and type(context) is EvaluationContext and context.task_id == job['task_id']
            and context.environment == job['contract']['context']['environment']
            and context.provenance == 'real', 'Previous prerequisite Run/Context differs')
        negative = name.startswith('oracle-negative')
        require(result['correctness'] == ('failed' if negative else 'passed')
            and result['confirmed'] is (not negative)
            and (negative or result['timing_validity'] == 'valid'), 'Previous prerequisite outcome differs')
        # Initial archive also invokes the original full gates below. Later
        # guards rebind this report and immutable receipt bytes, not large NPZ.
        for ref in result['receipt_refs'].values():
            store.artifacts.read(ArtifactRef(**ref))
        jobs[name], results[name] = job, result
    require(len({j['run_id'] for j in jobs.values()}) == 4
        and len({j['task_id'] for j in jobs.values()}) == 1, 'Previous prerequisite identities overlap')
    return config, jobs, results


def no_exploration(store, directory, generation, config, jobs, results, state):
    task_id = jobs['baseline-repair']['task_id']
    runtime_id = config['runtime_id']
    history = store.history(task_id)
    contexts = {r.id for r in history if type(r) is EvaluationContext
                and r.environment.get('runtime_session_id') == runtime_id}
    gate_runs = {j['run_id'] for j in jobs.values()}
    require(contexts == {j['context_id'] for j in jobs.values()}, 'Runtime acquired another Context')
    require({r.id for r in history if type(r) is Run and r.context_id in contexts} == gate_runs,
            'Exploration Run exists; completed-prerequisite loss path is forbidden')
    require(not state['cancelled'], 'Original Task was cancelled')
    gate_refs = {job['run_id']: results[name]['receipt_refs'] for name, job in jobs.items()}
    def related(job):
        return job.get('context_id') in contexts or job.get('variant', {}).get('device') == runtime_id
    require(not any(related(j) for j in state['jobs'].values()), 'Runtime acquired Controller exploration jobs')
    for record in history:
        if type(record) is not Decision:
            continue
        d = record.details
        if d.get('type') == 'workflow_intake.v1':
            require(not any(related(j) for item in d.get('result', {}).get('items', [])
                for j in item.get('jobs', [])), 'Runtime acquired exploration intake')
        if d.get('type') in ('colab_execution_request.v1', 'execution_request.v1', 'execution_receipt.v1'):
            request = d.get('request', {})
            job = request.get('job', {})
            bound = (d.get('runtime_id') == runtime_id or related(job)
                or job.get('contract', {}).get('context', {}).get('environment', {}).get('runtime_session_id') == runtime_id)
            if d['type'] == 'execution_receipt.v1':
                body = json.loads(store.artifacts.read(ArtifactRef(**d['artifact_ref'])))
                receipt_bound = body.get('context_id') in contexts or body.get('run_id') in gate_runs
                if receipt_bound:
                    run_id = body.get('run_id')
                    require(run_id in gate_runs and record.target_id == run_id
                        and body.get('context_id') in contexts
                        and gate_refs[run_id].get(body.get('stage')) == d['artifact_ref']
                        and body.get('operation_id') == d.get('operation_id')
                        and body.get('request_digest') == d.get('request_digest'),
                        'Runtime has an orphan or unbound exploration receipt')
            if bound:
                require(record.target_id in gate_runs and job.get('run_id', record.target_id) in gate_runs,
                        'Runtime has an orphan or exploration backend submission')
    target = Path(directory) / ('real-round-2-' + generation)
    require(not any((target / name).exists() for name in ('intake.json', 'execution-input.json', 'completed.json')),
            'Runtime candidate dispatch files exist; explicit reconciliation required')
    return contexts


def frozen_inputs(store, directory, generation, report):
    config, jobs, results = verify_report(store, directory, generation, report)
    state = WorkflowJournal(store, jobs['baseline-repair']['task_id']).load()
    contexts = no_exploration(store, directory, generation, config, jobs, results, state)
    return {'config_digest': digest(config), 'jobs_digest': digest(jobs),
        'results_digest': digest(results), 'report_digest': digest(report),
        'task_id': jobs['baseline-repair']['task_id'], 'runtime_id': config['runtime_id'],
        'context_id': jobs['baseline-repair']['context_id'], 'context_ids': sorted(contexts),
        'gate_run_ids': sorted(j['run_id'] for j in jobs.values())}, jobs, results, state


def summary_for(inputs, state, generation):
    return {**deepcopy(inputs), 'generation': generation, 'controller_revision': state['revision'],
        'state_digest': digest(state), 'spent': deepcopy(state['spent']), 'limits': deepcopy(state['limits']),
        'exploration_run_ids': [], 'exploration_job_ids': [], 'exploration_durable_requests': 0,
        'exploration_durable_receipts': 0, 'prerequisites_completed': 4,
        'runs_created_or_cancelled': 0, 'task_cancelled': False, 'round_execution_completed': False}


def require_completed_prerequisite_loss(store, directory, generation):
    generation_number(generation, minimum=6)
    config = read_json(Path(directory) / ('colab-config-' + generation + '.json'))
    base = read_json(Path(directory) / ('baseline-repair-' + generation + '-job.json'))
    intent_id, result_id = identities(base['task_id'], generation, config['runtime_id'])
    intent, note = store.get(intent_id), store.get(result_id)
    require(type(intent) is Decision and intent.kind == 'note' and intent.task_id == intent.target_id == base['task_id']
        and set(intent.details) == {'type', 'generation', 'inputs', 'observation', 'verification_ref', 'journal_id'}
        and intent.details['type'] == INTENT and intent.details['generation'] == generation,
        'Completed-prerequisite loss intent differs')
    validate_observation(intent.details['observation'], expected_runtime_id=config['runtime_id'])
    report = json.loads(store.artifacts.read(ArtifactRef(**intent.details['verification_ref'])))
    inputs, _, _, current = frozen_inputs(store, directory, generation, report)
    require(inputs == intent.details['inputs'], 'Completed prerequisite archive inputs changed')
    anchor = store.get(intent.details['journal_id'])
    journal = WorkflowJournal(store, base['task_id'])
    require(type(anchor) is Decision and anchor.kind == 'note' and anchor.task_id == base['task_id']
        and anchor.details.get('type') == 'workflow_state.v1'
        and anchor.id == journal.prefix + str(anchor.details['state']['revision'])
        and anchor.details['state']['task_id'] == base['task_id']
        and anchor.details['state_digest'] == digest(anchor.details['state']), 'Original Journal anchor missing')
    original = anchor.details['state']
    require(current['limits'] == original['limits'] and current['cancelled'] is False
        and all(current['jobs'].get(k) == j for k, j in original['jobs'].items()),
        'Archived Journal jobs, limits or cancellation changed')
    added = [j for k, j in current['jobs'].items() if k not in original['jobs']]
    require(current['spent'] == {k: original['spent'][k] + sum(j['variant']['cost'][k]
        for j in added if j['reserved']) for k in original['spent']}, 'Archived spent changed or refunded')
    summary = summary_for(inputs, original, generation)
    require(type(note) is Decision and note.kind == 'note' and note.task_id == note.target_id == base['task_id']
        and note.details == {'type': RESULT, 'intent_id': intent_id, 'summary': summary}, 'Loss result differs')
    output = read_json(output_path(directory, generation))
    require(set(output) == {'schema_version', 'intent_id', 'decision_id', 'summary', 'snapshot'}
        and output['schema_version'] == 1 and output['intent_id'] == intent_id
        and output['decision_id'] == result_id and output['summary'] == summary, 'Loss output differs')
    page = store.snapshot_page(output['snapshot']['id'], limit=output['snapshot']['record_count'])
    require(page['complete'] and page['snapshot'] == output['snapshot'], 'Loss Snapshot metadata differs')
    by_id = {r.id: r for r in page['records']}
    wanted = {intent_id, result_id, anchor.id, *inputs['gate_run_ids'],
              *(stable_id('workflow-result', rid) for rid in inputs['gate_run_ids'])}
    require(wanted <= set(by_id) and by_id[anchor.id] == anchor and by_id[intent_id] == intent
        and by_id[result_id] == note, 'Loss Snapshot omits exact archive closure')
    return output


def archive_completed_prerequisite_loss(store, directory, generation, observation, verification_path):
    generation_number(generation, minimum=6)
    report_raw = Path(verification_path).read_bytes()
    report = json.loads(report_raw)
    config = read_json(Path(directory) / ('colab-config-' + generation + '.json'))
    validate_observation(observation, expected_runtime_id=config['runtime_id'])
    base = read_json(Path(directory) / ('baseline-repair-' + generation + '-job.json'))
    journal = WorkflowJournal(store, base['task_id'])
    with journal.lock():
        require_previous_archives(store, directory, generation)
        inputs, jobs, results, state = frozen_inputs(store, directory, generation, report)
        require(all(j['state'] in ('completed', 'failed', 'cancelled') for j in state['jobs'].values()),
                'Prior exploration work is unfinished before loss archival')
        intent_id, result_id = identities(base['task_id'], generation, config['runtime_id'])
        existing = maybe(store, intent_id)
        journal_id = journal.prefix + str(state['revision'])
        details = {'type': INTENT, 'generation': generation, 'inputs': inputs, 'observation': deepcopy(observation),
            'verification_ref': {'digest': sha256(report_raw).hexdigest(), 'size': len(report_raw)}, 'journal_id': journal_id}
        if existing is not None:
            require(type(existing) is Decision and existing.kind == 'note'
                and existing.task_id == existing.target_id == base['task_id'] and existing.details == details,
                'Loss archive input or original Journal changed during recovery')
        else:
            verified_jobs, verified_results = require_gates(store, directory, {'generation': generation})
            require(verified_jobs == jobs and verified_results == results, 'Original prerequisite gates differ')
        # All identity, absence and state guards precede any persisted effect.
        ref = store.artifacts.put(report_raw)
        require(asdict(ref) == details['verification_ref'], 'Verification bytes changed')
        intent = Decision(intent_id, base['task_id'], 'note', base['task_id'],
            'Runtime lost after completed prerequisites, before exploration Run creation', details)
        store.append(intent, intent.id)
        summary = summary_for(inputs, state, generation)
        note = Decision(result_id, base['task_id'], 'note', base['task_id'],
            'Retained completed prerequisite evidence and unchanged exploration budget',
            {'type': RESULT, 'intent_id': intent_id, 'summary': summary})
        store.append(note, note.id)
        snapshot = store.create_snapshot(base['task_id'], stable_id('p35-completed-loss-snapshot', intent_id))
        output = {'schema_version': 1, 'intent_id': intent_id, 'decision_id': result_id,
                  'summary': summary, 'snapshot': snapshot}
        write_once(output_path(directory, generation), output)
        return require_completed_prerequisite_loss(store, directory, generation)


if __name__ == '__main__':
    from rsi_kernel_agent.store import ExperimentStore
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--generation', required=True)
    parser.add_argument('--observation', type=Path, required=True)
    parser.add_argument('--verification', type=Path, required=True)
    args = parser.parse_args()
    require((args.directory / 'experiments.sqlite3').is_file(), 'Original Store is required')
    store = ExperimentStore(args.directory / 'experiments.sqlite3', args.directory / 'artifacts')
    output = archive_completed_prerequisite_loss(store, args.directory, args.generation,
        read_json(args.observation), args.verification)
    print(canonical_json({'decision_id': output['decision_id'], 'snapshot': output['snapshot'],
        'generation': args.generation, 'spent': output['summary']['spent'], 'exploration_runs': 0,
        'runs_created_or_cancelled': 0, 'round_execution_completed': False}))
