"""Local prerequisite/recovery checks shared by Phase 3 host scripts.

These helpers never connect to Colab, execute a Candidate, or promote a result.
The Store and original receipt verifier remain the evidence authorities.
"""
from dataclasses import asdict
from hashlib import sha256
import json
import os
import re
from pathlib import Path
import tempfile

from rsi_kernel_agent.contracts import ArtifactRef, Candidate, Decision, EvaluationBundle, Evidence, ProfileBundle, canonical_json
from rsi_kernel_agent.store import StoreError
from rsi_kernel_agent.profiler.inputs import load_manifest
from rsi_kernel_agent.profiler.outputs import require
from rsi_kernel_agent.workflow.evaluation import _verified_prefix
from rsi_kernel_agent.workflow.execution import make_request, stable_id
from rsi_kernel_agent.workflow.intake import _id
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from rsi_kernel_agent.workflow.rounds import _prepared


LIMITS = {'max_concurrency': 1, 'executions': 16, 'tokens': 100000,
          'time_s': 28800, 'max_repairs': 1, 'max_retries': 1}


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def runtime_binding(directory, target=None):
    path = Path(target) / 'runtime-binding.json' if target is not None else None
    if path is None or not path.exists():
        path = Path(directory) / 'active-runtime-binding.json'
    value = read_json(path) if path.exists() else {'generation': 'runtime2'}
    require(type(value) is dict and set(value) == {'generation'} and type(value['generation']) is str
            and re.fullmatch(r'runtime[0-9]+', value['generation'])
            and int(value['generation'][7:]) >= 2, 'Invalid immutable Runtime binding')
    return value


def runtime_config_path(directory, binding):
    value = binding['generation']
    require(re.fullmatch(r'runtime[0-9]+', value) and int(value[7:]) >= 2, 'Invalid Runtime generation')
    return Path(directory) / ('colab-config-' + value + '.json')


def write_once(path, value):
    path = Path(path)
    raw = canonical_json(value).encode('utf-8')
    require(not any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction())
                    for p in (path, *path.parents)), 'Linked auxiliary output')
    if path.exists():
        require(canonical_json(read_json(path)).encode('utf-8') == raw,
                'Existing recovery input differs: ' + str(path))
        return
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.real-round-')
    try:
        with os.fdopen(descriptor, 'wb') as out:
            out.write(raw); out.flush(); os.fsync(out.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            write_once(path, value)
    finally:
        Path(temporary).unlink(missing_ok=True)


def verified_gate_result(store, job, *, negative=False):
    """Read the original completion marker and reverify its exact receipt prefix."""
    make_request(store, job, 'generate')
    require(job['contract']['context']['provenance'] == 'real', 'Gate requires real provenance')
    marker = store.get(stable_id('workflow-result', job['run_id']))
    require(type(marker) is Decision and marker.kind == 'note'
            and marker.task_id == job['task_id'] and marker.target_id == job['run_id']
            and marker.details.get('type') == 'workflow_result.v1'
            and marker.details.get('job_id') == job['id'] and marker.details.get('cancelled') is False,
            'Missing exact, uncancelled workflow completion marker')
    result = marker.details['result']
    require(result['run_id'] == job['run_id'] and result['candidate_id'] == job['candidate_id']
            and result['provenance'] == 'real', 'Gate result binding differs')
    receipts = {}
    for stage, ref in result['receipt_refs'].items():
        body = json.loads(store.artifacts.read(ArtifactRef(**ref)))
        receipts[stage] = {**body, 'artifact_ref': ref}
    verified = _verified_prefix(store, job, receipts, cancelled=False)
    evaluation = store.get(result['evaluation_id'])
    require(type(evaluation) is EvaluationBundle and evaluation.run_id == job['run_id']
            and evaluation.correctness == result['correctness']
            and evaluation.timing_validity == result['timing_validity']
            and evaluation.confirmed is result['confirmed']
            and evaluation.latency_ms == result['latency_ms'], 'Gate EvaluationBundle differs')
    if negative:
        require(set(verified) == {'generate', 'compile', 'correctness'}
                and verified['correctness']['status'] == 'failed'
                and verified['correctness']['outputs'].get('correctness') == 'failed'
                and result['classification'] == 'incorrect' and result['correctness'] == 'failed'
                and result['confirmed'] is False
                and store.run_state(job['run_id'])['execution_status'] == 'failed',
                'Known-wrong Candidate must be rejected at correctness, without benchmark')
    else:
        require(set(verified) == {'generate', 'compile', 'correctness', 'benchmark', 'profile'}
                and all(r['status'] == 'succeeded' for r in verified.values()), 'Baseline Profile or execution stage failed')
        require(result['classification'].startswith('correct_') and result['correctness'] == 'passed'
                and result['timing_validity'] == 'valid' and result['confirmed'] is True
                and store.run_state(job['run_id'])['execution_status'] == 'succeeded', 'Baseline Gate not passed')
        profile = store.get(result['profile_id'])
        require(type(profile) is ProfileBundle and profile.run_id == job['run_id']
                and profile.availability == 'available', 'Baseline Profile is unavailable')
        for eid in profile.evidence_ids:
            evidence = store.get(eid)
            require(type(evidence) is Evidence and evidence.run_id == job['run_id']
                    and evidence.availability == 'available' and evidence.claim_status == 'supported'
                    and evidence.request_status == 'completed', 'Profile evidence is not available')
            for ref in evidence.artifacts:
                store.artifacts.verify(ref)
    return result


def prerequisite_names(directory, generation):
    names = ('oracle-negative-attention', 'oracle-negative-cache', 'baseline-repair', 'baseline-b')
    if generation == 'runtime3' and (Path(directory) / 'p35-confirmation-policy.json').exists():
        names += ('confirmation-control',)
    return names


def require_gates(store, directory, binding=None):
    """Verify the bound Runtime gates, including the P3.5 independent B control."""
    directory = Path(directory)
    binding = runtime_binding(directory) if binding is None else binding
    config_path = runtime_config_path(directory, binding)
    generation = binding['generation']
    names = prerequisite_names(directory, generation)
    jobs = {name: read_json(directory / (name + '-' + generation + '-job.json')) for name in names}
    base, other = jobs['baseline-repair'], jobs['baseline-b']
    environment = base['contract']['context']['environment']
    config = read_json(config_path)
    require(config['runtime_id'] == environment['runtime_session_id'], 'Pinned Runtime differs from baseline')
    require(other['context_id'] == base['context_id'] and other['strategy_id'] != base['strategy_id'],
            'Expected independently measured baselines for two strategies in one Context')
    if 'confirmation-control' in jobs:
        control = jobs['confirmation-control']
        original = read_json(directory / 'baseline-b-runtime2-job.json')
        verified_gate_result(store, original)
        require(control['task_id'] == original['task_id'] == base['task_id']
                and control['strategy_id'] == original['strategy_id'] == other['strategy_id']
                and control['context_id'] == other['context_id']
                and control['candidate_id'] == original['candidate_id']
                and control['candidate_id'] not in (base['candidate_id'], other['candidate_id'])
                and control['run_id'] not in (original['run_id'], base['run_id'], other['run_id']),
                'Confirmation control must remeasure the original B baseline in the current Context')
        for key in ('source_ref', 'config_ref', 'build_ref', 'source_bundle_ref'):
            require(control['variant'][key] == original['variant'][key] == base['variant'][key],
                    'Confirmation control changed the original baseline artifacts')
        baseline_candidate = store.get(base['candidate_id'])
        control_candidate = store.get(control['candidate_id'])
        require(type(baseline_candidate) is Candidate and type(control_candidate) is Candidate
                and baseline_candidate.identity == control_candidate.identity,
                'Confirmation control identity differs from A baseline')
    results = {}
    for name, job in jobs.items():
        require(job['task_id'] == base['task_id'] and job['contract']['context']['environment'] == environment
                and job['contract']['context']['protocol'] == base['contract']['context']['protocol'],
                'Gate Runtime, task, or fixed protocol differs')
        negative = name.startswith('oracle-negative')
        if negative:
            admission = store.get(stable_id('oracle-negative-admission', job['candidate_id']))
            require(type(admission) is Decision and admission.task_id == base['task_id']
                    and admission.details.get('type') == 'oracle_negative_admission.v1',
                    'Negative Gate lacks original known-wrong admission')
            response = json.loads(store.artifacts.read(ArtifactRef(**admission.details['response_ref'])))
            require(response['purpose'] == 'known-wrong real-oracle validation; not optimization'
                    and response['source_sha256'] == job['variant']['source_ref']['digest']
                    and response['mechanism']['output'] == name.removeprefix('oracle-negative-'),
                    'Negative control identity differs from its explicit mutation')
            base_cases = {case['id']: case for case in base['contract']['workload']['spec']['cases']}
            cases = job['contract']['workload']['spec']['cases']
            require(bool(cases) and all(base_cases.get(case['id']) == case for case in cases),
                    'Negative control changed baseline correctness cases')
        results[name] = verified_gate_result(store, job, negative=negative)
    for record in store.history(base['task_id']):
        if type(record) is Decision and record.details.get('type') == 'oracle_gate_failure.v1':
            failed = store.get(record.target_id)
            context = store.get(failed.context_id)
            require(context.environment['runtime_session_id'] != config['runtime_id'],
                    'Persisted Oracle Gate failure in this Runtime')
    return jobs, results


def load_prepared(store, task_id, context_id, strategies, round_id):
    """Restore pinned manifest and original Memory, without a live Knowledge read."""
    try:
        marker = store.get(_id('wf-round', task_id, round_id))
    except StoreError:
        return None
    require(type(marker) is Decision and marker.kind == 'note' and marker.task_id == marker.target_id == task_id
            and marker.details.get('type') == 'workflow_round.v1' and marker.details.get('round_id') == round_id
            and marker.details.get('context_id') == context_id and marker.details.get('strategies') == list(strategies),
            'Saved round has different task/context/strategies/round identity')
    prepared = _prepared(store, marker.details)
    request = prepared['request']
    require(request['task_id'] == task_id and request['environment']['workflow_round'] == round_id
            and [b['strategy_id'] for b in request['strategies']] == list(strategies)
            and all(b['context_id'] == context_id for b in request['strategies'])
            and all(h['complete'] for h in prepared['histories'].values()), 'Saved manifest or history differs from round')
    return prepared


def validate_session(directory, manifest):
    state = read_json(Path(directory) / 'session.json')
    require(state['schema_version'] == 1 and state['manifest'] == asdict(manifest.artifact_ref),
            'Existing session has another manifest')
    return state


def completed_session(store, directory, manifest):
    state = validate_session(directory, manifest)
    result = read_json(Path(directory) / 'result.json')
    raw = canonical_json(result).encode('utf-8')
    require(state['result_digest'] == sha256(raw).hexdigest(), 'Session result digest changed or session is incomplete')
    require(store.artifacts.read(ArtifactRef(state['result_digest'], len(raw))) == raw,
            'Session result differs from saved result artifact')
    require(result['manifest_digest'] == manifest.artifact_ref.digest
            and result['exchanges'] == state['responses'] and result['model_transport'] == 'conversation_session',
            'Completed session exchanges or manifest differ')
    return result


def completed_round(store, task_id, round_id):
    """Require the exact previous intake's attempts, including repair/retry, terminal."""
    state = WorkflowJournal(store, task_id).load()
    require(state is not None, 'Previous round has no Controller journal')
    intakes = []
    for rid, digest in state['intakes'].items():
        record = store.get(rid)
        result = record.details.get('result', {})
        if result.get('round_id') == round_id:
            require(record.details.get('type') == 'workflow_intake.v1'
                    and record.task_id == task_id and sha256(canonical_json(result).encode()).hexdigest() == digest,
                    'Previous round intake digest differs')
            intakes.append(result)
    require(len(intakes) == 1, 'Previous round needs one exact accepted intake')
    expected = {job['id'] for item in intakes[0]['items'] if item['status'] == 'accepted' for job in item['jobs']}
    require(bool(expected) and expected <= set(state['jobs']), 'Previous intake has missing Controller jobs')
    jobs = [job for job in state['jobs'].values() if job['round_id'] == round_id]
    require(bool(jobs) and all(job['state'] in ('completed', 'failed', 'cancelled') and job['result'] for job in jobs),
            'Previous round still has unfinished, blocked, or deferred attempts')
    for job in jobs:
        marker = store.get(stable_id('workflow-result', job['run_id']))
        require(type(marker) is Decision and marker.details.get('type') == 'workflow_result.v1'
                and marker.details.get('job_id') == job['id'] and marker.details.get('result') == job['result'],
                'Previous round job lacks exact result completion marker')
    return jobs


def require_new_history(store, task_id, prepared, previous_round_id):
    jobs = completed_round(store, task_id, previous_round_id)
    rows = {row['payload']['id'] for history in prepared['histories'].values() for row in history['records']}
    require(all(job['run_id'] in rows and stable_id('workflow-result', job['run_id']) in rows for job in jobs),
            'New round snapshot does not include completed previous-round results')
