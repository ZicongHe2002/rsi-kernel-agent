"""Advance original Controller for exactly four explicitly ordered replicas."""
import argparse
import asyncio
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from rsi_kernel_agent.contracts import canonical_json
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.knowledge_mcp import StdioKnowledge
from rsi_kernel_agent.profiler.outputs import publish_result,require
from rsi_kernel_agent.profiler.session import _write
from rsi_kernel_agent.workflow.intake import intake_publication
from rsi_kernel_agent.workflow.controller import Controller
from rsi_kernel_agent.workflow.colab import ColabBackend
from rsi_kernel_agent.workflow.__main__ import colab_remote
from rsi_kernel_agent.workflow.scheduling import choose_jobs
from real_round_aux import LIMITS,read_json,write_once,completed_session,load_prepared,runtime_binding,runtime_config_path
from prepare_confirmation_round import validate_selection,target_for,prerequisites

def preview_order(state,jobs,s):
    temporary=deepcopy(state)
    require(not any(j['state'] not in ('completed','failed','cancelled') for j in temporary['jobs'].values()),'Other work prevents ABBA preview')
    temporary['jobs'].update({j['id']:deepcopy(j) for j in jobs})
    order=[]
    for row in s['sequence']:
        chosen=choose_jobs(temporary)
        require(len(chosen)==1,'ABBA blocked by budget/pause/concurrency')
        j=temporary['jobs'][chosen[0]]
        require(j['variant']['label']==row['label'] and j['variant']['priority']==row['priority'],'Scheduler order differs from explicit ABBA')
        order.append(j['id']);j['state']='completed'
    return order

async def run(selection_path,variants_path,*,steps=1,interval=15,directory=None):
    require(type(steps) is int and steps>0 and type(interval) is int and 1<=interval<=60,'Invalid polling limits')
    d=Path(directory) if directory is not None else Path(__file__).resolve().parent
    s=validate_selection(read_json(selection_path));target=target_for(d,s)
    require(read_json(target/'selection.json')==s,'Prepared selection changed')
    store=ExperimentStore(d/'experiments.sqlite3',d/'artifacts')
    from p35_publish_confirmation import require_frozen_policy
    require_frozen_policy(store,d,s)
    state,origins=prerequisites(store,d,s)
    prepared=load_prepared(store,s['task_id'],s['context_id'],[s['strategy_id']],s['round_id'])
    require(prepared is not None,'Missing confirmation preparation')
    result=completed_session(store,target,prepared['manifest']);variants=read_json(variants_path)
    plans={p['id']:p for p in result['plan_set']['plans']}
    require(set(plans)==set(variants)==set(s['plan_roles'].values()),'Need exactly two confirmation Plans')
    for role,pid in s['plan_roles'].items():
        rows=[r for r in s['sequence'] if r['role']==role]
        require(plans[pid]['strategy_id']==s['strategy_id'] and plans[pid]['variants']==[r['label'] for r in rows] and len(variants[pid])==2,'Replica Plan differs')
        for v,row in zip(variants[pid],rows):
            expected=deepcopy(origins[role]['variant'])
            expected.update(label=row['label'],priority=row['priority'],cost={'executions':1,'tokens':0,'time_s':1800.0})
            require(v==expected,'Replica changed original artifacts or configuration')
    key=s['round_id']+':'+prepared['manifest'].artifact_ref.digest
    write_once(target/'execution-input.json',{'selection':s,'variants':variants,'publication_key':key,
        'result_digest':sha256(canonical_json(result).encode()).hexdigest()})
    marker=publish_result(store,prepared['manifest'],result,key)
    async with StdioKnowledge(ROOT/'knowledge-site') as knowledge:
        intake=await intake_publication(store,marker.id,s['round_id'],variants,knowledge)
    require(all(i['status']=='accepted' for i in intake['items']),'Confirmation intake rejected/waiting')
    write_once(target/'intake.json',intake)
    jobs=[j for item in intake['items'] for j in item['jobs']]
    if not any(j['round_id']==s['round_id'] for j in state['jobs'].values()):
        preview_jobs=[dict(j,state='queued',reserved=False) for j in jobs]
        preview_order(state,preview_jobs,s)
    controller=Controller(store,s['task_id'],ColabBackend(store,colab_remote(runtime_config_path(d,runtime_binding(d,target)))),limits=LIMITS)
    controller.accept_intake(intake)
    for index in range(steps):
        state=controller.status();jobs=[j for j in state['jobs'].values() if j['round_id']==s['round_id']]
        if not all(j['state'] in ('completed','failed','cancelled') for j in jobs): controller.step()
        state=controller.status();jobs=[j for j in state['jobs'].values() if j['round_id']==s['round_id']]
        _write(target/'controller-status.json',state)
        print(canonical_json({'revision':state['revision'],'jobs':[{'label':j['variant']['label'],'state':j['state'],'stage':j['stage']} for j in jobs]}),flush=True)
        if all(j['state'] in ('completed','failed','cancelled') for j in jobs):
            snapshot=store.create_snapshot(s['task_id'],s['round_id']+':completed-snapshot')
            write_once(target/'completed.json',{'round_id':s['round_id'],'snapshot':snapshot,'order':[r['label'] for r in s['sequence']],
                'results':{j['id']:j['result'] for j in jobs}})
            break
        # Reconcile a persisted operation through the original Controller within
        # this invocation's step budget; an operationless block needs intervention.
        if any(j['state']=='blocked' and not j.get('operation') for j in jobs) or not any(
                j['state'] in ('active','evaluating','queued') or
                j['state']=='blocked' and j.get('operation') for j in jobs): break
        if index+1<steps: time.sleep(interval)
    return {'directory':str(target),'state':state['workflow_state']}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('selection',type=Path);p.add_argument('variants',type=Path)
    p.add_argument('--steps',type=int,default=1);p.add_argument('--interval',type=int,default=15);a=p.parse_args()
    print(canonical_json(asyncio.run(run(a.selection,a.variants,steps=a.steps,interval=a.interval))))
