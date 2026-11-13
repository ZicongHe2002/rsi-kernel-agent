"""Record observed allocation loss without guessing that remote work failed."""
from dataclasses import asdict
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from rsi_kernel_agent.contracts import Decision,RunEvent,canonical_json
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.workflow.execution import stable_id
d=Path(__file__).resolve().parent
s=ExperimentStore(d/'experiments.sqlite3',d/'artifacts')
job=json.loads((d/'oracle-negative-attention-job.json').read_text('utf-8'))
records=s.history(job['task_id'])
requests=[r for r in records if type(r) is Decision and r.details.get('type')=='colab_execution_request.v1'
          and r.target_id==job['run_id']]
assert not requests,'Unknown submitted operation must not be reclassified as unsubmitted'
result={'schema_version':1,'observed_at_utc':'2026-11-12T05:43:00Z',
    'runtime_id':job['contract']['context']['environment']['runtime_session_id'],
    'discovery':'original bridge list_assignments returned empty; control plane reachable',
    'lost_runtime_cause':'unknown','baseline_completed_receipts_preserved':True,
    'negative_run_id':job['run_id'],'durable_execution_request_count':0,
    'interpretation':'Host lookup could not establish a live ledger. No backend submit intent exists; no negative Kernel was submitted by this driver.',
    'resumption':'Allocate v6e under existing user authorization; new observed environment requires new Context and Run.'}
ref=s.artifacts.put(canonical_json(result).encode())
note=Decision(stable_id('runtime-loss',job['run_id']),job['task_id'],'note',job['run_id'],
    'Runtime disappeared before this negative-control submission; completed baseline evidence remains local',
    {'type':'runtime_loss.v1','report_ref':asdict(ref),'not_submitted':True})
s.append(note,note.id)
state=s.run_state(job['run_id'])
event=RunEvent(job['run_id']+':runtime-lost',job['run_id'],'incomplete','finished',
    'Runtime unavailable before backend submission; no correctness verdict')
if state['execution_status']=='running':s.transition(event,job['run_id']+':start',event.id)
(ROOT/'knowledge-site/manifests/phase3-2026-11-12/runtime1-loss.json').write_text(canonical_json(result),encoding='utf-8')
print(canonical_json({'run_id':job['run_id'],'state':s.run_state(job['run_id']),'submitted_requests':len(requests)}))
