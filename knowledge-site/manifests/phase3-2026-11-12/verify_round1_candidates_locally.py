"""Static source/package checks only. Never import candidate modules or open Store."""
import ast
import difflib
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
DIRECTORY = ROOT / '.artifacts/phase3-2026-11-12'
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.artifacts import ArtifactStore
from rsi_kernel_agent.contracts import ArtifactRef

artifacts = ArtifactStore.__new__(ArtifactStore)
artifacts.root = DIRECTORY / 'artifacts'
def read_json(path):
    return json.loads(path.read_text('utf-8-sig'))
def blob(ref):
    return artifacts.read(ArtifactRef(**ref))
def object_ref(ref):
    return json.loads(blob(ref))
def builder(tree):
    return next(node for node in tree.body if getattr(node, 'name', '') == 'build_mla_decode_contiguous')
def body_node(tree):
    return next(node for node in ast.walk(builder(tree)) if isinstance(node, ast.FunctionDef) and node.name == 'body')

base = read_json(DIRECTORY / 'baseline-repair-runtime2-job.json')
parent = (DIRECTORY / 'candidates/baseline-repair-1/mla_decode.py').read_bytes()
assert sha256(parent).hexdigest() == base['variant']['source_ref']['digest'] == '2fc23aa9d0358ea18698f916cbc7ed5e02a4218dec2de2fc955661a8b513ebae'
base_bundle = object_ref(base['variant']['source_bundle_ref'])
base_build = object_ref(base['variant']['build_ref'])
base_config = object_ref(base['variant']['config_ref'])
assert len(base_bundle['files']) == 375
assert base_config == {'schema_version': 1, 'kernel': 'mla_decode', 'case_overrides': {}}
parent_tree = ast.parse(parent)
parent_builder = builder(parent_tree)
old_lines = parent.splitlines(keepends=True)
result = read_json(DIRECTORY / 'real-round-1/result.json')
specs = read_json(DIRECTORY / 'real-round-1/variants.json')
packaged = read_json(DIRECTORY / 'real-round-1/candidate-artifacts.json')
plans = result['plan_set']['plans']
assert len(plans) == 4 and sum(len(p['variants']) for p in plans) == 5
assert set(specs) == {plan['id'] for plan in plans}
assert len(packaged) == 5
labels = {v['label'] for variants in specs.values() for v in variants}
assert labels == {'tile512', 'tile256', 'single-tile', 'delayed-store-wait', 'compact-new-row'}
base_files = {row['path']: row for row in base_bundle['files']}
for row in base_files.values():
    artifacts.verify(ArtifactRef(**row['artifact_ref']))
checks = []
for plan in plans:
    assert [v['label'] for v in specs[plan['id']]] == plan['variants']
    for variant in specs[plan['id']]:
        label = variant['label']
        folder = DIRECTORY / 'candidates' / ('round1-' + label)
        meta = read_json(folder / 'metadata.json')
        note = next(row for row in packaged if row['label'] == label)
        assert note == read_json(folder / 'packaged.json')
        assert note['proposal_id'] == plan['id'] and note['parent_id'] == plan['parent_id']
        assert note['variant'] == variant
        assert note['oracle_bundle_ref'] == base['contract']['context']['protocol']['oracle_bundle_ref']
        source = blob(variant['source_ref'])
        assert sha256(source).hexdigest() == meta['source_sha256']
        assert len(source) == meta['source_size'] and meta['parent_source_sha256'] == sha256(parent).hexdigest()
        candidate_bundle, candidate_build = [object_ref(variant[name]) for name in ('source_bundle_ref', 'build_ref')]
        candidate_config = object_ref(variant['config_ref'])
        assert note['config'] == candidate_config
        assert {key: value for key, value in candidate_bundle.items() if key != 'files'} == {
            key: value for key, value in base_bundle.items() if key != 'files'}
        candidate_files = {row['path']: row for row in candidate_bundle['files']}
        assert len(candidate_files) == 375 and candidate_files.keys() == base_files.keys()
        changed_files = [name for name in base_files if candidate_files[name] != base_files[name]]
        assert changed_files == note['changed_bundle_paths']
        assert candidate_build['source_bundle_digest'] == variant['source_bundle_ref']['digest']
        assert {k: v for k, v in candidate_build.items() if k != 'source_bundle_digest'} == {
            k: v for k, v in base_build.items() if k != 'source_bundle_digest'}
        assert variant['device'] == base['variant']['device']
        tree = ast.parse(source)
        compile(source, str(folder / 'mla_decode.py'), 'exec')
        changed = [getattr(a, 'name', type(a).__name__) for a, b in zip(parent_tree.body, tree.body)
                   if ast.dump(a) != ast.dump(b)]
        if label in ('tile512', 'tile256'):
            assert source == parent and variant['source_bundle_ref'] == base['variant']['source_bundle_ref']
            assert changed_files == changed == []
            assert candidate_build == base_build
            assert candidate_config == meta['requested_config'] == {
                'schema_version': 1, 'kernel': 'mla_decode',
                'case_overrides': {'decode-small': {'tiling': {'block_size': int(label[4:])}}}}
            diff = object_ref(note['diff_ref'])
            assert diff == {'parent_config': base_config, 'candidate_config': candidate_config}
            assert not (folder / 'mla_decode.py').exists()
        else:
            assert source == (folder / 'mla_decode.py').read_bytes()
            assert len(tree.body) == len(parent_tree.body)
            assert changed == ['build_mla_decode_contiguous']
            assert changed_files == [base_bundle['entry']['path']]
            assert candidate_files[changed_files[0]]['artifact_ref'] == variant['source_ref']
            assert candidate_config == base_config
            new_builder = builder(tree)
            lines = source.splitlines(keepends=True)
            assert old_lines[:parent_builder.lineno - 1] == lines[:new_builder.lineno - 1]
            assert old_lines[parent_builder.end_lineno:] == lines[new_builder.end_lineno:]
            diff = ''.join(difflib.unified_diff(parent.decode().splitlines(keepends=True), source.decode().splitlines(keepends=True),
                          fromfile='a/pallas_kernels/kernels/mla_decode.py', tofile='b/pallas_kernels/kernels/mla_decode.py')).encode()
            assert diff == (folder / 'change.diff').read_bytes() == blob(note['diff_ref'])
            assert sha256(diff).hexdigest() == meta['diff_sha256']
            if label == 'delayed-store-wait':
                old_body, new_body = body_node(parent_tree).body, body_node(tree).body
                wait = lambda node: (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                                     and ast.unparse(node.value) == 'store.wait()')
                assert len([n for n in old_body if wait(n)]) == len([n for n in new_body if wait(n)]) == 1
                assert [ast.dump(n) for n in old_body if not wait(n)] == [ast.dump(n) for n in new_body if not wait(n)]
                assert wait(new_body[-2]) and isinstance(new_body[-1], ast.Return)
                writes = [n for n in ast.walk(body_node(tree)) if isinstance(n, ast.Assign)
                          and any(isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id == 'tile' for t in n.targets)]
                assert len(writes) == 1 and writes[0].lineno == 2952
            elif label == 'single-tile':
                old_body, new_body = body_node(parent_tree).body, body_node(tree).body
                branch = new_body[-2]
                assert isinstance(branch, ast.If) and ast.unparse(branch.test) == 'capacity == block_size'
                assert [ast.dump(n) for n in branch.orelse] == [ast.dump(n) for n in old_body[-6:-1]]
                assert [ast.dump(n) for n in new_body[:-2]] == [ast.dump(n) for n in old_body[:-6]]
            else:
                replacements = [
                    ('np_ref[b] = jnp.broadcast_to(row, (block_size, padded_dim))', 'np_ref[b] = row'),
                    ('jax.ShapeDtypeStruct((batch, block_size, padded_dim), new.dtype)', 'jax.ShapeDtypeStruct((batch, 1, padded_dim), new.dtype)'),
                    ('query, new_tile, lens = q_ref[0], new_ref[0], len_ref[0]', 'query, new_row, lens = q_ref[0], new_ref[0], len_ref[0]\n      new_tile = jnp.broadcast_to(new_row, (block_size, padded_dim))'),
                    ('pl.BlockSpec((1, block_size, padded_dim), index)', 'pl.BlockSpec((1, 1, padded_dim), index)')]
                reconstructed = parent.decode()
                for before, after in replacements:
                    assert reconstructed.count(before) == 1
                    reconstructed = reconstructed.replace(before, after.replace('\n', '\r\n') if b'\r\n' in parent else after, 1)
                assert reconstructed.encode() == source
        checks.append({'plan_id': plan['id'], 'label': label, 'source_ref': variant['source_ref'],
                       'source_bundle_ref': variant['source_bundle_ref'], 'config_ref': variant['config_ref'],
                       'build_ref': variant['build_ref'], 'diff_ref': note['diff_ref'],
                       'changed_top_level_definitions': changed, 'changed_bundle_paths': changed_files,
                       'bundle_file_count': len(candidate_files), 'config': candidate_config,
                       'oracle_and_runner_build_digests_unchanged': True})
report = {'scope': 'Static source and packaged bytes; no candidate imports, JAX/TPU calls, network or SQLite access',
          'plans': len(plans), 'variants': len(checks), 'parent_source_sha256': sha256(parent).hexdigest(),
          'baseline_bundle_file_count': len(base_files), 'checked_variants': checks,
          'runtime_correctness_verified': False, 'performance_verified': False}
target = Path(__file__).with_name('round1-candidate-static-verification.json')
target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
