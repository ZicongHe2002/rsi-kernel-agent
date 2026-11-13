"""Read-only verification of archived real-round jobs; pending work is not a failure.

This helper imports the earlier stream-hashing ReadOnlyStore. It never constructs
a Controller, invokes a bridge, integrates results, or calls Store.consider.
"""
import argparse
import ast
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import statistics
import struct
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'knowledge-site/manifests/phase3-2026-11-12')]
from verify_runtime2_baselines_locally import ReadOnlyStore
from rsi_kernel_agent.contracts import ArtifactRef, Decision, Evidence, canonical_json
from rsi_kernel_agent.workflow.evaluation import _parent_latency, _verified_prefix
from rsi_kernel_agent.workflow.execution import stable_id
from rsi_kernel_agent.workflow.real_protocol import timing_statistics
from rsi_kernel_agent.profiler.profiles import normalize_duration_report


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(Path(path).read_text('utf-8-sig'))


def npy_header(stream):
    require(stream.read(6) == b'\x93NUMPY', 'Not an NPY member')
    version = tuple(stream.read(2))
    require(version in ((1, 0), (2, 0), (3, 0)), 'Unsupported NPY version')
    size = int.from_bytes(stream.read(2 if version == (1, 0) else 4), 'little')
    require(0 < size <= 65536, 'Unexpected NPY header size')
    header = ast.literal_eval(stream.read(size).decode('utf-8' if version == (3, 0) else 'latin1'))
    require(header.get('fortran_order') is False, 'Only C-order archived arrays are supported')
    return header


def npz_metadata(archive):
    require(len(archive.namelist()) == len(set(archive.namelist())), 'Duplicate NPZ members')
    with archive.open('metadata.npy') as stream:
        header = npy_header(stream)
        require(header['descr'] == '|u1' and len(header['shape']) == 1, 'Invalid NPZ metadata storage')
        raw = stream.read(1024 * 1024 + 1)
        require(len(raw) <= 1024 * 1024 and len(raw) == header['shape'][0], 'Invalid NPZ metadata size')
        rows = json.loads(raw)
    require(type(rows) is list and all(row['name'] == 'array_' + str(i) for i, row in enumerate(rows)),
            'Unexpected NPZ array sequence')
    require(set(archive.namelist()) == {'metadata.npy', *(r['name'] + '.npy' for r in rows)},
            'NPZ has missing or extra members')
    return rows


def bf16_values(raw):
    return [struct.unpack('<f', struct.pack('<I', word[0] << 16))[0]
            for word in struct.iter_unpack('<H', raw)]


def compare_npz(actual_path, oracle_path, specs, *, atol, rtol):
    """Stream full opaque cache bytes; only small numeric attention is expanded.

    The original oracle calls assert_allclose(reference, actual), so tolerance
    scales with actual. Diagnostic relative error still scales with reference.
    """
    rows = []
    with zipfile.ZipFile(actual_path) as az, zipfile.ZipFile(oracle_path) as ez:
        am, em = npz_metadata(az), npz_metadata(ez)
        require(len(am) == len(em) == len(specs), 'Wrong NPZ output count')
        for a, e, spec in zip(am, em, specs, strict=True):
            require(a['shape'] == e['shape'] == spec['shape'], 'NPZ shape differs from workload')
            require(a['dtype'] == e['dtype'] == spec['dtype'] == 'bfloat16'
                    and a['storage_dtype'] == e['storage_dtype'] == 'uint16', 'Unsupported NPZ dtype/storage')
            ahash, ehash = sha256(), sha256()
            total = mismatches = 0
            numeric_a, numeric_e = bytearray(), bytearray()
            with az.open(a['name'] + '.npy') as af, ez.open(e['name'] + '.npy') as ef:
                ah, eh = npy_header(af), npy_header(ef)
                require(ah == eh and ah['descr'] == '<u2' and list(ah['shape']) == spec['shape'],
                        'NPY header differs from metadata')
                while True:
                    av, ev = af.read(1024 * 1024), ef.read(1024 * 1024)
                    require(len(av) == len(ev) and len(av) % 2 == 0, 'Truncated BF16 output')
                    if not av:
                        break
                    ahash.update(av); ehash.update(ev)
                    total += len(av)
                    if spec['comparison'] == 'exact':
                        if av != ev:
                            mismatches += sum(x != y for x, y in zip(struct.iter_unpack('<H', av), struct.iter_unpack('<H', ev)))
                    else:
                        require(spec['comparison'] == 'allclose', 'Unknown comparison')
                        numeric_a.extend(av); numeric_e.extend(ev)
            require(total == math.prod(spec['shape']) * 2, 'NPZ payload size differs from shape')
            require(ahash.hexdigest() == a['sha256'] and ehash.hexdigest() == e['sha256'], 'NPZ internal digest differs')
            row = {'name': spec['name'], 'shape': spec['shape'], 'dtype': spec['dtype'],
                   'actual_array_sha256': ahash.hexdigest(), 'oracle_array_sha256': ehash.hexdigest(),
                   'array_bytes': total}
            if spec['comparison'] == 'exact':
                row.update(exact_raw_bytes_equal=mismatches == 0, mismatching_bf16_words=mismatches,
                           tolerance_violations=int(mismatches != 0), passed=mismatches == 0,
                           finite_recomputed=False)
            else:
                av, ev = bf16_values(numeric_a), bf16_values(numeric_e)
                finite = all(math.isfinite(x) for x in av + ev)
                row.update(finite=finite, max_abs=None, max_rel=None, rms=None,
                           tolerance_violations=max(1, len(av)), passed=False)
                if finite and av:
                    errors = [abs(x - y) for x, y in zip(av, ev)]
                    violations = sum(d > atol + rtol * abs(x) for d, x in zip(errors, av))
                    row.update(max_abs=max(errors), max_rel=max(d / max(abs(y), 1e-30) for d, y in zip(errors, ev)),
                               rms=math.sqrt(statistics.fmean(d * d for d in errors)),
                               tolerance_violations=violations, passed=violations == 0)
            rows.append(row)
    return rows


def reconcile_comparison(measured, recorded):
    row = dict(measured)
    mutation = (recorded.get('name') == measured.get('name') == 'cache'
                and recorded.get('metric_reason') == 'Candidate mutated the input cache')
    if mutation:
        require(recorded['passed'] is False and recorded['tolerance_violations'] >= 1,
                'Input mutation must retain the original failed verdict')
    else:
        require(measured['tolerance_violations'] == recorded['tolerance_violations']
                and measured['passed'] == recorded['passed'], 'Independent NPZ verdict differs from receipt')
    row.update(recorded_passed=recorded['passed'], recorded_tolerance_violations=recorded['tolerance_violations'],
               input_mutation_not_independently_reverified=mutation)
    return row


def verify_npz_cases(store, job, output, cache):
    cases = job['contract']['workload']['spec']['cases']
    refs = output['raw_artifact_refs']
    require(len(refs) == len(cases) * 6 + 1, 'Not the frozen MLA correctness archive layout')
    require(refs[-1]['digest'] == output['input_digest'], 'Input manifest is not last in frozen archive')
    manifest = json.loads(store.artifacts.read(ArtifactRef(**refs[-1])))
    require(manifest['schema_version'] == 1 and len(manifest['cases']) == len(cases), 'Input manifest case count differs')
    results = []
    for index, (case, recorded, input_case) in enumerate(zip(cases, output['cases'], manifest['cases'], strict=True)):
        items = refs[index * 6:index * 6 + 6]
        require(input_case['case_id'] == recorded['id'] == case['id'] and input_case['npz_digest'] == items[0]['digest'],
                'Correctness inputs are not bound to this case')
        specialization = json.loads(store.artifacts.read(ArtifactRef(**items[3])))
        require(specialization['case_id'] == case['id'] and specialization['input_arrays'] == input_case['arrays'],
                'Compiled specialization input metadata differs')
        actual, oracle = [store.artifacts.path_for(ArtifactRef(**item)) for item in (items[5], items[1])]
        identity = tuple((p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ino) for p in (actual, oracle))
        policy = job['contract']['context']['protocol']['correctness']
        key = (items[5]['digest'], items[1]['digest'], canonical_json(case['outputs']), canonical_json(policy), identity)
        if key not in cache:
            cache[key] = compare_npz(actual, oracle, case['outputs'], **policy)
        comparisons = [reconcile_comparison(measured, prior)
                       for measured, prior in zip(cache[key], recorded['outputs'], strict=True)]
        for measured, prior in zip(comparisons, recorded['outputs'], strict=True):
            if 'finite' in measured:
                require(measured['finite'] == prior['finite'], 'Independent finite check differs')
                for metric in ('max_abs', 'max_rel', 'rms'):
                    left, right = measured[metric], prior[metric]
                    require(left is right if left is None or right is None else math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-15),
                            'Independent NPZ metric differs: ' + metric)
        results.append({'case_id': case['id'], 'input_npz_ref': items[0], 'candidate_npz_ref': items[5],
                        'oracle_npz_ref': items[1], 'specialization': specialization, 'comparisons': comparisons})
    return results


def verify_job(store, job, *, recompute_npz=True, comparison_cache=None):
    row = {key: job.get(key) for key in ('id', 'state', 'stage', 'reason', 'strategy_id', 'plan_id',
                                        'candidate_id', 'parent_id', 'parent_evaluation_id', 'run_id', 'context_id')}
    row['label'] = job['variant']['label']
    row['recompute_npz_requested'] = recompute_npz
    if job['state'] not in ('completed', 'failed', 'cancelled'):
        return {**row, 'verification': 'pending', 'npz_comparison_status': 'pending_execution'}
    require(store is not None, 'Terminal verification needs the existing read-only Store')
    if job['state'] == 'cancelled' and job.get('result') is None:
        # verify_round separately binds the supplied job to its immutable journal.
        # This branch verifies absence of execution, never a successful evaluation.
        require(job.get('reserved') is False and not job.get('receipts') and not job.get('operation'),
                'Cancellation without result must be unstarted')
        run_state = store.run_state(job['run_id'])
        require(run_state['execution_status'] in ('not_run', 'ready', 'deferred'), 'Cancelled job already executed')
        return {**row, 'verification': 'verified_control_only', 'result_marker_id': None,
                'run_state': run_state, 'correctness': None, 'confirmed': False,
                'control_state_source': 'caller; verify_round additionally verifies persisted journal'}
    marker = store.get(stable_id('workflow-result', job['run_id']))
    require(type(marker) is Decision and marker.kind == 'note' and marker.task_id == job['task_id']
            and marker.target_id == job['run_id'] and marker.details.get('type') == 'workflow_result.v1'
            and marker.details['job_id'] == job['id'], 'Original completion marker does not bind this job')
    result = marker.details['result']
    require(result == job['result'] and result['run_id'] == job['run_id'] and result['candidate_id'] == job['candidate_id']
            and result['provenance'] == 'real', 'Stored result differs from terminal job identity')
    classification = result['classification']
    expected_state = ('completed' if classification.startswith('correct_') else
                      'cancelled' if classification == 'cancelled' else 'failed')
    require(job['state'] == expected_state, 'Controller terminal state contradicts recorded classification')
    receipts, artifacts = {}, {}
    for stage, ref in result['receipt_refs'].items():
        body = json.loads(store.artifacts.read(ArtifactRef(**ref)))
        receipts[stage] = {**body, 'artifact_ref': ref}
        for item in [ref, *body['artifact_refs']]:
            artifacts[item['digest']] = item
    receipts = _verified_prefix(store, job, receipts, cancelled=marker.details['cancelled'])
    evaluation = store.get(result['evaluation_id'])
    require(evaluation.run_id == job['run_id'], 'Evaluation Run differs')
    for field in ('correctness', 'timing_validity', 'latency_ms', 'confirmed'):
        require(getattr(evaluation, field) == result[field], 'Evaluation result differs: ' + field)
    parent_latency = _parent_latency(store, job)
    require(parent_latency == result['parent_latency_ms'], 'Parent measurement differs')
    run_state = store.run_state(job['run_id'])
    expected_run_state = ('succeeded' if classification.startswith('correct_') else
                          'invalid' if classification == 'invalid_measurement' else
                          'incomplete' if classification in ('cancelled', 'incomplete', 'profile_failed') else 'failed')
    require(run_state['execution_status'] == expected_run_state, 'Run terminal state contradicts recorded classification')
    pending, seen, reached = list(evaluation.evidence_ids), set(), set()
    while pending:
        eid = pending.pop()
        if eid in seen:
            continue
        seen.add(eid)
        evidence = store.get(eid)
        require(type(evidence) is Evidence and evidence.run_id == job['run_id'], 'Evidence belongs to another Run')
        for ref in evidence.artifacts:
            store.artifacts.verify(ref)
            reached.add(ref.digest)
        pending.extend(evidence.input_ids)
    require(set(artifacts) <= reached, 'Stage artifacts are missing from Evaluation Evidence closure')
    promotion = store.get(result['promotion_id'])
    require(type(promotion) is Decision and promotion.kind == 'promotion' and promotion.task_id == job['task_id']
            and promotion.target_id == evaluation.id, 'Promotion Decision differs from this Evaluation')
    profile = store.get(result['profile_id'])
    require(profile.run_id == job['run_id'] and set(profile.evidence_ids) <= seen, 'Profile is outside Evidence closure')
    measured, groups, quality = None, [], None
    medians = {'program': [], 'prepare': [], 'attention': []}
    benchmark = receipts.get('benchmark')
    if benchmark and benchmark['status'] == 'succeeded':
        measured = timing_statistics(benchmark['outputs']['samples_ms'])
        require(measured == result['statistics'] and measured['median_ms'] == result['latency_ms'], 'Raw samples disagree with result')
    parsed_receipt = receipts.get('profile')
    if parsed_receipt and parsed_receipt['status'] == 'succeeded':
        output = parsed_receipt['outputs']
        parsed = json.loads(store.artifacts.read(ArtifactRef(**output['report_ref'])))
        binding = {key: job[key] for key in ('strategy_id', 'parent_id', 'candidate_id', 'run_id', 'context_id')}
        binding.update(provenance='real', declared_identity=parsed_receipt['candidate_identity'])
        normalized = normalize_duration_report(parsed, binding=binding, artifact_refs=[output['report_ref']])
        quality = normalized['quality']
        require(quality['availability'] == profile.availability == 'available', 'Normalized Profile unavailable')
        for group in parsed['groups']:
            durations = [sample['duration_ps'] / 1e6 for sample in group['samples']]
            require(len(durations) == group['count'] and bool(durations), 'Profile sample count differs')
            require(math.isclose(statistics.mean(durations), group['mean_us'], rel_tol=1e-12)
                    and math.isclose(statistics.median(durations), group['median_us'], rel_tol=1e-12), 'Profile statistics differ')
            groups.append({key: group[key] for key in ('kind', 'name', 'count', 'mean_us', 'median_us', 'invalid_samples')})
            for kind in medians:
                if (kind == 'program' and group['kind'] == 'program') or (kind != 'program' and kind in group['name']):
                    medians[kind].append(statistics.median(durations))
    else:
        require(profile.availability == 'unavailable', 'Unexpected available Profile without successful capture')
    correctness = receipts.get('correctness')
    comparisons = None
    if recompute_npz and correctness and 'cases' in correctness['outputs']:
        comparisons = verify_npz_cases(store, job, correctness['outputs'], comparison_cache if comparison_cache is not None else {})
    row.update(verification='verified', classification=result['classification'], correctness=result['correctness'],
               confirmed=result['confirmed'], timing_validity=result['timing_validity'], latency_ms=result['latency_ms'],
               parent_latency_ms=parent_latency, run_state=run_state, result_marker_id=marker.id,
               result_digest=sha256(canonical_json(result).encode()).hexdigest(),
               runtime_id=job['contract']['context']['environment']['runtime_session_id'],
               declared_identity=receipts[next(iter(receipts))]['candidate_identity'] if receipts else None,
               source_bundle_ref=job['variant']['source_bundle_ref'],
               loaded_identities={stage: body['outputs']['identity'] for stage, body in receipts.items() if 'identity' in body['outputs']},
               receipt_refs=result['receipt_refs'], receipt_statuses={stage: body['status'] for stage, body in receipts.items()},
               failure_outputs={stage: body['outputs'] for stage, body in receipts.items() if body['status'] != 'succeeded'},
               unique_stage_artifacts=len(artifacts), unique_stage_artifact_bytes=sum(r['size'] for r in artifacts.values()),
               evidence_records_traversed=len(seen), all_stage_artifacts_reachable_from_evaluation=True,
               benchmark_statistics=measured, raw_samples_ms=None if measured is None else benchmark['outputs']['samples_ms'],
               profile_normalization_quality=quality, profile_groups=groups, profile_medians_us=medians,
               profile_report_ref=parsed_receipt['outputs'].get('report_ref') if parsed_receipt else None,
               raw_xplane_refs=parsed_receipt['outputs'].get('raw_xplane_refs') if parsed_receipt else None,
               promotion_at_completion=promotion.details, independent_npz_comparisons=comparisons,
               npz_comparison_status=('recomputed_from_raw_npz' if comparisons is not None else
                                      'disabled_raw_hashes_and_receipt_only' if not recompute_npz else
                                      'unavailable_no_correctness_arrays'),
               independent_raw_protobuf_reparse=False)
    return row


def verify_round(directory, number, *, labels=(), recompute_npz=True):
    directory = Path(directory)
    path = directory / ('real-round-' + str(number)) / 'controller-status.json'
    report = {'schema_version': 1, 'verified_at_utc': datetime.now(timezone.utc).isoformat(),
              'round': number, 'network_calls': 0, 'store_writes': 0, 'jobs': [],
              'recompute_npz_requested': recompute_npz,
              'limitations': ['Archived local verification only; no hardware re-execution.',
                  'Saved parser statistics and normalization are recomputed; raw XPlane protobuf is not reparsed.',
                  'Lower single-run median/Best promotion does not establish reproducible performance improvement.',
                  'Exact opaque cache bytes include NaNs; cache finite metadata and device input mutation checks are not independently rerun.']}
    if not recompute_npz:
        report['limitations'].append('NPZ numerical/byte comparisons were explicitly disabled; only archived raw SHA/size and original correctness receipts are verified.')
    if not path.exists():
        return {**report, 'status': 'pending_status_file'}
    raw = path.read_bytes()
    state = json.loads(raw)
    report.update(controller_status_sha256=sha256(raw).hexdigest(), revision=state['revision'],
                  workflow_state=state['workflow_state'])
    store = ReadOnlyStore(directory / 'experiments.sqlite3', directory / 'artifacts')
    prefix = 'wf-state:' + sha256(state['task_id'].encode()).hexdigest()[:32] + ':' + str(state['revision'])
    saved = store.get(prefix)
    require(type(saved) is Decision and saved.details['type'] == 'workflow_state.v1', 'Controller status has no journal record')
    persisted = {key: value for key, value in state.items() if key not in ('remaining_budget', 'workflow_state')}
    require(saved.details['state'] == persisted and saved.details['state_digest'] == sha256(canonical_json(persisted).encode()).hexdigest(),
            'Controller status differs from persisted journal revision')
    cache = {}
    for job in state['jobs'].values():
        if job['round_id'] != 'real-runtime2-round:' + str(number) or labels and job['variant']['label'] not in labels:
            continue
        try:
            row = verify_job(store, job, recompute_npz=recompute_npz, comparison_cache=cache)
        except (ValueError, AssertionError, KeyError, OSError, zipfile.BadZipFile) as error:
            row = {'id': job['id'], 'label': job['variant']['label'], 'run_id': job['run_id'],
                   'state': job['state'], 'verification': 'failed_verification', 'error_type': type(error).__name__, 'error': str(error)}
        report['jobs'].append(row)
    report['verified_unique_artifacts'] = len(store.artifacts.checked)
    report['verified_unique_artifact_bytes'] = sum(value[0] for value in store.artifacts.checked.values())
    report['counts'] = {status: sum(row['verification'] == status for row in report['jobs'])
                        for status in ('verified', 'verified_control_only', 'pending', 'failed_verification')}
    report['status'] = ('verification_failed' if report['counts']['failed_verification'] else
                        'pending' if report['counts']['pending'] or not report['jobs'] else 'selected_terminal_jobs_verified')
    report['status_file_changed_during_verification'] = path.read_bytes() != raw
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('round', type=int, choices=(1, 2))
    parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--label', action='append', default=[])
    parser.add_argument('--no-npz', action='store_true', help='Only verify raw hashes and original correctness receipts')
    args = parser.parse_args()
    report = verify_round(args.directory, args.round, labels=args.label, recompute_npz=not args.no_npz)
    target = args.directory / ('real-round-' + str(args.round)) / 'local-verifications'
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = target / (stamp + '-r' + str(report.get('revision', 0)) + '.json')
    with output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'report': str(output), 'status': report['status'], 'counts': report.get('counts'),
                      'jobs': [{key: job.get(key) for key in ('label', 'verification', 'classification', 'latency_ms',
                                                             'profile_medians_us', 'error')} for job in report['jobs']]}, ensure_ascii=False))
    sys.exit(1 if report['status'] == 'verification_failed' else 0)
