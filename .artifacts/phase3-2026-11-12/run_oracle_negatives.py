"""Run pre-admitted known-wrong candidates, stopping strictly at correctness."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.contracts import Decision, RunEvent, canonical_json
from rsi_kernel_agent.workflow.execution import make_request, stable_id
from rsi_kernel_agent.workflow.colab import ColabBackend
from rsi_kernel_agent.workflow.__main__ import colab_remote
from rsi_kernel_agent.workflow.evaluation import integrate_result
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from oracle_gate_aux import record_oracle_gate_failure, require_oracle_gate_open, require_resumable_run

directory = Path(__file__).resolve().parent
store = ExperimentStore(directory / 'experiments.sqlite3', directory / 'artifacts')
backend = ColabBackend(store, colab_remote(directory / 'colab-config.json'))
with WorkflowJournal(store,json.loads((directory/'oracle-negative-attention-job.json').read_text('utf-8'))['task_id']).lock():
    for name in ('oracle-negative-attention', 'oracle-negative-cache'):
        job = json.loads((directory / (name + '-job.json')).read_text('utf-8'))
        require_oracle_gate_open(store, job)
        require_resumable_run(store, job)
        run_id = job['run_id']
        if store.run_state(run_id)['execution_status'] == 'not_run':
            record = Decision(stable_id('oracle-negative-reservation', run_id), job['task_id'], 'note', run_id,
                'Known-wrong oracle validation; no benchmark or promotion',
                {'type': 'oracle_negative_reservation.v1', 'cost': job['variant']['cost'],
                 'allowed_stages': ['generate', 'compile', 'correctness']})
            store.append(record, record.id)
            store.transition(RunEvent(run_id + ':start', run_id, 'running', 'active', 'Known-wrong oracle validation'),
                             None, run_id + ':start')
        receipts = {}
        for stage in ('generate', 'compile', 'correctness'):
            request = make_request(store, job, stage)
            while True:
                state = backend.lookup(request['operation_id'])
                if state['status'] == 'definitely_not_started':
                    state = backend.submit(request)
                entry = {'utc': datetime.now(timezone.utc).isoformat(), 'name': name,
                         'stage': stage, 'operation_id': request['operation_id'], 'status': state['status']}
                with (directory / 'oracle-negative-progress.jsonl').open('a', encoding='utf-8') as log:
                    log.write(canonical_json(entry) + '\n')
                print(canonical_json(entry), flush=True)
                if state['status'] == 'completed':
                    break
                time.sleep(15)
            receipts[stage] = state['receipt']
            if stage != 'correctness' and state['receipt']['status'] != 'succeeded':
                result = integrate_result(store, job, receipts)
                (directory / (name + '-result.json')).write_text(canonical_json(result), encoding='utf-8')
                raise RuntimeError(name + ' failed before Oracle; negative acceptance not established')
            if stage == 'correctness':
                if state['receipt']['status'] == 'succeeded':
                    result = record_oracle_gate_failure(store, job, receipts)
                    (directory / (name + '-result.json')).write_text(canonical_json(result), encoding='utf-8')
                    print(canonical_json(result), flush=True)
                    raise RuntimeError(name + ' unexpectedly passed; persisted Oracle Gate failure')
                result = integrate_result(store, job, receipts)
                (directory / (name + '-result.json')).write_text(canonical_json(result), encoding='utf-8')
                if result['classification'] != 'incorrect':
                    raise RuntimeError(name + ' was not rejected for incorrect output')
                print(canonical_json({'name': name, 'result': result}), flush=True)
