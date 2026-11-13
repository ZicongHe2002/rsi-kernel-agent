"""Prepare a new analysis round from explicit frozen Memory and parent policy."""
from dataclasses import asdict

from ..associations import MemoryWriter
from ..contracts import (ArtifactRef, Candidate, Decision, EvaluationContext, ParentCandidate,
                         Plan, Strategy)
from ..memory import MemoryReader
from ..profiler.inputs import AnalysisRequest, capture_inputs, load_manifest
from ..profiler.outputs import require
from .intake import _digest, _get_optional, _id


def _source_ref(store, digest):
    # A candidate already stores the verified content address. Recover length
    # from that exact blob, then verify both length and hash through ArtifactStore.
    path = store.artifacts.path_for(ArtifactRef(digest, 0))
    ref = ArtifactRef(digest, path.stat().st_size)
    store.artifacts.verify(ref)
    return asdict(ref)


def _prepared(store, details):
    manifest = load_manifest(store, ArtifactRef(**details['manifest']))
    request = manifest.to_dict()['request']
    view = MemoryReader(store).open_snapshot(request['strategies'][0]['snapshot_id'])
    return {'manifest': manifest, 'request': request, 'snapshot': view.snapshot,
            'cards': view.status_cards(details['context_id']),
            'histories': view.histories(tuple(details['strategies']))}


async def prepare_round(store, knowledge, task_id, context_id, strategies, round_id, knowledge_refs,
                        *, parents=None, parent_policy='branch_best'):
    from .journal import WorkflowJournal
    with WorkflowJournal(store, task_id).lock():
        return await _prepare_round(store, knowledge, task_id, context_id, strategies, round_id, knowledge_refs,
                                    parents=parents, parent_policy=parent_policy)


async def _prepare_round(store, knowledge, task_id, context_id, strategies, round_id, knowledge_refs,
                         *, parents=None, parent_policy='branch_best'):
    """Pin all cards and full histories, select exact parents, capture 2C inputs.

    branch_best chooses the frozen branch best independently for each strategy;
    without a branch best it uses the existing explicit parent selection.
    explicit requires a ParentCandidate ID for every selected strategy. Neither
    policy lets model output select a winner or mutate existing records.
    """
    require(parent_policy in ('branch_best', 'explicit'), 'Unknown parent selection policy')
    require(type(strategies) in (tuple, list) and bool(strategies)
            and all(type(s) is str for s in strategies) and len(set(strategies)) == len(strategies),
            'Select distinct participating strategies')
    require(parents is None or type(parents) is dict, 'Parents must be a mapping')
    require(parent_policy != 'explicit' or parents is not None and set(parents) == set(strategies),
            'Explicit policy requires every participating parent')
    require(parent_policy != 'branch_best' or parents is None, 'Branch-best policy does not accept parent overrides')
    context = store.get(context_id)
    require(type(context) is EvaluationContext and context.task_id == task_id, 'Context belongs to another task')
    for sid in strategies:
        strategy = store.get(sid)
        require(type(strategy) is Strategy and strategy.task_id == task_id, 'Strategy belongs to another task')
    request_digest = _digest({'task_id': task_id, 'context_id': context_id, 'strategies': list(strategies),
                             'round_id': round_id, 'knowledge': knowledge_refs,
                             'parents': parents, 'parent_policy': parent_policy})
    rid = _id('wf-round', task_id, round_id)
    old = _get_optional(store, rid)
    if old is not None:
        require(old.details.get('type') == 'workflow_round.v1' and old.details['request_digest'] == request_digest,
                'Round ID reused with different preparation inputs')
        return _prepared(store, old.details)
    # This idempotent first snapshot fixes policy decisions even if preparation
    # is interrupted before its final publication marker.
    input_snapshot = store.create_snapshot(task_id, rid + ':input')
    initial = MemoryReader(store).open_snapshot(input_snapshot['id'])
    initial_cards = {card['strategy_id']: card for card in initial.status_cards(context_id)}
    rows = initial.histories(tuple(strategies))
    records = {row['payload']['id']: row for history in rows.values() for row in history['records']}
    bindings = []
    for sid in strategies:
        card = initial_cards[sid]
        chosen_best = card['branch_best'] if parent_policy == 'branch_best' else None
        if chosen_best is not None:
            candidate = store.get(chosen_best['candidate_id'])
            parent = ParentCandidate(_id('wf-parent', task_id, sid, candidate.id), task_id,
                                     candidate.identity, candidate.id)
            store.append(parent, parent.id)
        else:
            selected_id = parents[sid] if parent_policy == 'explicit' else (
                card['current_parent_candidate']['parent_id'] if card['current_parent_candidate'] else None)
            require(selected_id is not None, 'No branch best or explicit current parent for ' + sid)
            parent = store.get(selected_id)
        require(type(parent) is ParentCandidate and parent.task_id == task_id, 'Parent belongs to another task')
        if parent.candidate_id is not None:
            candidate = store.get(parent.candidate_id)
            plan = store.get(candidate.plan_id)
            require(type(candidate) is Candidate and type(plan) is Plan and plan.strategy_id == sid,
                    'Parent candidate belongs to another strategy')
        MemoryWriter(store).select_parent(sid, context_id, parent.id,
            'Deterministic round parent policy: ' + parent_policy, rid + ':parent:' + sid)
        run_id, benchmarks, profiles = None, [], []
        if chosen_best is not None:
            run_id, benchmarks = chosen_best['run_id'], [chosen_best['evaluation_id']]
        elif parent.candidate_id is not None:
            run_ids = {r['payload']['id'] for r in records.values() if r['kind'] == 'Run'
                       and r['payload']['candidate_id'] == parent.candidate_id and r['payload']['context_id'] == context_id}
            evaluations = [r['payload'] for r in records.values() if r['kind'] == 'EvaluationBundle'
                           and r['payload']['run_id'] in run_ids and r['payload']['confirmed']]
            if evaluations:
                selected = min(evaluations, key=lambda e: (e['latency_ms'], e['id']))
                run_id, benchmarks = selected['run_id'], [selected['id']]
        if run_id is not None:
            profiles = sorted(r['payload']['id'] for r in records.values()
                              if r['kind'] == 'ProfileBundle' and r['payload']['run_id'] == run_id)
        bindings.append({'strategy_id': sid, 'parent_id': parent.id, 'context_id': context_id,
                         'snapshot_id': '', 'run_id': run_id,
                         'source_artifact': _source_ref(store, parent.identity.source_digest),
                         'config_artifact': _source_ref(store, parent.identity.config_digest),
                         'profile_ids': profiles, 'benchmark_ids': benchmarks})
    snapshot = store.create_snapshot(task_id, rid + ':selected')
    for binding in bindings:
        binding['snapshot_id'] = snapshot['id']
    request = AnalysisRequest.from_dict({'schema_version': 1, 'request_id': _id('wf-analysis', task_id, round_id),
        'task_id': task_id, 'workload_id': context.workload_id, 'strategies': bindings, 'knowledge': knowledge_refs,
        'environment': {'analysis_host': 'conversation session', 'workflow_round': round_id},
        'tool_versions': {'workflow': '1', 'input_manifest': '1'}})
    manifest = await capture_inputs(store, knowledge, request)
    details = {'type': 'workflow_round.v1', 'request_digest': request_digest, 'round_id': round_id,
               'context_id': context_id, 'strategies': list(strategies), 'input_snapshot_id': input_snapshot['id'],
               'manifest': asdict(manifest.artifact_ref)}
    store.append(Decision(rid, task_id, 'note', task_id, 'Prepared frozen analysis round', details), rid)
    return _prepared(store, details)
