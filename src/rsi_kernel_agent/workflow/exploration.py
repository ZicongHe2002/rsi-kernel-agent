from dataclasses import asdict
from hashlib import sha256
import json

from ..contracts import (ArtifactRef, Candidate, Decision, EvaluationBundle, EvaluationContext,
                         Evidence, ParentCandidate, Plan, Run, Strategy, canonical_json)
from ..store import StoreError
from .execution import artifact_ref, make_request, stable_id, verify_receipt
from .journal import WorkflowJournal


_STRUCTURES = {'decomposition', 'reduction', 'reuse', 'layout_lifetime', 'movement_wait', 'fusion_boundary'}
_CHANGES = {'mechanism', 'parameter', 'research_question'}
_NONEXPLORATION = {'timing_repeat', 'abba', 'syntax_repair', 'infrastructure_retry'}
_COUNTEREVIDENCE = {'semantic_counterevidence', 'contract_counterevidence', 'capability_counterevidence'}
_CHANGE_IDENTITY_VERSION = 3


def _require(value, message):
    if not value:
        raise ValueError(message)


def _text(value):
    return type(value) is str and bool(value.strip())


def _digest(value):
    return sha256(canonical_json(value).encode()).hexdigest()


def _get(store, record_id, kind):
    value = store.get(record_id)
    _require(type(value) is kind, 'Unexpected record type: ' + record_id)
    return value


def _optional(store, record_id):
    try:
        return store.get(record_id)
    except StoreError as error:
        if str(error) == 'Missing record: ' + record_id:
            return None
        raise


def _result(record):
    return json.loads(canonical_json({'id': record.id, **record.details}))


def _append(store, task_id, strategy_id, record_id, reason, details):
    record = Decision(record_id, task_id, 'note', strategy_id, reason, details)
    store.append(record, record_id)
    return _result(record)


def _decisions(records, strategy_id):
    result = []
    for record in records:
        if type(record) is Decision:
            row = asdict(record)
        elif type(record) is dict and record.get('kind') == 'Decision':
            row = record['payload']
        else:
            continue
        if row['kind'] in ('note', 'paused', 'resumed') and row['target_id'] == strategy_id:
            result.append(row)
    return result


def exploration_status(records, strategy_id):
    rows = _decisions(records, strategy_id)
    admissions = [r for r in rows if r['details'].get('type') == 'exploration_admission.v1']
    _require(len(admissions) <= 1, 'Duplicate exploration admission')
    admission = admissions[0] if admissions else None
    events, seen = [], set()
    for row in rows:
        details = row['details']
        if details.get('type') != 'exploration_round.v1' or not details['effective']:
            continue
        identity = details['round_id']
        _require(identity not in seen, 'Duplicate effective route round')
        seen.add(identity)
        events.append(row)
    real = [r for r in events if r['details']['provenance'] == 'real']
    synthetic = [r for r in events if r['details']['provenance'] == 'synthetic']
    target = admission['details']['initial_exploration_rounds'] if admission else None
    remaining = max(0, target - len(real)) if target is not None else 0
    state = admission['details']['state'] if admission else 'legacy'
    queued = bool(admission and admission['details']['admission_queued'])
    route_decisions = []
    for row in rows:
        if row['kind'] in ('paused', 'resumed'):
            if state in ('legacy', 'active', 'completed', 'paused'):
                state = 'paused' if row['kind'] == 'paused' else 'active'
        elif row['details'].get('type') == 'exploration_state.v1':
            state = row['details']['state']
            if state == 'active':
                queued = False
        elif row['details'].get('type') == 'route_decision.v1':
            route_decisions.append(row)
            if state != 'stopped':
                state = row['details']['decision']['state']
    if admission and remaining == 0 and state not in ('stopped', 'paused') and not route_decisions:
        state = 'completed'
    return {'target_effective_rounds': target, 'completed_effective_rounds': len(real),
            'remaining_rounds': remaining, 'policy_revision': admission['details']['policy_revision'] if admission else None,
            'state': state, 'admission_id': admission['id'] if admission else None,
            'event_ids': [r['id'] for r in real], 'synthetic_effective_rounds': len(synthetic),
            'synthetic_event_ids': [r['id'] for r in synthetic], 'admission_queued': queued,
            'protected': bool(admission and remaining and state not in ('stopped', 'paused'))}


def record_route_decision(store, task_id, strategy_id, result, source_id, snapshot_id):
    plans = result['plan_set']['plans']
    outcome = result['plan_set'].get('outcome')
    decision = {'state': 'active' if plans else ('paused' if outcome['status'] == 'paused' else outcome['status']),
                'reason': 'New model-authored hypotheses and Plans' if plans else outcome['reason'],
                'refs': sorted({ref for p in plans for ref in p['refs']}) if plans else outcome['refs'],
                'restart_conditions': [] if plans else outcome.get('restart_conditions', []),
                'plan_ids': [p['id'] for p in plans]}
    rid = stable_id('route-decision', source_id, strategy_id)
    return _append(store, task_id, strategy_id, rid, decision['reason'],
        {'type': 'route_decision.v1', 'strategy_id': strategy_id, 'source_id': source_id,
         'snapshot_id': snapshot_id, 'decision': decision, 'policy': 'ai-evidence-directed.v1'})


def admit_strategy(store, strategy_id, *, initial_exploration_rounds=5, policy_revision,
                   reason, mechanism_difference, method_refs, parent_ids, review_ref, max_active=4):
    strategy = _get(store, strategy_id, Strategy)
    _require(type(initial_exploration_rounds) is int and 5 <= initial_exploration_rounds <= 8,
             'Initial exploration policy must be 5..8 rounds')
    _require(type(max_active) is int and max_active > 0, 'Positive admission capacity required')
    _require(all(_text(x) for x in (policy_revision, reason, mechanism_difference)), 'Admission rationale required')
    _require(type(method_refs) in (list, tuple), 'Method references must be a sequence')
    _require(type(parent_ids) in (list, tuple) and parent_ids and len(set(parent_ids)) == len(parent_ids),
             'Distinct exact parents required')
    review = json.loads(store.artifacts.read(artifact_ref(review_ref)))
    _require(review.get('validation_status') == 'validated_mechanism_review'
             and review.get('route_action') == 'fork' and review.get('task_relation') == 'new_to_task'
             and review.get('structural_change') in _STRUCTURES
             and review.get('strategy_id') == strategy_id
             and review.get('coverage', {}).get('complete') is True
             and review.get('mechanism_difference') == mechanism_difference
             and type(review.get('code_refs')) is list and review['code_refs'],
             'New protection needs a reviewed structural difference, not a renamed route or retry')
    lineage = []
    for pid in parent_ids:
        parent = _get(store, pid, ParentCandidate)
        _require(parent.task_id == strategy.task_id, 'Parent belongs to another Task')
        parent_strategy = None
        if parent.candidate_id is not None:
            candidate = _get(store, parent.candidate_id, Candidate)
            parent_strategy = _get(store, candidate.plan_id, Plan).strategy_id
        lineage.append({'parent_id': pid, 'candidate_id': parent.candidate_id, 'strategy_id': parent_strategy})
    reviewed_parent = review.get('lineage')
    _require(type(reviewed_parent) is dict and any(
        item['candidate_id'] == reviewed_parent.get('parent_candidate_id')
        and item['strategy_id'] == reviewed_parent.get('strategy_id') for item in lineage),
        'Admission lineage differs from reviewed exact parent')
    for ref in review['code_refs']:
        _require(type(ref) is str and ref.startswith('source:'), 'Reviewed code reference required')
        source = store.get(ref.removeprefix('source:'))
        _require(type(source) in (Candidate, ParentCandidate), 'Code review must reference an actual implementation')
        owner = source.task_id if type(source) is ParentCandidate else _get(
            store, _get(store, source.plan_id, Plan).strategy_id, Strategy).task_id
        _require(owner == strategy.task_id, 'Code review belongs to another Task')
    request = {'initial_exploration_rounds': initial_exploration_rounds, 'policy_revision': policy_revision,
               'reason': reason, 'mechanism_difference': mechanism_difference, 'method_refs': list(method_refs),
               'lineage': lineage, 'review_ref': review_ref, 'max_active': max_active}
    rid = stable_id('exploration-admission', strategy.task_id, strategy_id)
    with WorkflowJournal(store, strategy.task_id).lock():
        previous = _optional(store, rid)
        if previous is not None:
            _require(previous.details['request_digest'] == _digest(request), 'Admission policy is immutable; retry cannot refresh it')
            return _result(previous)
        history = store.history(strategy.task_id)
        existing_strategies = {r.id for r in history if type(r) is Strategy and r.id != strategy_id}
        _require(existing_strategies <= set(review.get('reviewed_strategy_ids', [])),
                 'Admission review does not cover every existing route')
        admissions = {r.id for r in history if type(r) is Decision
                      and r.details.get('type') == 'exploration_admission.v1'}
        comparisons = review.get('admission_comparisons', [])
        _require(type(comparisons) is list and len(comparisons) == len(admissions)
                 and {c.get('admission_id') for c in comparisons} == admissions,
                 'Every previous exploration identity needs an explicit mechanism comparison')
        for comparison in comparisons:
            _require(comparison.get('relation') == 'different' and _text(comparison.get('mechanism_difference'))
                     and comparison['admission_id'] in comparison.get('refs', []),
                     'Same or unknown mechanism cannot receive a new protection identity')
            for ref in comparison['refs']:
                record_id = ref
                if type(ref) is str:
                    for prefix in ('source:', 'plan:', 'feedback:', 'strategy:'):
                        if ref.startswith(prefix):
                            record_id = ref.removeprefix(prefix)
                            break
                record = store.get(record_id)
                if type(record) in (Strategy, ParentCandidate, Decision):
                    owner = record.task_id
                elif type(record) is Plan:
                    owner = _get(store, record.strategy_id, Strategy).task_id
                elif type(record) is Candidate:
                    owner = _get(store, _get(store, record.plan_id, Plan).strategy_id, Strategy).task_id
                else:
                    raise ValueError('Unsupported comparison reference')
                _require(owner == strategy.task_id, 'Comparison belongs to another Task')
        plans = {r.id for r in history if type(r) is Plan and r.strategy_id == strategy_id}
        _require(not any(type(r) is Candidate and r.plan_id in plans for r in history),
                 'Existing route history cannot be readmitted as a new exploration identity')
        current = [exploration_status(history, r.id) for r in history if type(r) is Strategy]
        active = sum(s['protected'] and not s['admission_queued'] for s in current)
        queued = active >= max_active
        return _append(store, strategy.task_id, strategy_id, rid, reason,
            {'type': 'exploration_admission.v1', 'strategy_id': strategy_id, **request,
             'request_digest': _digest(request), 'state': 'deferred' if queued else 'active', 'admission_queued': queued})


def _verified_feedback(store, job):
    result = job.get('result')
    if result is None:
        return None
    marker = _get(store, stable_id('workflow-result', job['run_id']), Decision)
    _require(marker.details == {'type': 'workflow_result.v1', 'job_id': job['id'],
                               'result': result, 'cancelled': marker.details.get('cancelled')}
             and marker.target_id == job['run_id'] and marker.task_id == job['task_id'],
             'Feedback differs from fixed Evaluator publication')
    run = _get(store, job['run_id'], Run)
    candidate = _get(store, run.candidate_id, Candidate)
    context = _get(store, run.context_id, EvaluationContext)
    evaluation = _get(store, result['evaluation_id'], EvaluationBundle)
    _require(run.context_id == job['context_id'] and candidate.id == job['candidate_id']
             and _get(store, candidate.plan_id, Plan).strategy_id == job['strategy_id']
             and context.task_id == job['task_id'] and evaluation.run_id == run.id
             and result['provenance'] == context.provenance,
             'Feedback record ownership or provenance differs')
    _require(result['correctness'] == evaluation.correctness and result['timing_validity'] == evaluation.timing_validity
             and result['confirmed'] == evaluation.confirmed, 'Feedback differs from fixed Evaluation')
    for evidence_id in evaluation.evidence_ids:
        evidence = _get(store, evidence_id, Evidence)
        _require(evidence.run_id == run.id and evidence.source.get('provenance') == context.provenance,
                 'Feedback evidence belongs to another Run')
        for ref in evidence.artifacts:
            store.artifacts.verify(ref)
    receipts = {}
    for stage, ref in result['receipt_refs'].items():
        receipt = json.loads(store.artifacts.read(artifact_ref(ref)))
        receipt['artifact_ref'] = ref
        receipts[stage] = verify_receipt(store, make_request(store, job, stage), receipt)
    producer_verified = True
    if context.provenance == 'real':
        for stage in receipts:
            request = make_request(store, job, stage)
            operation = _optional(store, stable_id('execution-request', request['operation_id']))
            if operation is None:
                producer_verified = False
                continue
            _require(type(operation) is Decision and operation.details.get('type') == 'colab_execution_request.v1'
                     and canonical_json(operation.details['request']) == canonical_json(request)
                     and operation.details['runtime_id'] == context.environment['runtime_session_id'],
                     'Real feedback requires the original durable Colab execution intent')
    correctness = receipts.get('correctness')
    state = store.run_state(run.id)['execution_status']
    success = (result['classification'].startswith('correct_') and state == 'succeeded'
               and evaluation.correctness == 'passed' and evaluation.timing_validity == 'valid'
               and evaluation.confirmed and correctness is not None and correctness['status'] == 'succeeded')
    failure = (result['classification'] == 'incorrect' and state == 'failed'
               and evaluation.correctness == 'failed' and correctness is not None and correctness['status'] == 'failed')
    return {'run_id': run.id, 'candidate_id': candidate.id, 'plan_id': candidate.plan_id,
            'repair_only': bool(job.get('repair_of') or candidate.change_kind == 'repair'),
            'evaluation_id': evaluation.id,
            'result_id': marker.id, 'receipt_refs': result['receipt_refs'], 'provenance': context.provenance,
            'valid': bool(producer_verified and (success or failure)),
            'producer_verified': producer_verified,
            'performance_validated': success and producer_verified,
            'correctness_failure': failure and producer_verified,
            'identity': asdict(candidate.identity), 'parent_id': candidate.parent_id}


def _analysis_bindings(store, journal, jobs, strategy_id, snapshot_id):
    bindings = {}
    for intake_id in journal['intakes']:
        intake = _get(store, intake_id, Decision).details['result']
        relevant = [item for item in intake['items'] if item['strategy_id'] == strategy_id
                    and item['plan_id'] in {job['plan_id'] for job in jobs} and item['status'] == 'accepted']
        if not relevant:
            continue
        publication = _get(store, intake['publication_id'], Decision)
        manifest = json.loads(store.artifacts.read(artifact_ref(publication.details['manifest'])))
        binding = next((row for row in manifest['request']['strategies'] if row['strategy_id'] == strategy_id), None)
        _require(binding is not None and binding['snapshot_id'] == snapshot_id,
                 'Feedback snapshot differs from the original published route analysis')
        result = json.loads(store.artifacts.read(artifact_ref(publication.details['result'])))
        proposals = {row['id']: row for row in result['plan_set']['plans']}
        for item in relevant:
            bindings[item['plan_id']] = proposals[item['proposal_id']]
    _require(set(bindings) == {job['plan_id'] for job in jobs}, 'Feedback jobs lack original analysis bindings')
    return bindings


def _implementation_identity(store, identity, context):
    if context.provenance != 'real':
        return _digest({name: getattr(identity, name) for name in ('source_digest', 'config_digest')})
    from .real_protocol import read_json, validate_bundle_identity

    def ref(digest):
        path = store.artifacts.path_for(ArtifactRef(digest, 0))
        return asdict(ArtifactRef(digest, path.stat().st_size))

    build_ref = ref(identity.build_digest)
    build = read_json(store, build_ref)
    variant = {'source_ref': ref(identity.source_digest), 'config_ref': ref(identity.config_digest),
               'build_ref': build_ref, 'source_bundle_ref': ref(build['source_bundle_digest'])}
    bundle = validate_bundle_identity(store, variant, context.protocol)
    return _digest({'source_files': {item['path']: item['artifact_ref']['digest'] for item in bundle['files']},
                    'entry': bundle['entry'],
                    'config_digest': identity.config_digest})


def record_round_feedback(store, task_id, round_id, strategy_id, *, input_snapshot_id,
                          hypothesis, change_kind, job_ids, next_step, key):
    strategy = _get(store, strategy_id, Strategy)
    _require(strategy.task_id == task_id, 'Feedback Strategy belongs to another Task')
    _require(all(_text(value) for value in (round_id, hypothesis, next_step, key)), 'Feedback and next-step judgment required')
    _require(change_kind in _CHANGES | _NONEXPLORATION, 'Unknown exploration change kind')
    _require(type(job_ids) in (list, tuple) and job_ids and len(set(job_ids)) == len(job_ids), 'Distinct feedback jobs required')
    request = {'task_id': task_id, 'round_id': round_id, 'strategy_id': strategy_id,
               'input_snapshot_id': input_snapshot_id, 'hypothesis': hypothesis,
               'change_kind': change_kind, 'job_ids': sorted(job_ids), 'next_step': next_step}
    effective_id = stable_id('exploration-effective', task_id, strategy_id, round_id)
    with WorkflowJournal(store, task_id).lock():
        previous = _optional(store, effective_id)
        if previous is not None:
            _require(previous.details['request_digest'] == _digest(request), 'A route round already has different feedback')
            return _result(previous)
        observation_id = stable_id('exploration-observation', task_id, strategy_id, round_id, key)
        previous = _optional(store, observation_id)
        if previous is not None:
            _require(previous.details['request_digest'] == _digest(request), 'Feedback key reused with different inputs')
            return _result(previous)
        journal = WorkflowJournal(store, task_id).load()
        _require(journal is not None, 'Feedback requires original Controller jobs')
        jobs = [j for j in journal['jobs'].values() if j['round_id'] == round_id and j['strategy_id'] == strategy_id]
        _require(set(job_ids) == {j['id'] for j in jobs}, 'Feedback must cover every route job in the global round')
        _require(all(j['state'] in ('completed', 'failed', 'cancelled', 'deferred') for j in jobs),
                 'Route jobs still queued, running or unknown; do not finalize feedback')
        snapshot = store.snapshot_page(input_snapshot_id, limit=2**31 - 1)
        _require(snapshot['snapshot']['task_id'] == task_id and snapshot['complete'], 'Incomplete or foreign input snapshot')
        frozen_ids = {r.id for r in snapshot['records']}
        _require(strategy_id in frozen_ids and all(j['run_id'] not in frozen_ids for j in jobs),
                 'Input snapshot must precede this route round feedback')
        plan_bindings = _analysis_bindings(store, journal, jobs, strategy_id, input_snapshot_id)
        rows = [row for job in jobs if (row := _verified_feedback(store, job)) is not None]
        provenances = {j['contract']['context']['provenance'] for j in jobs}
        _require(len(provenances) == 1, 'A route round cannot mix real and synthetic feedback')
        provenance = next(iter(provenances))
        previous_events = [r for r in _decisions(store.history(task_id), strategy_id)
                           if r['details'].get('type') == 'exploration_round.v1' and r['details']['effective']]
        _require(all(r['id'] in frozen_ids for r in previous_events),
                 'The next effective route round must use Memory containing the previous feedback')
        previous_rounds = [r['details'] for r in previous_events]
        prior_hypotheses = {' '.join(r['hypothesis'].split()).casefold() for r in previous_rounds}
        prior_identities = {identity for r in previous_rounds for identity in r['change_identities']}
        prior_identity_issues = []
        for previous in previous_rounds:
            # Old hashes omit bundle paths or the entry. Rebuild them from the
            # immutable feedback rather than changing historical Decisions.
            if previous.get('change_identity_version') == _CHANGE_IDENTITY_VERSION:
                continue
            for row in previous['feedback_refs']:
                if row['valid']:
                    try:
                        candidate = _get(store, row['candidate_id'], Candidate)
                        context = _get(store, _get(store, row['run_id'], Run).context_id, EvaluationContext)
                        prior_identities.add(_implementation_identity(store, candidate.identity, context))
                    except (ValueError, OSError, KeyError) as error:
                        prior_identity_issues.append({'round_id': previous['round_id'],
                            'candidate_id': row['candidate_id'], 'run_id': row['run_id'], 'reason': str(error)})
        valid = [r for r in rows if r['valid'] and not r['repair_only']]
        changed = []
        for row in valid:
            candidate = _get(store, row['candidate_id'], Candidate)
            parent = _get(store, candidate.parent_id, ParentCandidate)
            context = _get(store, _get(store, row['run_id'], Run).context_id, EvaluationContext)
            try:
                identity = _implementation_identity(store, candidate.identity, context)
                parent_identity = _implementation_identity(store, parent.identity, context)
            except (ValueError, OSError, KeyError) as error:
                row['change_comparison'] = {'status': 'unknown', 'reason': str(error)}
                continue
            row['change_comparison'] = {'status': 'different' if identity != parent_identity else 'same',
                                        'identity': identity, 'parent_identity': parent_identity}
            if identity != parent_identity and identity not in prior_identities:
                if prior_identity_issues:
                    row['change_comparison'].update(status='unknown',
                        reason='Previous implementation identities could not be reconstructed')
                else:
                    changed.append(identity)
        novel_question = (change_kind == 'research_question'
                          and ' '.join(hypothesis.split()).casefold() not in prior_hypotheses
                          and any(hypothesis in (plan_bindings[row['plan_id']]['mechanism'],
                                                 plan_bindings[row['plan_id']]['modification']) for row in valid))
        effective = bool(change_kind in _CHANGES and valid and (changed or novel_question))
        outcome = ('validated_feedback' if any(r['performance_validated'] for r in valid) else
                   'correctness_failure' if valid else 'deferred' if any(j['state'] == 'deferred' for j in jobs)
                   else 'no_valid_feedback')
        reason = ('Effective route feedback retained' if effective else
                  'Not an effective exploration round: ' + ('repeat or repair' if change_kind in _NONEXPLORATION else
                                                           'no distinct change or question' if valid else outcome))
        details = {'type': 'exploration_round.v1', **request, 'request_digest': _digest(request),
                   'effective': effective, 'provenance': provenance, 'outcome': outcome,
                   'performance_validated': any(r['performance_validated'] for r in valid),
                   'feedback_refs': rows, 'change_identities': sorted(set(changed)),
                   'change_identity_version': _CHANGE_IDENTITY_VERSION,
                   'prior_identity_issues': prior_identity_issues,
                   'journal_revision': journal['revision']}
        return _append(store, task_id, strategy_id, effective_id if effective else observation_id, reason, details)


def _counterevidence(store, value, task_id):
    if type(value) is str:
        evidence = _get(store, value, Evidence)
        run = _get(store, evidence.run_id, Run)
        context = _get(store, run.context_id, EvaluationContext)
        _require(context.task_id == task_id and evidence.availability == 'available'
                 and evidence.request_status == 'completed' and evidence.claim_status == 'supported',
                 'Stopping requires completed supported counterevidence')
        for ref in evidence.artifacts:
            store.artifacts.verify(ref)
        data = evidence.value
    else:
        data = json.loads(store.artifacts.read(artifact_ref(value)))
    _require(type(data) is dict and data.get('kind') in _COUNTEREVIDENCE and _text(data.get('claim'))
             and type(data.get('refs')) is list and data['refs'],
             'Stopping requires semantic, contract or target-capability counterevidence, not a slower result')
    for ref in data['refs']:
        record = store.get(ref)
        if hasattr(record, 'task_id'):
            owner = record.task_id
        elif type(record) is Plan:
            owner = _get(store, record.strategy_id, Strategy).task_id
        elif type(record) is Candidate:
            owner = _get(store, _get(store, record.plan_id, Plan).strategy_id, Strategy).task_id
        else:
            run = record if type(record) is Run else _get(store, record.run_id, Run)
            owner = _get(store, run.context_id, EvaluationContext).task_id
        _require(owner == task_id, 'Counterevidence belongs to another Task')


def set_exploration_state(store, strategy_id, state, reason, evidence_refs, key):
    strategy = _get(store, strategy_id, Strategy)
    _require(state in ('needs_research', 'deferred', 'active', 'stopped') and _text(reason) and _text(key),
             'Invalid exploration state or rationale')
    _require(type(evidence_refs) in (list, tuple), 'Counterevidence references must be a sequence')
    if state == 'stopped':
        _require(bool(evidence_refs), 'Stopping needs explicit counterevidence')
        for ref in evidence_refs:
            _counterevidence(store, ref, strategy.task_id)
    rid = stable_id('exploration-state', strategy.task_id, strategy_id, key)
    with WorkflowJournal(store, strategy.task_id).lock():
        history = store.history(strategy.task_id)
        status = exploration_status(history, strategy_id)
        _require(status['admission_id'] is not None, 'Route has no initial-exploration admission')
        details = {'type': 'exploration_state.v1', 'strategy_id': strategy_id, 'state': state,
                   'evidence_refs': list(evidence_refs), 'admission_id': status['admission_id']}
        previous = _optional(store, rid)
        if previous is not None:
            _require(previous.details == details and previous.reason == reason, 'State key reused with different inputs')
            return _result(previous)
        if state == 'active' and status['remaining_rounds']:
            admission = _get(store, status['admission_id'], Decision)
            active = sum((s := exploration_status(history, r.id))['protected'] and not s['admission_queued']
                         for r in history if type(r) is Strategy and r.id != strategy_id)
            _require(active < admission.details['max_active'], 'Admission capacity is still full')
        return _append(store, strategy.task_id, strategy_id, rid, reason, details)


def reserve_research_attempt(store, strategy_id, round_id, key, limit=2):
    strategy = _get(store, strategy_id, Strategy)
    _require(type(limit) is int and limit >= 0 and _text(round_id) and _text(key), 'Invalid research attempt limit or identity')
    rid = stable_id('exploration-research', strategy.task_id, strategy_id, round_id, key)
    with WorkflowJournal(store, strategy.task_id).lock():
        rows = [r for r in _decisions(store.history(strategy.task_id), strategy_id)
                if r['details'].get('type') == 'exploration_research.v1' and r['details']['round_id'] == round_id]
        _require(all(r['details']['limit'] == limit for r in rows), 'Research limit is fixed within the round')
        previous = _optional(store, rid)
        if previous is not None:
            return _result(previous)
        _require(len(rows) < limit, 'Research retry limit exhausted')
        return _append(store, strategy.task_id, strategy_id, rid, 'Bounded research attempt reservation',
                       {'type': 'exploration_research.v1', 'strategy_id': strategy_id, 'round_id': round_id,
                        'limit': limit, 'ordinal': len(rows) + 1, 'key': key})
