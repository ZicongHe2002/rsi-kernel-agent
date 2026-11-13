from dataclasses import asdict
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.contracts import ArtifactRef,canonical_json
from rsi_kernel_agent.workflow.execution import make_request,verify_receipt
d=Path(__file__).resolve().parent
s=ExperimentStore(d/'experiments.sqlite3',d/'artifacts')
job=json.loads((d/'baseline-repair-job.json').read_text('utf-8'))
result=json.loads((d/'baseline-repair-result.json').read_text('utf-8'))
summary={'job':{k:job[k] for k in ('task_id','strategy_id','plan_id','candidate_id','run_id','context_id')},'result':result,'stages':{}}
for stage,ref in result['receipt_refs'].items():
    receipt=json.loads(s.artifacts.read(ArtifactRef(**ref)))
    verify_receipt(s,make_request(s,job,stage),{**receipt,'artifact_ref':ref})
    output=receipt['outputs']
    row={k:v for k,v in output.items() if k not in ('identity','oracle_identity')}
    row['artifact_count']=len(receipt['artifact_refs'])
    row['artifact_bytes']=sum(x['size'] for x in receipt['artifact_refs'])
    if 'report_ref' in output:
        report=json.loads(s.artifacts.read(ArtifactRef(**output['report_ref'])))
        (d/'baseline-profile-raw-report.json').write_text(canonical_json(report),encoding='utf-8')
        row['profile_report']=report
    summary['stages'][stage]=row
summary['best']=asdict(s.best(job['task_id'],job['context_id']))
(d/'baseline-evidence-summary.json').write_text(canonical_json(summary),encoding='utf-8')
print(canonical_json(summary))
