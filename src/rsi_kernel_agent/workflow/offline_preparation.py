from dataclasses import asdict
import json

from ..contracts import ArtifactRef, Decision, canonical_json
from ..profiler.context import ContextReader, encode_analysis_context
from ..profiler.inputs import load_manifest
from ..profiler.outputs import require, validate_result
from .intake import _digest, _get_optional, _id
from .journal import WorkflowJournal


def _preparation(store, preparation_id):
    record = store.get(preparation_id)
    require(type(record) is Decision and record.details.get('type') == 'offline_preparation.v1',
            'Unknown offline preparation')
    return record


def _route(store, prep, sid):
    require(sid in prep.details['routes'], 'Route is outside this preparation')
    return _get_optional(store, _id('offline-route', prep.id, sid))


def _next(store, prep):
    return next((sid for sid in prep.details['order'] if prep.details['routes'][sid]['status'] == 'pending'
                 and _route(store, prep, sid) is None), None)


async def begin_preparation(store, knowledge, task_id, historical_context_id, preparation_key,
                            assessments, *, parents, knowledge_refs=None):
    from .design import _begin_inputs
    return await _begin_inputs(store, knowledge, task_id, historical_context_id, preparation_key,
                               assessments, parents=parents, knowledge_refs=knowledge_refs, offline=True)


def preparation_manifest(store, preparation_id, strategy_id):
    prep = _preparation(store, preparation_id)
    require(_get_optional(store, _id('offline-complete', prep.id)) is None, 'Offline preparation is complete')
    require(_next(store, prep) == strategy_id, 'Finish the previous offline route before opening another')
    manifest = prep.details['routes'][strategy_id]['manifest']
    require(manifest is not None, 'Offline route input is incomplete')
    return load_manifest(store, ArtifactRef(**manifest))


def require_delivery(data, result):
    reader = ContextReader(encode_analysis_context(data))
    for call in result['tool_calls']:
        if 'error' in call:
            continue
        if call['name'] == 'context_page':
            reader.page(**call['arguments'])
        elif call['name'] == 'context_read':
            reader.read(**call['arguments'])
    coverage = reader.coverage()
    require(coverage['complete'], 'Complete explicit page delivery coverage is required')
    if (data['request']['tool_versions'].get('runtime_delta') == '1'
            and data['request']['environment'].get('offline_route_id') is not None):
        coverage['scope'] = 'runtime_binding_delta'
        coverage['manifest_digest'] = _digest(data)
        coverage['full_history_read_in_this_review'] = False
    return coverage


def seal_preparation_route(store, preparation_id, strategy_id, result, drafts, checks):
    prep = _preparation(store, preparation_id)
    with WorkflowJournal(store, prep.task_id).lock():
        old = _route(store, prep, strategy_id)
        if old is not None:
            require(old.details['result_digest'] == _digest(result)
                    and old.details['drafts_digest'] == _digest(drafts)
                    and old.details['checks_digest'] == _digest(checks), 'Offline route already sealed with different inputs')
            payload = read_route(store, old.id)
            manifest = load_manifest(store, ArtifactRef(**payload['manifest']))
            if manifest.to_dict()['request']['tool_versions'].get('route_progress') == '1':
                from .exploration import record_route_decision
                record_route_decision(store, prep.task_id, strategy_id, result, old.id, prep.details['snapshot']['id'])
            return old
        manifest = preparation_manifest(store, prep.id, strategy_id)
        data, reviewed = validate_result(store, manifest, result)
        require(data['request']['environment'].get('analysis_phase') == 'offline_preparation',
                'Only offline analysis can seal a preparation')
        coverage = require_delivery(data, result)
        if reviewed['plans']:
            from .offline_checks import validate_checks, require_current_checks
            verified = validate_checks(store, manifest, result, drafts, checks)
            require_current_checks(store, verified)
        else:
            require(drafts == {} and checks is None, 'No-Plan route must not contain candidates or candidate checks')
        outcome = {'status': 'planned' if reviewed['plans'] else reviewed['outcome']['status'],
                   'reason': 'All offline proposals retained' if reviewed['plans'] else reviewed['outcome']['reason']}
        payload = {'schema_version': 1, 'preparation_id': prep.id, 'strategy_id': strategy_id,
                   'manifest': asdict(manifest.artifact_ref), 'result': result, 'drafts': drafts, 'checks': checks,
                   'coverage': coverage, 'outcome': outcome, 'executable': False}
        ref = asdict(store.artifacts.put(canonical_json(payload).encode('utf-8')))
        rid = _id('offline-route', prep.id, strategy_id)
        marker = Decision(rid, prep.task_id, 'note', strategy_id, 'Sealed offline research and candidate drafts',
            {'type': 'offline_route.v1', 'preparation_id': prep.id, 'route_ref': ref, 'outcome': outcome,
             'result_digest': _digest(result), 'drafts_digest': _digest(drafts), 'checks_digest': _digest(checks)})
        store.append(marker, marker.id)
        if data['request']['tool_versions'].get('route_progress') == '1':
            from .exploration import record_route_decision
            record_route_decision(store, prep.task_id, strategy_id, result, marker.id,
                                  prep.details['snapshot']['id'])
        return marker


def read_route(store, route_id):
    record = store.get(route_id)
    require(type(record) is Decision and record.details.get('type') == 'offline_route.v1', 'Unknown offline route')
    payload = json.loads(store.artifacts.read(ArtifactRef(**record.details['route_ref'])))
    prep = _preparation(store, payload['preparation_id'])
    sid = payload['strategy_id']
    require(record.id == _id('offline-route', prep.id, sid) and record.target_id == sid
            and record.task_id == prep.task_id and record.details['preparation_id'] == prep.id
            and payload['manifest'] == prep.details['routes'][sid]['manifest'] and payload['executable'] is False,
            'Offline route binding differs')
    require(all(record.details[name + '_digest'] == _digest(payload[name]) for name in ('result', 'drafts', 'checks'))
            and record.details['outcome'] == payload['outcome'], 'Offline route payload differs')
    manifest = load_manifest(store, ArtifactRef(**payload['manifest']))
    from ..profiler.outputs import _validate_pinned_result
    data, reviewed = _validate_pinned_result(store, manifest, payload['result'])
    require(set(data['strategies']) == {sid}
            and data['request']['environment'].get('analysis_phase') == 'offline_preparation', 'Offline route phase differs')
    require(require_delivery(data, payload['result']) == payload['coverage'], 'Offline delivery coverage differs')
    if reviewed['plans']:
        from .offline_checks import _inputs_from_validated_result, _validate_checks_for_inputs
        _, _, subject = _inputs_from_validated_result(
            manifest, payload['result'], payload['drafts'], data, reviewed)
        require(_validate_checks_for_inputs(
            store, data, reviewed, subject, payload['drafts'], payload['checks'])['ready'],
                'Offline candidate checks failed')
    else:
        require(payload['drafts'] == {} and payload['checks'] is None, 'Unexpected no-Plan candidate checks')
    return payload


def _require_measured_parent(store, context, parent_id):
    from ..contracts import Run, EvaluationBundle
    from .evaluation import _parent_latency
    parent = store.get(parent_id)
    history = store.history(context.task_id)
    runs = [r for r in history if type(r) is Run and r.candidate_id == parent.candidate_id
            and r.context_id == context.id]
    require(bool(runs), 'Formal Context has no exact parent measurement')
    benchmarks = [r for r in history if type(r) is EvaluationBundle and r.run_id == runs[-1].id]
    require(bool(benchmarks), 'Formal parent measurement has no evaluation')
    _parent_latency(store, {'parent_id': parent_id, 'context_id': context.id,
                            'parent_evaluation_id': benchmarks[-1].id})


async def validate_binding(store, knowledge, preparation_id, context, assessments, parents, knowledge_refs):
    prep = _preparation(store, preparation_id)
    complete = store.get(_id('offline-complete', prep.id))
    require(type(complete) is Decision and complete.details.get('type') == 'offline_complete.v1'
            and complete.details['preparation_id'] == prep.id and complete.task_id == prep.task_id,
            'A complete offline preparation is required')
    old_context = store.get(prep.details['context_id'])
    require(context.task_id == prep.task_id and context.workload_id == old_context.workload_id,
            'Preparation Task or workload differs')
    require(canonical_json(context.protocol) == canonical_json(old_context.protocol)
            and context.provenance == old_context.provenance, 'Preparation protocol or provenance drift; prepare again')
    require(set(assessments) == set(prep.details['routes']), 'Preparation route set differs; prepare again')
    routes, selected, pins = {}, dict(parents), dict(knowledge_refs)
    for sid in prep.details['order']:
        initial = prep.details['routes'][sid]
        route = _route(store, prep, sid)
        if route is None:
            require(assessments[sid] == prep.details['assessments'][sid], 'Unprepared route assessment changed')
            continue
        payload = read_route(store, route.id)
        if payload['checks'] is not None:
            from .offline_checks import require_current_checks
            require_current_checks(store, payload['checks'])
        manifest = load_manifest(store, ArtifactRef(**payload['manifest'])).to_dict()
        parent_id = manifest['strategies'][sid]['parent_id']
        require(selected.get(sid, parent_id) == parent_id, 'Exact preparation parent changed')
        selected[sid] = parent_id
        retained_outcome = (not payload['result']['plan_set']['plans']
                            and assessments[sid]['status'] == payload['outcome']['status'])
        if context.provenance == 'real' and not retained_outcome:
            _require_measured_parent(store, context, parent_id)
        original_pins = manifest['request']['knowledge']
        require(canonical_json(pins.get(sid, original_pins)) == canonical_json(original_pins),
                'Preparation Knowledge versions changed')
        pins[sid] = original_pins
        for item in manifest['knowledge'].values():
            require(knowledge is not None and item['availability'] == 'available', 'Prepared Knowledge is unavailable')
            await knowledge.fetch_document(item['note_id'], expected_revision=item['revision'])
            for source in item['sources']:
                require(source['availability'] == 'available', 'Prepared Knowledge source is unavailable')
                await knowledge.fetch_document(source['document_id'], expected_revision=source['revision'])
        require(assessments[sid]['status'] in ('hypothesis', 'needs_research') or retained_outcome,
                'Prepared route needs explicit formal review or its unchanged no-Plan outcome')
        routes[sid] = route.id
    return routes, selected, pins


def capture_draft(capture, raw, strategies):
    environment = raw['environment']
    route_id = environment.get('offline_route_id')
    if route_id is None:
        return
    sid, current = next(iter(strategies.items()))
    marker = capture.record(route_id, Decision)
    payload = read_route(capture.store, route_id)
    require(payload['preparation_id'] == environment['preparation_id'] and payload['strategy_id'] == sid,
            'Draft belongs to another preparation or route')
    capture.read(ArtifactRef(**marker.details['route_ref']))
    original = load_manifest(capture.store, ArtifactRef(**payload['manifest'])).to_dict()
    previous = original['strategies'][sid]
    require(current['parent'] == previous['parent'] and original['workload']['id'] == raw['workload_id']
            and canonical_json(current['context']['protocol']) == canonical_json(previous['context']['protocol'])
            and current['context']['provenance'] == previous['context']['provenance'], 'Prepared parent or contract drift')
    require(canonical_json(raw['knowledge']) == canonical_json(original['request']['knowledge']), 'Prepared Knowledge pins drift')
    sources = {}
    for variants in payload['drafts'].values():
        for variant in variants:
            for name in ('source_ref', 'config_ref', 'build_ref', 'source_bundle_ref'):
                capture.read(ArtifactRef(**variant[name]))
            bundle = json.loads(capture.read(ArtifactRef(**variant['source_bundle_ref'])))
            files = []
            for item in bundle['files']:
                raw_bytes = capture.read(ArtifactRef(**item['artifact_ref']))
                try:
                    content, encoding = raw_bytes.decode('utf-8-sig'), 'utf-8'
                except UnicodeDecodeError:
                    content, encoding = None, 'binary'
                files.append({**item, 'content': content, 'encoding': encoding})
            sources[variant['source_bundle_ref']['digest']] = {**bundle, 'files': files}
    if payload['checks'] is not None:
        capture.read(ArtifactRef(**payload['checks']['report_ref']))
        for check in payload['checks']['checks']:
            capture.read(ArtifactRef(**check['log_ref']))
            for ref in check['artifact_refs']:
                capture.read(ArtifactRef(**ref))
    def history_ids(history):
        if history.get('scope') == 'complete_metric_history':
            return [row['run_id'] or row['candidate_id'] for row in history['rows']]
        return [row['payload']['id'] for row in history['records']]
    old_ids = set(history_ids(previous['history']))
    value = {'preparation_id': payload['preparation_id'], 'route_id': route_id,
             'result_digest': marker.details['result_digest'], 'manifest': payload['manifest'],
             'analysis': payload['result']['analysis'], 'plan_set': payload['result']['plan_set'],
             'drafts': payload['drafts'], 'checks': payload['checks'], 'sources': sources,
             'outcome': payload['outcome'], 'executable': False,
             'changes': {'context': {'before': previous['context']['id'], 'after': current['context']['id']},
                         'environment': {'before': previous['context']['environment'], 'after': current['context']['environment']},
                         'parent_run': {'before': previous['run'], 'after': current['run']},
                         'new_history_ids': [rid for rid in history_ids(current['history']) if rid not in old_ids]}}
    if raw['tool_versions'].get('runtime_delta') == '1':
        require(previous['history'].get('scope') == 'complete_metric_history'
                and current['history'].get('scope') == 'complete_metric_history',
                'Runtime delta requires structured preparation history; prepare again')
        value['delta_binding'] = {
            'history_digest': _digest(previous['history']),
            'history_rows': [{'candidate_id': row['candidate_id'], 'run_id': row['run_id'], 'digest': _digest(row)}
                             for row in previous['history']['rows']],
        }
    capture.reference('draft:' + sid, 'OfflineDraft', value, strategy_id=sid,
                      artifacts=list(capture.artifacts.values()))


def require_prepared_implementations(store, manifest, result, proposal_id, variants, *, partial=False):
    data = manifest.to_dict() if hasattr(manifest, 'to_dict') else manifest
    if (data['request']['environment'].get('preparation_id') is None
            and next(iter(data['strategies'].values()))['context']['protocol'].get('execution_mode') != 'candidate.v1'):
        return
    from .offline_checks import validate_checks, require_current_checks
    sid = next(iter(data['strategies']))
    device = data['strategies'][sid]['context']['environment'].get('runtime_session_id')
    require(type(device) is str and device and all(row.get('device') == device for row in variants),
            'Candidate device differs from the formal Runtime')
    draft = data['references'].get('draft:' + sid, {}).get('value')
    payload = read_route(store, draft['route_id']) if draft is not None else None
    decision = next((item for item in result['proposal'].get('draft_review', {}).get('plan_decisions', [])
                     if proposal_id in item['final_plan_ids']), None)
    stripped = [{key: value for key, value in row.items() if key != 'device'} for row in variants]
    def matches(expected):
        if partial:
            return len(stripped) == 1 and any(canonical_json(stripped[0]) == canonical_json(row) for row in expected or ())
        return canonical_json(stripped) == canonical_json(expected)
    if decision is not None and decision['status'] == 'retained':
        require_current_checks(store, payload['checks'])
        require(matches(payload['drafts'][decision['draft_plan_id']]),
                'Retained implementation differs from the checked offline draft')
        return
    for record in reversed(store.history(data['request']['task_id'])):
        if type(record) is not Decision or record.target_id != sid or record.details.get('type') != 'offline_checks.v1':
            continue
        report = json.loads(store.artifacts.read(ArtifactRef(**record.details['report_ref'])))
        if (report['subject']['manifest_ref']['digest'] != result['manifest_digest']
                or report['subject']['result_digest'] != _digest(result)):
            continue
        saved_drafts = report.get('drafts')
        if saved_drafts is None or not matches(saved_drafts.get(proposal_id)):
            continue
        checks = {**report, 'report_ref': record.details['report_ref'], 'decision_id': record.id}
        frozen = load_manifest(store, ArtifactRef(**report['subject']['manifest_ref']))
        verified = validate_checks(store, frozen, result, saved_drafts, checks)
        require_current_checks(store, verified)
        return
    raise ValueError('Revised or new candidate implementation has no matching completed local checks')


def bind_prepared_variants(store, batch_id, *, revisions=None):
    from .design import _batch, _completion
    from .intake import _publication, validate_variant
    from .offline_checks import validate_checks
    batch = _batch(store, batch_id)
    require(batch.details.get('preparation_id') is not None, 'Design batch has no offline preparation')
    complete = _completion(store, batch.id)
    require(complete is not None, 'Complete formal design is required before binding candidates')
    require(revisions is None or type(revisions) is dict, 'Revised candidates must be per-publication checked sets')
    revisions, used, variants = revisions or {}, set(), {}
    for sid, outcome in complete.details['outcomes'].items():
        publication_id = outcome.get('publication_id')
        if publication_id is None:
            continue
        publication = store.get(publication_id)
        data, result = _publication(store, publication)
        require(set(data['strategies']) == {sid}, 'Formal publication belongs to another route')
        if not result['plan_set']['plans']:
            continue
        draft = data['references'].get('draft:' + sid, {}).get('value')
        retained = ({item['final_plan_ids'][0]: item['draft_plan_id']
                     for item in result['proposal']['draft_review']['plan_decisions'] if item['status'] == 'retained'}
                    if draft is not None else {})
        mapping = {row['proposal_id']: row['record_id'] for row in publication.details['plans']}
        needs_new = {p['id'] for p in result['plan_set']['plans']} - set(retained)
        checked_variants = None
        if needs_new:
            supplied = revisions.get(publication_id)
            require(type(supplied) is dict and set(supplied) == {'drafts', 'checks'},
                    'Revised or new final Plans require a checked full route candidate set')
            manifest = load_manifest(store, ArtifactRef(**store.get(publication_id).details['manifest']))
            require(validate_checks(store, manifest, result, supplied['drafts'], supplied['checks'])['ready'],
                    'Revised candidate checks failed')
            checked_variants = supplied['drafts']
            used.add(publication_id)
        else:
            require(publication_id not in revisions, 'Retained implementations cannot be overridden')
        device = data['strategies'][sid]['context']['environment'].get('runtime_session_id')
        require(type(device) is str and device, 'Formal Runtime identity is missing')
        for plan in result['plan_set']['plans']:
            pid = plan['id']
            key = mapping[pid]
            require(key not in variants, 'Duplicate published Plan identity')
            if pid in retained:
                chosen = draft['drafts'][retained[pid]]
                require(checked_variants is None or canonical_json(checked_variants[pid]) == canonical_json(chosen),
                        'Retained implementation changed during revision checks')
            else:
                chosen = checked_variants[pid]
            variants[key] = [validate_variant(store, {**row, 'device': device}, label, provenance='real')
                             for row, label in zip(chosen, plan['variants'])]
            require_prepared_implementations(store, data, result, pid, variants[key])
    require(set(revisions) == used, 'Unexpected revised candidate set')
    return variants


def complete_preparation(store, preparation_id):
    prep = _preparation(store, preparation_id)
    with WorkflowJournal(store, prep.task_id).lock():
        require(_next(store, prep) is None, 'Every offline route must close before preparation completion')
        outcomes = {}
        for sid in prep.details['order']:
            route = _route(store, prep, sid)
            if route is None:
                initial = prep.details['routes'][sid]
                require(initial['status'] != 'pending', 'Offline route is not closed')
                outcomes[sid] = {'status': initial['status'], 'reason': initial['reason'], 'route_id': None}
            else:
                payload = read_route(store, route.id)
                outcomes[sid] = {**payload['outcome'], 'route_id': route.id}
        rid = _id('offline-complete', prep.id)
        marker = Decision(rid, prep.task_id, 'note', prep.task_id, 'Offline preparation complete; no execution authority',
            {'type': 'offline_complete.v1', 'preparation_id': prep.id, 'outcomes': outcomes, 'executable': False})
        store.append(marker, marker.id)
        return marker
