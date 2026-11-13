"""Resume already accepted ABBA jobs without re-publication or re-reservation."""
import json
import sys
import time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.workflow.controller import Controller
from rsi_kernel_agent.workflow.colab import ColabBackend
from rsi_kernel_agent.workflow.__main__ import colab_remote
from rsi_kernel_agent.profiler.session import _write
from real_round_aux import LIMITS, read_json
from prepare_confirmation_round import target_for
from p35_publish_confirmation import require_frozen_policy

d = Path(__file__).resolve().parent
s = read_json(d / 'p35-confirmation-selection.json')
t = target_for(d, s)
store = ExperimentStore(d / 'experiments.sqlite3', d / 'artifacts')
require_frozen_policy(store, d, s)
assert read_json(t / 'selection.json') == s
assert read_json(t / 'runtime-binding.json') == {'generation': 'runtime3'}
execution = read_json(t / 'execution-input.json')
assert execution['selection'] == s
intake = read_json(t / 'intake.json')
accepted = {j['id']: j for item in intake['items'] for j in item['jobs']}
assert len(accepted) == 4 and all(item['status'] == 'accepted' for item in intake['items'])
controller = Controller(store, s['task_id'], ColabBackend(store, colab_remote(d / 'colab-config-runtime3.json')), limits=LIMITS)
for index in range(240):
    state = controller.status()
    jobs = [j for j in state['jobs'].values() if j['round_id'] == s['round_id']]
    assert {j['id'] for j in jobs} == set(accepted)
    assert all(j['state'] in ('completed', 'failed', 'cancelled') or j['round_id'] == s['round_id'] for j in state['jobs'].values())
    assert all(all(j[k] == v for k, v in accepted[j['id']].items()) for j in jobs)
    _write(t / 'controller-status.json', state)
    print(json.dumps({'index': index, 'revision': state['revision'], 'spent': state['spent'],
                      'jobs': [(j['variant']['label'], j['state'], j['stage']) for j in jobs]}), flush=True)
    if jobs and all(j['state'] in ('completed', 'failed', 'cancelled') for j in jobs):
        break
    controller.step()
    time.sleep(15)
