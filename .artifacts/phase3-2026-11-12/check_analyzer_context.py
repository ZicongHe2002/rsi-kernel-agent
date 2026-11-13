"""Check source/receipt references and analytical tool arguments, without TPU."""
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.profiler.models import calculate
from rsi_kernel_agent.workflow.mla_runner import validate_candidate_config

source = HERE / 'candidates/baseline-repair-1/mla_decode.py'
source_bytes = source.read_bytes()
assert hashlib.sha256(source_bytes).hexdigest() == '2fc23aa9d0358ea18698f916cbc7ed5e02a4218dec2de2fc955661a8b513ebae'
lines = source_bytes.decode().splitlines()
for line, text in {2902: 'def prepare(', 2931: 'def attention(', 2940: 'def body(',
                   2977: 'scores = jnp.where(valid', 2998: 'out_ref[0] =', 3018: 'scratch_shapes=('}.items():
    assert text in lines[line - 1], (line, lines[line - 1])
summary = json.loads((HERE / 'baseline-evidence-summary.json').read_bytes())
assert summary['result']['correctness'] == 'passed'
assert summary['stages']['benchmark']['latency_ms'] == 0.2207150005233416
assert len(summary['stages']['benchmark']['samples_ms']) == 20
for group, expected in zip(summary['stages']['profile']['profile_report']['groups'], (6.60375, .726875, 5.03875)):
    assert abs(group['median_us'] - expected) < 1e-12
for name in ('raw_xplane_refs',):
    for ref in summary['stages']['profile'][name]:
        data = (HERE / 'artifacts/sha256' / ref['digest'][:2] / ref['digest']).read_bytes()
        assert len(data) == ref['size'] and hashlib.sha256(data).hexdigest() == ref['digest']
ref = summary['stages']['profile']['report_ref']
data = (HERE / 'artifacts/sha256' / ref['digest'][:2] / ref['digest']).read_bytes()
assert len(data) == ref['size'] and hashlib.sha256(data).hexdigest() == ref['digest']
job = json.loads((HERE / 'baseline-job.json').read_bytes())
workload = job['contract']['workload']['spec']
config = {'schema_version': 1, 'kernel': 'mla_decode', 'case_overrides': {'decode-small': {'tiling': {'block_size': 512}}}}
assert validate_candidate_config(config, workload) == config
model = calculate('compute_memory', {'m': 16, 'n': 1024, 'k': 640, 'dtype_bytes': 2}, evidence_refs=[],
                  assumptions=['Single small-case QK matmul only; excludes softmax, PV, full cache writeback, prepare, mixed-precision accumulator and synchronization; no hardware rate assumed'])
assert model['classification'] == 'model_predicted'
assert model['outputs']['compute_bound_s'] is None
rows = []
for case in workload['cases']:
    shape = case['overrides']['shape']
    b, h, n, l, r = [shape[k] for k in ('batch', 'num_q_heads', 'seq_len', 'lkv_dim', 'r_dim')]
    d = l + (r + 127) // 128 * 128
    t = case['overrides']['tiling']['block_size']
    rows.append({'case': case['id'], 'legal_block_sizes': [size for size in range(128, n + 1, 128) if n % size == 0],
                 'current_tile_count': n // t, 'cache_read_plus_write_bytes': 2 * b * n * d * 2,
                 'qk_plus_pv_flops': 2 * b * h * n * (d + l),
                 'logical_prepare_output_bytes': 2 * b * h * d + 2 * b * t * d + 4 * b * h * t})
result = {'classification': 'Source audit and local deterministic calculation; no new hardware execution',
          'source_sha256': hashlib.sha256(source_bytes).hexdigest(), 'source_line_checks': 6,
          'profile_raw_hashes_verified': True, 'configuration_example_valid': True,
          'model_example': model, 'workload_shape_calculations': rows,
          'context_document_sha256': hashlib.sha256((HERE / 'analyzer-code-context.md').read_bytes()).hexdigest()}
(HERE / 'analyzer-code-context-check.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'status': 'checked', 'cases': rows, 'model_classification': model['classification']}, indent=2))
