"""Package four reviewed entry-only changes; original Intake creates records."""
import ast
from copy import deepcopy
from dataclasses import asdict
import difflib
from hashlib import sha256
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.contracts import ArtifactRef, canonical_json
from rsi_kernel_agent.store import ExperimentStore
from real_round_aux import read_json, write_once

d = Path(__file__).resolve().parent
t = d / 'real-round-2'
result = read_json(t / 'result.json')
assert result['winner_selected'] is False and result['analysis']['review'] == []
plans = result['plan_set']['plans']
assert len(plans) == 4 and {p['id'] for p in plans} == {
    'R2-A-length-row','R2-A-length-smem','R2-B-query','R2-B-b1-fusion'}
origins = {role:read_json(d / name) for role,name in {
    'A':'baseline-repair-runtime3-job.json','B':'baseline-b-runtime3-job.json'}.items()}
parent_dirs = {'A':'baseline-repair-1','B':'round1-delayed-store-wait'}
labels = {'R2-A-length-row':'length-row','R2-A-length-smem':'length-smem',
          'R2-B-query':'query-in-attention','R2-B-b1-fusion':'b1-fused-prepare'}
priorities = {'length-row':40,'length-smem':30,'query-in-attention':20,'b1-fused-prepare':10}
store = ExperimentStore(d / 'experiments.sqlite3', d / 'artifacts')
prepared = read_json(t / 'prepared.json')
parents = {row['strategy_id']:row for row in prepared['request']['strategies']}
specs, summary = {}, []
for plan in plans:
    role = 'A' if plan['id'].startswith('R2-A-') else 'B'
    origin = origins[role]
    variant = origin['variant']
    assert origin['strategy_id']==plan['strategy_id']
    assert plan['parent_id']==parents[plan['strategy_id']]['parent_id']
    assert parents[plan['strategy_id']]['source_artifact']==variant['source_ref']
    label = labels[plan['id']]
    assert plan['variants']==[label]
    path = d / 'candidates' / ('round2-'+label)
    parent = store.artifacts.read(ArtifactRef(**variant['source_ref']))
    raw = (path / 'mla_decode.py').read_bytes()
    assert raw != parent
    ast.parse(raw)
    newline = b'\r\n' if b'\r\n' in parent else b'\n'
    marker = b'@functools.lru_cache(maxsize=16)' + newline + b'def build_mla_decode_contiguous('
    assert parent.count(marker)==raw.count(marker)==1
    prefix = parent.index(marker)
    assert raw[:prefix]==parent[:prefix]
    assert raw[raw.index(b'\n__all__ =',prefix):]==parent[parent.index(b'\n__all__ =',prefix):]
    diff = ''.join(difflib.unified_diff(parent.decode().splitlines(True),raw.decode().splitlines(True),
        fromfile=parent_dirs[role]+'/mla_decode.py',tofile='round2-'+label+'/mla_decode.py')).encode()
    saved_diff = (path / 'change.diff').read_bytes()
    # Headers can include a/ b/ convention; the body must match the exact parent.
    assert saved_diff.splitlines()[2:]==diff.splitlines()[2:]
    source_ref = asdict(store.artifacts.put(raw))
    bundle = json.loads(store.artifacts.read(ArtifactRef(**variant['source_bundle_ref'])))
    build = json.loads(store.artifacts.read(ArtifactRef(**variant['build_ref'])))
    config = json.loads(store.artifacts.read(ArtifactRef(**variant['config_ref'])))
    assert len(bundle['files'])==375 and config['case_overrides']=={}
    updated_bundle = deepcopy(bundle)
    changed = []
    for item in updated_bundle['files']:
        if item['path']==updated_bundle['entry']['path']:
            assert item['artifact_ref']==variant['source_ref']
            item['artifact_ref']=source_ref
            changed.append(item['path'])
    assert changed==[bundle['entry']['path']]
    bundle_ref=asdict(store.artifacts.put(canonical_json(updated_bundle).encode()))
    updated_build=deepcopy(build)
    updated_build['source_bundle_digest']=bundle_ref['digest']
    build_ref=asdict(store.artifacts.put(canonical_json(updated_build).encode()))
    value=deepcopy(variant)
    value.update(label=label,source_ref=source_ref,source_bundle_ref=bundle_ref,build_ref=build_ref,
                 priority=priorities[label],cost={'executions':1,'tokens':0,'time_s':1800.0})
    assert value['config_ref']==variant['config_ref']
    note={'proposal_id':plan['id'],'label':label,'parent_id':plan['parent_id'],
          'parent_candidate_id':origin['candidate_id'],'parent_source_ref':variant['source_ref'],
          'variant':value,'diff_ref':asdict(store.artifacts.put(saved_diff)),
          'changed_bundle_paths':changed,'config':config,
          'oracle_bundle_ref':origin['contract']['context']['protocol']['oracle_bundle_ref'],
          'classification':'conversation-generated Round2 candidate; real validation pending'}
    write_once(path / 'packaged.json',note)
    summary.append(note)
    specs[plan['id']]=[value]
assert sum(len(v) for v in specs.values())==4
write_once(t / 'variants.json',specs)
write_once(t / 'candidate-artifacts.json',summary)
print(canonical_json({'plans':len(specs),'variants':len(summary),
    'sources':{row['label']:row['variant']['source_ref']['digest'] for row in summary}}))
