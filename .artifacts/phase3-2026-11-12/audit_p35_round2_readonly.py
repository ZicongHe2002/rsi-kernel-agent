"""Independent local-file review only; no Store, JAX, bridge or candidate imports."""
import ast
from copy import deepcopy
from datetime import datetime, timezone
import difflib
from hashlib import sha256
import json
from pathlib import Path

D = Path(__file__).resolve().parent
T = D / 'real-round-2'
START = datetime.now(timezone.utc).isoformat()
verified = {}


def read(path):
    return json.loads(path.read_bytes())


def artifact(ref):
    raw = (D / 'artifacts' / 'sha256' / ref['digest'][:2] / ref['digest']).read_bytes()
    assert len(raw) == ref['size'] and sha256(raw).hexdigest() == ref['digest']
    verified[ref['digest']] = ref['size']
    return raw


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def dump(node):
    return ast.dump(node, include_attributes=False)


def fn(body, name):
    return next(n for n in body if isinstance(n, ast.FunctionDef) and n.name == name)


def keyword(call, key):
    return next(k for k in call.keywords if k.arg == key)


def is_batch_one(expr):
    return dump(expr) == dump(ast.parse('batch == 1', mode='eval').body)


session = read(T / 'session.json')
result = read(T / 'result.json')
assert sha256(canonical(result)).hexdigest() == session['result_digest']
result_ref = {'digest': session['result_digest'], 'size': len(canonical(result))}
assert json.loads(artifact(result_ref)) == result
manifest = json.loads(artifact(session['manifest']))
assert result['manifest_digest'] == session['manifest']['digest']
assert result['exchanges'] == session['responses']
assert result['proposal'] == read(T / 'response-3-final.json')['final'] == session['responses'][-1]['final']
assert len(session['responses']) == 3
assert result['winner_selected'] is False and result['analysis']['review'] == []
proposal = result['proposal']
plans = result['plan_set']['plans']
assert len(plans) == 4
labels = {'R2-A-length-row': 'length-row', 'R2-A-length-smem': 'length-smem',
          'R2-B-query': 'query-in-attention', 'R2-B-b1-fusion': 'b1-fused-prepare'}
assert {p['id'] for p in plans} == set(labels)
assert sum(len(p['variants']) for p in plans) == 4
assert {p['id']: p['variants'] for p in plans} == {k: [v] for k, v in labels.items()}
A = 'mla-strategy:deee0989771642ee8e066be07adb0370a6c7a3bca6be04ebc8ab50e145c5b05e'
B = 'mla-strategy:ad3416851dfcc72142373541d6dc1f47121b390cce460b7fa479e9436b97e3cd'
expected_parents = {A: 'wf-parent:a37a1b7abd28b0681f9fd736f13eb97e37f15612',
                    B: 'wf-parent:88fc2af19b8d773ad8ce15664ff31573f958e8a3'}
expected_sources = {A: '2fc23aa9d0358ea18698f916cbc7ed5e02a4218dec2de2fc955661a8b513ebae',
                    B: '0579beaa21cbf1119b64585c11334ab7051976a0f4b22400997fa510bd3b3a34'}
expected_config = '078d6739d75ab25fadee5c0f5b71b6862497d782e2da7295f6d77c00241555f6'
refs = manifest['references']
predicate_ids = []
checked_ref_count = 0
for section in ('code_understanding', 'observations', 'hypotheses', 'plans'):
    for item in proposal[section]:
        sid = item['strategy_id']
        for name in item.get('refs', []):
            checked_ref_count += 1
            if name.startswith('calculation:'):
                assert result['calculations'][name.split(':', 1)[1]]['strategy_id'] == sid
            else:
                ref = refs[name]
                assert ref['availability'] == 'available'
                if ref.get('strategy_id') is not None:
                    assert ref['strategy_id'] == sid, (section, item.get('id'), name, ref['strategy_id'], sid)
        predicate = item.get('predicate')
        if predicate:
            assert predicate['ref'] in item['refs']
            value = refs[predicate['ref']]['value']
            for key in predicate['path']:
                value = value[key]
            assert value == predicate['value']
            predicate_ids.append(item['id'])
for role, sid in (('A', A), ('B', B)):
    observations = {o['id']: o for o in proposal['observations'] if o['strategy_id'] == sid}
    assert observations[role+'-ABBA-host']['predicate']['value'] == 'inconclusive_drift'
    assert observations[role+'-ABBA-program']['predicate']['value'] == 'replicated_observed_improvement'
    assert len([o for o in observations if '-R1-' in o]) == (3 if role == 'A' else 2)
    assert role+'-changed-direction' in observations
    for plan in (p for p in plans if p['strategy_id'] == sid):
        assert plan['parent_id'] == expected_parents[sid] == manifest['strategies'][sid]['parent_id']
        assert set(plan['observation_ids']) == set(observations)

expected_math = {'R2-A-length-expanded': 65536, 'R2-A-length-row': 4096,
                 'R2-A-length-scalar': 4, 'R2-B-query-prepared': 20480,
                 'R2-B-query-raw': 18432, 'R2-B-prepare-interface': 1396736,
                 'R2-B-raw-interface': 19588}
assert set(result['calculations']) == set(expected_math)
for name, calc in result['calculations'].items():
    assert calc['classification'] == 'model_predicted' and calc['model'] == 'live_memory'
    buffers = calc['inputs']['buffers']
    assert all(buf['start'] == 0 and buf['end'] == 1 for buf in buffers)
    peak = sum(buf['elements'] * buf['dtype_bytes'] * buf['copies'] for buf in buffers)
    assert peak == calc['outputs']['peak_bytes'] == expected_math[name]
    assert calc['outputs']['capacity_fraction'] is None and calc['outputs']['headroom_bytes'] is None
    assert 'capacity_bytes' not in calc['inputs'] and 'hardware_basis' not in calc['inputs']
    assert any('logical interface payloads' in a and 'not physical' in a for a in calc['assumptions'])

prepared = read(T / 'prepared.json')
assert prepared['snapshot']['version'] == 10 and len(prepared['cards']) == 3
assert all(not card['paused'] for card in prepared['cards'])
prepared_parents = {row['strategy_id']: row for row in prepared['request']['strategies']}
for sid in (A, B):
    row = manifest['strategies'][sid]
    hist = row['history']
    assert hist == prepared['histories'][sid]
    assert hist['complete'] and hist['next_offset'] is None and len(hist['records']) == hist['total']
    assert row['parent_id'] == prepared_parents[sid]['parent_id'] == expected_parents[sid]
    assert row['source_artifact'] == prepared_parents[sid]['source_artifact']
    assert row['source_artifact']['digest'] == expected_sources[sid]
    assert row['config_artifact']['digest'] == expected_config
del prepared

variants = read(T / 'variants.json')
summaries = read(T / 'candidate-artifacts.json')
assert len(summaries) == 4 and set(variants) == set(labels)
origins = {'A': read(D / 'baseline-repair-runtime3-job.json'),
           'B': read(D / 'baseline-b-runtime3-job.json')}
package_checks = []
for plan in plans:
    role = 'A' if plan['id'].startswith('R2-A-') else 'B'
    origin = origins[role]
    parent_variant = origin['variant']
    label = labels[plan['id']]
    directory = D / 'candidates' / ('round2-' + label)
    note = read(directory / 'packaged.json')
    assert note in summaries and variants[plan['id']] == [note['variant']]
    candidate = note['variant']
    assert plan['strategy_id'] == origin['strategy_id']
    assert note['parent_id'] == plan['parent_id']
    assert note['parent_candidate_id'] == origin['candidate_id']
    assert note['parent_source_ref'] == parent_variant['source_ref']
    assert parent_variant['source_ref']['digest'] == expected_sources[plan['strategy_id']]
    assert candidate['config_ref'] == parent_variant['config_ref']
    assert candidate['config_ref']['digest'] == expected_config
    config = json.loads(artifact(candidate['config_ref']))
    assert config == note['config'] and config['case_overrides'] == {}
    assert candidate['device'] == parent_variant['device']
    assert note['oracle_bundle_ref'] == origin['contract']['context']['protocol']['oracle_bundle_ref']
    artifact(note['oracle_bundle_ref'])
    assert candidate['cost'] == {'executions': 1, 'tokens': 0, 'time_s': 1800.0}
    parent = artifact(parent_variant['source_ref'])
    raw = artifact(candidate['source_ref'])
    assert raw == (directory / 'mla_decode.py').read_bytes()
    newline = b'\r\n' if b'\r\n' in parent else b'\n'
    marker = b'@functools.lru_cache(maxsize=16)' + newline + b'def build_mla_decode_contiguous('
    assert parent.count(marker) == raw.count(marker) == 1
    prefix = parent.index(marker)
    assert raw[:prefix] == parent[:prefix]
    assert raw[raw.index(b'\n__all__ =', prefix):] == parent[parent.index(b'\n__all__ =', prefix):]
    saved_diff = artifact(note['diff_ref'])
    assert saved_diff == (directory / 'change.diff').read_bytes()
    regenerated = ''.join(difflib.unified_diff(parent.decode().splitlines(True), raw.decode().splitlines(True))).encode()
    assert saved_diff.splitlines()[2:] == regenerated.splitlines()[2:]
    p_tree, c_tree = ast.parse(parent), ast.parse(raw)
    assert len(p_tree.body) == len(c_tree.body)
    different = [getattr(p, 'name', type(p).__name__) for p, c in zip(p_tree.body, c_tree.body) if dump(p) != dump(c)]
    assert different == ['build_mla_decode_contiguous']
    old_bundle = json.loads(artifact(parent_variant['source_bundle_ref']))
    new_bundle = json.loads(artifact(candidate['source_bundle_ref']))
    assert len(old_bundle['files']) == len(new_bundle['files']) == 375
    entry_path = old_bundle['entry']['path']
    changed = [a['path'] for a, b in zip(old_bundle['files'], new_bundle['files']) if a != b]
    assert changed == [entry_path] == note['changed_bundle_paths']
    normalized_bundle = deepcopy(new_bundle)
    for row in normalized_bundle['files']:
        if row['path'] == entry_path:
            assert row['artifact_ref'] == candidate['source_ref']
            row['artifact_ref'] = parent_variant['source_ref']
    assert normalized_bundle == old_bundle
    old_build = json.loads(artifact(parent_variant['build_ref']))
    new_build = json.loads(artifact(candidate['build_ref']))
    assert new_build['source_bundle_digest'] == candidate['source_bundle_ref']['digest']
    new_build['source_bundle_digest'] = old_build['source_bundle_digest']
    assert old_build == new_build
    normalized_variant = deepcopy(candidate)
    for key in ('label', 'source_ref', 'source_bundle_ref', 'build_ref', 'priority', 'cost'):
        normalized_variant[key] = parent_variant[key]
    assert normalized_variant == parent_variant
    package_checks.append({'label': label, 'parent_id': plan['parent_id'],
                           'source_sha256': candidate['source_ref']['digest'], 'size': len(raw),
                           'entry_only': True, 'other_bundle_files_unchanged': 374,
                           'config_device_oracle_build_options_unchanged': True})

parent_tree = ast.parse(artifact(origins['B']['variant']['source_ref']))
parent_entry = fn(parent_tree.body, 'build_mla_decode_contiguous')
parent_native = fn(parent_entry.body, 'native')
parent_prepare = fn(parent_native.body, 'prepare')
parent_attention = fn(parent_native.body, 'attention')
def tuple_assign_to(body, names):
    return next(n for n in body if isinstance(n, ast.Assign)
                and isinstance(n.targets[0], ast.Tuple)
                and [x.id for x in n.targets[0].elts if isinstance(x, ast.Name)] == list(names))


parent_prepare_assign = tuple_assign_to(parent_native.body, ('qp', 'np_', 'lp'))
parent_return = next(n for n in parent_native.body if isinstance(n, ast.Return))
parent_call = parent_return.value.func
b_reviews = []
for label in ('query-in-attention', 'b1-fused-prepare'):
    raw = (D / 'candidates' / ('round2-' + label) / 'mla_decode.py').read_bytes()
    entry = fn(ast.parse(raw).body, 'build_mla_decode_contiguous')
    native = fn(entry.body, 'native')
    prepare = fn(native.body, 'prepare')
    attention = fn(native.body, 'attention')
    call_return = next(n for n in native.body if isinstance(n, ast.Return))
    call = call_return.value.func
    assert dump(entry.args) == dump(parent_entry.args)
    assert dump(native.args) == dump(parent_native.args)
    assert dump(fn(attention.body, 'body')) == dump(fn(parent_attention.body, 'body'))
    normalized = deepcopy(entry)
    n_native = fn(normalized.body, 'native')
    n_attention = fn(n_native.body, 'attention')
    n_return = next(n for n in n_native.body if isinstance(n, ast.Return))
    if label == 'b1-fused-prepare':
        assert dump(prepare) == dump(parent_prepare)
        branch = next(n for n in n_native.body if isinstance(n, ast.If) and is_batch_one(n.test))
        assert len(branch.orelse) == 1 and dump(branch.orelse[0]) == dump(parent_prepare_assign)
        assert dump(branch.body[0]) == dump(ast.parse('qp, np_, lp = q, new, lengths').body[0])
        n_native.body[n_native.body.index(branch)] = branch.orelse[0]
        att_branch = n_attention.body[1]
        assert isinstance(att_branch, ast.If) and is_batch_one(att_branch.test)
        assert len(att_branch.orelse) == 1 and dump(att_branch.orelse[0]) == dump(parent_attention.body[1])
        n_attention.body[1] = att_branch.orelse[0]
        specs = keyword(n_return.value.func, 'in_specs')
        assert isinstance(specs.value, ast.IfExp) and is_batch_one(specs.value.test)
        assert isinstance(specs.value.body, ast.Tuple) and len(specs.value.body.elts) == 4
        assert all(not isinstance(v, ast.Tuple) for v in specs.value.body.elts)
        assert dump(specs.value.body) == dump(ast.parse('(vmem, vmem, vmem, hbm)', mode='eval').body)
        assert dump(specs.value.orelse) == dump(keyword(parent_call, 'in_specs').value)
        specs.value = specs.value.orelse
        assert dump(normalized) == dump(parent_entry)
        b_reviews.append({'label': label, 'parent_fallback_ast_exact': True,
                          'in_specs_node': 'IfExp', 'b1_input_tuple_length': 4,
                          'b1_condition_only_static_batch': True, 'cache_recurrence_wait_ast_exact': True})
    else:
        assert [x.arg for x in prepare.args.args] == ['new_ref', 'len_ref', 'np_ref', 'lp_ref']
        expected_prepare = deepcopy(parent_prepare)
        expected_prepare.args.args = [x for x in expected_prepare.args.args if x.arg not in ('q_ref', 'qp_ref')]
        loop = next(n for n in expected_prepare.body if isinstance(n, ast.For))
        loop.body = loop.body[1:]
        assert dump(prepare) == dump(expected_prepare)
        assert [dump(n) for n in attention.body[3:]] == [dump(n) for n in parent_attention.body[2:]]
        assert dump(attention.body[0]) == dump(parent_attention.body[0])
        assert dump(attention.body[1]) == dump(ast.parse('query = jnp.concatenate((q_ref[0], jnp.zeros((heads, padded_dim - dim), q.dtype)), axis=1)').body[0])
        assert dump(attention.body[2]) == dump(ast.parse('new_tile, lens = new_ref[0], len_ref[0]').body[0])
        prepare_assign = tuple_assign_to(native.body, ('np_', 'lp'))
        assert [dump(x) for x in prepare_assign.value.args] == [dump(ast.Name(id=x, ctx=ast.Load())) for x in ('new', 'lengths')]
        for key in ('in_specs', 'out_specs'):
            assert len(keyword(prepare_assign.value.func, key).value.elts) == 2
        assert [dump(x) for x in keyword(prepare_assign.value.func, 'out_shape').value.elts] == [dump(x) for x in keyword(parent_prepare_assign.value.func, 'out_shape').value.elts[1:]]
        assert dump(keyword(call, 'in_specs').value.elts[0]) == dump(ast.parse('pl.BlockSpec((1, heads, dim), index)', mode='eval').body)
        assert [dump(x) for x in keyword(call, 'in_specs').value.elts[1:]] == [dump(x) for x in keyword(parent_call, 'in_specs').value.elts[1:]]
        n_native.body[n_native.body.index(fn(n_native.body, 'prepare'))] = deepcopy(parent_prepare)
        idx = n_native.body.index(tuple_assign_to(n_native.body, ('np_', 'lp')))
        n_native.body[idx] = deepcopy(parent_prepare_assign)
        n_attention.body = [deepcopy(parent_attention.body[0]), deepcopy(parent_attention.body[1]), *n_attention.body[3:]]
        keyword(n_return.value.func, 'in_specs').value = deepcopy(keyword(parent_call, 'in_specs').value)
        assert [dump(x) for x in n_return.value.args] == [dump(ast.Name(id=x, ctx=ast.Load())) for x in ('q', 'np_', 'lp', 'cache')]
        n_return.value.args = deepcopy(parent_return.value.args)
        assert dump(normalized) == dump(parent_entry)
        b_reviews.append({'label': label, 'only_query_placement_changed': True,
                          'prepare_new_and_lengths_ast_exact': True,
                          'cache_recurrence_wait_ast_exact': True})

packager = (D / 'package_p35_round2_candidates.py').read_text()
assert "newline = b'\\r\\n' if b'\\r\\n' in parent else b'\\n'" in packager
helper_raw = (D / 'recover_p35_round2_controller.py').read_bytes()
ast.parse(helper_raw)
output = {'schema_version': 1, 'started_utc': START, 'finished_utc': datetime.now(timezone.utc).isoformat(),
          'classification': 'static_readonly_review_passed',
          'scope': 'Author/final evidence and all four packages; independent B source review. A source author is this reviewer, so no independent A review claim.',
          'session_result_digest': session['result_digest'], 'manifest': session['manifest'],
          'predicate_ids_verified': predicate_ids, 'reference_uses_verified': checked_ref_count,
          'logical_math_peak_bytes': expected_math, 'plan_count': len(plans), 'variant_count': 4,
          'packages': package_checks, 'B_source_reviews': b_reviews,
          'immutable_artifacts_rehashed': len(verified), 'immutable_bytes_rehashed': sum(verified.values()),
          'crlf_marker_blocker_resolved': True,
          'recovery_helper': {'sha256': sha256(helper_raw).hexdigest(), 'ast_parse': 'passed',
                              'review': 'Fixed 4 admitted R2 jobs and 9 prior jobs; validates original costs and attempt 0; original Controller.step only, bounded 240 iterations. Never invoked by this review.',
                              'operational_constraint': 'Its diagnostic driver lock is not acquired by the official run_real_round driver; official process exit must be checked before use.'},
          'limitations': ['No JAX import, candidate execution, TPU compilation, numeric correctness or performance validation.',
                          'No production Store import, constructor, write, publication, intake or driver invocation.']}
path = D / 'round2-author-package-B-independent-review.json'
path.write_bytes(canonical(output) + b'\n')
print(json.dumps({'review_path': str(path), 'classification': output['classification'], 'predicates': len(predicate_ids),
                  'references': checked_ref_count, 'packages': len(package_checks), 'B_candidates': len(b_reviews),
                  'artifacts_rehashed': len(verified), 'bytes_rehashed': sum(verified.values())}))
