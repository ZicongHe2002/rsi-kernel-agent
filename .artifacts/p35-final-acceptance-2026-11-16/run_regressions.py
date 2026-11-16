"""Capture a fresh unchanged unittest command, output, timing and source identity."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
kind = sys.argv[1]
assert kind in ('root', 'auxiliary')
test_dir = 'tests' if kind == 'root' else '.artifacts/phase3-2026-11-12'
paths = sorted(set(ROOT.joinpath('src').rglob('*.py')) | set(ROOT.joinpath('tests').rglob('*.py')) |
               set(ROOT.joinpath('.artifacts/phase3-2026-11-12').glob('*.py')))
def identities():
    return {p.relative_to(ROOT).as_posix(): sha256(p.read_bytes()).hexdigest() for p in paths}

before = identities()
command = [sys.executable, '-B', '-X', 'utf8', '-m', 'unittest', 'discover', '-s', test_dir, '-v']
started = datetime.now(timezone.utc).isoformat()
start = time.perf_counter()
with (OUT / (kind + '-tests.stdout.txt')).open('wb') as stdout, (OUT / (kind + '-tests.stderr.txt')).open('wb') as stderr:
    process = subprocess.Popen(command, cwd=ROOT, stdout=stdout, stderr=stderr)
    (OUT / (kind + '-tests.running.json')).write_text(json.dumps({'started_utc': started, 'pid': process.pid,
        'command': command, 'cwd': str(ROOT)}, indent=2), encoding='utf-8')
    code = process.wait()
duration = time.perf_counter() - start
output = '\n'.join((OUT / (kind + '-tests.' + stream + '.txt')).read_text('utf-8', errors='replace') for stream in ('stdout', 'stderr'))
match = re.search(r'Ran (\d+) tests? in ([\d.]+)s', output)
ending = re.findall(r'^(?:OK(?: \([^\n]+\))?|FAILED \([^\n]+\))\s*$', output, re.MULTILINE)
summary = ending[-1].strip() if ending else None
counts = {key: int(re.search(key + r'=(\d+)', summary).group(1)) if summary and re.search(key + r'=(\d+)', summary) else 0
          for key in ('skipped', 'failures', 'errors', 'expected failures', 'unexpected successes')}
total = int(match.group(1)) if match else None
# unittest counts failing subtests as failure entries without incrementing testsRun.
# Count successful verbose outcomes instead of subtracting failure entries.
test_output = re.split(r'^={70}\s*$', output, maxsplit=1, flags=re.MULTILINE)[0]
passed = len(re.findall(r'(?:^ok$|^\w+ \([^\n]+\) \.\.\. ok$)', test_output, re.MULTILINE))
if code == 0 and total is not None:
    assert passed + counts['skipped'] + counts['expected failures'] == total, 'Verbose outcome count differs from unittest total'
after = identities()
result = {'kind': kind, 'started_utc': started, 'finished_utc': datetime.now(timezone.utc).isoformat(),
    'command': command, 'cwd': str(ROOT), 'python': sys.version, 'executable': sys.executable,
    'exit_code': code, 'total': total, **counts, 'passed': passed if total is not None else None,
    'passed_method': 'count verbose ok outcomes; failure entries may include subtests',
    'unittest_seconds': float(match.group(2)) if match else None, 'wall_seconds': duration, 'summary': summary,
    'source_files_checked': len(before), 'source_changed': [p for p in before if before[p] != after.get(p)],
    'logs': {s: {'path': kind+'-tests.'+s+'.txt', 'sha256': sha256((OUT/(kind+'-tests.'+s+'.txt')).read_bytes()).hexdigest()} for s in ('stdout','stderr')}}
(OUT / (kind + '-tests.source-identities.json')).write_text(json.dumps(before, indent=2), encoding='utf-8')
(OUT / (kind + '-tests.result.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result), flush=True)
raise SystemExit(code)
