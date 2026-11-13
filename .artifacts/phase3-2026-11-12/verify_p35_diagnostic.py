"""Verify archived single-tile diagnostic jobs with the original read-only verifier.

This wrapper never runs a Controller, invokes a bridge, or writes to the Store.
Only the CLI's unique timestamped local report is written; pending is not failure.
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
import zipfile

from p35_prepare_diagnostic_round import PLAN_ROLES, runtime_binding, target_for, validate_selection
from verify_real_round import ReadOnlyStore, read_json, require, verify_job
from rsi_kernel_agent.contracts import Decision, canonical_json


def verify_diagnostic(selection_path, *, directory=None, recompute_npz=True):
    directory = Path(directory) if directory is not None else Path(__file__).resolve().parent
    selection = validate_selection(read_json(selection_path))
    target = target_for(directory, selection)
    report = {
        'schema_version': 1, 'verified_at_utc': datetime.now(timezone.utc).isoformat(),
        'round_id': selection['round_id'], 'selection': selection, 'directory': str(target),
        'network_calls': 0, 'store_writes': 0, 'jobs': [],
        'recompute_npz_requested': recompute_npz,
        'limitations': [
            'Archived local verification only; no hardware re-execution.',
            'All job verdicts come from the unchanged verify_real_round.verify_job.',
            'Saved parser statistics are recomputed; raw XPlane protobuf is not reparsed.',
            'This separate Workload cannot replace original Workload or ABBA results.',
            'Input cache immutability remains the producer check: post-execution input cache is not archived.',
            'Opaque cache exact bytes are rechecked when NPZ comparison is enabled; cache finite metadata is not independently recomputed.',
            'LSE is not exposed by the frozen interface and was not tested.',
        ],
    }
    if not recompute_npz:
        report['limitations'].append('NPZ comparisons explicitly disabled; only raw hashes and original correctness receipts are verified.')
    frozen_selection = target / 'selection.json'
    if frozen_selection.exists():
        require(read_json(frozen_selection) == selection, 'Frozen diagnostic selection differs')
    path = target / 'controller-status.json'
    if not path.exists():
        return {**report, 'status': 'pending_status_file'}
    require(frozen_selection.is_file(), 'Frozen diagnostic selection is required')
    packet = read_json(target / 'diagnostic-packet.json')
    if selection['schema_version'] == 2:
        require(read_json(target / 'runtime-binding.json') == runtime_binding(selection),
                'Diagnostic Runtime binding differs from selection v2')
        require(packet['original_context_id'] == selection['context_id']
                and packet['context']['environment']['runtime_session_id'] ==
                    read_json(directory / 'colab-config-runtime4.json')['runtime_id'],
                'Diagnostic v2 Context or Runtime differs from admitted Runtime 4')
    raw = path.read_bytes()
    state = json.loads(raw)
    require(state['task_id'] == selection['task_id'], 'Diagnostic status belongs to another task')
    report.update(controller_status_sha256=sha256(raw).hexdigest(), revision=state['revision'],
                  workflow_state=state['workflow_state'], context_id=packet['context']['id'])
    store = ReadOnlyStore(directory / 'experiments.sqlite3', directory / 'artifacts')
    prefix = 'wf-state:' + sha256(state['task_id'].encode()).hexdigest()[:32] + ':' + str(state['revision'])
    saved = store.get(prefix)
    require(type(saved) is Decision and saved.details['type'] == 'workflow_state.v1',
            'Controller status has no journal record')
    persisted = {key: value for key, value in state.items() if key not in ('remaining_budget', 'workflow_state')}
    require(saved.details['state'] == persisted
            and saved.details['state_digest'] == sha256(canonical_json(persisted).encode()).hexdigest(),
            'Controller status differs from persisted journal revision')
    jobs = [job for job in state['jobs'].values() if job['round_id'] == selection['round_id']]
    expected_labels = set(PLAN_ROLES.values())
    labels = [job['variant']['label'] for job in jobs]
    require(len(labels) == len(set(labels)) and set(labels) <= expected_labels,
            'Diagnostic jobs differ from the two frozen roles')
    require(all(job['task_id'] == selection['task_id'] and job['strategy_id'] == selection['strategy_id']
                and job['context_id'] == packet['context']['id'] for job in jobs),
            'Diagnostic job scope differs from the frozen selection and Context')
    report['missing_labels'] = sorted(expected_labels - set(labels))
    cache = {}
    for job in jobs:
        try:
            row = verify_job(store, job, recompute_npz=recompute_npz, comparison_cache=cache)
        except (ValueError, AssertionError, KeyError, OSError, sqlite3.Error, zipfile.BadZipFile) as error:
            row = {'id': job['id'], 'label': job['variant']['label'], 'run_id': job['run_id'],
                   'state': job['state'], 'verification': 'failed_verification',
                   'error_type': type(error).__name__, 'error': str(error)}
        report['jobs'].append(row)
    report['verified_unique_artifacts'] = len(store.artifacts.checked)
    report['verified_unique_artifact_bytes'] = sum(value[0] for value in store.artifacts.checked.values())
    report['counts'] = {status: sum(row['verification'] == status for row in report['jobs'])
                        for status in ('verified', 'verified_control_only', 'pending', 'failed_verification')}
    report['status'] = ('verification_failed' if report['counts']['failed_verification'] else
                        'pending' if report['counts']['pending'] or report['missing_labels'] else
                        'selected_terminal_jobs_verified')
    report['status_file_changed_during_verification'] = path.read_bytes() != raw
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('selection', type=Path)
    parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--no-npz', action='store_true', help='Only verify raw hashes and original correctness receipts')
    args = parser.parse_args()
    selection = validate_selection(read_json(args.selection))
    target = target_for(args.directory, selection)
    try:
        report = verify_diagnostic(args.selection, directory=args.directory, recompute_npz=not args.no_npz)
    except (ValueError, AssertionError, KeyError, OSError, sqlite3.Error, zipfile.BadZipFile) as error:
        report = {'schema_version': 1, 'verified_at_utc': datetime.now(timezone.utc).isoformat(),
                  'round_id': selection['round_id'], 'status': 'verification_failed', 'jobs': [],
                  'network_calls': 0, 'store_writes': 0, 'error_type': type(error).__name__, 'error': str(error)}
    output_directory = target / 'local-verifications'
    output_directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = output_directory / (stamp + '-r' + str(report.get('revision', 0)) + '.json')
    with output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'report': str(output), 'status': report['status'], 'counts': report.get('counts'),
                      'jobs': [{key: job.get(key) for key in ('label', 'verification', 'classification', 'error')}
                               for job in report['jobs']], 'error': report.get('error')}, ensure_ascii=False))
    sys.exit(1 if report['status'] == 'verification_failed' else 0)
