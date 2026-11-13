"""Read one SQLite snapshot and existing files; write only a detached facts JSON."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

D = Path(__file__).resolve().parent
M = D.parents[1] / 'knowledge-site/manifests/phase3-2026-11-12'
TASK = 'mla-decode-v1'
NAMES = ('oracle-negative-attention', 'oracle-negative-cache', 'baseline-repair', 'baseline-b')
references = []


def read(path):
    raw = path.read_bytes()
    references.append({'path': str(path.relative_to(D.parents[1])).replace('\\', '/'),
                       'sha256': sha256(raw).hexdigest(), 'size': len(raw)})
    return json.loads(raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'))


def decode(row):
    assert sha256(row['payload'].encode()).hexdigest() == row['digest']
    return json.loads(row['payload'])


archive = read(D / 'real-round-2/runtime-loss-archive.json')
intent = read(D / 'allocation-intent.json')
env = read(M / 'environment-isolated-runtime4.json')
setup = read(M / 'isolated-environment-setup-runtime4.json')
probe = read(M / 'isolated-probe-runtime4.json')
probe_row = json.loads(next(x.removeprefix('RSI_PROBE:') for x in probe['stdout'].splitlines() if x.startswith('RSI_PROBE:')))
assert probe_row['environment'] == env
jobs = {name: read(D / (name + '-runtime4-job.json')) for name in NAMES}
allocations = [json.loads(line) for line in (D / 'allocation-attempts.jsonl').read_text('utf-8-sig').splitlines()]
allocated = [row for row in allocations if row.get('status') == 'allocated' and row.get('runtime_id') == env['runtime_session_id']][-1]
progress_path = D / 'runtime4-progress.jsonl'
progress = [json.loads(line) for line in progress_path.read_text('utf-8-sig').splitlines()] if progress_path.exists() else []
process_file = D / 'p35-runtime4-driver-process-readonly-20261113T1041Z.json'
processes = read(process_file)

con = sqlite3.connect((D / 'experiments.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)
con.row_factory = sqlite3.Row
try:
    con.execute('PRAGMA query_only=ON')
    con.execute('BEGIN')
    rows = list(con.execute('SELECT * FROM records WHERE task_id=? ORDER BY seq', (TASK,)))
    records = {row['id']: decode(row) for row in rows}
    kinds = {row['id']: row['kind'] for row in rows}
    journal = [value['details']['state'] for value in records.values()
               if value.get('details', {}).get('type') == 'workflow_state.v1'][-1]
    snap = con.execute('SELECT * FROM snapshots WHERE task_id=? ORDER BY version DESC LIMIT 1', (TASK,)).fetchone()
    assert sha256(snap['body'].encode()).hexdigest() == snap['digest']
    snap_body = json.loads(snap['body'])
    snapshot = {**snap_body['metadata'], 'digest': snap['digest']}
    assert records[archive['decision_id']]['details']['summary'] == archive['summary']
    outcomes = []
    for name, job in jobs.items():
        run = records[job['run_id']]
        assert kinds[job['run_id']] == 'Run' and run['candidate_id'] == job['candidate_id'] and run['context_id'] == job['context_id']
        assert records[job['context_id']]['environment'] == env
        events = [value for identity, value in records.items() if kinds[identity] == 'RunEvent' and value['run_id'] == job['run_id']]
        owned = [value for value in records.values() if value.get('target_id') == job['run_id']]
        requests = [value for value in owned if value.get('details', {}).get('type') == 'colab_execution_request.v1']
        receipts = [value for value in owned if value.get('details', {}).get('type') == 'execution_receipt.v1']
        results = [value for value in owned if value.get('details', {}).get('type') == 'workflow_result.v1']
        state = ({key: events[-1][key] for key in ('execution_status', 'scheduling_status')} if events else
                 {'execution_status': 'not_run', 'scheduling_status': 'ready'})
        outcomes.append({'name': name, 'job_id': job['id'], 'run_id': job['run_id'], 'candidate_id': job['candidate_id'],
            'context_id': job['context_id'], 'created_in_original_store': True, 'state': state,
            'last_event_id': events[-1]['id'] if events else None,
            'source_digest': job['variant']['source_ref']['digest'], 'config_digest': job['variant']['config_ref']['digest'],
            'build_digest': job['variant']['build_ref']['digest'],
            'durable_request_stages': [row['details']['request']['stage'] for row in requests],
            'durable_receipt_count': len(receipts), 'completion_marker': results[-1]['id'] if results else None,
            'classification': results[-1]['details']['result']['classification'] if results else None})
    context_ids = sorted({job['context_id'] for job in jobs.values()})
    best = [dict(row) for row in con.execute('SELECT * FROM best WHERE task_id=? ORDER BY context_id,scope', (TASK,)) if row['context_id'] in context_ids]
    frontier = [dict(row) for row in con.execute('SELECT * FROM frontier WHERE task_id=? ORDER BY context_id,candidate_id', (TASK,)) if row['context_id'] in context_ids]
    task_history = {'record_count': len(rows), 'high_watermark': rows[-1]['seq'], 'snapshot': snapshot}
finally:
    con.close()

summary = {key: value for key, value in archive['summary'].items() if key != 'original_operation'}
summary['original_operation_id'] = archive['summary']['original_operation']['operation_id']
summary['original_request_digest'] = archive['summary']['original_operation']['request_digest']
facts = {'schema_version': 1, 'observed_at_utc': datetime.now(timezone.utc).isoformat(),
    'reader': '/root/p35_recovery_review', 'mode': 'SQLite mode=ro; query_only; one explicit read transaction; files read separately',
    'runtime3_loss_archive': {'decision_id': archive['decision_id'], 'intent_id': archive['intent_id'], 'summary': summary, 'snapshot': archive['snapshot']},
    'runtime4': {'allocation': allocated, 'allocation_status': intent['status'], 'environment': env,
        'setup_status': setup['status'], 'probe_exit_code': probe['returncode'], 'probe_result': probe_row['result'],
        'synchronized': probe_row['synchronized'], 'xplane_parser_import': probe_row['xplane_parser_import'],
        'process_observation': processes, 'latest_progress_event': progress[-1] if progress else None},
    'controller': {'revision': journal['revision'], 'spent': journal['spent'], 'limits': journal['limits'],
        'task_cancelled': journal['cancelled'], 'job_count': len(journal['jobs']),
        'remaining': {key: journal['limits'][key] - journal['spent'][key] for key in ('executions', 'tokens', 'time_s')}},
    'task_history': task_history, 'runtime4_prerequisites': outcomes,
    'runtime4_best_projection_at_read': best, 'runtime4_frontier_projection_at_read': frontier,
    'evidence_files': references,
    'limitations': ['Local detached facts, not a new official Snapshot or hardware gate result.',
        'File/process observations and SQLite transaction have separate capture times; driver may advance afterward.',
        'Job creation, running events, and generate completion do not establish full prerequisite correctness or performance.',
        'No bridge calls, new Run/Snapshot/Decision, or database writes were performed by this collector.',
        'Raw NPZ, full artifact closures, and XPlane protobuf were not independently decoded by this facts collector.']}
target = D / 'p35-runtime4-progress-facts.json'
with target.open('x', encoding='utf-8') as stream:
    json.dump(facts, stream, ensure_ascii=False, indent=2)
print(json.dumps({'path': str(target), 'observed_at_utc': facts['observed_at_utc'],
    'record_count': task_history['record_count'], 'high_watermark': task_history['high_watermark'],
    'snapshot_version': snapshot['version'], 'controller_revision': journal['revision'],
    'prerequisites': [{key: row[key] for key in ('name', 'state', 'durable_request_stages', 'durable_receipt_count', 'classification')} for row in outcomes]}, ensure_ascii=False))
