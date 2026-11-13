"""Verify one archived runtime2 baseline and merge its independent local report."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3
import statistics
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.artifacts import ArtifactStore
from rsi_kernel_agent.contracts import ArtifactRef, Evidence, canonical_json
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.workflow.evaluation import _verified_prefix
from rsi_kernel_agent.workflow.execution import make_request, stable_id, verify_receipt
from rsi_kernel_agent.workflow.real_protocol import timing_statistics
from rsi_kernel_agent.profiler.profiles import normalize_duration_report


class ReadOnlyArtifacts(ArtifactStore):
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.checked = {}

    def verify(self, ref):
        path = self.path_for(ref)
        before = path.stat()
        identity = (before.st_size, before.st_mtime_ns, before.st_ino)
        assert before.st_size == ref.size
        if self.checked.get(ref.digest) == identity:
            return
        digest = sha256()
        total = 0
        with path.open('rb') as stream:
            while data := stream.read(8 * 1024 * 1024):
                digest.update(data)
                total += len(data)
        after = path.stat()
        assert identity == (after.st_size, after.st_mtime_ns, after.st_ino)
        assert total == ref.size and digest.hexdigest() == ref.digest
        self.checked[ref.digest] = identity


class ReadOnlyStore(ExperimentStore):
    def __init__(self, db, artifacts):
        self.db_path = Path(db).resolve()
        self.artifacts = ReadOnlyArtifacts(artifacts)

    @contextmanager
    def _connection(self):
        con = sqlite3.connect(self.db_path.as_uri() + '?mode=ro', uri=True)
        con.row_factory = sqlite3.Row
        try:
            con.execute('PRAGMA query_only=ON')
            yield con
        finally:
            con.close()


def read_json(path):
    return json.loads(path.read_text('utf-8-sig'))


def verify(name, directory):
    store = ReadOnlyStore(directory / 'experiments.sqlite3', directory / 'artifacts')
    job_path, result_path = [directory / (name + '-runtime2-' + kind + '.json') for kind in ('job', 'result')]
    job, result = read_json(job_path), read_json(result_path)
    assert result['provenance'] == 'real' and result['correctness'] == 'passed'
    assert result['confirmed'] is True and result['timing_validity'] == 'valid'
    expected = {'generate', 'compile', 'correctness', 'benchmark', 'profile'}
    assert set(result['receipt_refs']) == expected
    marker = store.get(stable_id('workflow-result', job['run_id']))
    assert marker.details['result'] == result and marker.details['job_id'] == job['id']
    assert marker.details['cancelled'] is False
    config = read_json(directory / 'colab-config-runtime2.json')
    assert job['contract']['context']['environment']['runtime_session_id'] == config['runtime_id']
    old = read_json(directory / 'baseline-repair-job.json')
    assert job['context_id'] != old['context_id']
    assert job['variant']['source_ref'] == old['variant']['source_ref']
    receipts, refs = {}, {}
    for stage, ref in result['receipt_refs'].items():
        body = json.loads(store.artifacts.read(ArtifactRef(**ref)))
        receipts[stage] = verify_receipt(store, make_request(store, job, stage), {**body, 'artifact_ref': ref})
        assert body['status'] == 'succeeded'
        for item in body['artifact_refs']:
            refs[item['digest']] = item
    _verified_prefix(store, job, receipts, cancelled=False)
    evaluation = store.get(result['evaluation_id'])
    assert evaluation.run_id == job['run_id'] and evaluation.confirmed is True
    assert evaluation.correctness == 'passed' and evaluation.timing_validity == 'valid'
    assert evaluation.latency_ms == result['latency_ms']
    assert store.run_state(job['run_id'])['execution_status'] == 'succeeded'
    pending, seen, reached = list(evaluation.evidence_ids), set(), set()
    while pending:
        eid = pending.pop()
        if eid in seen:
            continue
        seen.add(eid)
        item = store.get(eid)
        assert type(item) is Evidence and item.run_id == job['run_id']
        for ref in item.artifacts:
            store.artifacts.verify(ref)
            reached.add(ref.digest)
        pending.extend(item.input_ids)
    assert set(refs) <= reached
    correctness = receipts['correctness']['outputs']
    cases = correctness['cases']
    assert [case['id'] for case in cases] == ['decode-small', 'decode-boundary', 'decode-default']
    assert all(case['passed'] and all(o['passed'] and o['tolerance_violations'] == 0 for o in case['outputs']) for case in cases)
    benchmark = receipts['benchmark']['outputs']
    measured = timing_statistics(benchmark['samples_ms'])
    assert measured == result['statistics'] and measured['median_ms'] == result['latency_ms']
    assert len(benchmark['samples_ms']) == 20 and benchmark['warmup_count'] == 5
    assert benchmark['synchronized'] is True
    profile = receipts['profile']['outputs']
    assert profile['lease']['started_ns'] > benchmark['lease']['finished_ns']
    assert profile['capture_operation_id'] != profile['benchmark_operation_id']
    parsed = json.loads(store.artifacts.read(ArtifactRef(**profile['report_ref'])))
    binding = {key: job[key] for key in ('strategy_id', 'parent_id', 'candidate_id', 'run_id', 'context_id')}
    binding.update(provenance='real', declared_identity=receipts['profile']['candidate_identity'])
    normalized = normalize_duration_report(parsed, binding=binding, artifact_refs=[profile['report_ref']])
    assert normalized['quality']['availability'] == 'available'
    assert parsed['errors'] == []
    for group in parsed['groups']:
        durations = [sample['duration_ps'] / 1e6 for sample in group['samples']]
        assert len(durations) == group['count'] == 20 and group['invalid_samples'] == 0
        assert math.isclose(statistics.median(durations), group['median_us'], rel_tol=1e-12)
        assert math.isclose(statistics.mean(durations), group['mean_us'], rel_tol=1e-12)
    stored_profile = store.get(result['profile_id'])
    assert stored_profile.run_id == job['run_id'] and stored_profile.availability == 'available'
    for eid in stored_profile.evidence_ids:
        evidence = store.get(eid)
        assert evidence.run_id == job['run_id'] and evidence.availability == 'available'
        for ref in evidence.artifacts:
            store.artifacts.verify(ref)
    promotion = store.get(result['promotion_id'])
    branch_best = store.best(job['task_id'], job['context_id'], job['strategy_id'])
    return {'name': name, 'verified_at_utc': datetime.now(timezone.utc).isoformat(),
            'result_file_sha256': sha256(result_path.read_bytes()).hexdigest(),
            'job_file_sha256': sha256(job_path.read_bytes()).hexdigest(),
            'run_id': job['run_id'], 'candidate_id': job['candidate_id'], 'strategy_id': job['strategy_id'],
            'context_id': job['context_id'], 'runtime_id': config['runtime_id'],
            'source_ref': job['variant']['source_ref'], 'classification': result['classification'],
            'evaluation_id': evaluation.id, 'confirmed': evaluation.confirmed,
            'verified_stage_receipts': sorted(receipts), 'receipt_refs': result['receipt_refs'],
            'unique_stage_artifacts': len(refs), 'unique_stage_artifact_bytes': sum(r['size'] for r in refs.values()),
            'evidence_records_traversed': len(seen), 'all_stage_artifacts_reachable_from_evaluation': True,
            'correctness_from_reverified_receipts': cases, 'benchmark_case_id': job['contract']['context']['protocol']['benchmark_case_id'],
            'benchmark_raw_samples_ms': benchmark['samples_ms'], 'benchmark_statistics_recomputed': measured,
            'benchmark_warmup_count': benchmark['warmup_count'], 'benchmark_synchronized': True,
            'parent_latency_ms': result['parent_latency_ms'], 'profile_report_ref': profile['report_ref'],
            'raw_xplane_refs': profile['raw_xplane_refs'], 'profile_trace_mode': profile['trace_mode'],
            'profile_operation_id': profile['capture_operation_id'], 'benchmark_operation_id': profile['benchmark_operation_id'],
            'profile_and_benchmark_nonoverlapping': True, 'profile_normalization_quality': normalized['quality'],
            'profile_groups': [{key: group[key] for key in ('kind', 'name', 'count', 'median_us', 'mean_us', 'invalid_samples')}
                               for group in parsed['groups']],
            'profile_multi_device_span': normalized['multi_device_span'],
            'promotion_decision_at_completion': promotion.details,
            'current_branch_best_evaluation_id': None if branch_best is None else branch_best.id,
            'independent_raw_correctness_recomparison': False, 'independent_raw_protobuf_reparse': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('name', choices=('baseline-repair', 'baseline-b'))
    args = parser.parse_args()
    directory = ROOT / '.artifacts/phase3-2026-11-12'
    target = Path(__file__).with_name('runtime2-baseline-verification.json')
    report = read_json(target) if target.exists() else {
        'schema_version': 1, 'network_calls': 0,
        'store_access': 'sqlite mode=ro and query_only; no Store writes or consider calls',
        'scope': 'Archived runtime2 receipts, complete Evidence closure, raw timing samples and saved Profile parser output',
        'baselines': {},
        'limitations': ['Correctness metrics verified against original receipt protocol; raw NPZ hashes verified but comparisons not recomputed.',
                        'Raw XPlane SHA/size verified and saved parser samples/normalization recomputed; protobuf not reparsed.',
                        'No cross-Runtime speedup claim; baseline source is unchanged compatibility repair.']}
    row = verify(args.name, directory)
    report['baselines'][args.name] = row
    report['both_runtime2_baselines_verified'] = set(report['baselines']) == {'baseline-repair', 'baseline-b'}
    if report['both_runtime2_baselines_verified']:
        a, b = [report['baselines'][name] for name in ('baseline-repair', 'baseline-b')]
        assert a['runtime_id'] == b['runtime_id'] and a['context_id'] == b['context_id']
        assert a['strategy_id'] != b['strategy_id'] and a['source_ref'] == b['source_ref']
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: row[key] for key in ('name', 'run_id', 'context_id', 'confirmed', 'unique_stage_artifacts',
          'unique_stage_artifact_bytes', 'evidence_records_traversed', 'benchmark_statistics_recomputed',
          'profile_normalization_quality', 'profile_groups')}, ensure_ascii=False, indent=2))
