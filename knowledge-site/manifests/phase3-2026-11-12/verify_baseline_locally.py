"""Read-only verification of archived Phase 3 baseline; never contacts a Runtime."""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import ast
import json
import math
from pathlib import Path
import sqlite3
import statistics
import struct
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.artifacts import ArtifactStore
from rsi_kernel_agent.contracts import ArtifactRef, Evidence, canonical_json
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.workflow.evaluation import _verified_prefix
from rsi_kernel_agent.workflow.execution import make_request, verify_receipt
from rsi_kernel_agent.workflow.real_protocol import timing_statistics
from rsi_kernel_agent.profiler.profiles import normalize_duration_report


class ReadOnlyStore(ExperimentStore):
    def __init__(self, db, artifacts):
        self.db_path = Path(db).resolve()
        self.artifacts = ArtifactStore.__new__(ArtifactStore)
        self.artifacts.root = Path(artifacts).resolve()

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.db_path.as_uri() + '?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('PRAGMA query_only=ON')
            yield connection
        finally:
            connection.close()


def npy_header(stream):
    assert stream.read(6) == b'\x93NUMPY'
    version = tuple(stream.read(2))
    width = 2 if version == (1, 0) else 4
    size = int.from_bytes(stream.read(width), 'little')
    header = ast.literal_eval(stream.read(size).decode('latin1'))
    assert header['fortran_order'] is False
    return header


def metadata(archive):
    with archive.open('metadata.npy') as stream:
        npy_header(stream)
        return json.loads(stream.read())


def verify_npz(path):
    with zipfile.ZipFile(path) as archive:
        rows = metadata(archive)
        for row in rows:
            with archive.open(row['name'] + '.npy') as stream:
                header = npy_header(stream)
                assert list(header['shape']) == row['shape']
                digest = sha256()
                while block := stream.read(8 * 1024 * 1024):
                    digest.update(block)
                assert digest.hexdigest() == row['sha256']
    return rows


def compare_npz(candidate, oracle, specs):
    with zipfile.ZipFile(candidate) as actual, zipfile.ZipFile(oracle) as expected:
        actual_meta, expected_meta = metadata(actual), metadata(expected)
        results = []
        for index, spec in enumerate(specs):
            am, em = actual_meta[index], expected_meta[index]
            assert am['shape'] == em['shape'] == spec['shape']
            assert am['dtype'] == em['dtype'] == spec['dtype'] == 'bfloat16'
            with actual.open(am['name'] + '.npy') as a, expected.open(em['name'] + '.npy') as e:
                ah, eh = npy_header(a), npy_header(e)
                assert ah == eh
                if spec['comparison'] == 'exact':
                    equal = True
                    while True:
                        ab, eb = a.read(8 * 1024 * 1024), e.read(8 * 1024 * 1024)
                        equal &= ab == eb
                        if not ab and not eb:
                            break
                    assert equal
                    results.append({'name': spec['name'], 'shape': spec['shape'], 'exact_raw_bytes_equal': True})
                else:
                    def floats(raw):
                        return [struct.unpack('<f', struct.pack('<I', word[0] << 16))[0]
                                for word in struct.iter_unpack('<H', raw)]
                    av, ev = floats(a.read()), floats(e.read())
                    assert len(av) == len(ev) == math.prod(spec['shape'])
                    assert all(math.isfinite(v) for v in av + ev)
                    differences = [abs(x - y) for x, y in zip(av, ev)]
                    violations = sum(d > .01 + .01 * abs(x) for d, x in zip(differences, av))
                    assert violations == 0
                    results.append({'name': spec['name'], 'finite': True, 'tolerance_violations': violations,
                                    'max_abs': max(differences),
                                    'max_rel': max(d / max(abs(y), 1e-30) for d, y in zip(differences, ev)),
                                    'rms': math.sqrt(statistics.fmean(d * d for d in differences))})
        return results


directory = ROOT / '.artifacts/phase3-2026-11-12'
store = ReadOnlyStore(directory / 'experiments.sqlite3', directory / 'artifacts')
job = json.loads((directory / 'baseline-repair-job.json').read_text('utf-8'))
result = json.loads((directory / 'baseline-repair-result.json').read_text('utf-8'))
summary = json.loads((directory / 'baseline-evidence-summary.json').read_text('utf-8'))
assert result == summary['result']
receipts = {}
all_refs = {}
for stage, ref in result['receipt_refs'].items():
    body = json.loads(store.artifacts.read(ArtifactRef(**ref)))
    receipts[stage] = verify_receipt(store, make_request(store, job, stage), {**body, 'artifact_ref': ref})
    for artifact in body['artifact_refs']:
        all_refs[artifact['digest']] = artifact
_verified_prefix(store, job, receipts, False)
assert store.get(result['evaluation_id']).run_id == job['run_id']
assert canonical_json(asdict(store.best(job['task_id'], job['context_id']))) == canonical_json(summary['best'])
pending = list(store.get(result['evaluation_id']).evidence_ids)
seen = set()
evidence_refs = set()
while pending:
    identity = pending.pop()
    if identity in seen:
        continue
    seen.add(identity)
    evidence = store.get(identity)
    assert type(evidence) is Evidence and evidence.run_id == job['run_id']
    for ref in evidence.artifacts:
        store.artifacts.verify(ref)
        evidence_refs.add(ref.digest)
    pending.extend(evidence.input_ids)
assert set(all_refs) <= evidence_refs

correct = receipts['correctness']['outputs']
refs = correct['raw_artifact_refs']
case_results = []
for index, case in enumerate(job['contract']['workload']['spec']['cases']):
    group = refs[index * 6:index * 6 + 6]
    paths = [store.artifacts.path_for(ArtifactRef(**ref)) for ref in group]
    input_meta = verify_npz(paths[0])
    verify_npz(paths[1])
    verify_npz(paths[5])
    comparisons = compare_npz(paths[5], paths[1], case['outputs'])
    recorded = correct['cases'][index]
    for comparison, tensor in zip(comparisons, recorded['outputs']):
        if comparison['name'] == 'attention':
            for name in ('max_abs', 'max_rel', 'rms'):
                assert math.isclose(comparison[name], tensor[name], rel_tol=1e-12, abs_tol=1e-15)
    case_results.append({'id': case['id'], 'input_arrays_verified': len(input_meta), 'comparisons': comparisons})

bench = receipts['benchmark']['outputs']
assert timing_statistics(bench['samples_ms']) == result['statistics']
profile = receipts['profile']['outputs']
parsed = json.loads(store.artifacts.read(ArtifactRef(**profile['report_ref'])))
assert parsed == summary['stages']['profile']['profile_report']
binding = {name: job[name] for name in ('strategy_id', 'parent_id', 'candidate_id', 'run_id', 'context_id')}
binding.update(provenance='real', declared_identity=receipts['profile']['candidate_identity'])
normalized = normalize_duration_report(parsed, binding=binding, artifact_refs=[profile['report_ref']])
assert normalized['quality']['availability'] == 'available'
for group in parsed['groups']:
    times = [sample['duration_ps'] / 1e6 for sample in group['samples']]
    assert len(times) == group['count'] == 20
    assert math.isclose(statistics.median(times), group['median_us'], rel_tol=1e-12)
    assert math.isclose(statistics.mean(times), group['mean_us'], rel_tol=1e-12)

old_job = json.loads((directory / 'baseline-job.json').read_text('utf-8'))
old_result = json.loads((directory / 'baseline-result.json').read_text('utf-8'))
for stage, ref in old_result['receipt_refs'].items():
    body = json.loads(store.artifacts.read(ArtifactRef(**ref)))
    verify_receipt(store, make_request(store, old_job, stage), {**body, 'artifact_ref': ref})
assert old_result['classification'] == 'compile_failed'
tz = timezone(timedelta(hours=8))
def moment(ns):
    return datetime.fromtimestamp(ns / 1e9, tz).isoformat()
report = {
    'classification': 'Local independent re-verification of archived real TPU evidence',
    'network_calls': 0, 'store_access': 'sqlite mode=ro and query_only; no consider/integrate/write',
    'run_id': job['run_id'], 'original_run_id': old_job['run_id'], 'original_status': old_result['classification'],
    'recorded_evaluation_confirmed': store.get(result['evaluation_id']).confirmed,
    'verified_stage_receipts': list(receipts), 'unique_stage_artifacts': len(all_refs),
    'unique_stage_artifact_bytes': sum(ref['size'] for ref in all_refs.values()),
    'evidence_records_traversed': len(seen), 'all_stage_artifacts_reachable_from_evaluation': True,
    'case_results_independently_recomputed_from_npz': case_results,
    'benchmark_statistics_recomputed': result['statistics'],
    'benchmark_interval_asia_shanghai': [moment(bench['lease'][key]) for key in ('started_ns', 'finished_ns')],
    'profile_interval_asia_shanghai': [moment(profile['lease'][key]) for key in ('started_ns', 'finished_ns')],
    'profile_report_ref': profile['report_ref'], 'raw_xplane_refs': profile['raw_xplane_refs'],
    'profile_normalization_availability': normalized['quality']['availability'],
    'profile_groups': [{key: group[key] for key in ('kind', 'name', 'count', 'median_us', 'mean_us', 'invalid_samples')}
                       for group in parsed['groups']],
    'parser_rerun_on_raw_protobuf': False,
    'limitation': 'Raw XPlane SHA256/size checked; saved parser samples and normalization rechecked. Local TensorFlow parser not rerun. Oracle known-wrong real candidates remain unaccepted.'}
output = Path(__file__).with_name('independent-baseline-verification.json')
output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
