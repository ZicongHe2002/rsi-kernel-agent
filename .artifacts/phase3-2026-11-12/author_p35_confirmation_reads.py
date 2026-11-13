"""Current conversation's explicit input requests to the original Analyzer."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.profiler.session import submit_response
from real_round_aux import read_json, write_once
from prepare_confirmation_round import target_for

d = Path(__file__).resolve().parent
selection = read_json(d / 'p35-confirmation-selection.json')
target = target_for(d, selection)
packet = read_json(target / 'packet.json')
sid = selection['strategy_id']
strategy = packet['manifest']['strategies'][sid]
calls = [
    {'id': 'confirmation-source', 'name': 'source', 'arguments': {'strategy_id': sid}},
    {'id': 'confirmation-history', 'name': 'history', 'arguments': {'strategy_id': sid}},
]
for profile in strategy['profiles']:
    calls.append({'id': 'confirmation-profile', 'name': 'profile',
                  'arguments': {'strategy_id': sid, 'profile_id': profile['id']}})
for mid in ('hist-decode-dma2', 'hist-profile-accounting', 'official-jax-synchronized-benchmark'):
    calls.append({'id': 'read-' + mid, 'name': 'knowledge', 'arguments': {'method_id': mid}})
response = {'tool_calls': calls}
path = target / 'response-1-read-inputs.json'
write_once(path, response)
state = read_json(target / 'session.json')
if response not in state['responses']:
    submit_response(ExperimentStore(d / 'experiments.sqlite3', d / 'artifacts'), target, path)
print(str(target / 'packet.json'))
