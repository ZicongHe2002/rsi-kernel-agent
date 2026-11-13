"""Repair only the local completion projection, preserving its original bytes."""
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from rsi_kernel_agent.contracts import canonical_json
from rsi_kernel_agent.store import ExperimentStore
from real_round_aux import completed_round, read_json, write_once
d=Path(__file__).resolve().parent
p=d/'real-round-1/completed.json'
before=p.read_bytes(); value=json.loads(before)
intake=read_json(p.with_name('intake.json'))
store=ExperimentStore(d/'experiments.sqlite3',d/'artifacts')
jobs=completed_round(store,'mla-decode-v1',value['round_id'])
assert value['intake_id']==intake['id']
assert value['results']=={j['id']:j['result'] for j in jobs}
assert store.get(intake['publication_id']).details['type']=='analysis_result.v1'
if 'publication_id' not in value:
    backup=p.with_name('completed-before-publication-field.json.original')
    if backup.exists(): assert backup.read_bytes()==before
    else: backup.write_bytes(before)
    value['publication_id']=intake['publication_id']
    pending=p.with_suffix('.pending')
    with pending.open('wb') as f:
        f.write(canonical_json(value).encode());f.flush();os.fsync(f.fileno())
    assert p.read_bytes()==before
    os.replace(pending,p)
else: assert value['publication_id']==intake['publication_id']
print(json.dumps({'projection_verified':True,'jobs':len(jobs),'sha256':sha256(p.read_bytes()).hexdigest()}))
