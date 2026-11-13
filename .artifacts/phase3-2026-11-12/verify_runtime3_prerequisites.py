"""Read-only Runtime 3 prerequisite audit; archived queued jobs are not status."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
import zipfile

from verify_real_round import ReadOnlyStore, require, verify_job
from rsi_kernel_agent.contracts import Decision
from rsi_kernel_agent.store import StoreError
from rsi_kernel_agent.workflow.execution import stable_id
from real_round_aux import read_json, require_gates
from runtime_readmission import save_observation


NAMES = ('oracle-negative-attention', 'oracle-negative-cache', 'baseline-repair',
         'baseline-b', 'confirmation-control')
ERRORS = (ValueError, AssertionError, KeyError, TypeError, OSError, StoreError,
          sqlite3.Error, zipfile.BadZipFile)


def terminal_view(store, archived, history):
    """Build only an in-memory view from immutable completion and Run state."""
    marker = history.get(stable_id('workflow-result', archived['run_id']))
    if marker is None:
        return None
    require(type(marker) is Decision and marker.kind == 'note'
            and marker.task_id == archived['task_id'] and marker.target_id == archived['run_id']
            and marker.details.get('type') == 'workflow_result.v1'
            and marker.details.get('job_id') == archived['id'], 'Completion marker does not bind archived job')
    result = marker.details['result']
    state = store.run_state(archived['run_id'])['execution_status']
    require(state in ('succeeded', 'failed', 'invalid', 'incomplete'), 'Completion marker has a nonterminal Run')
    view = deepcopy(archived)
    view.update(result=deepcopy(result),
                state=('completed' if state == 'succeeded' else
                       'cancelled' if result['classification'] == 'cancelled' else 'failed'),
                stage=None, reason='Local view reconstructed from immutable completion marker and Run state')
    return view


def verify_prerequisites(directory):
    directory = Path(directory)
    report = {'schema_version': 1, 'verified_at_utc': datetime.now(timezone.utc).isoformat(),
              'generation': 'runtime3', 'network_calls': 0, 'store_writes': 0,
              'binding_writes': 0, 'expected_names': list(NAMES), 'jobs': [],
              'recompute_npz_requested': True, 'aggregate_gate_verification': 'pending_all_five_results',
              'limitations': [
                  'Local archived evidence only; no new hardware execution or bridge calls.',
                  'Terminal rows with correctness arrays independently recompute original candidate/oracle NPZ comparisons; see each row npz_comparison_status.',
                  'Saved profile sample statistics and normalization are recomputed; raw XPlane protobuf is not reparsed.',
                  'Device input-cache mutation and opaque cache finite metadata checks are not independently rerun.',
                  'Single-run latency and Store Best do not establish reproducible improvement.',
                  'Concurrent execution may finish after an item was observed; missing completion markers remain pending.']}
    store = ReadOnlyStore(directory / 'experiments.sqlite3', directory / 'artifacts')
    config_runtime = read_json(directory / 'colab-config-runtime3.json')['runtime_id']
    report['runtime_id'] = config_runtime
    cache = {}
    completed = 0
    for name in NAMES:
        path = directory / (name + '-runtime3-job.json')
        row = {'name': name, 'verification': 'pending', 'completion_marker_observed': False}
        try:
            if not path.is_file():
                row['reason'] = 'missing_archived_job'
                report['jobs'].append(row)
                continue
            raw = path.read_bytes()
            job = json.loads(raw)
            row.update(job_file_sha256=sha256(raw).hexdigest(), archived_state=job['state'],
                       id=job['id'], run_id=job['run_id'], context_id=job['context_id'])
            require(job['contract']['context']['environment']['runtime_session_id'] == config_runtime,
                    'Archived prerequisite belongs to another Runtime')
            history = {record.id: record for record in store.history(job['task_id'])}
            row['completion_marker_observed'] = stable_id('workflow-result', job['run_id']) in history
            view = terminal_view(store, job, history)
            if view is None:
                row.update(reason='missing_completion_marker', run_state=store.run_state(job['run_id']),
                           npz_comparison_status='pending_completion_marker')
                diagnostic = history.get(stable_id('oracle-gate-failure', job['run_id']))
                if diagnostic is not None:
                    row['observed_oracle_gate_failure_id'] = diagnostic.id
            else:
                completed += 1
                checked = verify_job(store, view, recompute_npz=True, comparison_cache=cache)
                row.update(checked, terminal_view_source='workflow_result.v1 and original Run state')
            require(path.read_bytes() == raw, 'Archived job changed during verification')
        except ERRORS as error:
            row.update(verification='failed_verification', error_type=type(error).__name__, error=str(error))
        report['jobs'].append(row)
    report['counts'] = {status: sum(row['verification'] == status for row in report['jobs'])
                        for status in ('verified', 'pending', 'failed_verification')}
    report['completed_views'] = completed
    if completed == len(NAMES) and report['counts']['verified'] == len(NAMES):
        try:
            jobs, results = require_gates(store, directory, {'generation': 'runtime3'})
            require(set(jobs) == set(results) == set(NAMES), 'Aggregate gates omit a Runtime 3 prerequisite')
            report['aggregate_gate_verification'] = 'all_five_original_gates_verified'
        except ERRORS as error:
            report.update(aggregate_gate_verification='failed', aggregate_error_type=type(error).__name__,
                          aggregate_error=str(error))
    report['status'] = ('verification_failed' if report['counts']['failed_verification']
                        or report['aggregate_gate_verification'] == 'failed' else
                        'runtime3_prerequisites_verified' if report['aggregate_gate_verification']
                        == 'all_five_original_gates_verified' else 'partial_pending')
    report['verified_unique_artifacts'] = len(store.artifacts.checked)
    report['verified_unique_artifact_bytes'] = sum(item[0] for item in store.artifacts.checked.values())
    report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    report = verify_prerequisites(args.directory)
    output = args.directory / 'runtime3-prerequisite-verification.json'
    save_observation(output, report)
    print(json.dumps({'report': str(output), 'status': report['status'], 'counts': report['counts'],
                      'aggregate_gate_verification': report['aggregate_gate_verification'],
                      'jobs': [{key: row.get(key) for key in ('name', 'verification', 'classification',
                                'latency_ms', 'npz_comparison_status', 'error')} for row in report['jobs']]}, ensure_ascii=False))
    sys.exit(1 if report['status'] == 'verification_failed' else 0)
