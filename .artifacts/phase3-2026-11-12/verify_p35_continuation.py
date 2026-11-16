"""Read-only B2 completion verification through original receipt/NPZ verifier."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.contracts import ArtifactRef, Decision, canonical_json
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from p35_b2_milestone import MilestoneStore
from p35_continuation_round import (check_scope, digest, require_history, same,
    target_for, validate_spec, validate_variants)
from real_round_aux import completed_session, load_prepared, read_json
from verify_p35_final_memory import TransactionStore
from verify_real_round import require, verify_job


def verify_continuation(directory, spec, *, recompute_npz=True):
    directory = Path(directory)
    target = target_for(directory, spec)
    report = {'schema_version': 1, 'verified_at_utc': datetime.now(timezone.utc).isoformat(),
        'round_id': spec['round_id'], 'runtime_generation': spec['runtime_generation'],
        'store_writes': 0, 'network_calls': 0, 'jobs': [], 'recompute_npz_requested': recompute_npz,
        'limitations': ['Local archived evidence verification; no hardware rerun.',
            'Saved parser statistics are checked; raw XPlane protobuf is not reparsed.',
            'Single-run timing or Best promotion does not establish reproducible performance improvement.']}
    with TransactionStore(directory / 'experiments.sqlite3', directory / 'artifacts') as store:
        state, checkpoint, entry, anchor_id = validate_spec(store, directory, spec)
        jobs = check_scope(state, checkpoint, spec)
        require(same(read_json(target / 'spec.json'), spec), 'Frozen continuation spec changed')
        prepared = load_prepared(store, spec['task_id'], spec['context_id'], spec['strategies'], spec['round_id'])
        require(prepared is not None, 'Continuation prepared round is missing')
        require_history(store, prepared, spec, anchor_id)
        if not (target / 'execution-input.json').exists():
            require(not jobs and not (target / 'completed.json').exists(), 'Unbound continuation attempts exist')
            return {**report, 'status': 'pending_execution'}
        inputs = read_json(target / 'execution-input.json')
        result = completed_session(store, target, prepared['manifest'])
        key = spec['round_id'] + ':' + prepared['manifest'].artifact_ref.digest
        require(set(inputs) == {'spec_digest', 'manifest', 'result_digest', 'variants', 'publication_key'}
                and inputs['spec_digest'] == digest(spec) and inputs['manifest'] == asdict(prepared['manifest'].artifact_ref)
                and inputs['result_digest'] == digest(result) and inputs['publication_key'] == key,
                'Continuation execution inputs differ from the frozen session')
        validate_variants(store, result, inputs['variants'], entry['grant'], spec)
        if not (target / 'intake.json').exists():
            require(not jobs and not (target / 'completed.json').exists(), 'Missing bound intake for admitted jobs')
            return {**report, 'status': 'pending_intake'}
        intake = read_json(target / 'intake.json')
        marker = store.get(intake['id'])
        fingerprint = sha256(json.dumps({'publication_id': intake['publication_id'],
            'round_id': spec['round_id'], 'variants': inputs['variants']},
            sort_keys=True, ensure_ascii=True).encode()).hexdigest()
        require(type(marker) is Decision and marker.kind == 'note'
                and marker.task_id == marker.target_id == spec['task_id']
                and set(marker.details) == {'type', 'request_digest', 'result'}
                and marker.details.get('type') == 'workflow_intake.v1' and marker.details['result'] == intake,
                'Continuation intake differs from the original immutable publication')
        require(marker.details['request_digest'] == fingerprint,
                'Continuation variants differ from the original intake request')
        publication_id = 'analysis:' + sha256(key.encode()).hexdigest()[:24] + ':complete'
        publication = store.get(publication_id)
        require(intake['publication_id'] == publication_id and intake['round_id'] == spec['round_id']
                and intake['task_id'] == spec['task_id'] and type(publication) is Decision
                and publication.task_id == spec['task_id'] and publication.details.get('type') == 'analysis_result.v1'
                and publication.details['manifest'] == inputs['manifest']
                and json.loads(store.artifacts.read(ArtifactRef(**publication.details['result']))) == result,
                'Continuation Analyzer publication binding differs')
        require(intake['items'] and all(i['status'] == 'accepted' for i in intake['items']),
                'Continuation contains rejected or waiting intake items')
        expected = {j['id']: j for item in intake['items'] for j in item['jobs']}
        require(len(expected) == 2, 'Continuation intake must retain exactly two original attempts')
        jobs = check_scope(state, checkpoint, spec, expected)
        accepted_digest = state['intakes'].get(intake['id'])
        require((not jobs and accepted_digest is None) or accepted_digest == digest(intake),
                'Continuation intake differs from the original Journal acceptance')
        frozen = None
        if (target / 'completed.json').exists():
            completion = read_json(target / 'completed.json')
            require(set(completion) == {'spec_digest', 'round_id', 'context_id', 'snapshot', 'grant_key', 'results'}
                    and completion['spec_digest'] == digest(spec) and completion['round_id'] == spec['round_id']
                    and completion['context_id'] == spec['context_id'] and completion['grant_key'] == entry['key'],
                    'Continuation completion identity or grant differs')
            frozen = MilestoneStore(store, completion['snapshot'])
            completed_state = WorkflowJournal(frozen, spec['task_id']).load()
            completed_jobs = check_scope(completed_state, checkpoint, spec, expected)
            require(len(completed_jobs) == 2 and all(j['state'] in ('completed', 'failed', 'cancelled')
                    for j in completed_jobs.values()) and completed_jobs == jobs
                    and completed_state['intakes'].get(intake['id']) == digest(intake)
                    and completion['results'] == {k: j['result'] for k, j in completed_jobs.items()},
                    'Continuation completion omits or changes original results')
            report['snapshot'] = completion['snapshot']
        else:
            require(not jobs or not all(j['state'] in ('completed', 'failed', 'cancelled') for j in jobs.values()),
                    'Terminal continuation is missing its completion Snapshot')
        # Pending reports use a pinned read surface too; no mutable Store API is
        # handed to per-job verification.
        cache = {}
        for job in jobs.values():
            if frozen is None:
                row = {'id': job['id'], 'run_id': job['run_id'], 'verification': 'pending'}
            else:
                try:
                    row = verify_job(frozen, job, recompute_npz=recompute_npz, comparison_cache=cache)
                except (ValueError, AssertionError, KeyError, OSError, zipfile.BadZipFile) as error:
                    row = {'id': job['id'], 'run_id': job['run_id'], 'verification': 'failed_verification',
                           'error_type': type(error).__name__, 'error': str(error)}
            report['jobs'].append(row)
        report['counts'] = {s: sum(row['verification'] == s for row in report['jobs'])
                            for s in ('verified', 'pending', 'failed_verification')}
        report['status'] = ('verification_failed' if report['counts']['failed_verification'] else
            'selected_terminal_jobs_verified' if report['counts']['verified'] == 2 else 'pending')
        report['verified_unique_artifacts'] = len(store.artifacts.checked)
        report['verified_unique_artifact_bytes'] = sum(v[0] for v in store.artifacts.checked.values())
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--no-npz', action='store_true')
    args = parser.parse_args()
    spec = read_json(args.spec)
    report = verify_continuation(args.directory, spec, recompute_npz=not args.no_npz)
    target = target_for(args.directory, spec) / 'local-verifications'
    target.mkdir(exist_ok=True)
    output = target / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    with output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(canonical_json({'report': str(output), 'status': report['status'], 'counts': report.get('counts')}))
    sys.exit(1 if report['status'] == 'verification_failed' else 0)
