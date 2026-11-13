"""Detached upload-time progress facts. SQLite read-only; no bridge or GitHub."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

D = Path(__file__).resolve().parent
TASK = 'mla-decode-v1'
NAMES = ('oracle-negative-attention', 'oracle-negative-cache', 'baseline-repair', 'baseline-b')
evidence = []


def read(path):
    raw = path.read_bytes()
    evidence.append({'path': path.name, 'sha256': sha256(raw).hexdigest(), 'size': len(raw)})
    return json.loads(raw.decode('utf-8-sig'))


processes = read(D / 'p35-upload-driver-processes.json')
assert processes['processes'] == []
driver_exit = read(D / 'runtime4-prerequisite-execution-result-20261113.json')
test_exit = read(D / 'p35-root-tests-runtime4-20261113-result.json')
test_log = D / 'p35-root-tests-runtime4-20261113.txt'
raw = test_log.read_bytes()
test_text = raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig')
test_run = re.findall(r'Ran (\d+) tests in ([0-9.]+)s', test_text)
test_ok = re.findall(r'OK \(skipped=(\d+)\)', test_text)
assert test_run[-1] == ('321', '423.235') and test_ok[-1] == '3' and test_exit['exit_code'] == 0
evidence.append({'path': test_log.name, 'sha256': sha256(raw).hexdigest(), 'size': len(raw)})
progress_path = D / 'runtime4-progress.jsonl'
raw = progress_path.read_bytes()
progress = [json.loads(line) for line in raw.decode('utf-8-sig').splitlines()]
evidence.append({'path': progress_path.name, 'sha256': sha256(raw).hexdigest(), 'size': len(raw)})
jobs = {name: read(D / (name + '-runtime4-job.json')) for name in NAMES}
archive = read(D / 'real-round-2/runtime-loss-archive.json')
binding = read(D / 'active-runtime-binding.json')

con = sqlite3.connect((D / 'experiments.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)
con.row_factory = sqlite3.Row
try:
    con.execute('PRAGMA query_only=ON')
    con.execute('BEGIN')
    rows = list(con.execute('SELECT * FROM records WHERE task_id=? ORDER BY seq', (TASK,)))
    records = {}
    for row in rows:
        assert sha256(row['payload'].encode()).hexdigest() == row['digest']
        records[row['id']] = {'kind': row['kind'], 'seq': row['seq'], 'created_at': row['created_at'],
                              'payload': json.loads(row['payload'])}
    decisions = [row for row in records.values() if row['kind'] == 'Decision']
    state = [row['payload']['details']['state'] for row in decisions
             if row['payload']['details'].get('type') == 'workflow_state.v1'][-1]
    snap = con.execute('SELECT * FROM snapshots WHERE task_id=? ORDER BY version DESC LIMIT 1', (TASK,)).fetchone()
    assert sha256(snap['body'].encode()).hexdigest() == snap['digest']
    snapshot = {**json.loads(snap['body'])['metadata'], 'digest': snap['digest']}
    outcomes = []
    for name, job in jobs.items():
        run = records[job['run_id']]
        assert run['kind'] == 'Run' and run['payload']['candidate_id'] == job['candidate_id']
        assert run['payload']['context_id'] == job['context_id']
        events = [row for row in records.values() if row['kind'] == 'RunEvent' and row['payload']['run_id'] == job['run_id']]
        owned = [row for row in decisions if row['payload']['target_id'] == job['run_id']]
        requests = [row for row in owned if row['payload']['details'].get('type') == 'colab_execution_request.v1']
        receipts = {row['payload']['details']['operation_id']: row for row in owned
                    if row['payload']['details'].get('type') == 'execution_receipt.v1'}
        results = [row for row in owned if row['payload']['details'].get('type') == 'workflow_result.v1']
        stages = []
        for request in requests:
            body = request['payload']['details']['request']
            receipt_note = receipts.get(body['operation_id'])
            receipt_status = None
            if receipt_note is not None:
                ref = receipt_note['payload']['details']['artifact_ref']
                blob = (D / 'artifacts/sha256' / ref['digest'][:2] / ref['digest']).read_bytes()
                assert len(blob) == ref['size'] and sha256(blob).hexdigest() == ref['digest']
                receipt = json.loads(blob)
                receipt_status = receipt['status']
            stages.append({'stage': body['stage'], 'operation_id': body['operation_id'],
                           'request_id': request['payload']['id'],
                           'local_receipt_id': receipt_note['payload']['id'] if receipt_note else None,
                           'local_receipt_status': receipt_status})
        run_state = ({key: events[-1]['payload'][key] for key in ('execution_status', 'scheduling_status')}
                     if events else {'execution_status': 'not_run', 'scheduling_status': 'ready'})
        outcomes.append({'name': name, 'run_id': job['run_id'], 'context_id': job['context_id'],
                         'candidate_id': job['candidate_id'], 'state': run_state, 'stages': stages,
                         'workflow_result_id': results[-1]['payload']['id'] if results else None,
                         'classification': results[-1]['payload']['details']['result']['classification'] if results else None})
    recovery_rows = [row['payload']['id'] for row in decisions
                     if row['payload']['details'].get('type') == 'workflow_round.v1'
                     and row['payload']['details'].get('round_id') == 'real-runtime4-recovery-round:2']
finally:
    con.close()

facts = {
    'schema_version': 1, 'observed_at_utc': datetime.now(timezone.utc).isoformat(),
    'reader': '/root/p35_recovery_review', 'mode': 'SQLite mode=ro, query_only, one read transaction; existing local files; no network',
    'task': TASK, 'scope': 'Upload-time progress evidence; P3.5 remains incomplete',
    'local_driver': {'running': False, 'process_observation': processes,
                     'last_process_exit_record': driver_exit, 'last_persisted_progress': progress[-1]},
    'remote_worker': {'queried_in_this_review': False, 'final_state': 'not independently observed by this review',
                      'root_report': 'Original remote worker was not stopped; local prerequisite driver was stopped before upload preparation.'},
    'controller': {'revision': state['revision'], 'spent': state['spent'], 'limits': state['limits'],
                   'remaining': {key: state['limits'][key] - state['spent'][key] for key in ('executions', 'time_s', 'tokens')},
                   'task_cancelled': state['cancelled'], 'job_count': len(state['jobs'])},
    'live_task_records': {'record_count': len(rows), 'high_watermark': rows[-1]['seq']},
    'latest_saved_snapshot': snapshot,
    'runtime3_loss_archive': {'decision_id': archive['decision_id'], 'snapshot': archive['snapshot'],
                              'budget_retained': archive['summary']['spent_after'], 'round_execution_completed': False},
    'runtime4_prerequisites': outcomes, 'runtime4_recovery_prepared_round_ids': recovery_rows,
    'active_binding_file': binding,
    'latest_root_tests': {**test_exit, 'total': 321, 'passed': 318, 'skipped': 3, 'failed': 0, 'errors': 0, 'duration_s': 423.235},
    'evidence_files': evidence,
    'limits_of_claim': [
        'A local running RunEvent is persisted scheduling state, not evidence that its local driver is currently alive.',
        'The absence of a local correctness receipt/completion marker means its final result has not been integrated; it does not prove remote execution is incomplete.',
        'No prerequisite gate pass, second-round hardware completion, single-tile hardware result or stable end-to-end gain is claimed.',
        'No Store writes, snapshot creation, bridge calls, remote worker stop or GitHub access occurred in this review.',
        'Runtime4 prerequisite costs are separate reservation Decisions; original exploration budget is unchanged by their Run creation.',
    ],
}
target = D / 'p35-upload-progress-facts.json'
with target.open('x', encoding='utf-8') as stream:
    json.dump(facts, stream, ensure_ascii=False, indent=2)
print(json.dumps({'path': str(target), 'observed_at_utc': facts['observed_at_utc'],
    'live_task_records': facts['live_task_records'], 'controller': facts['controller'],
    'runtime4_prerequisites': outcomes, 'tests': facts['latest_root_tests']}, ensure_ascii=False))
