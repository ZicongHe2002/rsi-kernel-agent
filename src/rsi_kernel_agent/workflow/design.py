"""Frozen per-route design batches; publication never grants execution budget."""
from dataclasses import asdict
import json

from ..contracts import ArtifactRef, Decision, EvaluationContext, ParentCandidate, canonical_json
from ..memory import MemoryReader
from ..profiler.inputs import AnalysisRequest, capture_inputs, load_manifest
from ..profiler.outputs import require
from .exploration import exploration_status
from .intake import _digest, _get_optional, _id, _publication
from .journal import WorkflowJournal
from .rounds import _source_ref


_TERMINAL = {'inapplicable', 'awaiting_conditions', 'deferred', 'paused'}
_ASSESSMENTS = _TERMINAL | {'hypothesis', 'needs_research'}


def _batch(store, batch_id):
    record = store.get(batch_id)
    require(type(record) is Decision and record.details.get('type') == 'design_round.v1', 'Unknown design batch')
    return record


def _completion(store, batch_id):
    return _get_optional(store, _id('design-complete', batch_id))


def _outcome(store, batch, sid):
    initial = batch.details['routes'][sid]
    if initial['status'] != 'pending':
        return initial
    marker = _get_optional(store, _id('route-design', batch.id, sid))
    return marker.details['outcome'] if marker is not None else None


def _next(store, batch):
    return next((sid for sid in batch.details['order'] if _outcome(store, batch, sid) is None), None)


async def begin_design_round(store, knowledge, task_id, context_id, round_id, assessments, *,
                             parents=None, knowledge_refs=None, preparation_id=None):
    return await _begin_inputs(store, knowledge, task_id, context_id, round_id, assessments,
                               parents=parents, knowledge_refs=knowledge_refs, preparation_id=preparation_id)


async def _begin_inputs(store, knowledge, task_id, context_id, round_id, assessments, *,
                        parents=None, knowledge_refs=None, offline=False, preparation_id=None):
    require(type(assessments) is dict and assessments, 'Every strategy needs an assessment')
    require(parents is None or type(parents) is dict, 'Parents must be a mapping')
    require(knowledge_refs is None or type(knowledge_refs) is dict, 'Knowledge pins must be per route')
    parents, knowledge_refs = parents or {}, knowledge_refs or {}
    context = store.get(context_id)
    require(type(context) is EvaluationContext and context.task_id == task_id, 'Context belongs to another Task')
    rid = _id('offline-preparation' if offline else 'design-batch', task_id, round_id)
    request = dict(context_id=context_id, round_id=round_id, assessments=assessments,
                   parents=parents, knowledge_refs=knowledge_refs)
    if preparation_id is not None:
        request['preparation_id'] = preparation_id
    fingerprint = _digest(request)
    with WorkflowJournal(store, task_id).lock():
        old = _get_optional(store, rid)
        if old is not None:
            require(old.details['request_digest'] == fingerprint, 'Design round identity is immutable')
            return old
        prepared_routes = {}
        if preparation_id is not None:
            from .offline_preparation import validate_binding
            prepared_routes, parents, knowledge_refs = await validate_binding(
                store, knowledge, preparation_id, context, assessments, parents, knowledge_refs)
        state = WorkflowJournal(store, task_id).load()
        require(state is None or not any(job['round_id'] == round_id for job in state['jobs'].values()),
                'An existing execution round cannot be retroactively frozen; use the next round')
        snapshot = store.create_snapshot(task_id, rid + ':input')
        view = MemoryReader(store).open_snapshot(snapshot['id'], page_size=max(100, snapshot['record_count']))
        cards = {card['strategy_id']: card for card in view.status_cards(context_id)}
        require(set(assessments) == set(cards), 'Assessment must cover every strategy in the snapshot')
        require(set(parents) <= set(cards) and set(knowledge_refs) <= set(cards), 'Input mapping names unknown strategies')
        page = store.snapshot_page(snapshot['id'], limit=snapshot['record_count'])
        records = {record.id: record for record in page['records']}
        for sid, assessment in assessments.items():
            require(type(assessment) is dict and set(assessment) == {'status', 'reason', 'hypothesis', 'refs'},
                    'Invalid strategy assessment fields')
            require(assessment['status'] in _ASSESSMENTS and type(assessment['reason']) is str
                    and assessment['reason'].strip(), 'Assessment requires a concrete disposition and reason')
            require(assessment['hypothesis'] is None or type(assessment['hypothesis']) is str
                    and assessment['hypothesis'].strip(), 'Invalid assessment hypothesis')
            require(assessment['status'] != 'hypothesis' or assessment['hypothesis'], 'Hypothesis assessment needs a hypothesis')
            require(type(assessment['refs']) is list and all(ref in records for ref in assessment['refs']),
                    'Assessment references must belong to frozen Memory')
            protection = exploration_status(page['records'], sid)
            cards[sid]['initial_exploration'] = protection
        captured = {}
        routes = {}
        for sid in cards:
            assessment = assessments[sid]
            protection = cards[sid]['initial_exploration']
            if assessment['status'] in _TERMINAL or protection['admission_queued'] or protection['state'] == 'stopped':
                status = 'deferred' if protection['admission_queued'] else assessment['status']
                routes[sid] = {'status': status, 'reason': assessment['reason'], 'input_complete': False,
                               'manifest': None, 'publication_id': None, 'plans': []}
                continue
            selected = cards[sid]['current_parent_candidate']
            parent_id = parents.get(sid, selected['parent_id'] if selected else None)
            parent = records.get(parent_id)
            if parent is None:
                routes[sid] = {'status': 'needs_research', 'reason': 'Exact parent is unavailable in the frozen snapshot',
                               'input_complete': False, 'manifest': None, 'publication_id': None, 'plans': []}
                continue
            require(type(parent) is ParentCandidate and parent.task_id == task_id, 'Invalid exact route parent')
            if preparation_id is not None and context.provenance == 'real':
                from .offline_preparation import _require_measured_parent
                _require_measured_parent(store, context, parent.id)
            runs = [r for r in page['records'] if type(r).__name__ == 'Run' and r.candidate_id == parent.candidate_id
                    and r.context_id == context_id]
            run = runs[-1] if runs else None
            profiles = [r.id for r in page['records'] if type(r).__name__ == 'ProfileBundle' and run and r.run_id == run.id]
            benchmarks = [r.id for r in page['records'] if type(r).__name__ == 'EvaluationBundle' and run and r.run_id == run.id]
            pins = knowledge_refs.get(sid, [])
            require(type(pins) is list, 'Route Knowledge pins must be an array')
            for pin in pins:
                if pin['method_id'] in captured:
                    require(captured[pin['method_id']]['pin'] == pin, 'A batch cannot mix revisions of the same method')
                else:
                    captured[pin['method_id']] = {'pin': pin}
            environment = {'analysis_host': 'fresh conversation context', 'design_round_id': round_id,
                           'design_batch_id': rid}
            if offline:
                environment.update(analysis_phase='offline_preparation', preparation_id=rid)
            elif preparation_id is not None:
                environment.update(analysis_phase='execution', preparation_id=preparation_id)
                if sid in prepared_routes:
                    environment['offline_route_id'] = prepared_routes[sid]
            try:
                request = AnalysisRequest.from_dict({'schema_version': 2, 'request_id': _id('route-input', rid, sid),
                    'task_id': task_id, 'workload_id': context.workload_id, 'strategies': [{
                        'strategy_id': sid, 'parent_id': parent.id, 'context_id': context_id, 'snapshot_id': snapshot['id'],
                        'run_id': run.id if run else None, 'source_artifact': _source_ref(store, parent.identity.source_digest),
                        'config_artifact': _source_ref(store, parent.identity.config_digest),
                        'profile_ids': profiles, 'benchmark_ids': benchmarks}], 'knowledge': pins,
                    'environment': environment, 'tool_versions': {'workflow_design': '1', 'input_manifest': '2',
                                                                  'route_progress': '1',
                                                                  **({'runtime_delta': '1'} if preparation_id is not None else {})}})
                manifest = await capture_inputs(store, knowledge, request)
            except (OSError, ValueError) as error:
                routes[sid] = {'status': 'needs_research', 'reason': 'Incomplete route input: ' + str(error),
                               'input_complete': False, 'manifest': None, 'publication_id': None, 'plans': []}
                continue
            routes[sid] = {'status': 'pending', 'manifest': asdict(manifest.artifact_ref),
                           'input_complete': True, 'publication_id': None, 'plans': []}
        details = {'type': 'offline_preparation.v1' if offline else 'design_round.v1',
                   'request_digest': fingerprint, 'round_id': round_id,
                   'context_id': context_id, 'snapshot': snapshot, 'order': list(cards),
                   'cards': cards, 'assessments': assessments, 'routes': routes,
                   'knowledge_corpus_snapshot_consistent': False, 'combined_atomic_snapshot': False}
        if preparation_id is not None:
            details['preparation_id'] = preparation_id
        marker = Decision(rid, task_id, 'note', task_id, 'Frozen all-route assessments and independent inputs', details)
        store.append(marker, rid)
        return marker


def route_manifest(store, batch_id, strategy_id):
    batch = _batch(store, batch_id)
    require(strategy_id in batch.details['routes'], 'Strategy is not in this frozen batch')
    require(_completion(store, batch_id) is None, 'Design batch is already complete')
    require(_next(store, batch) == strategy_id, 'Finish the previous route context before opening this one')
    ref = batch.details['routes'][strategy_id]['manifest']
    require(ref is not None, 'Route input is incomplete')
    return load_manifest(store, ArtifactRef(**ref))


def finish_route_design(store, batch_id, strategy_id, publication_id):
    batch = _batch(store, batch_id)
    with WorkflowJournal(store, batch.task_id).lock():
        rid = _id('route-design', batch_id, strategy_id)
        old = _get_optional(store, rid)
        if old is not None:
            require(old.details['outcome']['publication_id'] == publication_id, 'Route already closed with different publication')
            data, result = _publication(store, store.get(publication_id))
            if data['request']['tool_versions'].get('route_progress') == '1':
                from .exploration import record_route_decision
                record_route_decision(store, batch.task_id, strategy_id, result, old.id, batch.details['snapshot']['id'])
            return old
        manifest = route_manifest(store, batch_id, strategy_id)
        publication = store.get(publication_id)
        require(type(publication) is Decision and publication.details.get('type') == 'analysis_result.v1'
                and publication.details['manifest'] == asdict(manifest.artifact_ref), 'Publication is outside the frozen route input')
        data, result = _publication(store, publication)
        require(set(data['strategies']) == {strategy_id}, 'One route context cannot publish another route')
        plans = publication.details['plans']
        disposition = result['plan_set'].get('outcome', result['proposal'].get('outcome'))
        require(plans or disposition is not None, 'No-Plan design needs a concrete reason')
        outcome = {'status': 'planned' if plans else disposition['status'],
                   'reason': 'All proposed Plans retained' if plans else disposition['reason'],
                   'publication_id': publication_id, 'manifest': asdict(manifest.artifact_ref),
                   'input_complete': True, 'plans': plans}
        marker = Decision(rid, batch.task_id, 'note', strategy_id, 'Closed independent route design context',
                          {'type': 'route_design.v1', 'batch_id': batch_id, 'outcome': outcome})
        store.append(marker, rid)
        if data['request']['tool_versions'].get('route_progress') == '1':
            from .exploration import record_route_decision
            record_route_decision(store, batch.task_id, strategy_id, result, marker.id,
                                  batch.details['snapshot']['id'])
        return marker


def complete_design_round(store, batch_id):
    batch = _batch(store, batch_id)
    with WorkflowJournal(store, batch.task_id).lock():
        old = _completion(store, batch_id)
        if old is not None:
            return old
        require(_next(store, batch) is None, 'Every route must close before design_complete')
        outcomes = {sid: _outcome(store, batch, sid) for sid in batch.details['order']}
        plans = [{'strategy_id': sid, 'publication_id': row['publication_id'], **plan}
                 for sid, row in outcomes.items() for plan in row['plans']]
        rid = _id('design-complete', batch_id)
        marker = Decision(rid, batch.task_id, 'note', batch.task_id, 'All route designs complete; budget gate remains unchanged',
            {'type': 'design_complete.v1', 'batch_id': batch_id, 'round_id': batch.details['round_id'],
             'snapshot_id': batch.details['snapshot']['id'], 'outcomes': outcomes, 'plans': plans})
        store.append(marker, rid)
        return marker


def require_design_complete(store, publication_id, round_id):
    publication = store.get(publication_id)
    raw = json.loads(store.artifacts.read(ArtifactRef(**publication.details['manifest'])))
    environment = raw['request']['environment']
    if raw['request']['schema_version'] == 1:
        batches = [row for row in store.history(publication.task_id) if type(row) is Decision
                   and row.details.get('type') == 'design_round.v1']
        require(not batches, 'A task using frozen route design cannot accept a new legacy publication')
        return None
    batch = _batch(store, environment['design_batch_id'])
    complete = _completion(store, batch.id)
    require(batch.task_id == publication.task_id and round_id == batch.details['round_id']
            and environment['design_round_id'] == round_id, 'Publication belongs to another design round')
    require(complete is not None, 'design_complete is required before batch execution')
    require(any(row['publication_id'] == publication_id for row in complete.details['outcomes'].values()),
            'Publication was not included in design_complete')
    return complete.id


def ready_design_jobs(store, state):
    accepted = {item['plan_id'] for rid in state['intakes']
                for item in store.get(rid).details['result']['items']
                if item['status'] == 'accepted' and 'plan_id' in item}
    eligible = set()
    records = store.history(state['task_id'])
    for job in state['jobs'].values():
        if job['state'] not in ('queued', 'active', 'evaluating', 'blocked'):
            continue
        protection = exploration_status(records, job['strategy_id'])
        if job['state'] == 'queued' and protection['state'] in ('stopped', 'paused', 'deferred', 'needs_research', 'awaiting_conditions', 'inapplicable'):
            continue
        require_job_design(store, job)
        batch = _get_optional(store, _id('design-batch', job['task_id'], job['round_id']))
        if batch is None:
            eligible.add(job['id'])
            continue
        complete = _completion(store, batch.id)
        required = {plan['record_id'] for plan in complete.details['plans']}
        if required <= accepted:
            eligible.add(job['id'])
    return eligible


def require_job_design(store, job):
    publication_id = job.get('publication_id')
    if publication_id is not None:
        manifest = json.loads(store.artifacts.read(ArtifactRef(**store.get(publication_id).details['manifest'])))
        if manifest['request']['schema_version'] == 2:
            require_design_complete(store, publication_id, job['round_id'])
        else:
            require(_get_optional(store, _id('design-batch', job['task_id'], job['round_id'])) is None,
                    'Legacy job cannot enter a frozen batch')
        if (manifest['request']['environment'].get('preparation_id') is not None
                or job['contract']['context']['protocol'].get('execution_mode') == 'candidate.v1') and job.get('state', 'queued') == 'queued':
            from .offline_preparation import require_prepared_implementations
            data, result = _publication(store, store.get(publication_id))
            mapping = next(row for row in store.get(publication_id).details['plans'] if row['record_id'] == job['plan_id'])
            variant = job['variant']
            if job.get('repair_of') is not None:
                raise ValueError('Prepared repairs require a new design round and same-source CPU checks')
            require_prepared_implementations(store, data, result, mapping['proposal_id'], [variant], partial=True)
    else:
        batch = _get_optional(store, _id('design-batch', job['task_id'], job['round_id']))
        require(batch is None, 'Unbound job cannot enter a frozen design batch')
