"""Read saved HLO custom-call metadata; no device resource measurements."""
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.contracts import ArtifactRef,canonical_json
from rsi_kernel_agent.workflow.execution import stable_id
d=Path(__file__).resolve().parent
s=ExperimentStore(d/'experiments.sqlite3',d/'artifacts')
j=json.loads((d/'baseline-repair-runtime2-job.json').read_bytes())
op=stable_id('operation',j['run_id'],'compile')
record=s.get(stable_id('execution-receipt',op))
receipt=json.loads(s.artifacts.read(ArtifactRef(**record.details['artifact_ref'])))
rows=[]
for ref in receipt['artifact_refs']:
    if ref['size']>2000000:continue
    raw=s.artifacts.read(ArtifactRef(**ref))
    if not raw.startswith(b'HloModule'):continue
    lines=raw.decode('utf-8').splitlines()
    calls=[]
    for line in lines:
        if ' custom-call(' not in line or 'backend_config=' not in line:continue
        prefix,payload=line.split('backend_config=',1)
        config=json.loads(payload)
        calls.append({'name':prefix.strip().split(' = ',1)[0],
            'declared_scoped_memory_configs':config.get('scoped_memory_configs'),
            'used_scoped_memory_configs':config.get('used_scoped_memory_configs'),
            'retry_config':config.get('retry_config')})
    rows.append({'raw_hlo_ref':ref,'hlo_header':lines[0],'custom_calls':calls})
result={'classification':'compiler-estimated metadata; not measured hardware counters',
        'run_id':j['run_id'],'operation_id':op,'receipt_ref':record.details['artifact_ref'],
        'source_identity':receipt['candidate_identity'],'hlo':rows,
        'limitations':['Compiler used_scoped_memory_configs does not measure live VMEM use or spill',
            'Logical broadcast tensor sizes are not physical materialization evidence',
            'Serialized custom-call body is preserved in original HLO, not decoded as LLO']}
ref=s.artifacts.put(canonical_json(result).encode('utf-8'))
(d/'baseline-runtime2-hlo-metadata.json').write_text(canonical_json({**result,'report_ref':asdict(ref)}),encoding='utf-8')
print(json.dumps({'report_ref':asdict(ref),'hlo':rows,'classification':result['classification']}))
