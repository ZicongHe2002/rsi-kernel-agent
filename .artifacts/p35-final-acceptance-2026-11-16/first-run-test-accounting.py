"""Correct the first run's subtest accounting without rewriting its artifacts."""
import json
from pathlib import Path
import re
OUT = Path(__file__).resolve().parent
FIRST = OUT / 'regression-attempt-1'
report = {}
for kind in ('root', 'auxiliary'):
    result = json.loads((FIRST / (kind + '-tests.result.json')).read_text('utf-8'))
    output = (FIRST / (kind + '-tests.stderr.txt')).read_text('utf-8')
    body = re.split(r'^={70}\s*$', output, maxsplit=1, flags=re.MULTILINE)[0]
    passed = len(re.findall(r'(?:^ok$|^\w+ \([^\n]+\) \.\.\. ok$)', body, re.MULTILINE))
    failures = set(re.findall(r'^FAIL: \w+ \(([^)]+)\)', output, re.MULTILINE))
    errors = set(re.findall(r'^ERROR: \w+ \(([^)]+)\)', output, re.MULTILINE))
    assert passed + len(failures | errors) + result['skipped'] == result['total']
    report[kind] = {'total': result['total'], 'passed': passed, 'skipped': result['skipped'],
                    'failed_test_methods': len(failures), 'error_test_methods': len(errors),
                    'failure_entries': result['failures'], 'error_entries': result['errors'],
                    'exit_code': result['exit_code'], 'original_wrapper_passed': result['passed'],
                    'original_files_unchanged': True}
assert report['root']['passed'] == 340 and report['auxiliary']['passed'] == 353
(OUT / 'first-run-corrected-accounting.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report))
