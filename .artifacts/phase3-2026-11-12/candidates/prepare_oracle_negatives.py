"""Make deliberately incorrect isolated candidates for the fixed real oracle.

No TPU work, Store mutation, oracle change, or baseline rewrite occurs here.
"""
from pathlib import Path
import ast
import difflib
import hashlib
import json


ROOT = Path(__file__).resolve().parent
PARENT = ROOT / 'baseline-repair-1/mla_decode.py'
PARENT_SHA = '2fc23aa9d0358ea18698f916cbc7ed5e02a4218dec2de2fc955661a8b513ebae'
BUILDER = 'build_mla_decode_contiguous'
PATH = 'pallas_kernels/kernels/mla_decode.py'


def sha(data):
    return hashlib.sha256(data).hexdigest()


parent_bytes = PARENT.read_bytes()
assert sha(parent_bytes) == PARENT_SHA, 'Frozen repair source changed'
parent_text = parent_bytes.decode('utf-8')
newline = '\r\n' if '\r\n' in parent_text else '\n'
parent_tree = ast.parse(parent_bytes)
builder = next(node for node in parent_tree.body if isinstance(node, ast.FunctionDef) and node.name == BUILDER)
final_return = builder.body[-1]
assert isinstance(final_return, ast.Return) and isinstance(final_return.value, ast.Name)
assert final_return.value.id == 'native' and final_return.lineno == final_return.end_lineno
lines = parent_text.splitlines(keepends=True)
assert lines[final_return.lineno - 1].strip() == 'return native'
prefix = ''.join(lines[:final_return.lineno - 1])
suffix = ''.join(lines[final_return.end_lineno:])

wrappers = {
    'attention': '''  @jax.jit
  def oracle_negative_attention(q, new, cache, lengths):
    attention, updated_cache = native(q, new, cache, lengths)
    # Deliberately wrong validation candidate: corrupt only one attention value.
    attention = attention.at[0, 0, 0].add(jnp.asarray(1, dtype=attention.dtype))
    return attention, updated_cache

  return oracle_negative_attention
''',
    'cache': '''  @jax.jit
  def oracle_negative_cache(q, new, cache, lengths):
    attention, updated_cache = native(q, new, cache, lengths)
    # The boundary case's row 1, token 0 is appended and finite; B1 uses row 0.
    row = 1 if updated_cache.shape[0] > 1 else 0
    # Deliberately wrong validation candidate: corrupt only one cache value.
    updated_cache = updated_cache.at[row, 0, 0].add(jnp.asarray(1, dtype=updated_cache.dtype))
    return attention, updated_cache

  return oracle_negative_cache
''',
}
rows = []
for kind, wrapper in wrappers.items():
    candidate_text = prefix + wrapper.replace('\n', newline) + suffix
    candidate_bytes = candidate_text.encode('utf-8')
    tree = ast.parse(candidate_bytes)
    candidate_builder = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == BUILDER)
    assert len(candidate_builder.body) == len(builder.body) + 1
    # Every original statement, including the actual native kernel and its
    # interpreter warning, remains identical except the final returned callable.
    assert [ast.dump(node) for node in builder.body[:-1]] == [ast.dump(node) for node in candidate_builder.body[:-2]]
    changed = []
    assert len(parent_tree.body) == len(tree.body)
    for before, after in zip(parent_tree.body, tree.body):
        if ast.dump(before) != ast.dump(after):
            changed.append(getattr(before, 'name', type(before).__name__))
    assert changed == [BUILDER]
    old_oracle = next(node for node in parent_tree.body if isinstance(node, ast.FunctionDef) and node.name == 'mla_decode_paged_reference')
    new_oracle = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'mla_decode_paged_reference')
    assert ast.dump(old_oracle) == ast.dump(new_oracle)
    output = ROOT / ('oracle-negative-' + kind)
    output.mkdir(exist_ok=True)
    source_path = output / 'mla_decode.py'
    source_path.write_bytes(candidate_bytes)
    diff = ''.join(difflib.unified_diff(parent_text.splitlines(keepends=True), candidate_text.splitlines(keepends=True),
                                      fromfile='a/' + PATH, tofile='b/' + PATH))
    (output / 'change.diff').write_bytes(diff.encode('utf-8'))
    response = {
        'purpose': 'known-wrong real-oracle validation; not optimization',
        'reason': 'Deliberately corrupt one ' + kind + ' output value by BF16 +1 after the unchanged native kernel returns; require the fixed independent oracle to reject this new Candidate.',
        'source_path': str(source_path), 'source_sha256': sha(candidate_bytes),
        'parent_source_path': str(PARENT), 'parent_source_sha256': PARENT_SHA,
        'source_size': len(candidate_bytes),
        'changed_lines': {'parent_final_return': final_return.lineno,
                          'candidate_wrapper_start': final_return.lineno,
                          'candidate_wrapper_end': candidate_builder.end_lineno,
                          'diff_path': str(output / 'change.diff')},
        'mechanism': {'output': kind, 'mutation': 'add BF16 1',
                      'index': '[0,0,0]' if kind == 'attention' else '[1 if batch > 1 else 0,0,0]',
                      'expected_rejection': 'allclose tolerance violation' if kind == 'attention' else 'complete cache byte mismatch',
                      'fixed_boundary_case': 'decode-boundary attention[0] is exactly zero; cache[1,0,0] is finite appended new-KV'},
        'static_validation': {'python_ast_parses': True, 'only_changed_top_level_definition': changed,
                              'native_kernel_ast_unchanged': True, 'oracle_ast_unchanged': True,
                              'parent_source_unchanged': sha(PARENT.read_bytes()) == PARENT_SHA,
                              'original_external_source_not_written': True},
        'validation_status': 'prepared; no real correctness execution yet',
        'required_execution': 'Full existing compile and correctness matrix, same oracle and tolerance; do not benchmark if rejected',
    }
    (output / 'response.json').write_text(json.dumps(response, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    rows.append({'kind': kind, 'path': str(source_path), 'sha256': sha(candidate_bytes), 'size': len(candidate_bytes)})
print(json.dumps(rows, indent=2))
