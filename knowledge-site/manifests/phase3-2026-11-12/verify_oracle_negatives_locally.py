"""Read archived runtime2 Oracle controls only; no network or Store mutation."""
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import ast
import difflib
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
from rsi_kernel_agent.contracts import ArtifactRef, Decision, Evidence, canonical_json
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.workflow.evaluation import _verified_prefix
from rsi_kernel_agent.workflow.execution import make_request, stable_id, verify_receipt


class ReadOnlyStore(ExperimentStore):
    def __init__(self, db, artifacts):
        self.db_path = Path(db).resolve()
        self.artifacts = ArtifactStore.__new__(ArtifactStore)
        self.artifacts.root = Path(artifacts).resolve()

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
    return json.loads(Path(path).read_text('utf-8-sig'))


def read_npy(stream):
    assert stream.read(6) == b'\x93NUMPY'
    version = tuple(stream.read(2))
    assert version in ((1, 0), (2, 0), (3, 0))
    size = int.from_bytes(stream.read(2 if version == (1, 0) else 4), 'little')
    header = ast.literal_eval(stream.read(size).decode('latin1'))
    assert header['fortran_order'] is False
    return header, stream.read()


def arrays(path):
    with zipfile.ZipFile(path) as archive:
        with archive.open('metadata.npy') as stream:
            _, raw = read_npy(stream)
        metadata = json.loads(raw)
        result = []
        for row in metadata:
            with archive.open(row['name'] + '.npy') as stream:
                header, raw = read_npy(stream)
            assert list(header['shape']) == row['shape']
            assert sha256(raw).hexdigest() == row['sha256']
            result.append((row, header, raw))
        return result


def floats(raw):
    return [struct.unpack('<f', struct.pack('<I', word[0] << 16))[0]
            for word in struct.iter_unpack('<H', raw)]


def coordinate(index, shape):
    coords = []
    for width in reversed(shape):
        coords.append(index % width)
        index //= width
    return list(reversed(coords))


def compare(candidate, oracle, specs):
    result = []
    for actual, expected, spec in zip(arrays(candidate), arrays(oracle), specs, strict=True):
        am, ah, av = actual
        em, eh, ev = expected
        assert am['shape'] == em['shape'] == spec['shape'] and ah == eh
        assert am['dtype'] == em['dtype'] == spec['dtype'] == 'bfloat16'
        assert len(av) == len(ev) == math.prod(spec['shape']) * 2
        if spec['comparison'] == 'exact':
            indices = [index for index in range(len(av) // 2)
                       if av[index * 2:index * 2 + 2] != ev[index * 2:index * 2 + 2]]
            result.append({'name': spec['name'], 'exact_raw_bytes_equal': av == ev,
                           'mismatching_bf16_words': len(indices),
                           'mismatch_coordinates': [coordinate(i, spec['shape']) for i in indices[:10]]})
        else:
            af, ef = floats(av), floats(ev)
            assert all(math.isfinite(value) for value in af + ef)
            delta = [abs(a - e) for a, e in zip(af, ef)]
            indices = [i for i, (a, d) in enumerate(zip(af, delta)) if d > .01 + .01 * abs(a)]
            result.append({'name': spec['name'], 'finite': True, 'tolerance_violations': len(indices),
                           'violation_coordinates': [coordinate(i, spec['shape']) for i in indices[:10]],
                           'max_abs': max(delta),
                           'max_rel': max(d / max(abs(e), 1e-30) for d, e in zip(delta, ef)),
                           'rms': math.sqrt(statistics.fmean(d * d for d in delta))})
    return result


def verify_one(store, directory, kind):
    stem = 'oracle-negative-' + kind
    job = read_json(directory / (stem + '-runtime2-job.json'))
    result = read_json(directory / (stem + '-runtime2-result.json'))
    assert result['provenance'] == 'real' and result['classification'] == 'incorrect'
    assert result['correctness'] == 'failed' and result['confirmed'] is False
    assert result['latency_ms'] is result['statistics'] is None
    assert set(result['receipt_refs']) == {'generate', 'compile', 'correctness'}
    marker = store.get(stable_id('workflow-result', job['run_id']))
    assert marker.details['result'] == result and marker.details['job_id'] == job['id']
    assert marker.details['cancelled'] is False
    receipts, artifact_refs = {}, {}
    for stage, ref in result['receipt_refs'].items():
        body = json.loads(store.artifacts.read(ArtifactRef(**ref)))
        receipts[stage] = verify_receipt(store, make_request(store, job, stage), {**body, 'artifact_ref': ref})
        for item in body['artifact_refs']:
            artifact_refs[item['digest']] = item
    _verified_prefix(store, job, receipts, cancelled=False)
    assert receipts['generate']['status'] == receipts['compile']['status'] == 'succeeded'
    assert receipts['correctness']['status'] == 'failed'
    evaluation = store.get(result['evaluation_id'])
    assert evaluation.run_id == job['run_id'] and evaluation.correctness == 'failed'
    assert evaluation.confirmed is False and evaluation.latency_ms is None
    assert store.run_state(job['run_id'])['execution_status'] == 'failed'
    promotion = store.get(result['promotion_id'])
    assert promotion.kind == 'promotion' and promotion.target_id == evaluation.id
    assert promotion.details['eligible'] is False
    assert promotion.details['branch_promoted'] is promotion.details['task_promoted'] is False
    best = store.best(job['task_id'], job['context_id'])
    assert best is None or best.run_id != job['run_id']
    with store._connection() as con:
        assert con.execute('SELECT COUNT(*) FROM best WHERE evaluation_id=?', (evaluation.id,)).fetchone()[0] == 0
        rows = con.execute("SELECT * FROM records WHERE kind='Decision' AND task_id=?", (job['task_id'],)).fetchall()
    stages = []
    for row in rows:
        record = store._decode(row)
        if record.details.get('type') == 'colab_execution_request.v1':
            request = record.details['request']
            if request['job']['run_id'] == job['run_id']:
                stages.append(request['stage'])
    assert sorted(stages) == ['compile', 'correctness', 'generate']
    pending, seen, reached = list(evaluation.evidence_ids), set(), set()
    while pending:
        eid = pending.pop()
        if eid in seen:
            continue
        seen.add(eid)
        evidence = store.get(eid)
        assert type(evidence) is Evidence and evidence.run_id == job['run_id']
        for ref in evidence.artifacts:
            store.artifacts.verify(ref)
            reached.add(ref.digest)
        pending.extend(evidence.input_ids)
    assert set(artifact_refs) <= reached

    admission = store.get(stable_id('oracle-negative-admission', job['candidate_id']))
    assert type(admission) is Decision and admission.details['type'] == 'oracle_negative_admission.v1'
    source = store.artifacts.read(ArtifactRef(**job['variant']['source_ref']))
    assert source == (directory / 'candidates' / stem / 'mla_decode.py').read_bytes()
    parent = (directory / 'candidates/baseline-repair-1/mla_decode.py').read_bytes()
    assert sha256(parent).hexdigest() == '2fc23aa9d0358ea18698f916cbc7ed5e02a4218dec2de2fc955661a8b513ebae'
    before, after = ast.parse(parent), ast.parse(source)
    assert len(before.body) == len(after.body)
    changed = [getattr(a, 'name', type(a).__name__) for a, b in zip(before.body, after.body)
               if ast.dump(a) != ast.dump(b)]
    assert changed == ['build_mla_decode_contiguous']
    old_builder = next(n for n in before.body if getattr(n, 'name', '') == changed[0])
    builder = next(n for n in after.body if getattr(n, 'name', '') == changed[0])
    assert [ast.dump(n) for n in old_builder.body[:-1]] == [ast.dump(n) for n in builder.body[:-2]]
    assert builder.body[-2].name == 'oracle_negative_' + kind
    assert isinstance(builder.body[-1], ast.Return) and builder.body[-1].value.id == builder.body[-2].name
    diff = ''.join(difflib.unified_diff(parent.decode().splitlines(keepends=True), source.decode().splitlines(keepends=True),
                                      fromfile='a/pallas_kernels/kernels/mla_decode.py',
                                      tofile='b/pallas_kernels/kernels/mla_decode.py')).encode()
    assert diff == store.artifacts.read(ArtifactRef(**admission.details['diff_ref']))
    assert diff == (directory / 'candidates' / stem / 'change.diff').read_bytes()
    protocol = job['contract']['context']['protocol']
    bundle = json.loads(store.artifacts.read(ArtifactRef(**job['variant']['source_bundle_ref'])))
    oracle = json.loads(store.artifacts.read(ArtifactRef(**protocol['oracle_bundle_ref'])))
    bm = {r['path']: r['artifact_ref'] for r in bundle['files']}
    om = {r['path']: r['artifact_ref'] for r in oracle['files']}
    assert bm.keys() == om.keys()
    changed_files = [name for name in bm if bm[name] != om[name]]
    assert changed_files == ['pallas_kernels/kernels/mla_decode.py']
    assert bm[changed_files[0]] == job['variant']['source_ref']
    frozen = store.artifacts.read(ArtifactRef(**om[changed_files[0]]))
    assert sha256(frozen).hexdigest() == protocol['oracle_digest']
    oracle_symbol = oracle['entry']['symbol']
    assert ast.dump(next(n for n in ast.parse(frozen).body if getattr(n, 'name', '') == oracle_symbol)) == ast.dump(
        next(n for n in after.body if getattr(n, 'name', '') == oracle_symbol))

    output = receipts['correctness']['outputs']
    cases = []
    for index, case in enumerate(job['contract']['workload']['spec']['cases']):
        refs = output['raw_artifact_refs'][index * 6:index * 6 + 6]
        comparisons = compare(store.artifacts.path_for(ArtifactRef(**refs[5])),
                              store.artifacts.path_for(ArtifactRef(**refs[1])), case['outputs'])
        attention, cache = comparisons
        recorded = output['cases'][index]
        assert attention['tolerance_violations'] == recorded['outputs'][0]['tolerance_violations']
        for metric in ('max_abs', 'max_rel', 'rms'):
            assert math.isclose(attention[metric], recorded['outputs'][0][metric], rel_tol=1e-12, abs_tol=1e-15)
        assert cache['exact_raw_bytes_equal'] == recorded['outputs'][1]['passed']
        if kind == 'attention':
            assert attention['tolerance_violations'] == 1 and attention['violation_coordinates'] == [[0, 0, 0]]
            assert cache['exact_raw_bytes_equal'] is True
        else:
            assert attention['tolerance_violations'] == 0 and cache['mismatching_bf16_words'] == 1
            assert cache['mismatch_coordinates'] == [[1 if case['outputs'][1]['shape'][0] > 1 else 0, 0, 0]]
        cases.append({'id': case['id'], 'comparisons': comparisons})
    return {'kind': kind, 'run_id': job['run_id'], 'candidate_id': job['candidate_id'],
            'context_id': job['context_id'], 'runtime_id': job['contract']['context']['environment']['runtime_session_id'],
            'classification': result['classification'], 'verified_stage_receipts': sorted(receipts),
            'stored_execution_request_stages': sorted(stages), 'benchmark_or_profile_requested': False,
            'evaluation_confirmed': evaluation.confirmed, 'promotion_decision': promotion.details,
            'promoted': False, 'source_ref': job['variant']['source_ref'],
            'source_bundle_ref': job['variant']['source_bundle_ref'], 'diff_ref': admission.details['diff_ref'],
            'changed_top_level_definitions': changed, 'native_builder_body_unchanged': True,
            'frozen_oracle_source_sha256': protocol['oracle_digest'],
            'frozen_oracle_bundle_ref': protocol['oracle_bundle_ref'], 'oracle_ast_unchanged': True,
            'unique_stage_artifacts': len(artifact_refs),
            'unique_stage_artifact_bytes': sum(ref['size'] for ref in artifact_refs.values()),
            'evidence_records_traversed': len(seen), 'all_stage_artifacts_reachable_from_evaluation': True,
            'cases_independently_recomputed_from_npz': cases}


directory = ROOT / '.artifacts/phase3-2026-11-12'
store = ReadOnlyStore(directory / 'experiments.sqlite3', directory / 'artifacts')
report = {'schema_version': 1, 'verified_at_utc': datetime.now(timezone.utc).isoformat(),
          'network_calls': 0, 'store_access': 'sqlite mode=ro and query_only; no Store writes or consider calls',
          'scope': 'Only saved runtime2 small/boundary Oracle negative controls; no baseline/default NPZ reads',
          'controls': [verify_one(store, directory, kind) for kind in ('attention', 'cache')],
          'known_wrong_attention_and_cache_rejected': True,
          'limitation': 'Archived real execution independently verified locally; no hardware re-execution. Does not prove every incorrect Candidate is rejected.'}
target = Path(__file__).with_name('oracle-negative-verification.json')
target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
