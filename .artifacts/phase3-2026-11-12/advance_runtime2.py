"""Sequential prerequisite validation through existing backend and Evaluator."""
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from rsi_kernel_agent.contracts import Decision,RunEvent,canonical_json
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.workflow.execution import make_request,stable_id
from rsi_kernel_agent.workflow.evaluation import integrate_result
from rsi_kernel_agent.workflow.colab import ColabBackend
from rsi_kernel_agent.workflow.__main__ import colab_remote
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from oracle_gate_aux import record_oracle_gate_failure, require_oracle_gate_open, require_resumable_run
from real_round_aux import prerequisite_names

def advance(directory, generation, *, guard=None):
    d=Path(directory)
    s=ExperimentStore(d/'experiments.sqlite3',d/'artifacts')
    with WorkflowJournal(s,json.loads((d/('oracle-negative-attention-'+generation+'-job.json')).read_text('utf-8'))['task_id']).lock():
        if guard is not None:
            guard(s,d,generation)
        backend=ColabBackend(s,colab_remote(d/('colab-config-'+generation+'.json')))
        for name in prerequisite_names(d,generation):
            name+='-'+generation
            job=json.loads((d/(name+'-job.json')).read_text('utf-8'))
            negative=name.startswith('oracle-negative-')
            if negative:require_oracle_gate_open(s,job)
            require_resumable_run(s,job)
            run_id=job['run_id']
            if s.run_state(run_id)['execution_status']=='not_run':
                note=Decision(stable_id('prerequisite-reservation',run_id),job['task_id'],'note',run_id,
                    'New-runtime prerequisite reservation',{'type':'prerequisite_reservation.v1','cost':job['variant']['cost']})
                s.append(note,note.id)
                s.transition(RunEvent(run_id+':start',run_id,'running','active','New-runtime prerequisite'),None,run_id+':start')
            receipts={}
            stages=('generate','compile','correctness') if negative else ('generate','compile','correctness','benchmark','profile')
            for stage in stages:
                request=make_request(s,job,stage)
                while True:
                    state=backend.lookup(request['operation_id'])
                    if state['status']=='definitely_not_started':state=backend.submit(request)
                    entry={'utc':datetime.now(timezone.utc).isoformat(),'name':name,'stage':stage,'status':state['status'],
                           'operation_id':request['operation_id']}
                    with (d/(generation+'-progress.jsonl')).open('a',encoding='utf-8') as f:f.write(canonical_json(entry)+'\n')
                    print(canonical_json(entry),flush=True)
                    if state['status']=='completed':break
                    time.sleep(15)
                receipt=state['receipt'];receipts[stage]=receipt
                if negative and stage=='correctness' and receipt['status']=='succeeded':
                    result=record_oracle_gate_failure(s,job,receipts)
                    (d/(name+'-result.json')).write_text(canonical_json(result),encoding='utf-8')
                    print(canonical_json(result),flush=True)
                    raise RuntimeError('Known-wrong Candidate passed; persisted Oracle Gate failure')
                if receipt['status']!='succeeded' or stage==stages[-1]:
                    result=integrate_result(s,job,receipts)
                    (d/(name+'-result.json')).write_text(canonical_json(result),encoding='utf-8')
                    print(canonical_json({'name':name,'result':result}),flush=True)
                    if negative and result['classification']!='incorrect':raise RuntimeError('Negative not rejected by Oracle')
                    if not negative and not result['confirmed']:raise RuntimeError('Baseline Gate not passed')
                    break


if __name__ == '__main__':
    advance(Path(__file__).resolve().parent, 'runtime3' if '--runtime3' in sys.argv else 'runtime2')
