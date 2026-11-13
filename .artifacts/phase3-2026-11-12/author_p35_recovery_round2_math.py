"""Explicit logical-payload calculations for the current Round 2 conversation."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.contracts import canonical_json
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.profiler.session import submit_response
from real_round_aux import read_json, write_once

d = Path(__file__).resolve().parent
t = d / 'real-round-2-runtime4'
p = read_json(t / 'packet.json')
A = 'mla-strategy:deee0989771642ee8e066be07adb0370a6c7a3bca6be04ebc8ab50e145c5b05e'
B = 'mla-strategy:ad3416851dfcc72142373541d6dc1f47121b390cce460b7fa479e9436b97e3cd'
assert set(p['manifest']['strategies']) == {A, B}
calls = []
def logical(cid, sid, buffers, assumption):
    calls.append({'id': cid, 'name': 'calculate', 'arguments': {
        'model': 'live_memory', 'inputs': {'buffers': [
            {'name': name, 'elements': elements, 'dtype_bytes': width, 'copies': 1, 'start': 0, 'end': 1}
            for name, elements, width in buffers]}, 'evidence_refs': ['source:' + sid],
        'assumptions': ['Small B1/H16/N1024/Dpad640 case only; these are logical interface payloads, not physical VMEM allocation or measured traffic.',
                        'Compiler folding, hidden temporaries, synchronization and layout conversions are excluded. No runtime speedup is predicted.', assumption]}})
logical('R2-A-length-expanded', A, [('expanded_lengths', 16*1024, 4)], 'Original prepare materializes an H by T int32 length tensor.')
logical('R2-A-length-row', A, [('one_length_row', 1024, 4)], 'One row preserves the token axis and broadcasts across heads within attention.')
logical('R2-A-length-scalar', A, [('raw_lengths', 1, 4)], 'Raw B1 length payload only; scalar memory placement and vector broadcast legality need real compilation.')
logical('R2-B-query-prepared', B, [('padded_query', 16*640, 2)], 'Padded query intermediate size; its removal still requires raw query input and padding arithmetic.')
logical('R2-B-query-raw', B, [('raw_query', 16*576, 2)], 'Raw query input size; raw and padded layouts may have different compiler costs.')
logical('R2-B-prepare-interface', B, [('padded_query', 16*640, 2), ('broadcast_new', 1024*640, 2), ('lengths', 16*1024, 4)],
        'If B1 prepare is fused, these separate outputs disappear from the interface; equivalent values may still occupy compiler-managed temporary storage.')
logical('R2-B-raw-interface', B, [('raw_query', 16*576, 2), ('raw_new', 576, 2), ('raw_lengths', 1, 4)],
        'Raw inputs replace prepared inputs only for static batch==1; full cache input/output are unchanged and excluded from this comparison.')
response = {'tool_calls': calls}
path = t / 'response-2-math.json'
write_once(path, response)
if response not in read_json(t / 'session.json')['responses']:
    submit_response(ExperimentStore(d / 'experiments.sqlite3', d / 'artifacts'), t, path)
for row in read_json(t / 'packet.json')['tool_results']:
    if row['name'] == 'calculate':
        print(canonical_json({'id': row['id'], 'error': row.get('error'), 'result': row.get('result')}))
