"""Prepare original-source baseline and advance at most one durable operation."""
from dataclasses import asdict
import json
from pathlib import Path
import sys
import uuid
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.contracts import Decision,RunEvent,canonical_json
from rsi_kernel_agent.workflow.mla_baseline import create_baseline
from rsi_kernel_agent.workflow.execution import make_request,stable_id
from rsi_kernel_agent.workflow.colab import ColabBackend
from rsi_kernel_agent.workflow.__main__ import colab_remote
from rsi_kernel_agent.workflow.evaluation import integrate_result

directory=Path(__file__).resolve().parent
config_path=directory/'colab-config.json'
job_path=directory/('baseline-repair-job.json' if '--repair' in sys.argv else 'baseline-job.json')
intent=json.loads((directory/'allocation-intent.json').read_text('utf-8'))
store=ExperimentStore(directory/'experiments.sqlite3',directory/'artifacts')
if not config_path.exists():
    config={'python':r'C:\Users\fengluo\Desktop\colab\google-colab-cli\.venv\Scripts\python.exe',
            'session':intent['session'],'runtime_id':intent['runtime_id'],'nonce':uuid.uuid4().hex,
            'remote_root':'/content/rsi-kernel-agent/phase3-20261112',
            'remote_python':'/content/rsi-kernel-agent/env-20261112/venv/bin/python',
            'cache':str(directory/'downloads')}
    config_path.write_text(json.dumps(config,indent=2),encoding='utf-8')
remote=colab_remote(config_path)
if '--bootstrap' in sys.argv:
    print(json.dumps(remote.bootstrap()))
    raise SystemExit(0)
if job_path.exists():
    job=json.loads(job_path.read_text('utf-8'))
else:
    env=json.loads((ROOT/'knowledge-site/manifests/phase3-2026-11-12/environment-isolated.json').read_text('utf-8'))
    job=create_baseline(store,env)
    job_path.write_text(canonical_json(job),encoding='utf-8')
backend=ColabBackend(store,remote)
run_id=job['run_id']
if store.run_state(run_id)['execution_status']=='not_run':
    # Fixed baseline validation is a prerequisite, not an Analyzer optimization round.
    reserve=Decision(stable_id('baseline-reservation',run_id),job['task_id'],'note',run_id,
                      'One declared baseline attempt reservation',
                      {'type':'baseline_reservation.v1','cost':job['variant']['cost']})
    store.append(reserve,reserve.id)
    store.transition(RunEvent(run_id+':start',run_id,'running','active','Real unmodified baseline validation'),None,run_id+':start')
receipts={}
for stage in ('generate','compile','correctness','benchmark','profile'):
    request=make_request(store,job,stage)
    result=backend.lookup(request['operation_id'])
    if result['status']=='definitely_not_started':
        result=backend.submit(request)
    if result['status']!='completed':
        print(json.dumps({'stage':stage,'operation_id':request['operation_id'],'status':result['status']}))
        break
    receipts[stage]=result['receipt']
    if result['receipt']['status']!='succeeded' or stage=='profile':
        evaluation=integrate_result(store,job,receipts)
        (directory/('baseline-repair-result.json' if '--repair' in sys.argv else 'baseline-result.json')).write_text(canonical_json(evaluation),encoding='utf-8')
        print(json.dumps({'result':evaluation,'last_outputs':result['receipt']['outputs']},ensure_ascii=False))
        break
else:
    raise AssertionError('Unreachable baseline state')
