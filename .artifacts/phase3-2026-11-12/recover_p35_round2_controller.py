"""Bounded original Controller reconciliation for exactly four accepted R2 jobs.

Run only after the official run_real_round driver has exited. This helper does
not publish, intake, retry, repair, allocate a Runtime, or reset any budget.
The original driver is used again for the completion snapshot after all jobs
terminate. Stage submission remains solely the original Controller's decision.
"""
from pathlib import Path
import json
import sys
import time
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.workflow.controller import Controller
from rsi_kernel_agent.workflow.colab import ColabBackend
from rsi_kernel_agent.workflow.__main__ import colab_remote
from rsi_kernel_agent.workflow.scheduling import validate_limits
from rsi_kernel_agent.profiler.session import _write
from real_round_aux import LIMITS, read_json
from p35_run_diagnostic_round import driver_lock

d = Path(__file__).resolve().parent
t = d / 'real-round-2'
round_id = 'real-runtime2-round:2'
task_id = 'mla-decode-v1'
assert read_json(t / 'runtime-binding.json') == {'generation':'runtime3'}
inputs = read_json(t / 'execution-input.json')
variants = read_json(t / 'variants.json')
assert inputs['round_id'] == round_id and inputs['variants'] == variants
assert set(variants)=={'R2-A-length-row','R2-A-length-smem','R2-B-query','R2-B-b1-fusion'}
assert all(len(v)==1 for v in variants.values())
intake = read_json(t / 'intake.json')
assert intake['round_id'] == round_id
assert len(intake['items'])==4 and all(item['status']=='accepted' for item in intake['items'])
expected = {job['id']:job for item in intake['items'] for job in item['jobs']}
assert len(expected)==4 and {j['variant']['label'] for j in expected.values()}=={
    'length-row','length-smem','query-in-attention','b1-fused-prepare'}
prior = read_json(d / 'confirmation-f686274a57bb7ebb/controller-status.json')
assert prior['spent']=={'executions':9,'tokens':0,'time_s':16200.0} and len(prior['jobs'])==9
store = ExperimentStore(d / 'experiments.sqlite3', d / 'artifacts')
with driver_lock(store, task_id):
    controller = Controller(store,task_id,ColabBackend(store,colab_remote(d / 'colab-config-runtime3.json')),limits=LIMITS)
    for index in range(240):
        state=controller.status()
        assert state['limits']==validate_limits(LIMITS) and not state['cancelled']
        assert {key:job for key,job in state['jobs'].items() if job['round_id']!=round_id}==prior['jobs']
        jobs=[job for job in state['jobs'].values() if job['round_id']==round_id]
        assert {job['id'] for job in jobs}==set(expected)
        for job in jobs:
            assert all(job[key]==value for key,value in expected[job['id']].items())
            assert job['attempt']==0 and job['repair_depth']==0 and not job.get('retry_of') and not job.get('repair_of')
        assert state['spent']=={key:prior['spent'][key]+sum(job['variant']['cost'][key] for job in jobs if job['reserved'])
                                for key in ('executions','tokens','time_s')}
        _write(t / 'controller-status.json',state)
        print(json.dumps({'index':index,'revision':state['revision'],'spent':state['spent'],
                          'jobs':[(job['variant']['label'],job['state'],job['stage']) for job in jobs]}),flush=True)
        if all(job['state'] in ('completed','failed','cancelled') for job in jobs):
            break
        if any(job['state']=='blocked' and not job.get('operation') for job in jobs):
            break
        controller.step()
        time.sleep(15)
