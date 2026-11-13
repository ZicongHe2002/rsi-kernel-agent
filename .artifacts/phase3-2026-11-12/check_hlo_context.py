"""Read-only audit of archived HLO/compiler bindings; no Runtime or Store writes."""
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.contracts import canonical_json
from rsi_kernel_agent.workflow.execution import stable_id


def read(ref):
    raw = (HERE / 'artifacts/sha256' / ref['digest'][:2] / ref['digest']).read_bytes()
    if len(raw) != ref['size'] or sha256(raw).hexdigest() != ref['digest']:
        raise ValueError('Archived artifact digest or size mismatch')
    return raw


metadata = json.loads((HERE / 'baseline-runtime2-hlo-metadata.json').read_bytes())
job = json.loads((HERE / 'baseline-repair-runtime2-job.json').read_bytes())
report = json.loads(read(metadata['report_ref']))
if report != {k: v for k, v in metadata.items() if k != 'report_ref'}:
    raise ValueError('Metadata file differs from its content-addressed report')
receipt = json.loads(read(metadata['receipt_ref']))
operation = stable_id('operation', job['run_id'], 'compile')
if not (receipt['operation_id'] == operation == metadata['operation_id']
        and receipt['run_id'] == metadata['run_id'] == job['run_id']
        and receipt['candidate_identity'] == metadata['source_identity']
        and receipt['candidate_id'] == job['candidate_id'] and receipt['context_id'] == job['context_id']):
    raise ValueError('Compile receipt binding differs')
rows = []
for case, hlo in zip(job['contract']['workload']['spec']['cases'], metadata['hlo'], strict=True):
    if hlo['raw_hlo_ref'] not in receipt['artifact_refs']:
        raise ValueError('HLO not bound to compile receipt')
    text = read(hlo['raw_hlo_ref']).decode('utf-8')
    if text.splitlines()[0] != hlo['hlo_header']:
        raise ValueError('HLO header mismatch')
    calls = []
    for line in text.splitlines():
        if ' custom-call(' not in line or 'backend_config=' not in line:
            continue
        prefix, body = line.split('backend_config=', 1)
        config = json.loads(body)
        calls.append({'name': prefix.strip().split(' = ', 1)[0],
                      'declared_scoped_memory_configs': config.get('scoped_memory_configs'),
                      'used_scoped_memory_configs': config.get('used_scoped_memory_configs'),
                      'retry_config': config.get('retry_config')})
    if calls != hlo['custom_calls']:
        raise ValueError('Extracted HLO compiler fields differ')
    rows.append({'case_id': case['id'], 'raw_hlo_ref': hlo['raw_hlo_ref'], 'custom_calls': calls,
                 'signatures': [line.split('metadata=', 1)[0].split('backend_config=', 1)[0].strip()
                                for line in text.splitlines() if ' custom-call(' in line]})
with sqlite3.connect((HERE / 'experiments.sqlite3').as_uri() + '?mode=ro', uri=True) as connection:
    def record(rid):
        row = connection.execute('SELECT payload, digest FROM records WHERE id=?', (rid,)).fetchone()
        if row is None or sha256(row[0].encode()).hexdigest() != row[1]:
            raise ValueError('Missing or corrupt record')
        return json.loads(row[0])
    saved = record(stable_id('execution-receipt', operation))
    if saved['details']['artifact_ref'] != metadata['receipt_ref']:
        raise ValueError('Store receipt marker does not match archive')
    evidence = record(stable_id('stage-evidence', job['run_id'], 'compile'))
    evaluation = record(stable_id('evaluation', job['run_id']))
    if not (evidence['id'] in evaluation['evidence_ids'] and evidence['run_id'] == job['run_id']
            and evidence['evidence_kind'] == 'code_fact'
            and all(h['raw_hlo_ref'] in evidence['artifacts'] for h in metadata['hlo'])):
        raise ValueError('Compile HLOs are not linked through existing EvaluationBundle')
result = {'classification': 'Read-only archive and compiler metadata verification; no new hardware measurement',
          'run_id': job['run_id'], 'context_id': job['context_id'], 'strategy_id': job['strategy_id'],
          'runtime_session_id': job['contract']['context']['environment']['runtime_session_id'],
          'report_ref': metadata['report_ref'], 'receipt_ref': metadata['receipt_ref'],
          'compile_evidence_id': evidence['id'], 'evaluation_id': evaluation['id'],
          'compile_value_keys': list(evidence['value']['outputs']),
          'metadata_fields_in_evidence_value': 'used_scoped_memory_configs' in canonical_json(evidence['value']),
          'rows': rows, 'verified_artifacts': 5, 'store_open_mode': 'ro',
          'metadata_report_attached_to_store_evidence': metadata['report_ref'] in evidence['artifacts']}
(HERE / 'hlo-context-check.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=2))
