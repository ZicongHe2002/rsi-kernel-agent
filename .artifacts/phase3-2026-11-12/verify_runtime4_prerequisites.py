"""Read-only Runtime 4 audit using the original receipt and NPZ verifier.

Only four basic prerequisites are required. The old P3.5 confirmation control
is not rerun or reclassified. Reports are new local files, never Store writes.
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
import uuid

from verify_runtime3_prerequisites import ERRORS, terminal_view
from verify_real_round import ReadOnlyStore, require, verify_job
from real_round_aux import read_json, require_gates, write_once


NAMES = ('oracle-negative-attention', 'oracle-negative-cache', 'baseline-repair', 'baseline-b')


def verify_prerequisites(directory):
    directory = Path(directory)
    report = {
        'schema_version': 1, 'verified_at_utc': datetime.now(timezone.utc).isoformat(),
        'generation': 'runtime4', 'network_calls': 0, 'store_writes': 0, 'binding_writes': 0,
        'expected_names': list(NAMES), 'jobs': [], 'recompute_npz_requested': True,
        'aggregate_gate_verification': 'pending_all_four_results',
        'limitations': [
            'Archived local evidence only; no bridge calls or hardware execution.',
            'The original verifier recomputes saved correctness NPZ comparisons and profile statistics.',
            'Raw XPlane protobuf and device input-cache mutation are not independently rerun.',
            'Four basic prerequisites do not establish a new ABBA confirmation or reproducible speedup.',
            'Missing completion markers remain pending; concurrent work may finish after observation.'],
    }
    store, runtime, preflight_error = None, None, None
    config_path = directory / 'colab-config-runtime4.json'
    try:
        if config_path.is_file():
            runtime = read_json(config_path)['runtime_id']
            require(type(runtime) is str and bool(runtime.strip()), 'Invalid Runtime 4 identity')
            report['runtime_id'] = runtime
            store = ReadOnlyStore(directory / 'experiments.sqlite3', directory / 'artifacts')
    except ERRORS as error:
        preflight_error = error
        report.update(preflight_error_type=type(error).__name__, preflight_error=str(error))
    cache = {}
    completed = 0
    for name in NAMES:
        path = directory / (name + '-runtime4-job.json')
        row = {'name': name, 'verification': 'pending'}
        try:
            if not path.is_file():
                row['reason'] = 'missing_archived_job'
            elif preflight_error is not None:
                raise preflight_error
            elif store is None:
                row['reason'] = 'missing_runtime_config'
            else:
                raw = path.read_bytes()
                job = json.loads(raw)
                row.update(job_file_sha256=sha256(raw).hexdigest(), id=job['id'],
                           run_id=job['run_id'], context_id=job['context_id'])
                require(job['contract']['context']['environment']['runtime_session_id'] == runtime,
                        'Archived prerequisite belongs to another Runtime')
                history = {record.id: record for record in store.history(job['task_id'])}
                view = terminal_view(store, job, history)
                if view is None:
                    row.update(reason='missing_completion_marker',
                               npz_comparison_status='pending_completion_marker')
                else:
                    completed += 1
                    row.update(verify_job(store, view, recompute_npz=True, comparison_cache=cache),
                               terminal_view_source='workflow_result.v1 and original Run state')
                require(path.read_bytes() == raw, 'Archived job changed during verification')
        except ERRORS as error:
            row.update(verification='failed_verification', error_type=type(error).__name__, error=str(error))
        report['jobs'].append(row)
    report['counts'] = {status: sum(row['verification'] == status for row in report['jobs'])
                        for status in ('verified', 'pending', 'failed_verification')}
    report['completed_views'] = completed
    if completed == len(NAMES) and report['counts']['verified'] == len(NAMES):
        try:
            jobs, results = require_gates(store, directory, {'generation': 'runtime4'})
            require(set(jobs) == set(results) == set(NAMES), 'Aggregate gates omit a Runtime 4 prerequisite')
            report['aggregate_gate_verification'] = 'all_four_original_gates_verified'
        except ERRORS as error:
            report.update(aggregate_gate_verification='failed', aggregate_error_type=type(error).__name__,
                          aggregate_error=str(error))
    report['status'] = (
        'verification_failed' if preflight_error or report['counts']['failed_verification']
        or report['aggregate_gate_verification'] == 'failed' else
        'runtime4_prerequisites_verified' if report['aggregate_gate_verification']
        == 'all_four_original_gates_verified' else 'partial_pending')
    checked = store.artifacts.checked if store is not None else {}
    report['verified_unique_artifacts'] = len(checked)
    report['verified_unique_artifact_bytes'] = sum(item[0] for item in checked.values())
    report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args(argv)
    report = verify_prerequisites(args.directory)
    target = args.directory / 'runtime4-local-verifications'
    target.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = target / (stamp + '-' + uuid.uuid4().hex + '.json')
    write_once(output, report)
    print(json.dumps({'report': str(output), 'status': report['status'], 'counts': report['counts'],
                      'aggregate_gate_verification': report['aggregate_gate_verification']}))
    return 1 if report['status'] == 'verification_failed' else 0


if __name__ == '__main__':
    sys.exit(main())
