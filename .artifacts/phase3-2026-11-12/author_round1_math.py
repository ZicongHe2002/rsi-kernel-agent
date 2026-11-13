"""Serialize this conversation's explicit mathematical requests, then submit once."""
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.contracts import canonical_json
from rsi_kernel_agent.profiler.session import submit_response
from real_round_aux import write_once
d = Path(__file__).resolve().parent
target = d / 'real-round-1'
p = json.loads((target / 'packet.json').read_text('utf-8'))
A = 'mla-strategy:deee0989771642ee8e066be07adb0370a6c7a3bca6be04ebc8ab50e145c5b05e'
B = 'mla-strategy:ad3416851dfcc72142373541d6dc1f47121b390cce460b7fa479e9436b97e3cd'
calls = []
def calc(cid, sid, model, inputs, assumptions):
    calls.append({'id':cid,'name':'calculate','arguments':{'model':model,'inputs':inputs,
        'evidence_refs':['source:'+sid], 'assumptions':assumptions}})
calc('A-small-QK',A,'compute_memory',{'m':16,'n':1024,'k':640,'dtype_bytes':2},
     ['Small case only; padded feature dimension 640. BF16 operand approximation; actual accumulation is FP32.',
      'No hardware rate supplied; not a runtime prediction or complete MLA traffic model.'])
calc('A-small-PV',A,'compute_memory',{'m':16,'n':512,'k':1024,'dtype_bytes':2},
     ['Small case only; P cast to BF16, actual accumulator FP32; softmax and complete cache copy excluded.'])
for tile in (1024,512,256):
    calc('A-QK-tile-'+str(tile),A,'tile',{'m':16,'n':1024,'k':640,'tile_m':16,'tile_n':tile,'tile_k':640,'dtype_bytes':2},
         ['Idealized QK tiling only; smaller tokens tile repeats query access and online softmax, without assuming actual HBM reload.',
          'Boundary and default workloads retain their original 128 and 1024 block sizes.'])
for name,elements in (('broadcast',1024*640),('compact',640)):
    calc('B-new-row-'+name,B,'live_memory',{'buffers':[{'name':'logical_prepared_new','elements':elements,'dtype_bytes':2,
         'copies':1,'start':0,'end':2}]},
         ['Logical tensor payload of small-case prepared new KV only, not physical allocation or measured VMEM.',
          'Compiler broadcast folding, layouts, hidden temporaries and unchanged length broadcast excluded.'])
calc('B-single-item-pipeline-limit',B,'pipeline',{'stages':1,'transfer_s':1e-6,'compute_s':5e-6},
     ['Illustrative sensitivity inputs, neither transfer nor compute is a measured separate duration.',
      'One small-case tile has no cross-item pipelining gain in this model; delayed store wait instead tests same-tile overlap and is not modeled here.'])
path = target / 'response-2-math.json'
write_once(path, {'tool_calls':calls})
store = ExperimentStore(d/'experiments.sqlite3',d/'artifacts')
state=json.loads((target/'session.json').read_text('utf-8'))
response=json.loads(path.read_text('utf-8'))
if response not in state['responses']:
    submit_response(store,target,path)
p=json.loads((target/'packet.json').read_text('utf-8'))
for c in p['tool_results']:
    print(canonical_json({'id':c['id'],'error':c.get('error'),'outputs':c.get('result',{}).get('outputs')}))
print(canonical_json({'remaining':p['remaining']}))
