"""Consume verified 2C completion markers and retain each Plan's intake outcome."""
from hashlib import sha256
import json
import re

from ..associations import plan_binding_id
from ..contracts import AnalysisBundle, ArtifactRef, Decision, Plan, canonical_json, record_to_dict
from ..profiler.analyzer import Analyzer
from ..profiler.inputs import load_manifest
from ..profiler.outputs import require
from ..store import StoreError


def _digest(value):
    return sha256(canonical_json(value).encode('utf-8')).hexdigest()


def _id(prefix, *parts):
    return prefix + ':' + _digest(list(parts))[:40]


def _get_optional(store, rid):
    try:
        return store.get(rid)
    except StoreError:
        return None


def fixed_contract(manifest, strategy_id):
    from .execution import policy_for
    data = manifest.to_dict() if hasattr(manifest, 'to_dict') else manifest
    return {'schema_version': 1, 'task': data['task'], 'workload': data['workload'],
            'context': data['strategies'][strategy_id]['context'],
            'policy': policy_for(data['strategies'][strategy_id]['context']['provenance'])}


def validate_variant(store, variant, label=None, *, provenance='synthetic'):
    from .execution import validate_variant as validate_execution_variant
    canonical_json(variant)
    checked = validate_execution_variant(store, variant, provenance=provenance)
    require(label is None or checked['label'] == label, 'Variant label differs from published Plan')
    return checked


def _publication(store, marker):
    details = marker.details
    require(set(details) == {'type', 'manifest', 'result', 'bundles', 'plans'}, 'Invalid publication fields')
    manifest = load_manifest(store, ArtifactRef(**details['manifest']))
    result = json.loads(store.artifacts.read(ArtifactRef(**details['result'])))
    require(manifest.to_dict()['request']['task_id'] == marker.task_id == marker.target_id,
            'Publication belongs to another Task')
    replay = Analyzer(manifest, artifacts=store.artifacts, skills=result.get('skill_captures', {}),
                      configuration=result['configuration'], **result['limits'])
    for response in result['exchanges']:
        replay.advance(response)
    supplied = {k: v for k, v in result.items() if k not in ('model_label', 'model_transport')}
    require(replay.result is not None and canonical_json(replay.result) == canonical_json(supplied)
            and result['review_enabled'], 'Published result differs from validated Analyzer replay')
    require(re.fullmatch(r'analysis:[0-9a-f]{24}:complete', marker.id), 'Unknown publication marker identity')
    prefix = marker.id.removesuffix(':complete')
    data = manifest.to_dict()
    expected_bundles = []
    for sid, binding in data['strategies'].items():
        if binding['run'] is None:
            continue
        ids = sorted({eid for b in binding['profiles'] + binding['benchmarks'] for eid in b['evidence_ids']})
        expected = AnalysisBundle(prefix + ':' + sha256(sid.encode()).hexdigest()[:24], binding['run']['id'],
            tuple(ids), tuple(b['id'] for b in binding['profiles']), 'hypothesis',
            'Exploration plans; immutable report SHA256 ' + details['result']['digest'])
        require(store.get(expected.id) == expected, 'Published AnalysisBundle differs from exact Run binding')
        expected_bundles.append(record_to_dict(expected))
    require(canonical_json(details['bundles']) == canonical_json(expected_bundles), 'Publication Bundle coverage differs')
    expected_mapping = [{'proposal_id': p['id'], 'record_id': prefix + ':plan:' + sha256(p['id'].encode()).hexdigest()[:16]}
                        for p in result['plan_set']['plans']]
    require(details['plans'] == expected_mapping, 'Publication Plan mapping differs')
    return data, result


async def _fresh_knowledge(knowledge, data):
    status = {}
    for mid, item in data['knowledge'].items():
        try:
            require(knowledge is not None, 'Live Knowledge freshness check is unavailable')
            require(item['availability'] == 'available', 'Pinned Knowledge method is unavailable or stale')
            await knowledge.fetch_document(item['note_id'], expected_revision=item['revision'])
            for source in item['sources']:
                require(source['availability'] == 'available', 'Pinned Knowledge source is unavailable or stale')
                await knowledge.fetch_document(source['document_id'], expected_revision=source['revision'])
            status['knowledge:' + mid] = None
        except (ValueError, KeyError, OSError) as error:
            status['knowledge:' + mid] = str(error)
    return status


async def intake_publication(store, publication_id, round_id, variant_specs, knowledge=None, *, retry_key=None):
    """Serialize intake with Controller operations on the same local Task."""
    from .journal import WorkflowJournal
    marker = store.get(publication_id)
    require(type(marker) is Decision and marker.details.get('type') == 'analysis_result.v1',
            'A completed 2C publication is required')
    with WorkflowJournal(store, marker.task_id).lock():
        return await _intake_publication(store, publication_id, round_id, variant_specs, knowledge, retry_key=retry_key)


async def _intake_publication(store, publication_id, round_id, variant_specs, knowledge=None, *, retry_key=None):
    """Persist immutable outcomes; repeating a request returns its exact result.

    A design-bound v2 publication permits three explicit retries per publication
    and round, using distinct retry keys after prerequisites recover. Each retry
    appends an outcome and reuses accepted jobs. A repeated key replays its result.
    Legacy publications retain recovery by new round ID; changed pins need new analysis.
    Invalid finite-JSON values are fingerprinted but never persisted as records.
    """
    require(type(round_id) is str and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', round_id)), 'Invalid Round ID')
    require(retry_key is None or (type(retry_key) is str
            and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', retry_key))), 'Invalid Intake retry key')
    require(type(variant_specs) is dict and all(type(k) is str for k in variant_specs), 'Variant mapping must be an object')
    marker = store.get(publication_id)
    require(type(marker) is Decision and marker.kind == 'note'
            and marker.details.get('type') == 'analysis_result.v1', 'A completed 2C publication is required')
    from .design import require_design_complete
    rid = _id('wf-intake', round_id, publication_id)
    # allow_nan is used only for a request fingerprint. Invalid values remain
    # Plan-local rejections, and are never put in canonical stored JSON.
    fingerprint = sha256(json.dumps({'publication_id': publication_id, 'round_id': round_id,
        'variants': variant_specs}, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    old = _get_optional(store, rid)
    if old is not None and old.details.get('request_digest') == fingerprint:
        require(type(old) is Decision and old.details.get('type') == 'workflow_intake.v1', 'Invalid intake marker')
        require(retry_key is None, 'Explicit Intake retry requires a design-bound v2 publication')
        return old.details['result']
    completion = require_design_complete(store, publication_id, round_id)
    if completion is not None:
        rid = _id('wf-intake-v2', round_id, publication_id, fingerprint)
        old = _get_optional(store, rid)
    retry = None
    if retry_key is not None:
        require(completion is not None, 'Explicit Intake retry requires a design-bound v2 publication')
        retry_id = _id('wf-intake-retry', round_id, publication_id, retry_key)
        saved = _get_optional(store, retry_id)
        if saved is not None:
            require(type(saved) is Decision and saved.details.get('type') == 'workflow_intake.v1'
                    and saved.details['request_digest'] == fingerprint, 'Intake retry key reused with different inputs')
            return saved.details['result']
        require(old is not None and type(old) is Decision
                and old.details.get('type') == 'workflow_intake.v1'
                and old.details['request_digest'] == fingerprint, 'Intake retry requires an existing identical request')
        retries = [record for record in store.history(marker.task_id) if type(record) is Decision
                   and record.details.get('type') == 'workflow_intake.v1'
                   and record.details['result'].get('retry') is not None
                   and record.details['result']['publication_id'] == publication_id
                   and record.details['result']['round_id'] == round_id]
        previous = next((record for record in reversed(retries)
                         if record.details['request_digest'] == fingerprint), old)
        require(any(item['status'] in ('waiting', 'rejected') for item in previous.details['result']['items']),
                'Intake retry requires a waiting or rejected Plan')
        require(len(retries) < 3, 'Intake retry limit reached for this publication and round')
        retry = {'key': retry_key, 'base_intake_id': rid, 'previous_intake_id': previous.id,
                 'attempt': len(retries) + 1}
        rid = retry_id
    elif old is not None:
        require(type(old) is Decision and old.details.get('type') == 'workflow_intake.v1'
                and old.details['request_digest'] == fingerprint, 'Intake identity reused with different inputs')
        return old.details['result']
    result = {'id': rid, 'task_id': marker.task_id, 'round_id': round_id,
              'publication_id': publication_id, 'items': []}
    if retry is not None:
        result['retry'] = retry
    try:
        prior = [r for r in store.history(marker.task_id) if type(r) is Decision
                 and r.details.get('type') == 'workflow_intake.v1'
                 and r.details['result']['publication_id'] == publication_id
                 and any(item['status'] == 'accepted' for item in r.details['result']['items'])]
        require(completion is not None or not prior, 'Publication already consumed in a previous round; use a new Analyzer publication')
        accepted = {item['plan_id']: item for record in prior for item in record.details['result']['items']
                    if item['status'] == 'accepted'}
        data, analysis = _publication(store, marker)
    except (ValueError, KeyError, TypeError, OSError) as error:
        for mapping in marker.details.get('plans', []):
            result['items'].append({'proposal_id': mapping.get('proposal_id'), 'plan_id': mapping.get('record_id'),
                'strategy_id': None, 'parent_id': None, 'context_id': None, 'status': 'rejected',
                'reason': 'Publication verification failed: ' + str(error), 'jobs': []})
    else:
        freshness = await _fresh_knowledge(knowledge, data)
        mapped = {row['proposal_id']: row['record_id'] for row in marker.details['plans']}
        for proposal in analysis['plan_set']['plans']:
            sid, pid = proposal['strategy_id'], mapped[proposal['id']]
            binding = data['strategies'][sid]
            item = {'proposal_id': proposal['id'], 'plan_id': pid, 'strategy_id': sid,
                'parent_id': proposal['parent_id'], 'context_id': binding['context']['id'],
                'status': 'rejected', 'reason': '', 'jobs': []}
            try:
                require(store.get(pid) == Plan(pid, sid, proposal['modification']), 'Published Plan differs from proposal')
                parent_binding = store.get(plan_binding_id(pid))
                require(type(parent_binding) is Decision and parent_binding.details == {
                    'type': 'plan_parent.v1', 'strategy_id': sid, 'plan_id': pid, 'parent_id': proposal['parent_id']},
                    'Published Plan has the wrong exact parent binding')
                from .execution import execution_contract
                execution_contract(store, binding['context']['id'])
                prepared = data['request']['environment'].get('preparation_id') is not None
                require(not (prepared and proposal['id'] in variant_specs and pid in variant_specs),
                        'Candidate variants have ambiguous proposal and published Plan keys')
                variants = variant_specs.get(pid, variant_specs.get(proposal['id'])) if prepared else variant_specs.get(proposal['id'])
                require(type(variants) is list and len(variants) == len(proposal['variants']), 'Variant count differs from published Plan')
                validated = [validate_variant(store, v, label, provenance=binding['context']['provenance'])
                             for v, label in zip(variants, proposal['variants'])]
                require(len({_digest(v) for v in validated}) == len(validated), 'Duplicate Candidate variant')
                if (data['request']['environment'].get('preparation_id') is not None
                        or binding['context']['protocol'].get('execution_mode') == 'candidate.v1'):
                    from .offline_preparation import require_prepared_implementations
                    require_prepared_implementations(store, data, analysis, proposal['id'], validated)
                if pid in accepted:
                    require(canonical_json(validated) == canonical_json([job['variant'] for job in accepted[pid]['jobs']]),
                            'Already accepted Plan implementations cannot change')
                    result['items'].append(accepted[pid])
                    continue
                pending = []
                cited = set(proposal['refs'])
                for observation in analysis['analysis']['observations']:
                    if observation['id'] in proposal['observation_ids']:
                        cited.update(observation['refs'])
                calculation_ids = set(proposal['calculation_ids'])
                calculation_ids.update(ref.removeprefix('calculation:') for ref in cited if ref.startswith('calculation:'))
                for calculation_id in calculation_ids:
                    cited.update(analysis['calculations'][calculation_id]['evidence_refs'])
                for ref in sorted(cited):
                    if ref.startswith('calculation:'):
                        continue
                    entry = data['references'][ref]
                    if entry.get('availability') != 'available':
                        pending.append(ref + ' is unavailable or stale')
                    if freshness.get(ref):
                        pending.append(ref + ': ' + freshness[ref])
                if pending:
                    item.update(status='waiting', reason='; '.join(pending))
                else:
                    contract = fixed_contract(data, sid)
                    parent_evaluations = [b['id'] for b in binding['benchmarks']
                        if data['references']['benchmark:' + b['id']]['availability'] == 'available']
                    jobs = [{'id': _id('wf-job', rid, pid, index), 'task_id': marker.task_id, 'round_id': round_id,
                             'strategy_id': sid, 'plan_id': pid, 'parent_id': proposal['parent_id'],
                             'context_id': binding['context']['id'], 'variant': v, 'contract': contract,
                             'contract_digest': _digest(contract),
                             'parent_evaluation_id': sorted(parent_evaluations)[0] if parent_evaluations else None,
                             'publication_id': publication_id}
                            for index, v in enumerate(validated)]
                    if data['request']['schema_version'] == 2:
                        for job in jobs:
                            job['design_batch_id'] = data['request']['environment']['design_batch_id']
                    item.update(status='accepted', reason='Verified publication, exact bindings and live references', jobs=jobs)
            except (ValueError, KeyError, TypeError, OSError) as error:
                item.update(status='rejected', reason=str(error), jobs=[])
            result['items'].append(item)
    # The complete array is one append transaction: each Plan's outcome is
    # retained, with no partial prefix that a changed live source could strand.
    store.append(Decision(rid, marker.task_id, 'note', marker.task_id, 'Completed per-Plan workflow intake',
        {'type': 'workflow_intake.v1', 'request_digest': fingerprint, 'result': result}), rid)
    return result
