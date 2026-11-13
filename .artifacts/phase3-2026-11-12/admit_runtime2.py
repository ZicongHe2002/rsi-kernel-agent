"""New observed runtime means new Context and Runs; old evidence is immutable."""
from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from rsi_kernel_agent.contracts import (Candidate, Decision, EvaluationContext, ParentCandidate,
    Plan, Run, RunEvent, Strategy, canonical_json)
from rsi_kernel_agent.associations import MemoryWriter
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.workflow.execution import execution_contract, make_request, stable_id
from rsi_kernel_agent.workflow.mla_baseline import _observed_v6e
from rsi_kernel_agent.workflow.__main__ import colab_remote


def write_immutable_json(path, value):
    """Publish once without overwriting an existing recovery input."""
    path = Path(path)
    raw = canonical_json(value).encode('utf-8')
    if path.is_symlink():
        raise ValueError('Recovery input must not be a link')
    if path.exists():
        if canonical_json(json.loads(path.read_bytes())).encode('utf-8') != raw:
            raise ValueError('Existing recovery input differs: ' + str(path))
        return
    handle, name = tempfile.mkstemp(prefix='.runtime2-', dir=path.parent)
    try:
        with os.fdopen(handle, 'wb') as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(name, path)
        except FileExistsError:
            write_immutable_json(path, value)
    finally:
        Path(name).unlink(missing_ok=True)


def prepare_config(path, old_config, environment):
    runtime_id = environment['runtime_session_id']
    if not isinstance(runtime_id, str) or not runtime_id or runtime_id == old_config['runtime_id']:
        raise ValueError('Expected a distinct observed runtime')
    path = Path(path)
    remote_root = '/content/rsi-kernel-agent/phase3-20261112-runtime2'
    if path.exists():
        config = json.loads(path.read_bytes())
        if config.get('runtime_id') != runtime_id or config.get('remote_root') != remote_root:
            raise ValueError('Existing runtime configuration differs from observed runtime or spool')
        if not config.get('nonce') or config['nonce'] == old_config['nonce']:
            raise ValueError('New runtime requires its own persistent nonce')
        for key in ('python', 'session', 'cache'):
            if not isinstance(config.get(key), str) or not config[key].strip():
                raise ValueError('Invalid runtime configuration: ' + key)
    else:
        config = {**old_config, 'runtime_id': runtime_id, 'nonce': uuid.uuid4().hex,
                  'remote_root': remote_root}
    write_immutable_json(path, config)
    return config


d=Path(__file__).resolve().parent
env=json.loads((ROOT/'knowledge-site/manifests/phase3-2026-11-12/environment-isolated-runtime2.json').read_text('utf-8'))
_observed_v6e(env)
old_config=json.loads((d/'colab-config.json').read_text('utf-8'))
config_path=d/'colab-config-runtime2.json'
config=prepare_config(config_path,old_config,env)
write_immutable_json(d/'runtime2-admitted-environment.json',env)
s=ExperimentStore(d/'experiments.sqlite3',d/'artifacts')
base=json.loads((d/'baseline-repair-job.json').read_text('utf-8'))
jobs=[]
for name in ('baseline-repair','oracle-negative-attention','oracle-negative-cache'):
    old=json.loads((d/(name+'-job.json')).read_text('utf-8'))
    old_context=s.get(old['context_id'])
    context=EvaluationContext(stable_id('runtime-context',old_context.id,env),old['task_id'],
        old_context.workload_id,env,deepcopy(old_context.protocol),'real')
    s.append(context,context.id)
    run=Run(stable_id('runtime-run',old['candidate_id'],context.id),old['candidate_id'],context.id)
    s.append(run,run.id)
    contract,digest=execution_contract(s,context.id)
    job=deepcopy(old)
    job.update(id=stable_id('runtime-job',run.id),run_id=run.id,context_id=context.id,
               round_id=stable_id('runtime-validation',context.id),contract=contract,contract_digest=digest,
               parent_evaluation_id=None,stage='generate',state='queued',receipts={},result=None,reserved=False,
               reason='Fresh runtime validation; prior completed measurements remain in their original Context')
    job['variant']['device']=env['runtime_session_id']
    make_request(s,job,'generate')
    path=d/(name+'-runtime2-job.json')
    write_immutable_json(path,job)
    note=Decision(stable_id('runtime-readmission',run.id),job['task_id'],'note',run.id,
        'Distinct observed runtime; new Run and Context, no re-labelling of prior evidence',
        {'type':'runtime_readmission.v1','prior_run_id':old['run_id'],'prior_context_id':old_context.id,
         'new_context_id':context.id,'completed_results_preserved':True})
    s.append(note,note.id)
    jobs.append(job)
newbase=jobs[0]
strategy=Strategy(stable_id('mla-strategy',base['task_id'],'data-movement'),base['task_id'],
                  'MLA contiguous data movement and reuse exploration')
s.append(strategy,strategy.id)
parent_candidate=s.get(base['candidate_id'])
parent=ParentCandidate(stable_id('strategy-seed-parent',strategy.id,parent_candidate.id),base['task_id'],
                       parent_candidate.identity,parent_candidate.id)
s.append(parent,parent.id)
plan=Plan(stable_id('strategy-seed-plan',strategy.id,newbase['context_id']),strategy.id,
          'Independently measure repaired baseline bytes for this strategy before optimization')
s.append(plan,plan.id)
MemoryWriter(s).bind_plan(plan.id,parent.id,plan.id+':bind')
candidate=Candidate(stable_id('strategy-seed-candidate',plan.id),plan.id,parent.id,parent_candidate.identity)
s.append(candidate,candidate.id)
run=Run(stable_id('strategy-seed-run',candidate.id,newbase['context_id']),candidate.id,newbase['context_id'])
s.append(run,run.id)
job=deepcopy(newbase)
job.update(id=stable_id('strategy-seed-job',run.id),strategy_id=strategy.id,plan_id=plan.id,
    parent_id=parent.id,candidate_id=candidate.id,run_id=run.id,reason='Independent strategy baseline measurement')
job.pop('repair_of',None)
job['repair_depth']=0
make_request(s,job,'generate')
write_immutable_json(d/'baseline-b-runtime2-job.json',job)
source=Decision(stable_id('strategy-seed-source',candidate.id),base['task_id'],'note',candidate.id,
    'Immutable identical baseline bytes, independently evaluated for strategy B',
    {'type':'workflow_candidate.v1',**{k:job['variant'][k] for k in
    ('source_ref','source_bundle_ref','config_ref','build_ref')},'job_id':job['id'],'provenance':'real'})
s.append(source,source.id)
jobs.append(job)
print(canonical_json({'jobs':[{k:j[k] for k in ('id','run_id','context_id','strategy_id')} for j in jobs]}))
if '--bootstrap' in sys.argv:
    print(canonical_json(colab_remote(config_path).bootstrap()))
