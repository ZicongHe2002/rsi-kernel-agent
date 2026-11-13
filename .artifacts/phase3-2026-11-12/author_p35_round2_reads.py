"""Submit this conversation's explicit requests to the original Round 2 session."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.contracts import canonical_json
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.profiler.session import submit_response
from real_round_aux import read_json, write_once

d = Path(__file__).resolve().parent
t = d / 'real-round-2'
p = read_json(t / 'packet.json')
calls = []
for label, sid in (('A', 'mla-strategy:deee0989771642ee8e066be07adb0370a6c7a3bca6be04ebc8ab50e145c5b05e'),
                   ('B', 'mla-strategy:ad3416851dfcc72142373541d6dc1f47121b390cce460b7fa479e9436b97e3cd')):
    row = p['manifest']['strategies'][sid]
    calls += [{'id': label + '-' + name, 'name': name, 'arguments': {'strategy_id': sid}}
              for name in ('source', 'history')]
    for i, profile in enumerate(row['profiles']):
        calls.append({'id': label + '-profile-' + str(i), 'name': 'profile',
                      'arguments': {'strategy_id': sid, 'profile_id': profile['id']}})
for mid in ('hist-decode-dma2', 'hist-decode-prepare-padding', 'hist-fwd-state-lifetime',
            'hist-profile-accounting', 'official-jax-synchronized-benchmark'):
    calls.append({'id': 'read-' + mid, 'name': 'knowledge', 'arguments': {'method_id': mid}})
response = {'tool_calls': calls}
path = t / 'response-1-read-inputs.json'
write_once(path, response)
state = read_json(t / 'session.json')
if response not in state['responses']:
    submit_response(ExperimentStore(d / 'experiments.sqlite3', d / 'artifacts'), t, path)
p = read_json(t / 'packet.json')
print(canonical_json({'tool_results': [{'id': r['id'], 'name': r['name'], 'error': r.get('error')}
                                     for r in p['tool_results']], 'remaining': p['remaining']}))
