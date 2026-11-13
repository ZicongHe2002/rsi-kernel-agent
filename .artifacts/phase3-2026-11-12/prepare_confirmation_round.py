"""Prepare explicit ABBA confirmation using existing session and frozen artifacts."""
import argparse
import asyncio
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from rsi_kernel_agent.contracts import Candidate,ParentCandidate,canonical_json
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.knowledge_mcp import StdioKnowledge
from rsi_kernel_agent.profiler.outputs import require
from rsi_kernel_agent.profiler.session import start_session
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from rsi_kernel_agent.workflow.rounds import prepare_round
from rsi_kernel_agent.workflow.scheduling import validate_limits
from real_round_aux import (LIMITS,read_json,write_once,require_gates,verified_gate_result,
    completed_round,load_prepared,validate_session,require_new_history,runtime_binding)

def validate_selection(s):
    require(set(s)==set('schema_version task_id context_id strategy_id candidate_id baseline_candidate_id round_id previous_round_ids knowledge plan_roles sequence'.split()),'Invalid selection fields')
    require(s['schema_version']==1 and all(type(s[k]) is str and s[k] for k in ('task_id','context_id','strategy_id','candidate_id','baseline_candidate_id','round_id')),'Invalid selection identity')
    require(s['candidate_id']!=s['baseline_candidate_id'],'Need independent baseline and candidate identities')
    require(s['previous_round_ids'] in (
        ['real-runtime2-round:1'],
        ['real-runtime2-round:1', 'real-runtime2-round:2'],
    ), 'Need round1, optionally followed by original round2')
    require(type(s['knowledge']) is list and set(s['plan_roles'])=={'baseline','candidate'} and len(set(s['plan_roles'].values()))==2,'Invalid knowledge or Plan roles')
    rows=s['sequence']
    require(type(rows) is list and len(rows)==4 and [r['role'] for r in rows]==['baseline','candidate','candidate','baseline'],'Need explicit ABBA order')
    require(all(set(r)=={'role','label','priority'} and type(r['label']) is str and r['label'] and type(r['priority']) is int for r in rows),'Invalid replicate')
    require(len({r['label'] for r in rows})==4 and all(rows[i]['priority']>rows[i+1]['priority'] for i in range(3)),'Replicate labels/priorities must be unique and descending')
    return s

def target_for(directory,s):
    return Path(directory)/('confirmation-'+sha256(s['round_id'].encode()).hexdigest()[:16])

def prerequisites(store,directory,s):
    gates,_=require_gates(store,directory,runtime_binding(directory,target_for(directory,s)))
    require(gates['baseline-repair']['task_id']==s['task_id'] and gates['baseline-repair']['context_id']==s['context_id'],'Gate context differs')
    for rid in s['previous_round_ids']: completed_round(store,s['task_id'],rid)
    state=WorkflowJournal(store,s['task_id']).load()
    require(state and state['limits']==validate_limits(LIMITS) and not state['cancelled'],'Missing original budget or cancelled task')
    other=[j for j in state['jobs'].values() if j['round_id']!=s['round_id']]
    require(all(j['state'] in ('completed','failed','cancelled') for j in other),'Other work unfinished')
    existing=[j for j in state['jobs'].values() if j['round_id']==s['round_id']]
    if not existing:
        require(state['limits']['executions']-state['spent']['executions']>=4 and state['limits']['time_s']-state['spent']['time_s']>=7200,'Insufficient confirmation budget')
    origins={}
    for role,cid in [('candidate',s['candidate_id']),('baseline',s['baseline_candidate_id'])]:
        jobs=[j for j in other+list(gates.values()) if j['candidate_id']==cid and j['context_id']==s['context_id'] and j['strategy_id']==s['strategy_id']]
        require(bool(jobs),'Candidate has no completed same-strategy/context origin')
        job=jobs[-1];result=verified_gate_result(store,job)
        require(result['confirmed'] and result['correctness']=='passed','Origin lacks confirmed correctness')
        origins[role]=job
    return state,origins

async def prepare(selection_path,*,directory=None):
    d=Path(directory) if directory is not None else Path(__file__).resolve().parent
    s=validate_selection(read_json(selection_path));target=target_for(d,s)
    store=ExperimentStore(d/'experiments.sqlite3',d/'artifacts')
    prerequisites(store,d,s)
    if (target/'selection.json').exists():
        require(read_json(target/'selection.json')==s,'Prepared selection differs before policy freeze')
    from p35_publish_confirmation import freeze_policy
    freeze_policy(store,d,s)
    target.mkdir(exist_ok=True);write_once(target/'selection.json',s)
    write_once(target/'runtime-binding.json',runtime_binding(d,target))
    prepared=load_prepared(store,s['task_id'],s['context_id'],[s['strategy_id']],s['round_id'])
    if prepared is None:
        candidate=store.get(s['candidate_id']);require(type(candidate) is Candidate,'Wrong selected record type')
        parent=ParentCandidate('confirm-parent:'+sha256(canonical_json(s).encode()).hexdigest(),s['task_id'],candidate.identity,candidate.id)
        store.append(parent,parent.id)
        async with StdioKnowledge(ROOT/'knowledge-site') as knowledge:
            prepared=await prepare_round(store,knowledge,s['task_id'],s['context_id'],[s['strategy_id']],s['round_id'],s['knowledge'],
                parents={s['strategy_id']:parent.id},parent_policy='explicit')
    for rid in s['previous_round_ids']:
        # Prior rounds may contain other strategies. This snapshot pins this
        # participating strategy's full history; check its own attempts below.
        jobs=completed_round(store,s['task_id'],rid)
        ids={r['payload']['id'] for h in prepared['histories'].values() for r in h['records']}
        require(all(j['run_id'] in ids for j in jobs if j['strategy_id']==s['strategy_id']),'Selected history omits prior attempt')
    write_once(target/'manifest-ref.json',asdict(prepared['manifest'].artifact_ref))
    write_once(target/'prepared.json',{k:v for k,v in prepared.items() if k!='manifest'})
    if (target/'session.json').exists(): validate_session(target,prepared['manifest'])
    else: start_session(store,prepared['manifest'],target,model_label='current conversation model; confirmation only',max_turns=6,max_tool_calls=16)
    return {'directory':str(target),'manifest':asdict(prepared['manifest'].artifact_ref)}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('selection',type=Path)
    print(canonical_json(asyncio.run(prepare(p.parse_args().selection))))
