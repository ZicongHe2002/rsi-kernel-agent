"""Poll an already admitted round through the original Controller only.

No publication, new attempt, retry, allocation, or budget initialization.
Unknown remains blocked; Controller reconciles its original operation on lookup.
"""
import argparse
import json
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.workflow.controller import Controller
from rsi_kernel_agent.workflow.colab import ColabBackend
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from rsi_kernel_agent.workflow.__main__ import colab_remote
from rsi_kernel_agent.profiler.session import _write
from rsi_kernel_agent.profiler.outputs import require
from rsi_kernel_agent.workflow.scheduling import validate_limits
from real_round_aux import LIMITS, runtime_binding, runtime_config_path
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('round',type=int,choices=(1,2));p.add_argument('--steps',type=int,default=100)
a=p.parse_args();require(a.steps>0,'Positive step count required')
d=Path(__file__).resolve().parent;target=d/('real-round-'+str(a.round))
intake=json.loads((target/'intake.json').read_text('utf-8'))
require((d/'experiments.sqlite3').is_file(),'Existing experiment database required')
store=ExperimentStore(d/'experiments.sqlite3',d/'artifacts')
saved=WorkflowJournal(store,intake['task_id']).load()
require(saved is not None and intake['id'] in saved['intakes'],'Existing admitted Controller journal required')
require(saved['limits']==validate_limits(LIMITS),'Original task budget differs')
c=Controller(store,intake['task_id'],ColabBackend(store,colab_remote(runtime_config_path(d,runtime_binding(d,target)))))
rid='real-runtime2-round:'+str(a.round)
for index in range(a.steps):
    state=c.status()
    require(intake['id'] in state['intakes'],'Round was not admitted')
    require(all(j['round_id']==rid or j['state'] in ('completed','failed','cancelled') for j in state['jobs'].values()),'Other round unfinished')
    jobs=[j for j in state['jobs'].values() if j['round_id']==rid]
    pending=[j for j in jobs if j['state'] not in ('completed','failed','cancelled')]
    if not pending or any(j['strategy_id'] in state['paused'] for j in pending): break
    c.step();state=c.status();_write(target/'controller-status.json',state)
    jobs=[j for j in state['jobs'].values() if j['round_id']==rid]
    print(json.dumps({'revision':state['revision'],'jobs':[{'label':j['variant']['label'],'state':j['state'],'stage':j['stage']} for j in jobs]}),flush=True)
    if all(j['state'] in ('completed','failed','cancelled') for j in jobs): break
    if not any(j['state'] in ('queued','active','evaluating','blocked') for j in jobs): break
    if index+1<a.steps: time.sleep(15)
