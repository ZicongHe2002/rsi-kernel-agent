"""Distinguish executed unittest entries from distinct fully qualified IDs."""
from collections import Counter
import json
from pathlib import Path
import re

OUT = Path(__file__).resolve().parent
report = {}
for kind in ('root', 'auxiliary'):
    result = json.loads((OUT / (kind + '-tests.result.json')).read_text('utf-8'))
    log = (OUT / (kind + '-tests.stderr.txt')).read_text('utf-8')
    ids = re.findall(r'^\w+ \(([^\n)]+)\) \.\.\. ', log, re.MULTILINE)
    counter = Counter(ids)
    assert len(ids) == result['total'], (kind, len(ids), result['total'])
    report[kind] = {'executed_entries': len(ids), 'distinct_test_ids': len(counter),
                    'duplicate_extra_executions': len(ids) - len(counter),
                    'duplicates': {k: v for k, v in counter.items() if v > 1},
                    'exit_code': result['exit_code']}
(OUT / 'test-discovery-accounting.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps({k: {p: v for p, v in row.items() if p != 'duplicates'} for k, row in report.items()}))
