"""Read-only P3.5 confirmation gate used before preparing or publishing round 2.

The gate is deliberately host-side.  It verifies the collector's immutable
ABBA report and the original Store receipts; it never creates a snapshot,
updates a Controller journal, or calls ``consider``.
"""
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.contracts import ArtifactRef, Candidate, Decision, Plan, Strategy, canonical_json
from rsi_kernel_agent.memory import MemoryReader
from rsi_kernel_agent.workflow.execution import stable_id
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from real_round_aux import read_json, verified_gate_result
from p35_confirmation_statistics import analyze_abba


POLICY_FILE = 'p35-confirmation-policy.json'
POINTER_FILE = 'p35-confirmation-result.json'
POLICY = {'version': 'p35-abba-v1', 'min_paired_reduction': 0.02,
          'max_within_role_spread': 0.05}


def _require(value, message):
    if not value:
        raise ValueError(message)


def _artifact(store, value, label):
    _require(type(value) is dict and set(value) == {'digest', 'size'},
             'Invalid ' + label + ' reference')
    ref = ArtifactRef(**value)
    store.artifacts.verify(ref)
    return ref


def _receipt(store, result, stage):
    ref = _artifact(store, result['receipt_refs'][stage], stage + ' receipt')
    try:
        body = json.loads(store.artifacts.read(ref).decode('utf-8'))
    except (UnicodeDecodeError, ValueError, KeyError) as exc:
        raise ValueError('Invalid ' + stage + ' receipt') from exc
    _require(body.get('status') == 'succeeded', stage + ' receipt is not succeeded')
    return body


def _profile_samples(report):
    _require(type(report) is dict and type(report.get('groups')) is list,
             'Profile report has no groups')
    found = {}
    for group in report['groups']:
        name = group.get('name', '')
        keys = (['program'] if group.get('kind') == 'program' else
                [key for key in ('prepare', 'attention') if key in name])
        if not keys:
            continue
        _require(len(keys) == 1 and keys[0] not in found, 'Profile contains ambiguous or duplicate groups')
        key = keys[0]
        raw = [sample.get('duration_ps') for sample in group.get('samples', ())]
        _require(all(type(x) in (int, float) and math.isfinite(x) and x > 0 for x in raw),
                 'Profile duration is not finite positive')
        samples = [x / 1e6 for x in raw]
        _require(len(samples) == 20 and all(type(x) in (int, float) and math.isfinite(x) and x > 0 for x in samples),
                 'Profile group must contain 20 finite positive samples')
        found[key] = samples
    _require(set(found) == {'program', 'prepare', 'attention'},
             'Profile must expose exactly program/prepare/attention groups')
    return found


def receipt_row(store, job, role):
    """Extract one report row from an already completed real job.

    ``verified_gate_result`` is the authoritative receipt/evidence check.  The
    row only normalizes the fixed fields consumed by ``analyze_abba`` and keeps
    the lease bounds needed for chronological review.
    """
    _require(role in ('control', 'treatment'), 'Invalid ABBA role')
    result = verified_gate_result(store, job)
    _require(job.get('result') == result, 'Controller result differs from immutable completion result')
    _require(result.get('provenance') == 'real' and result.get('confirmed') is True
             and result.get('correctness') == 'passed'
             and result.get('timing_validity') == 'valid', 'Job is not confirmed real evidence')
    benchmark = _receipt(store, result, 'benchmark')
    profile = _receipt(store, result, 'profile')
    bout = benchmark.get('outputs', {})
    pout = profile.get('outputs', {})
    samples = bout.get('samples_ms')
    _require(type(samples) is list and len(samples) == 20
             and all(type(x) in (int, float) and math.isfinite(x) and x > 0 for x in samples),
             'Benchmark must contain 20 finite positive samples')
    report_ref = _artifact(store, pout.get('report_ref'), 'profile report')
    report = json.loads(store.artifacts.read(report_ref).decode('utf-8'))
    profile_samples = _profile_samples(report)
    lease_b, lease_p = bout.get('lease', {}), pout.get('lease', {})
    for lease, label in ((lease_b, 'benchmark'), (lease_p, 'profile')):
        _require(type(lease) is dict and type(lease.get('started_ns')) is int
                 and type(lease.get('finished_ns')) is int and lease['finished_ns'] >= lease['started_ns'],
                 label + ' lease is missing')
    identity = job['variant']
    env = job['contract']['context']['environment']
    protocol = job['contract']['context']['protocol']
    _require(protocol['benchmark']['version'] == 'mla-contiguous-host-sync-v1'
             and protocol['benchmark']['warmup'] == 5 and protocol['benchmark']['measurements'] == 20
             and protocol['benchmark']['outlier_rule'] == 'none', 'Confirmation benchmark protocol differs')
    return {
        'role': role, 'job_id': job['id'], 'label': identity['label'],
        'candidate_id': job['candidate_id'], 'run_id': job['run_id'],
        'context_id': job['context_id'], 'runtime_id': env['runtime_session_id'],
        'protocol_version': protocol['benchmark']['version'],
        'input_digest': bout.get('input_digest') or _receipt(store, result, 'correctness').get('outputs', {}).get('input_digest'),
        'source_digest': identity['source_ref']['digest'],
        'config_digest': identity['config_ref']['digest'],
        'build_digest': identity['build_ref']['digest'],
        'raw_samples_ms': list(samples), 'profile_samples_us': profile_samples,
        'profile_run_id': job['run_id'],
        'profile_capture_operation_id': pout.get('capture_operation_id'),
        'benchmark_started_ns': lease_b['started_ns'], 'benchmark_finished_ns': lease_b['finished_ns'],
        'profile_started_ns': lease_p['started_ns'], 'profile_finished_ns': lease_p['finished_ns'],
    }


def validate_origin(store, job, selection, role):
    """Bind replica bytes to the selected control/treatment's original owner."""
    _require(role in ('control', 'treatment'), 'Invalid ABBA origin role')
    origin_id = selection['baseline_candidate_id'] if role == 'control' else selection['candidate_id']
    origin, replica = store.get(origin_id), store.get(job['candidate_id'])
    _require(type(origin) is Candidate and type(replica) is Candidate, 'ABBA origin or replica is not a Candidate')
    for candidate in (origin, replica):
        plan = store.get(candidate.plan_id)
        _require(type(plan) is Plan and plan.strategy_id == selection['strategy_id'],
                 'ABBA origin or replica belongs to another strategy')
        strategy = store.get(plan.strategy_id)
        _require(type(strategy) is Strategy and strategy.task_id == selection['task_id'],
                 'ABBA origin or replica belongs to another task')
    _require(job['task_id'] == selection['task_id'] and job['strategy_id'] == selection['strategy_id'],
             'ABBA job origin task or strategy differs')
    _require(replica.identity == origin.identity, 'ABBA replica identity differs from selected role origin')


def _load_report(store, directory, task_id, context_id, strategies):
    policy_path = Path(directory) / POLICY_FILE
    _require(policy_path.is_file(), 'P3.5 confirmation policy is required before round 2')
    policy = read_json(policy_path)
    _require(policy == POLICY, 'Confirmation policy differs from p35-abba-v1')
    pointer_path = Path(directory) / POINTER_FILE
    _require(pointer_path.is_file(), 'P3.5 confirmation result is required before round 2')
    pointer = read_json(pointer_path)
    _require(type(pointer) is dict and set(pointer) >= {'decision_id', 'artifact_ref', 'strategy_note_ids'},
             'Confirmation pointer is incomplete')
    result = store.get(pointer['decision_id'])
    _require(type(result) is Decision and result.kind == 'note' and result.task_id == result.target_id == task_id
             and result.details.get('type') == 'p35_confirmation_result.v1',
             'Missing confirmation result Decision')
    details = result.details
    _require(details.get('context_id') == context_id, 'Confirmation context differs')
    _require(pointer['artifact_ref'] == details.get('artifact_ref'), 'Confirmation artifact pointer differs')
    report_ref = _artifact(store, details['artifact_ref'], 'confirmation report')
    report = json.loads(store.artifacts.read(report_ref).decode('utf-8'))
    _require(report.get('schema_version') == 1 and report.get('policy') == policy
             and report.get('selection', {}).get('task_id') == task_id
             and report.get('selection', {}).get('context_id') == context_id,
             'Confirmation report identity differs')
    _require(report.get('statistics') == analyze_abba(report['rows'], policy),
             'Confirmation statistics are not reproducible from rows')
    selection = report['selection']
    _require(selection.get('round_id') == details.get('round_id')
             and [row['run_id'] for row in report['rows']] == details.get('run_ids'),
             'Confirmation order or Run IDs differ')
    policy_ref_value = details.get('policy_ref')
    _require(policy_ref_value is not None, 'Confirmation result has no policy reference')
    policy_ref = _artifact(store, policy_ref_value, 'confirmation policy')
    _require(store.artifacts.read(policy_ref).decode('utf-8') == canonical_json(policy),
             'Confirmation policy artifact differs')
    policy_id = pointer.get('policy_decision_id') or stable_id('p35-confirmation-policy', task_id, details['round_id'])
    from p35_publish_confirmation import require_frozen_policy
    policy_note = require_frozen_policy(store, directory, selection)
    _require(policy_note.id == policy_id
             and policy_note.details.get('policy_ref') == policy_ref_value,
             'Confirmation policy Decision is missing or differs')
    notes = {}
    note_ids = pointer['strategy_note_ids']
    _require(type(note_ids) is dict and set(note_ids) == set(strategies),
             'Confirmation strategy association pointers differ')
    for sid in strategies:
        note = store.get(note_ids[sid])
        _require(type(note) is Decision and note.kind == 'note' and note.task_id == task_id and note.target_id == sid
                 and note.details.get('type') == 'p35_confirmation_association.v1'
                 and note.details.get('confirmation_id') == result.id
                 and note.details.get('artifact_ref') == details['artifact_ref']
                 and note.details.get('context_id') == context_id
                 and note.details.get('summary') == report['statistics'],
                 'Confirmation strategy association summary differs')
        notes[sid] = note
    jobs_state = WorkflowJournal(store, task_id).load()
    _require(jobs_state is not None, 'Controller journal missing for confirmation')
    round_job_ids = {job['id'] for job in jobs_state['jobs'].values()
                     if job['round_id'] == selection['round_id']}
    _require(len(round_job_ids) == 4 and round_job_ids == {row.get('job_id') for row in report['rows']},
             'Confirmation report does not cover all four round jobs')
    sequence = selection.get('sequence', [])
    _require(len(sequence) == 4 and [item.get('role') for item in sequence] == ['baseline', 'candidate', 'candidate', 'baseline'],
             'Confirmation selection is not ABBA')
    order = {record.id: index for index, record in enumerate(store.history(task_id))}
    jobs = []
    previous_finish = None
    for row, selected in zip(report['rows'], sequence):
        job = jobs_state['jobs'].get(row.get('job_id'))
        _require(job is not None and job.get('run_id') == row['run_id']
                 and job.get('context_id') == context_id
                 and job.get('round_id') == selection['round_id']
                 and job.get('strategy_id') == selection['strategy_id']
                 and job.get('state') == 'completed' and job.get('reserved') is True,
                 'ABBA job is not completed')
        _require(selected['label'] == row.get('label')
                 and row['role'] == ('control' if selected['role'] == 'baseline' else 'treatment'),
                 'ABBA row differs from selected execution order')
        _require(order[policy_note.id] < order[job['run_id']],
                 'Confirmation policy was not frozen before ABBA Runs')
        validate_origin(store, job, selection, row['role'])
        actual = receipt_row(store, job, row['role'])
        _require(actual == row, 'Confirmation report row differs from verified receipts')
        _require(previous_finish is None or actual['benchmark_started_ns'] >= previous_finish,
                 'ABBA receipt times do not follow selected order')
        previous_finish = actual['profile_finished_ns']
        jobs.append(job)
    _require(len(jobs) == 4 and len({job['run_id'] for job in jobs}) == 4,
             'ABBA requires four independent completed Runs')
    return {'policy': policy, 'pointer': pointer, 'result': result, 'report': report,
            'policy_note': policy_note, 'association_notes': notes, 'jobs': jobs}


def _check_prepared(store, prepared, loaded, strategies):
    view = MemoryReader(store).open_snapshot(prepared['snapshot']['id'])
    _require(view.snapshot == prepared['snapshot'], 'Prepared snapshot metadata differs')
    page = store.snapshot_page(view.snapshot['id'], limit=view.snapshot['record_count'])
    records = {record.id: record for record in page['records']}
    required = [loaded['result'], loaded['policy_note'], *loaded['association_notes'].values()]
    _require(all(records.get(note.id) == note for note in required)
             and all(row['run_id'] in records for row in loaded['report']['rows']),
             'ABBA result and associations are absent from the new snapshot')
    histories = view.histories(tuple(strategies))
    _require(prepared['histories'] == histories, 'Prepared history differs from the complete snapshot')
    for sid in strategies:
        ids = {row['payload']['id'] for row in histories[sid]['records']}
        _require(loaded['association_notes'][sid].id in ids,
                 'ABBA conclusion is missing from participating strategy history')
    ids = {row['payload']['id'] for history in histories.values() for row in history['records']}
    _require(all(row['run_id'] in ids for row in loaded['report']['rows']),
             'ABBA Runs are missing from participating history')


def require_confirmation(store, directory, task_id, context_id, strategies, *, prepared=None):
    """Require a frozen policy and the immutable ABBA report before round 2.

    Round 1 never calls this gate. A non-``None`` ``prepared`` value proves that the association
    notes and all four Run IDs are in the newly captured Memory snapshot.
    """
    _require(type(strategies) in (tuple, list) and strategies, 'Confirmation strategies are required')
    loaded = _load_report(store, directory, task_id, context_id, list(strategies))
    if prepared is not None:
        _check_prepared(store, prepared, loaded, strategies)
    return loaded

