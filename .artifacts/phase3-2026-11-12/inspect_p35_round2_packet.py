"""Print bounded, relevant views of the original large frozen session packet."""
from pathlib import Path
import json
import sys
d = Path(__file__).resolve().parent
p = json.loads((d / 'real-round-2/packet.json').read_text('utf-8'))
if 'manifest' not in p:
    raise SystemExit('Packet is complete; inspect saved result instead')
m = p['manifest']
print('ROUTES')
for sid, row in m['strategies'].items():
    print(json.dumps({'strategy': sid, 'parent': row['parent'], 'run': row['run'],
                      'benchmarks': row['benchmarks'], 'profile_ids': [v['id'] for v in row['profiles']],
                      'history': {k: row['history'].get(k) for k in ('complete','total','owned_total','dependency_total','snapshot')}}, ensure_ascii=False))
    source = row['source']
    print('SOURCE_SHAPE', type(source).__name__, list(source) if isinstance(source, dict) else '')
    if isinstance(source, str) and 'def build_mla_decode_contiguous(' in source:
        print(source[source.index('def build_mla_decode_contiguous('):])
    elif isinstance(source, dict):
        for k, v in source.items():
            if isinstance(v, str) and 'def build_mla_decode_contiguous(' in v:
                print(v[v.index('def build_mla_decode_contiguous('):])
for mid, row in m['knowledge'].items():
    if mid != 'official-tpu-profile-mode-version-gates':
        print('KNOWLEDGE', mid, json.dumps(row['method'], ensure_ascii=False))
print('TOOLS')
for row in p.get('tool_results', []):
    if row['name'] == 'profile':
        result = row['result']
        print('PROFILE', row['id'], json.dumps(result, ensure_ascii=False))
print('REMAINING', json.dumps(p.get('remaining')))
