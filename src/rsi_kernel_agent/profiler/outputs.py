"""Validate proposed Plans; deterministic evidence review never certifies prose."""
from hashlib import sha256
import json
import re

from ..contracts import AnalysisBundle, Decision, Plan, canonical_json, record_to_dict
from ..associations import MemoryWriter


def require(condition, message):
    if not condition:
        raise ValueError(message)


def detached(value):
    return json.loads(canonical_json(value))


def fields(value, expected):
    require(type(value) is dict and set(value) == set(expected.split()), 'Unexpected schema fields')


def texts(value, *, nonempty=True):
    require(type(value) is list and (bool(value) or not nonempty) and
            all(type(x) is str and x.strip() for x in value), 'Expected nonempty text array')


def references(refs, strategy, catalog):
    texts(refs)
    require(len(set(refs)) == len(refs), 'Duplicate references')
    for ref in refs:
        require(ref in catalog, 'Unknown reference: ' + ref)
        owner = catalog[ref].get('strategy_id')
        # Shared source material is independent of experiment ownership.
        require(owner in (None, strategy), 'Reference belongs to another strategy: ' + ref)


def _predicate(observation, catalog):
    pred = observation['predicate']
    if pred is None:
        return None
    fields(pred, 'ref path value')
    require(type(pred['path']) is list and all(type(x) in (int, str) for x in pred['path']), 'Invalid predicate path')
    require(pred['ref'] in observation['refs'], 'Predicate must reference cited input')
    ref = catalog[pred['ref']]
    if ref.get('availability') not in ('available', None):
        return None
    if ref.get('kind') == 'Evidence' and (ref.get('value',{}).get('request_status') != 'completed'
            or ref.get('value',{}).get('claim_status') != 'supported'):
        return None
    value = ref.get('value')
    try:
        for part in pred['path']:
            if isinstance(value, list):
                require(type(part) is int and 0 <= part < len(value), 'Invalid array predicate index')
            value = value[part]
    except (KeyError, TypeError, IndexError):
        return None
    if value is None or type(value) is not type(pred['value']) or value != pred['value']:
        return None
    kind = ref.get('kind')
    source = ref.get('value')
    evidence_kind = source.get('evidence_kind') if type(source) is dict else None
    if kind == 'calculation': category = 'mathematical_prediction'
    elif kind in ('knowledge', 'Knowledge', 'MethodRecord'): category = 'source_claim'
    elif kind in ('history', 'History'): category = 'historical_claim'
    elif evidence_kind == 'compiler_estimated': category = 'compiler_estimate'
    elif evidence_kind == 'model_predicted': category = 'mathematical_prediction'
    elif evidence_kind in ('trace_derived', 'derived_measurement'): category = 'derived_measurement'
    elif evidence_kind in ('historical_claim', 'source_claim'): category = evidence_kind
    else: category = 'observed_fact'
    return category, f"{pred['ref']} {canonical_json(pred['path'])} = {canonical_json(value)}"


def review_proposal(proposal, manifest, calculations, *, evidence_review=True):
    """Structural validation always; factual promotion only through exact predicates.

    Arbitrary explanatory prose cannot be machine-proven by a citation. Preserve
    it as a hypothesis even when a referenced numeric/string value is verified.
    """
    p = detached(proposal)
    fields(p, 'code_understanding observations hypotheses plans unknowns')
    strategies = manifest['strategies']
    catalog = detached(manifest['references'])
    for key, calc in calculations.items():
        catalog['calculation:' + key] = {'kind':'calculation','value':calc,'availability':'available',
                                       'strategy_id':calc.get('strategy_id')}
    require(type(p['code_understanding']) is list, 'Missing source analysis')
    seen = set()
    for code in p['code_understanding']:
        fields(code, 'strategy_id stages loops movement dependencies layout_memory synchronization edge_cases refs')
        sid = code['strategy_id']
        require(sid in strategies and sid not in seen, 'Invalid code understanding strategy')
        seen.add(sid)
        for name in ('stages','loops','movement','dependencies','layout_memory','synchronization','edge_cases'):
            texts(code[name])
        references(code['refs'], sid, catalog)
        require('source:' + sid in code['refs'], 'Code analysis must cite pinned source')
        code['status'] = 'model_interpretation_requires_validation'
    require(seen == set(strategies), 'Analyze source for every participating strategy')
    require(type(p['observations']) is list and type(p['hypotheses']) is list, 'Expected observations and hypotheses')
    review, observations, ids = [], {}, set()
    categories = {'observed_fact','derived_measurement','compiler_estimate','mathematical_prediction',
                  'source_claim','historical_claim','hypothesis','unknown'}
    for obs in p['observations']:
        fields(obs, 'id strategy_id category text refs predicate')
        require(type(obs['id']) is str and obs['id'] and obs['id'] not in ids, 'Duplicate observation ID')
        ids.add(obs['id'])
        sid = obs['strategy_id']
        require(sid in strategies and obs['category'] in categories, 'Invalid observation strategy/category')
        require(type(obs['text']) is str and obs['text'].strip(), 'Empty observation')
        references(obs['refs'], sid, catalog)
        if evidence_review and obs['category'] not in ('hypothesis','unknown'):
            verified = _predicate(obs, catalog)
            if verified:
                obs['proposed_explanation'] = obs['text']
                obs['explanation_status'] = 'hypothesis'
                obs['category'], obs['text'] = verified
            else:
                review.append({'id':obs['id'], 'action':'downgraded_to_hypothesis',
                               'reason':'Reference does not establish the proposed assertion'})
                obs['category'] = 'hypothesis'
        observations[obs['id']] = sid
    require(bool(p['hypotheses']), 'At least one explicit alternative hypothesis required')
    for hyp in p['hypotheses']:
        fields(hyp, 'id strategy_id text refs alternatives')
        require(type(hyp['id']) is str and hyp['id'] and hyp['id'] not in ids, 'Duplicate hypothesis ID')
        ids.add(hyp['id'])
        require(hyp['strategy_id'] in strategies and type(hyp['text']) is str and hyp['text'].strip(), 'Invalid hypothesis')
        references(hyp['refs'], hyp['strategy_id'], catalog)
        texts(hyp['alternatives'])
    require(type(p['plans']) is list, 'Expected Plan list')
    counts, signatures = {s:0 for s in strategies}, {s:set() for s in strategies}
    for plan in p['plans']:
        fields(plan, 'id strategy_id parent_id mechanism modification refs observation_ids calculation_ids tradeoffs risks unknowns variants validation')
        sid = plan['strategy_id']
        require(sid in strategies and plan['parent_id'] == strategies[sid]['parent_id'], 'Wrong strategy or exact parent')
        require(type(plan['id']) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', plan['id'])
                and plan['id'] not in ids, 'Invalid or duplicate Plan ID')
        ids.add(plan['id'])
        for name in ('mechanism','modification'):
            require(type(plan[name]) is str and plan[name].strip(), 'Empty Plan modification/mechanism')
        signature = re.sub(r'\W+', '', plan['mechanism'].casefold())
        change = re.sub(r'\W+', '', plan['modification'].casefold())
        require(signature not in signatures[sid] and change not in signatures[sid], 'Redundant Plan mechanism or modification')
        signatures[sid].update((signature,change)); counts[sid] += 1
        references(plan['refs'], sid, catalog)
        for name in ('tradeoffs','risks','unknowns','variants'):
            texts(plan[name])
        fields(plan['validation'], 'correctness performance')
        texts(plan['validation']['correctness']); texts(plan['validation']['performance'])
        texts(plan['observation_ids'], nonempty=False); texts(plan['calculation_ids'], nonempty=False)
        require(all(observations.get(x) == sid for x in plan['observation_ids']), 'Unbound Plan observation')
        for cid in plan['calculation_ids']:
            require(cid in calculations, 'Unknown calculation')
            references(calculations[cid]['evidence_refs'], sid, catalog)
        plan['status'] = 'proposal_requires_validation'
    require(all(n >= 2 for n in counts.values()), 'Each participating strategy needs at least two distinct Plans')
    texts(p['unknowns'])
    p['review'] = review
    p['review_enabled'] = evidence_review
    return p


def publish_result(store, manifest, result, key):
    """Publish retry-safe records then a completion marker; not a batch transaction."""
    from .inputs import load_manifest
    from ..contracts import ArtifactRef
    require(type(key) is str and bool(key.strip()), 'Publication key required')
    pinned = load_manifest(store, manifest.artifact_ref)
    verified = pinned.to_dict()
    require(result['manifest_digest'] == manifest.artifact_ref.digest, 'Result belongs to another manifest')
    from .analyzer import Analyzer
    replay = Analyzer(pinned,artifacts=store.artifacts,skills=result.get('skill_captures',{}),
                      configuration=result['configuration'],**result['limits'])
    for response in result['exchanges']:
        replay.advance(response)
    require(replay.result is not None, 'Result has no completed model exchange')
    supplied = {k:v for k,v in result.items() if k not in ('model_label','model_transport')}
    require(canonical_json(replay.result) == canonical_json(supplied), 'Result differs from deterministic exchange replay')
    # Validate the original model output again rather than accepting a caller-edited PlanSet.
    reviewed = review_proposal(result['proposal'], verified, result['calculations'], evidence_review=result['review_enabled'])
    require(canonical_json(reviewed['plans']) == canonical_json(result['plan_set']['plans']), 'PlanSet was modified after validation')
    require(result['review_enabled'], 'Unreviewed evaluation output cannot be published')
    ref = store.artifacts.put(canonical_json(result).encode('utf-8'))
    prefix = 'analysis:' + sha256(key.encode()).hexdigest()[:24]
    bundles = []
    for sid, data in verified['strategies'].items():
        run = data['run']
        if run is None: continue
        evidence_ids = sorted({e for b in data['profiles'] + data['benchmarks'] for e in b['evidence_ids']})
        bundle = AnalysisBundle(prefix + ':' + sha256(sid.encode()).hexdigest()[:24], run['id'], tuple(evidence_ids),
                                tuple(b['id'] for b in data['profiles']), 'hypothesis',
                                'Exploration plans; immutable report SHA256 ' + ref.digest)
        store.append(bundle, prefix + ':bundle:' + sid)
        bundles.append(record_to_dict(bundle))
    plan_ids = []
    for p in reviewed['plans']:
        pid = prefix + ':plan:' + sha256(p['id'].encode()).hexdigest()[:16]
        store.append(Plan(pid,p['strategy_id'],p['modification']), pid)
        MemoryWriter(store).bind_plan(pid,p['parent_id'],pid+':parent')
        plan_ids.append({'proposal_id':p['id'],'record_id':pid})
    decision = Decision(prefix + ':complete',verified['request']['task_id'],'note',verified['request']['task_id'],
                        'Completed Phase 2C analysis publication',
                        {'type':'analysis_result.v1','manifest':record_to_dict(manifest.artifact_ref),
                         'result':record_to_dict(ref),'bundles':bundles,'plans':plan_ids})
    store.append(decision,prefix+':complete')
    return decision
