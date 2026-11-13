"""Auxiliary Oracle Gate diagnostics; never evaluates or promotes a Candidate."""
import json
from rsi_kernel_agent.contracts import ArtifactRef, Decision, RunEvent
from rsi_kernel_agent.store import StoreError
from rsi_kernel_agent.workflow.execution import stable_id
from rsi_kernel_agent.workflow.evaluation import _verified_prefix


def record_oracle_gate_failure(store, job, receipts):
    if set(receipts) != {'generate', 'compile', 'correctness'}:
        raise ValueError('Oracle Gate diagnostic needs exactly the correctness prefix')
    # This validates a successful prefix only; it does not integrate, cancel,
    # create an EvaluationBundle or call consider.
    verified = _verified_prefix(store, job, receipts, cancelled=True)
    if any(r['status'] != 'succeeded' for r in verified.values()) or verified['correctness']['outputs'].get('correctness') != 'passed':
        raise ValueError('Oracle Gate unexpected-pass record requires a verified passed receipt')
    run_id = job['run_id']
    marker_id = stable_id('oracle-gate-failure', run_id)
    result = {'classification': 'oracle_gate_failure', 'run_id': run_id,
              'candidate_id': job['candidate_id'], 'context_id': job['context_id'],
              'correctness': 'passed', 'known_wrong_candidate': True,
              'expected_correctness': 'failed', 'confirmed': False,
              'evaluation_id': None, 'promotion_id': None,
              'receipt_refs': {stage: value['artifact_ref'] for stage, value in verified.items()},
              'raw_correctness_refs': verified['correctness']['outputs'].get('raw_artifact_refs', [])}
    try:
        old = store.get(marker_id)
    except StoreError:
        old = None
    if old is None:
        state = store.run_state(run_id)
        if state['execution_status'] != 'running':
            raise ValueError('Unexpected pass can only close the active validation Run')
        details = {'type': 'oracle_gate_failure.v1', 'job_id': job['id'], 'result': result,
                   'expected_event_id': state['event_id']}
        old = Decision(marker_id, job['task_id'], 'note', run_id,
                       'Known-wrong Candidate passed fixed Oracle; Gate closed, no benchmark or promotion', details)
        store.append(old, old.id)
    elif (type(old) is not Decision or old.task_id != job['task_id'] or old.target_id != run_id
          or old.details.get('type') != 'oracle_gate_failure.v1'
          or old.details.get('job_id') != job['id'] or old.details.get('result') != result):
        raise ValueError('Existing Oracle Gate diagnostic differs from receipts')
    event_id = stable_id('oracle-gate-incomplete', run_id)
    store.transition(RunEvent(event_id, run_id, 'incomplete', 'finished',
                     'Oracle Gate failed: known-wrong output unexpectedly passed; execution stopped'),
                     old.details['expected_event_id'], event_id)
    return result


def require_oracle_gate_open(store, job):
    try:
        marker = store.get(stable_id('oracle-gate-failure', job['run_id']))
    except StoreError:
        return
    receipts = {}
    for stage, ref in marker.details['result']['receipt_refs'].items():
        body = json.loads(store.artifacts.read(ArtifactRef(**ref)))
        receipts[stage] = {**body, 'artifact_ref': ref}
    record_oracle_gate_failure(store, job, receipts)
    raise RuntimeError('Persisted Oracle Gate failure; do not execute later validation or optimization')


def require_resumable_run(store, job):
    """An administratively closed/lost Run must never be executed again."""
    state = store.run_state(job['run_id'])
    if state['execution_status'] in ('not_run', 'running'):
        return
    try:
        marker = store.get(stable_id('workflow-evaluation-intent', job['run_id']))
    except StoreError:
        raise RuntimeError('Terminal Run has no resumable evaluation intent; preserve it and use an explicitly admitted new Run') from None
    if (type(marker) is not Decision or marker.task_id != job['task_id'] or marker.target_id != job['run_id']
            or marker.details.get('type') != 'workflow_evaluation_intent.v1'
            or marker.details.get('job_id') != job['id']):
        raise ValueError('Terminal Run has an invalid evaluation intent')
