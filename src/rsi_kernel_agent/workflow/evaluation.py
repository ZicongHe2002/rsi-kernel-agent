"""Fixed receipt verification and integration through the original Store APIs."""
from dataclasses import asdict
import json
import math

from ..contracts import (Candidate, Decision, EvaluationBundle, Evidence, ParentCandidate,
                         ProfileBundle, Run, RunEvent, canonical_json)
from ..profiler.profiles import normalize_duration_report
from .execution import (STAGES, _copy, _get, _maybe, _require, artifact_ref,
                        make_request, stable_id, verify_receipt, stages_for)


def _parent_latency(store, job):
    evaluation_id = job.get('parent_evaluation_id')
    if evaluation_id is None:
        return None
    evaluation = _get(store, evaluation_id, EvaluationBundle)
    parent = _get(store, job['parent_id'], ParentCandidate)
    run = _get(store, evaluation.run_id, Run)
    _require(parent.candidate_id is not None and run.candidate_id == parent.candidate_id
             and run.context_id == job['context_id'], 'Comparison benchmark does not measure the exact parent')
    _require(evaluation.correctness == 'passed' and evaluation.timing_validity == 'valid' and evaluation.confirmed
             and store.run_state(run.id)['execution_status'] == 'succeeded', 'Parent benchmark is not confirmed')
    pending, seen, supported = list(evaluation.evidence_ids), set(), False
    evidence_by_id, timing_roots = {}, []
    while pending:
        evidence_id = pending.pop()
        if evidence_id in seen:
            continue
        seen.add(evidence_id)
        evidence = _get(store, evidence_id, Evidence)
        evidence_by_id[evidence_id] = evidence
        _require(evidence.run_id == run.id, 'Parent evidence input is not bound to the same Run')
        for ref in evidence.artifacts:
            store.artifacts.verify(ref)
        pending.extend(evidence.input_ids)
        if (evidence_id in evaluation.evidence_ids and evidence.evidence_kind in
                {'measured_timing', 'trace_derived', 'derived_measurement'} and evidence.availability == 'available'
                and evidence.request_status == 'completed' and evidence.claim_status == 'supported'
                and evidence.unit == 'ms' and type(evidence.value) in (int, float)
                and evidence.value == evaluation.latency_ms and evidence.artifacts):
            supported = True
            timing_roots.append(evidence_id)
    _require(supported, 'Parent benchmark has no verified bound timing evidence')
    pending, seen = list(timing_roots), set()
    while pending:
        evidence_id = pending.pop()
        if evidence_id in seen:
            continue
        seen.add(evidence_id)
        evidence = evidence_by_id[evidence_id]
        _require(evidence.evidence_kind not in {'model_predicted', 'compiler_estimated', 'source_claim', 'historical_claim'}
                 and evidence.availability == 'available' and evidence.request_status == 'completed'
                 and evidence.claim_status == 'supported' and evidence.artifacts,
                 'Parent timing has an unavailable or nonmeasurement ancestor')
        pending.extend(evidence.input_ids)
    return evaluation.latency_ms


def _verified_prefix(store, job, receipts, cancelled):
    provenance = job['contract']['context']['provenance']
    stages = stages_for(provenance)
    _require(type(receipts) is dict and not (set(receipts) - set(stages)), 'Invalid receipt stage set')
    verified = {}
    for stage in stages:
        if stage not in receipts:
            break
        request = make_request(store, job, stage)
        receipt = verify_receipt(store, request, receipts[stage])
        expected_cost = job['variant']['cost'] if stage == 'generate' else {'executions': 0, 'tokens': 0, 'time_s': 0}
        _require(canonical_json(receipt['cost']) == canonical_json(expected_cost), 'Receipt cost differs from admitted budget')
        output, status = receipt['outputs'], receipt['status']
        if provenance == 'real':
            from .real_protocol import validate_stage
            validate_stage(store, request, receipt)
            if stage != 'generate' and status == 'succeeded':
                _require(output['identity']['workspace'] == verified['generate']['outputs']['workspace'],
                         'Loaded workspace differs from materialization')
            if stage == 'profile' and status == 'succeeded':
                _require(output['lease']['started_ns'] >= verified['benchmark']['outputs']['lease']['finished_ns'],
                         'Profile overlaps the formal benchmark')
        elif stage == 'generate' and status == 'succeeded':
            _require(set(output) == {'workspace', 'source_digest', 'config_digest', 'build_digest'}
                     and type(output['workspace']) is str and output['workspace'], 'Invalid materialization result')
            for key, value in request['candidate_identity'].items():
                _require(output[key] == value, 'Materialized identity mismatch')
        elif stage == 'compile' and status == 'succeeded':
            _require(output == {'compiled_identity': request['candidate_identity'], 'compiler': 'synthetic-v1',
                                'executed': False}, 'Synthetic compile identity mismatch')
        elif stage == 'correctness':
            _require(set(output) == {'correctness', 'contract_digest', 'cases', 'synthetic'}
                     and output['contract_digest'] == job['contract_digest'] and type(output['cases']) is int
                     and output['cases'] > 0 and output['synthetic'] is True
                     and ((status == 'succeeded' and output['correctness'] == 'passed')
                          or (status == 'failed' and output['correctness'] == 'failed')), 'Invalid correctness receipt')
        verified[stage] = receipt
        if status != 'succeeded':
            break
    _require(set(verified) == set(receipts), 'Receipt stages are missing, out of order, or occur after a failure')
    if not cancelled:
        _require(bool(verified), 'Execution has no receipts')
        last = verified[next(reversed(verified))]
        _require(last['status'] != 'succeeded' or len(verified) == len(stages), 'Execution is not complete')
    return verified


def integrate_result(store, job, receipts, *, cancelled=False):
    """Recheck a durable receipt prefix, append facts, and delegate promotion.

    Explicit cancellation is the only way a successful incomplete prefix becomes
    an incomplete result. Missing stages otherwise remain an error, not success.
    A workflow_evaluation_intent.v1 Decision pins the receipt set and cancellation
    mode before any evidence or terminal effects. The final workflow_result.v1
    Decision is the completion marker; retries use the original intent and keys.
    """
    _require(type(cancelled) is bool, 'cancelled must be boolean')
    request = make_request(store, job, 'generate')
    provenance = request['provenance']
    real = provenance == 'real'
    run_id, task_id = job['run_id'], job['task_id']
    intent_id = stable_id('workflow-evaluation-intent', run_id)
    prior_intent = _maybe(store, intent_id)
    if prior_intent is not None:
        _require(type(prior_intent) is Decision and prior_intent.kind == 'note'
                 and prior_intent.task_id == task_id and prior_intent.target_id == run_id
                 and set(prior_intent.details) == {'type', 'job_id', 'request_digest', 'receipt_refs', 'cancelled'}
                 and prior_intent.details['type'] == 'workflow_evaluation_intent.v1'
                 and prior_intent.details['job_id'] == job['id']
                 and prior_intent.details['request_digest'] == request['request_digest']
                 and type(prior_intent.details['cancelled']) is bool,
                 'Evaluation intent does not match this exact execution job')
        cancelled = prior_intent.details['cancelled']
    verified = _verified_prefix(store, job, receipts, cancelled)
    parent_latency = _parent_latency(store, job)
    classification, execution_status = 'correct_inconclusive', 'succeeded'
    correctness, timing_validity, latency, confirmed = 'unknown', 'unknown', None, False
    profile_ref, normalized, raw_profile_refs, statistics = None, None, (), None
    correctness_receipt = verified.get('correctness')
    if correctness_receipt is not None:
        correctness = correctness_receipt['outputs'].get('correctness', 'unknown')
    last_stage = next(reversed(verified), None)
    last = verified.get(last_stage)
    # A completed receipt is a fact even when cancellation arrived before the
    # Controller received it. Only an unfinished successful prefix is cancelled.
    cancel_prefix = cancelled and (last is None or (last['status'] == 'succeeded'
                    and last_stage != stages_for(provenance)[-1]))
    if cancel_prefix:
        classification, execution_status = 'cancelled', 'incomplete'
    elif last['status'] == 'deferred':
        _require(last_stage == 'generate' and store.run_state(run_id)['execution_status'] == 'not_run',
                 'Deferred must occur before the Run starts')
        return {'classification': 'deferred', 'provenance': provenance, 'run_id': run_id,
                'candidate_id': job['candidate_id'], 'evaluation_id': None, 'profile_id': None,
                'promotion_id': None, 'correctness': 'unknown', 'timing_validity': 'unknown', 'latency_ms': None}
    elif last['status'] == 'failed':
        classification = {'generate': 'generation_failed', 'compile': 'compile_failed',
                          'correctness': 'incorrect', 'benchmark': 'execution_failed', 'profile': 'profile_failed'}[last_stage]
        execution_status = 'incomplete' if last_stage == 'profile' else 'failed'
    elif last['status'] == 'incomplete':
        classification, execution_status = 'incomplete', 'incomplete'
    elif last['status'] == 'invalid':
        classification, execution_status, timing_validity = 'invalid_measurement', 'invalid', 'invalid'
    elif not real:
        output = verified['benchmark']['outputs']
        _require(set(output) == {'latency_ms', 'unit', 'timing_validity', 'confirmed', 'reason', 'profile_ref'}
                 and output['unit'] == 'ms' and output['timing_validity'] in {'valid', 'unknown'}
                 and type(output['confirmed']) is bool, 'Invalid benchmark receipt')
        timing_validity = output['timing_validity']
        if timing_validity == 'valid':
            latency, confirmed = output['latency_ms'], output['confirmed']
            _require(correctness == 'passed' and confirmed and type(latency) in (int, float)
                     and math.isfinite(latency) and latency > 0, 'Benchmark is not confirmed, finite, and correct')
            profile_ref = artifact_ref(output['profile_ref'])
            profile = json.loads(store.artifacts.read(profile_ref))
            _require(profile.get('provenance') == 'synthetic'
                     and profile.get('candidate_identity') == request['candidate_identity'], 'Profile identity mismatch')
            for name in ('run_id', 'candidate_id', 'context_id', 'contract_digest'):
                _require(profile.get(name) == job[name], f'Profile {name} mismatch')
            binding = {name: job[name] for name in ('strategy_id', 'parent_id', 'candidate_id', 'run_id', 'context_id')}
            binding.update(provenance='synthetic', declared_identity=request['candidate_identity'])
            normalized = normalize_duration_report(profile, binding=binding, artifact_refs=[asdict(profile_ref)])
            _require(normalized['quality']['availability'] == 'available', 'Profile is incomplete')
            _require(len(profile['groups']) == 1 and len(profile['groups'][0]['samples']) == 1
                     and profile['groups'][0]['samples'][0]['duration_ps'] == latency * 1_000_000_000,
                     'Profile timing differs from benchmark receipt')
            if parent_latency is not None and latency != parent_latency:
                classification = 'correct_faster' if latency < parent_latency else 'correct_slower'
        else:
            _require(output['latency_ms'] is None and not output['confirmed'] and output['profile_ref'] is None,
                     'Unknown timing cannot carry a confirmed measurement')

    if real:
        measured = verified.get('benchmark')
        if measured is not None and measured['status'] == 'succeeded':
            from .real_protocol import timing_statistics
            statistics = timing_statistics(measured['outputs']['samples_ms'])
            latency, timing_validity = statistics['median_ms'], 'valid'
        profile_receipt = verified.get('profile')
        if profile_receipt is not None and profile_receipt['status'] == 'succeeded':
            output = profile_receipt['outputs']
            profile_ref = artifact_ref(output['report_ref'])
            raw_profile_refs = tuple(artifact_ref(r) for r in output['raw_xplane_refs'])
            profile = json.loads(store.artifacts.read(profile_ref))
            binding = {name: job[name] for name in ('strategy_id', 'parent_id', 'candidate_id', 'run_id', 'context_id')}
            binding.update(provenance='real', declared_identity=request['candidate_identity'])
            normalized = normalize_duration_report(profile, binding=binding, artifact_refs=[asdict(profile_ref)])
            _require(normalized['quality']['availability'] == 'available', 'Real Profile is incomplete')
        confirmed = (correctness == 'passed' and timing_validity == 'valid' and normalized is not None
                     and execution_status == 'succeeded' and not cancel_prefix)
        if confirmed and parent_latency is not None and latency != parent_latency:
            classification = 'correct_faster' if latency < parent_latency else 'correct_slower'

    evidence_ids = []

    def append(record):
        return store.append(record, stable_id('integrate', run_id, record.id))

    intent_details = {'type': 'workflow_evaluation_intent.v1', 'job_id': job['id'],
                      'request_digest': request['request_digest'],
                      'receipt_refs': {stage: receipt['artifact_ref'] for stage, receipt in verified.items()},
                      'cancelled': cancelled}
    if prior_intent is not None:
        _require(canonical_json(prior_intent.details) == canonical_json(intent_details),
                 'Evaluation receipt set differs from the committed intent')
    append(Decision(intent_id, task_id, 'note', run_id,
                    'Pinned fixed evaluation inputs before integration', intent_details))

    source = {'provenance': provenance, 'producer': request['backend_id'], 'contract_digest': job['contract_digest'],
              'candidate_identity': request['candidate_identity'], 'parent_identity': request['parent_identity']}
    # Every stage receipt remains discoverable through Evidence, including cost
    # and failed materialization/compile results, not only successful timing.
    for stage, receipt in verified.items():
        stage_artifacts = (artifact_ref(receipt['artifact_ref']),) + tuple(
            artifact_ref(item) for item in receipt.get('artifact_refs', []))
        evidence = Evidence(stable_id('stage-evidence', run_id, stage), run_id, 'code_fact', 'available',
                            'completed', 'supported', {'stage': stage, 'status': receipt['status'],
                                                       'cost': receipt['cost'], 'outputs': receipt['outputs']},
                            None, stage_artifacts, (), {**source, 'stage': stage})
        append(evidence)
        evidence_ids.append(evidence.id)

    correct_id, timing_id, trace_id = (stable_id(prefix, run_id) for prefix in ('correctness', 'timing', 'profile-evidence'))
    correct_artifacts = () if correctness_receipt is None else (artifact_ref(correctness_receipt['artifact_ref']),)
    if real and correctness_receipt is not None:
        correct_artifacts += tuple(artifact_ref(r) for r in correctness_receipt['outputs'].get('raw_artifact_refs', []))
    append(Evidence(correct_id, run_id, 'measured_counter', 'unavailable' if correctness == 'unknown' else 'available',
                    'not_requested' if correctness == 'unknown' else 'completed',
                    'inconclusive' if correctness == 'unknown' else 'supported',
                    None if correctness == 'unknown' else {'correctness': correctness}, None, correct_artifacts, (),
                    {**source, 'reason': ('Real correctness not completed' if correctness == 'unknown' else 'Fixed real oracle results') if real
                     else ('Synthetic correctness not completed' if correctness == 'unknown' else 'Synthetic correctness fixture')}))
    evidence_ids.append(correct_id)
    timing_artifacts = () if 'benchmark' not in verified else (artifact_ref(verified['benchmark']['artifact_ref']),)
    timing_available = statistics is not None if real else confirmed
    append(Evidence(timing_id, run_id, 'measured_timing', 'available' if timing_available else 'unavailable',
                    'completed' if 'benchmark' in verified else 'not_requested', 'supported' if timing_available else 'inconclusive',
                    latency if timing_available else None, 'ms', timing_artifacts, (),
                    {**source, 'reason': ('Raw synchronized benchmark samples' if timing_available else classification) if real
                     else ('Synthetic timing fixture' if confirmed else classification),
                     **({'statistics': statistics} if real and statistics else {})}))
    evidence_ids.append(timing_id)
    profile_inputs = ()
    if real and raw_profile_refs:
        raw_id = stable_id('raw-profile-evidence', run_id)
        append(Evidence(raw_id, run_id, 'trace_derived', 'available', 'completed', 'supported',
            {'format': 'XPlane protobuf', 'operation_id': verified['profile']['operation_id']},
            None, raw_profile_refs, (), {**source, 'parser_digest': job['contract']['context']['protocol']['profile']['parser_digest']}))
        profile_inputs = (raw_id,)
    append(Evidence(trace_id, run_id, 'trace_derived', 'available' if normalized is not None else 'unavailable',
                    'completed' if normalized is not None else 'not_requested',
                    'supported' if normalized is not None else 'inconclusive',
                    {'format': 'PallasKernels.read_profile', 'synthetic': not real} if normalized is not None else None,
                    None, (profile_ref,) if profile_ref else (), profile_inputs,
                    {**source, 'reason': ('Parsed real XPlane duration report' if profile_ref else classification) if real
                     else ('Synthetic saved duration format' if profile_ref else classification)}))
    profile_id = stable_id('profile', run_id)
    append(ProfileBundle(profile_id, run_id, (trace_id,), 'available' if normalized is not None else 'unavailable'))
    evaluation_id = stable_id('evaluation', run_id)
    append(EvaluationBundle(evaluation_id, run_id, correctness, timing_validity, latency, confirmed,
                            tuple([*evidence_ids, trace_id])))
    event_id = run_id + ':finish' if len(run_id) <= 121 else stable_id('finished', run_id)
    store.transition(RunEvent(event_id, run_id, execution_status, 'finished', ('Real' if real else 'Synthetic') + ' result: ' + classification),
                     run_id + ':start', stable_id('integrate-finish', run_id))
    promotion = store.consider(evaluation_id, stable_id('integrate-consider', run_id))
    cost = {name: sum(r['cost'][name] for r in verified.values()) for name in ('executions', 'tokens', 'time_s')}
    result = {'classification': classification, 'provenance': provenance, 'run_id': run_id,
              'candidate_id': job['candidate_id'], 'evaluation_id': evaluation_id, 'profile_id': profile_id,
              'promotion_id': promotion.id, 'correctness': correctness, 'timing_validity': timing_validity,
              'latency_ms': latency, 'confirmed': confirmed, 'parent_latency_ms': parent_latency,
              'receipt_refs': {stage: receipt['artifact_ref'] for stage, receipt in verified.items()},
              'cost': cost}
    if real:
        result['statistics'] = statistics
    append(Decision(stable_id('workflow-result', run_id), task_id, 'note', run_id,
                    'Fixed ' + provenance + ' execution evaluation', {'type': 'workflow_result.v1', 'job_id': job['id'],
                                                             'result': result, 'cancelled': cancelled}))
    return _copy(result)
