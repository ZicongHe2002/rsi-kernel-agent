"""Package explicitly reviewed experiment bytes; original intake owns records."""
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from rsi_kernel_agent.contracts import ArtifactRef,canonical_json
from rsi_kernel_agent.store import ExperimentStore
from real_round_aux import read_json,write_once
d=Path(__file__).resolve().parent
s=ExperimentStore(d/'experiments.sqlite3',d/'artifacts')
base=read_json(d/'baseline-repair-runtime2-job.json')
variant=base['variant']
bundle=json.loads(s.artifacts.read(ArtifactRef(**variant['source_bundle_ref'])))
build=json.loads(s.artifacts.read(ArtifactRef(**variant['build_ref'])))
config=json.loads(s.artifacts.read(ArtifactRef(**variant['config_ref'])))
# The source-audit inventory has390 files; the existing execution bundle has375.
assert len(bundle['files'])==375 and config['case_overrides']=={}
expected={
 'single-tile':'325e0e9098633d625bcac832f9cb043935c7ce205df64a13b53fc84e22133e23',
 'delayed-store-wait':'0579beaa21cbf1119b64585c11334ab7051976a0f4b22400997fa510bd3b3a34',
 'compact-new-row':'c9e270a86764555f38cbbfe658857d9c48bf9d0e8163a1b7d4edc41959236c0d'}
specs={};summary=[]
result=read_json(d/'real-round-1/result.json')
for plan in result['plan_set']['plans']:
    values=[]
    for label in plan['variants']:
        path=d/'candidates'/('round1-'+label)
        raw=(path/'mla_decode.py').read_bytes() if label in expected else s.artifacts.read(ArtifactRef(**variant['source_ref']))
        assert sha256(raw).hexdigest()==expected.get(label,variant['source_ref']['digest'])
        source_ref=asdict(s.artifacts.put(raw))
        new_bundle=deepcopy(bundle)
        changed=[]
        for item in new_bundle['files']:
            if item['path']==new_bundle['entry']['path']:
                if item['artifact_ref']!=source_ref: changed.append(item['path'])
                item['artifact_ref']=source_ref
        assert changed==([bundle['entry']['path']] if label in expected else [])
        bundle_ref=asdict(s.artifacts.put(canonical_json(new_bundle).encode('utf-8')))
        new_build=deepcopy(build);new_build['source_bundle_digest']=bundle_ref['digest']
        build_ref=asdict(s.artifacts.put(canonical_json(new_build).encode('utf-8')))
        new_config=deepcopy(config)
        if label in ('tile512','tile256'):
            new_config['case_overrides']={'decode-small':{'tiling':{'block_size':int(label[4:])}}}
        config_ref=asdict(s.artifacts.put(canonical_json(new_config).encode('utf-8')))
        value={'label':label,'source_ref':source_ref,'source_bundle_ref':bundle_ref,
               'config_ref':config_ref,'build_ref':build_ref,'priority':0,'device':variant['device'],
               'cost':{'executions':1,'tokens':0,'time_s':1800.0}}
        values.append(value)
        diff=(path/'change.diff').read_bytes() if label in expected else canonical_json({'parent_config':config,'candidate_config':new_config}).encode('utf-8')
        diff_ref=asdict(s.artifacts.put(diff))
        note={'proposal_id':plan['id'],'label':label,'parent_id':plan['parent_id'],'variant':value,
              'diff_ref':diff_ref,'changed_bundle_paths':changed,'config':new_config,
              'classification':'conversation-generated candidate; real validation pending',
              'oracle_bundle_ref':base['contract']['context']['protocol']['oracle_bundle_ref']}
        write_once(path/'packaged.json',note)
        summary.append(note)
    specs[plan['id']]=values
write_once(d/'real-round-1/variants.json',specs)
write_once(d/'real-round-1/candidate-artifacts.json',summary)
print(canonical_json({'plans':len(specs),'variants':len(summary),'source_paths_changed':[x['changed_bundle_paths'] for x in summary],
                      'source_digests':[x['variant']['source_ref']['digest'] for x in summary]}))
