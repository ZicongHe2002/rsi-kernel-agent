"""Read-only final delivery check; writes only its audit result."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
TASK = 'mla-decode-v1'

def read(path):
    return json.loads(path.read_text('utf-8-sig'))

def digest(path):
    return sha256(path.read_bytes()).hexdigest()

checks = []
for file in sorted(OUT.glob('*.md')) + [ROOT / 'docs/phase3/phase3-final-acceptance-report.md', ROOT / 'README.md']:
    if file.name == 'request.md':
        continue
    for target in re.findall(r'(?<!!)\[[^\]]*\]\(([^)]+)\)', file.read_text('utf-8')):
        if target.startswith(('http:', 'https:', '#', 'mailto:')):
            continue
        clean = unquote(target.strip('<>').split('#')[0])
        clean = re.sub(r':\d+$', '', clean)
        path = (file.parent / clean).resolve()
        checks.append({'document': file.relative_to(ROOT).as_posix(), 'target': target,
                       'exists': path.exists()})

tests = {}
for kind in ('root', 'auxiliary'):
    result = read(OUT / (kind + '-tests.result.json'))
    assert result['exit_code'] == 0 and result['failures'] == result['errors'] == 0
    assert result['source_changed'] == []
    for stream, meta in result['logs'].items():
        assert digest(OUT / meta['path']) == meta['sha256'], (kind, stream)
    baseline = read(OUT / (kind + '-tests.source-identities.json'))
    changed = [p for p, h in baseline.items() if not (ROOT / p).is_file() or digest(ROOT / p) != h]
    assert not changed, changed
    tests[kind] = {k: result[k] for k in ('total', 'passed', 'skipped', 'failures', 'errors', 'exit_code', 'unittest_seconds')}

uri = (ROOT / '.artifacts/phase3-2026-11-12/experiments.sqlite3').resolve().as_uri() + '?mode=ro'
with sqlite3.connect(uri, uri=True) as con:
    con.execute('PRAGMA query_only=ON')
    con.execute('BEGIN')
    count, water = con.execute('SELECT count(*),max(seq) FROM records WHERE task_id=?', (TASK,)).fetchone()
    sid, version, shash = con.execute('SELECT id,version,digest FROM snapshots WHERE task_id=? ORDER BY version DESC LIMIT 1', (TASK,)).fetchone()
    payload = con.execute("SELECT payload FROM records WHERE task_id=? AND id LIKE 'wf-state:%' ORDER BY seq DESC LIMIT 1", (TASK,)).fetchone()[0]
    last = json.loads(payload)
    initial = read(OUT / 'memory-audit-result.json')['after_new_connection']
    assert (count, water, sid, version, shash) == (initial['record_count'], initial['high_watermark'], initial['snapshot']['id'], initial['snapshot']['version'], initial['snapshot']['digest'])
    state = last['details']['state']
    journal = {k: state[k] for k in ('revision', 'spent', 'limits', 'cancelled')}
    assert journal == initial['journal']
    # Current journal entry remains byte-bound by the already verified immutable record.
    current = {'records': count, 'high_watermark': water, 'snapshot_id': sid,
               'snapshot_version': version, 'snapshot_digest': shash,
               'journal': journal,
               'latest_journal_record_digest': sha256(payload.encode()).hexdigest(),
               'latest_journal_record_id': last['id']}

broken = [x for x in checks if not x['exists']]
files = [p for p in OUT.iterdir() if p.is_file() and p.suffix in ('.md', '.json', '.py', '.log', '.txt')
         and p.name != 'delivery-verification.json' and not p.name.endswith('.running.json')]
files += [ROOT / name for name in (
    'docs/phase3/phase3-final-acceptance-report.md', 'README.md',
    'docs/superpowers/plans/2026-11-16-p35-final-acceptance.md',
    'knowledge-site/manifests/phase3-2026-11-12/implementation-report.md',
    'docs/phase3/phase3-p35-progress-report-2026-11-14.md',
    'docs/phase3/phase3-p35-final-validation.md')]
report = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'status': 'verified' if not broken else 'failed',
          'store_writes': 0, 'network_calls': 0, 'tests': tests, 'store_after': current,
          'source_files_unchanged_since_tests': True, 'link_count': len(checks), 'broken_links': broken,
          'file_sha256': {p.relative_to(ROOT).as_posix(): digest(p) for p in files}}
(OUT / 'delivery-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k: v for k, v in report.items() if k != 'file_sha256'}, ensure_ascii=False))
raise SystemExit(bool(broken))
