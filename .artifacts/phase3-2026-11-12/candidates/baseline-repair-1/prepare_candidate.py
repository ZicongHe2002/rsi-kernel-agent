"""Prepare one isolated compile-compatibility repair from audited source bytes.

This script does not execute JAX, change the external repository, or write Store.
The real baseline compilation receipt is the RED; the same frozen matrix must
be executed for this new Candidate before calling the repair verified.
"""
from pathlib import Path
import ast
import difflib
import hashlib
import json
import os


ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
SOURCE_ROOT = (Path('C:/Users/fengluo/Desktop/training/PallasKernels') if os.name == 'nt'
               else Path('/mnt/c/Users/fengluo/Desktop/training/PallasKernels'))
RELATIVE = Path('pallas_kernels/kernels/mla_decode.py')
MANIFEST = ROOT / '.artifacts/phase3-2026-11-12'
ARTIFACTS = MANIFEST / 'artifacts/sha256'
RECEIPT = '43ba495153a5c701872273826704cb0083a89f19b824a75fe02d8b4256a5e160'
STDERR = 'a741295551e873d5475185fc026d20b4e25bd64881304adcd397483b627a725c'
SMALL_HLO = '4b31433bc45198fc100f860ea889d6bde14f6982404e3983943eaf4be69263d8'
EXPECTED = 'e53b0e5ccff3ecf18192f30ef179cdb207c177f2f2dac48b2a08f40acc5000a6'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def artifact(value):
    data = (ARTIFACTS / value[:2] / value).read_bytes()
    assert digest(data) == value, 'Archived artifact digest mismatch'
    return data


source = (SOURCE_ROOT / RELATIVE).read_bytes()
assert digest(source) == EXPECTED, 'Authoritative source changed after baseline'
text = source.decode('utf-8')
newline = '\r\n' if '\r\n' in text else '\n'
before = '''    def prepare(q_ref, new_ref, len_ref, qp_ref, np_ref, lp_ref):
      new_rows = new_ref[...]
      lens = len_ref[...]
      for b in range(batch):
        qp_ref[b] = jnp.concatenate(
          (q_ref[b], jnp.zeros((heads, padded_dim - dim), q.dtype)), axis=1
        )
        row = jnp.concatenate(
          (new_rows[b : b + 1], jnp.zeros((1, padded_dim - dim), new.dtype)), axis=1
        )
        np_ref[b] = jnp.broadcast_to(row, (block_size, padded_dim))
        lp_ref[b] = jnp.broadcast_to(lens[b : b + 1], (heads, block_size))
'''.replace('\n', newline)
after = '''    def prepare(q_ref, new_ref, len_ref, qp_ref, np_ref, lp_ref):
      # Pad before slicing so concatenate sees matching row offsets.
      new_rows = jnp.concatenate(
        (new_ref[...], jnp.zeros((batch, padded_dim - dim), new.dtype)), axis=1
      )
      lens = len_ref[...]
      for b in range(batch):
        qp_ref[b] = jnp.concatenate(
          (q_ref[b], jnp.zeros((heads, padded_dim - dim), q.dtype)), axis=1
        )
        row = new_rows[b : b + 1]
        np_ref[b] = jnp.broadcast_to(row, (block_size, padded_dim))
        lp_ref[b] = jnp.broadcast_to(lens[b : b + 1], (heads, block_size))
'''.replace('\n', newline)
assert text.count(before) == 1, 'Repair context is not unique'
candidate = text.replace(before, after).encode('utf-8')
assert candidate.replace(after.encode('utf-8'), before.encode('utf-8')) == source
original_ast = ast.parse(source)
candidate_ast = ast.parse(candidate)
original_top = {node.name: node for node in original_ast.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
candidate_top = {node.name: node for node in candidate_ast.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
changed_top = [name for name in original_top if ast.dump(original_top[name]) != ast.dump(candidate_top[name])]
assert changed_top == ['build_mla_decode_contiguous']
assert ast.dump(original_top['mla_decode_paged_reference']) == ast.dump(candidate_top['mla_decode_paged_reference'])

receipt_bytes = artifact(RECEIPT)
receipt = json.loads(receipt_bytes)
assert receipt['stage'] == 'compile' and receipt['status'] == 'failed'
assert receipt['outputs']['error_type'] == 'MosaicError'
assert 'result/input offset mismatch on non-concat dimension' in receipt['outputs']['message']
assert 'vector<1x192xbf16>' in receipt['outputs']['message']
assert receipt['candidate_identity']['source_digest'] == EXPECTED
artifact_rows = []
for ref in receipt['artifact_refs']:
    data = artifact(ref['digest'])
    assert len(data) == ref['size']
    artifact_rows.append({'digest': ref['digest'], 'size': ref['size'], 'verified': True})
stderr = artifact(STDERR)
assert b'mla_decode.py":2909:14' in stderr
hlo = artifact(SMALL_HLO)
assert b'bf16[1,16,576]' in hlo and b'bf16[1,1024,640]' in hlo

destination = OUT / 'mla_decode.py'
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_bytes(candidate)
diff = ''.join(difflib.unified_diff(text.splitlines(keepends=True), candidate.decode('utf-8').splitlines(keepends=True),
                                  fromfile='a/' + RELATIVE.as_posix(), tofile='b/' + RELATIVE.as_posix()))
(OUT / 'change.diff').write_bytes(diff.encode('utf-8'))
(OUT / 'compile-red-receipt.json').write_bytes(receipt_bytes)
(OUT / 'compile-red.stderr.txt').write_bytes(stderr)
summary = {
    'classification': 'Source audit; real TPU compile failure observed; repair not yet executed',
    'parent_candidate_id': receipt['candidate_id'], 'parent_run_id': receipt['run_id'],
    'red_compile_operation_id': receipt['operation_id'], 'red_receipt_digest': RECEIPT,
    'stderr_digest': STDERR, 'small_compiled_hlo_digest': SMALL_HLO,
    'failing_case': 'decode-boundary',
    'failing_workload': {'batch': 3, 'heads': 16, 'lkv_dim': 128, 'r_dim': 64, 'seq_len': 256, 'block_size': 128,
                         'lengths': [0, 1, 129], 'opaque_tail_nan': True},
    'case_progress': {'decode-small': 'compiled; no correctness or timing claim',
                      'decode-boundary': 'compile_failed', 'decode-default': 'not reached'},
    'source_path': str(SOURCE_ROOT / RELATIVE), 'candidate_path': 'mla_decode.py',
    'parent_source_sha256': EXPECTED, 'candidate_source_sha256': digest(candidate),
    'candidate_size': len(candidate), 'diff_sha256': digest(diff.encode('utf-8')),
    'change': 'Pad complete new-KV batch before row selection; retain per-row broadcast and all remaining operations',
    'hypothesis': 'Full-batch concatenate avoids incompatible non-concat row offsets introduced by slicing batch row b=1 before padding',
    'historical_source': 'C:/Users/fengluo/Desktop/project2/result/mla_decode_fix_20261104/mla_decode_fix.patch',
    'historical_patch_sha256': digest((SOURCE_ROOT.parents[1] / 'project2/result/mla_decode_fix_20261104/mla_decode_fix.patch').read_bytes()),
    'static_checks': {'python_ast_parses': True, 'only_changed_top_level_definition': changed_top,
                      'oracle_function_ast_unchanged': True, 'all_other_source_bytes_unchanged': True,
                      'authoritative_source_sha256_unchanged': digest((SOURCE_ROOT / RELATIVE).read_bytes()) == EXPECTED},
    'archived_compile_artifacts': artifact_rows,
    'required_validation': 'Same frozen compile/correctness matrix on actual TPU; unchanged oracle, tolerances, runner and benchmark',
    'verified_performance_improvement': False,
}
(OUT / 'repair-analysis.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
response = {
    'reason': 'Repair real Mosaic compile failure in decode-boundary: pad the full new-KV batch before selecting each row, avoiding the observed non-concat row-offset mismatch. This is baseline compatibility repair, not performance optimization.',
    'source_path': str(destination), 'source_sha256': digest(candidate), 'parent_source_sha256': EXPECTED,
    'changed_lines': {'original_start': 2902, 'original_end': 2913, 'candidate_start': 2902, 'candidate_end': 2914,
                      'changed_top_level_definition': changed_top, 'diff_path': str(OUT / 'change.diff')},
    'history_evidence': {'path': summary['historical_source'], 'sha256': summary['historical_patch_sha256'],
                         'method_id': 'hist-decode-prepare-padding', 'relation': 'Same prepare transformation; current real RED independently observed'},
    'current_red_evidence': {'receipt_digest': RECEIPT, 'stderr_digest': STDERR, 'failing_case': 'decode-boundary',
                             'run_id': receipt['run_id'], 'operation_id': receipt['operation_id']},
    'validation_status': 'static checks only; real TPU matrix required',
}
(OUT / 'response.json').write_text(json.dumps(response, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'candidate': str(destination), 'sha256': summary['candidate_source_sha256'],
                  'size': len(candidate), 'failing_case': summary['failing_case'], 'static_checks': summary['static_checks']}, indent=2))
