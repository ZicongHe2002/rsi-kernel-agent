"""Explicit diagnostic source/history/Knowledge requests from this conversation."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.contracts import canonical_json
from rsi_kernel_agent.profiler.session import submit_response
from p35_prepare_diagnostic_round import target_for
from real_round_aux import read_json,write_once
d=Path(__file__).resolve().parent
s=read_json(d/'p35-single-tile-selection-input.json')
t=target_for(d,s)
p=read_json(t/'packet.json')
sid=s['strategy_id']
calls=[{'id':'diagnostic-'+name,'name':name,'arguments':{'strategy_id':sid}} for name in ('source','history')]
calls += [{'id':'diagnostic-'+mid,'name':'knowledge','arguments':{'method_id':mid}}
          for mid in ('hist-decode-prepare-padding','hist-fwd-state-lifetime','official-jax-synchronized-benchmark')]
response={'tool_calls':calls}
path=t/'response-1-read-inputs.json'
write_once(path,response)
if response not in read_json(t/'session.json')['responses']:
    submit_response(ExperimentStore(d/'experiments.sqlite3',d/'artifacts'),t,path)
p=read_json(t/'packet.json')
print(canonical_json({'results':[{'id':r['id'],'error':r.get('error')} for r in p['tool_results']], 'remaining':p['remaining']}))
