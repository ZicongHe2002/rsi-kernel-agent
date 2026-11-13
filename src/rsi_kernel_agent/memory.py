"""Read-only, detached Memory views of existing Phase 2A task snapshots.

The caller explicitly creates a snapshot before opening it. Status cards and
selected histories share that immutable version; no read invokes live best
queries, changes decisions, or silently creates another checkpoint.
"""
from dataclasses import dataclass
import json
import re

from .contracts import canonical_json, record_to_dict
from .knowledge import KnowledgeError, method_from_dict, method_note_id, method_to_dict


class MemoryError(ValueError):
    """Invalid request for a Memory read view."""


class MemoryIncomplete(MemoryError):
    """A snapshot cannot prove complete, consistent record coverage."""


def _require(condition, message):
    if not condition:
        raise MemoryIncomplete(message)


def _pagination(offset, limit, total):
    if type(offset) is not int or type(limit) is not int or offset < 0 or limit < 1 or offset > total:
        raise MemoryError('Pagination requires a valid offset and positive integer limit')
    return min(offset + limit, total)


class MemoryReader:
    def __init__(self, store):
        self.store = store

    def open_snapshot(self, snapshot_id, page_size=100):
        if type(page_size) is not int or page_size < 1:
            raise MemoryError('page_size must be a positive integer')
        records, seen, offset = [], set(), 0
        metadata = projections = None
        while True:
            page = self.store.snapshot_page(snapshot_id, offset=offset, limit=page_size)
            try:
                current = page['snapshot']
                total = page['total']
                _require(type(total) is int and total > 0, 'Invalid snapshot record total')
                _require(current['id'] == snapshot_id and current['record_count'] == total, 'Snapshot identity/count mismatch')
                _require(page['offset'] == offset, 'Snapshot page offset is not contiguous')
                if metadata is None:
                    metadata = json.loads(canonical_json(current))
                    projections = json.loads(canonical_json(page['projections']))
                _require(current == metadata and page['projections'] == projections, 'Snapshot changed across pages')
                items = page['records']
                _require(type(items) is list and 0 < len(items) <= page_size, 'Empty or oversized snapshot page')
                end = offset + len(items)
                _require(end <= total, 'Snapshot exceeds declared coverage')
                _require(page['next_offset'] == (end if end < total else None), 'Snapshot page skipped or terminated early')
                _require(page['complete'] is (offset == 0 and end == total), 'Incorrect snapshot completeness flag')
                for record in items:
                    _require(record.id not in seen, 'Duplicate record in snapshot coverage')
                    seen.add(record.id)
                    records.append({'kind': type(record).__name__, 'payload': record_to_dict(record)})
            except (KeyError, TypeError, AttributeError) as error:
                raise MemoryIncomplete('Malformed snapshot page') from error
            offset = end
            if end == total:
                break
        body = {'snapshot': metadata, 'records': records, 'projections': projections}
        _Index(body)  # Validate ownership and references before promising coverage.
        return MemoryView(canonical_json(body))


class _Index:
    def __init__(self, body):
        self.body = body
        self.records = body['records']
        self.by_id = {r['payload']['id']: r for r in self.records}
        self.order = {r['payload']['id']: i for i, r in enumerate(self.records)}
        self.owners = {}
        self.refs = {}
        self.task_id = body['snapshot']['task_id']
        self.expect(self.task_id, 'Task')
        for row in self.records:
            p = row['payload']
            references = []
            for field in ('task_id', 'workload_id', 'strategy_id', 'plan_id', 'parent_id', 'candidate_id', 'run_id', 'target_id'):
                if p.get(field) is not None:
                    references.append(p[field])
            for field in ('evidence_ids', 'profile_ids', 'input_ids'):
                references.extend(p.get(field, ()))
            for ref in references:
                self.expect(ref)
            _require(p.get('task_id', self.task_id) == self.task_id, 'Cross-task record in snapshot')
            self.refs[p['id']] = references
        for row in self.records:
            self.owner(row['payload']['id'])
        counts = {r['payload']['id']: 0 for r in self.records if r['kind'] == 'Strategy'}
        for owner in self.owners.values():
            if owner is not None:
                counts[owner] += 1
        _require(counts == body['snapshot']['strategy_counts'], 'Strategy history counts are incomplete')
        self._typed_decisions()
        self._projections()

    def expect(self, record_id, kind=None):
        _require(isinstance(record_id, str) and record_id in self.by_id, f'Missing history dependency: {record_id}')
        row = self.by_id[record_id]
        _require(kind is None or row['kind'] == kind, f'Wrong history dependency type: {record_id}')
        return row['payload']

    def owner(self, record_id, visiting=None):
        if record_id in self.owners:
            return self.owners[record_id]
        visiting = set() if visiting is None else visiting
        _require(record_id not in visiting, 'Cyclic record ownership')
        visiting.add(record_id)
        row = self.by_id[record_id]
        kind, p = row['kind'], row['payload']
        if kind == 'Strategy':
            owner = record_id
        elif kind in ('Task', 'Workload', 'EvaluationContext', 'ParentCandidate'):
            owner = None
        else:
            field = {'Plan': 'strategy_id', 'Candidate': 'plan_id', 'Run': 'candidate_id', 'Decision': 'target_id'}.get(kind, 'run_id')
            owner = self.owner(p[field], visiting)
        visiting.remove(record_id)
        self.owners[record_id] = owner
        return owner

    def _typed_decisions(self):
        bindings = {}
        for row in self.records:
            if row['kind'] != 'Decision' or row['payload']['kind'] != 'note':
                continue
            p = row['payload']
            d = p['details']
            kind = d.get('type')
            if kind not in ('parent_selection.v1', 'plan_parent.v1', 'knowledge_link.v1'):
                continue
            required = {'type', 'strategy_id', 'parent_id'}
            if kind == 'parent_selection.v1':
                required.add('context_id')
            else:
                required.add('plan_id')
            if kind == 'knowledge_link.v1':
                required.update({'candidate_id', 'candidate_parent_id', 'run_id', 'evidence_ids',
                                 'method_id', 'method_note_id', 'method_note_revision', 'source_refs', 'method_snapshot'})
            _require(set(d) == required, 'Recognized association fields do not match version 1')
            strategy = d.get('strategy_id')
            self.expect(strategy, 'Strategy')
            parent = self.expect(d.get('parent_id'), 'ParentCandidate')
            references = [strategy, parent['id']]
            if parent['candidate_id'] is not None:
                original = self.expect(parent['candidate_id'], 'Candidate')
                _require(original['identity'] == parent['identity'], 'Parent identity mismatch')
            if kind == 'parent_selection.v1':
                self.expect(d.get('context_id'), 'EvaluationContext')
                _require(p['target_id'] == strategy, 'Parent selection target differs from strategy')
                references.append(d['context_id'])
            else:
                plan = self.expect(d.get('plan_id'), 'Plan')
                _require(plan['strategy_id'] == strategy and p['target_id'] == plan['id'], 'Association belongs to another Plan/strategy')
                references.append(plan['id'])
                if kind == 'plan_parent.v1':
                    _require(plan['id'] not in bindings or bindings[plan['id']] == parent['id'], 'Conflicting Plan parents')
                    bindings[plan['id']] = parent['id']
                    for candidate in self.records:
                        if candidate['kind'] == 'Candidate' and candidate['payload']['plan_id'] == plan['id']:
                            _require(self.descends_from(candidate['payload']['id'], parent['id']), 'Plan/Candidate lineage differs')
                else:
                    _require(bindings.get(plan['id']) == parent['id'], 'Knowledge link has no matching prior Plan parent binding')
                    try:
                        method = method_from_dict(d['method_snapshot'])
                        method_payload = method_to_dict(method)
                    except (KnowledgeError, ValueError) as error:
                        raise MemoryIncomplete('Invalid captured Knowledge method') from error
                    _require(d['method_id'] == method.method_id, 'Method identity differs from captured method')
                    _require(d['method_note_id'] == method_note_id(method.method_id), 'Method note path differs from captured method')
                    _require(type(d['method_note_revision']) is str and bool(re.fullmatch(r'[0-9a-f]{64}', d['method_note_revision'])), 'Method note revision must be SHA-256')
                    _require(d['source_refs'] == method_payload['source_refs'], 'Source references differ from captured method')
                    if d.get('candidate_id') is not None:
                        candidate = self.expect(d['candidate_id'], 'Candidate')
                        _require(candidate['plan_id'] == plan['id'] and self.descends_from(candidate['id'], parent['id']), 'Knowledge Candidate association differs')
                        _require(d.get('candidate_parent_id') == candidate['parent_id'], 'Exact Candidate parent association differs')
                        references.append(candidate['id'])
                        references.append(candidate['parent_id'])
                    else:
                        _require(d.get('candidate_parent_id') is None, 'Candidate parent supplied without Candidate')
                    if d.get('run_id') is not None:
                        run = self.expect(d['run_id'], 'Run')
                        _require(run['candidate_id'] == d.get('candidate_id'), 'Knowledge Run association differs')
                        references.append(run['id'])
                    _require(isinstance(d.get('evidence_ids'), list), 'Evidence association must be a list')
                    for eid in d['evidence_ids']:
                        evidence = self.expect(eid, 'Evidence')
                        _require(evidence['run_id'] == d.get('run_id'), 'Knowledge Evidence association differs')
                        references.append(eid)
            self.refs[p['id']].extend(references)

    def descends_from(self, candidate_id, parent_id):
        visited = set()
        while candidate_id is not None:
            _require(candidate_id not in visited, 'Cyclic Candidate lineage')
            visited.add(candidate_id)
            candidate = self.expect(candidate_id, 'Candidate')
            if candidate['parent_id'] == parent_id:
                return True
            parent = self.expect(candidate['parent_id'], 'ParentCandidate')
            candidate_id = parent['candidate_id']
        return False

    def _projections(self):
        for row in self.body['projections']['best']:
            evaluation = self.expect(row['evaluation_id'], 'EvaluationBundle')
            run = self.expect(evaluation['run_id'], 'Run')
            self.expect(row['context_id'], 'EvaluationContext')
            _require(row['task_id'] == self.task_id and row['context_id'] == run['context_id'], 'Best projection context differs')
            _require(row['latency_ms'] == evaluation['latency_ms'], 'Best projection timing differs')
            _require(row['scope'] in ('task', 'strategy:' + self.owner(run['id'])), 'Best projection strategy differs')
        for row in self.body['projections']['frontier']:
            self.expect(row['candidate_id'], 'Candidate')
            self.expect(row['context_id'], 'EvaluationContext')
            _require(row['task_id'] == self.task_id, 'Frontier task differs')

    def strategy_ids(self):
        return [r['payload']['id'] for r in self.records if r['kind'] == 'Strategy']

    def selected(self, strategy_ids):
        ids = self.strategy_ids() if strategy_ids is None else list(strategy_ids)
        if len(set(ids)) != len(ids):
            raise MemoryError('Participating strategies must be unique')
        for sid in ids:
            if sid not in self.strategy_ids():
                raise MemoryError(f'Unknown strategy: {sid}')
        return ids

    def context(self, context_id):
        if context_id not in self.by_id or self.by_id[context_id]['kind'] != 'EvaluationContext':
            raise MemoryError(f'Unknown comparison context: {context_id}')
        return self.by_id[context_id]['payload']

    def states(self):
        states = {r['payload']['id']: {'execution_status': 'not_run', 'scheduling_status': 'ready', 'event_id': None}
                  for r in self.records if r['kind'] == 'Run'}
        for row in self.records:
            if row['kind'] == 'RunEvent':
                p = row['payload']
                states[p['run_id']] = {key: p[key] for key in ('execution_status', 'scheduling_status')}
                states[p['run_id']]['event_id'] = p['id']
        return states

    def context_of(self, record_id):
        row = self.by_id[record_id]
        p = row['payload']
        if row['kind'] == 'EvaluationContext':
            return p['id']
        if row['kind'] == 'Run':
            return p['context_id']
        if 'run_id' in p:
            return self.expect(p['run_id'], 'Run')['context_id']
        if row['kind'] == 'Decision':
            return p['details'].get('context_id') or self.context_of(p['target_id'])
        return None

    def history(self, strategy_id):
        self.selected((strategy_id,))
        owned = {rid for rid, owner in self.owners.items() if owner == strategy_id}
        included = set(owned) | {self.task_id}
        # Workload/context records are shared task contracts, not other routes.
        included.update(r['payload']['id'] for r in self.records if r['kind'] in ('Workload', 'EvaluationContext'))
        pending = list(included)
        while pending:
            rid = pending.pop()
            related = list(self.refs[rid])
            row = self.by_id[rid]
            if row['kind'] == 'Candidate' and rid not in owned:
                related.extend(r['payload']['id'] for r in self.records
                               if r['kind'] == 'Run' and r['payload']['candidate_id'] == rid)
            if row['kind'] == 'Run':
                related.extend(r['payload']['id'] for r in self.records if r['payload'].get('run_id') == rid)
            if row['kind'] not in ('Task', 'Strategy', 'Workload', 'EvaluationContext'):
                related.extend(r['payload']['id'] for r in self.records
                               if r['kind'] == 'Decision' and r['payload']['target_id'] == rid)
            for dependency in related:
                if dependency not in included:
                    included.add(dependency)
                    pending.append(dependency)
        records = [r for r in self.records if r['payload']['id'] in included]
        return records, [r['payload']['id'] for r in records if r['payload']['id'] in owned], [r['payload']['id'] for r in records if r['payload']['id'] not in owned]


@dataclass(frozen=True)
class MemoryView:
    """A value view. All returned data is detached from this canonical snapshot."""
    _body: str

    @property
    def snapshot(self):
        return json.loads(self._body)['snapshot']

    def history_page(self, strategy_id, offset=0, limit=100):
        index = _Index(json.loads(self._body))
        records, owned, dependencies = index.history(strategy_id)
        total = len(records)
        end = _pagination(offset, limit, total)
        page_records = records[offset:end]
        page_ids = {record['payload']['id'] for record in page_records}
        return {'snapshot': index.body['snapshot'], 'strategy_id': strategy_id, 'records': page_records,
                'total': total, 'owned_total': len(owned), 'dependency_total': len(dependencies),
                'owned_ids': [rid for rid in owned if rid in page_ids],
                'dependency_ids': [rid for rid in dependencies if rid in page_ids],
                'offset': offset, 'next_offset': end if end < total else None,
                'complete': offset == 0 and end == total}

    def histories(self, strategy_ids):
        index = _Index(json.loads(self._body))
        ids = index.selected(strategy_ids)
        return {sid: self.history_page(sid, limit=len(index.records)) for sid in ids}

    def search_history(self, query='', strategy_ids=None, context_id=None, kinds=None):
        if not isinstance(query, str):
            raise MemoryError('History query must be text')
        index = _Index(json.loads(self._body))
        ids = index.selected(strategy_ids)
        if context_id is not None:
            index.context(context_id)
        records = [row for row in index.records
                   if index.owner(row['payload']['id']) in ids
                   and (kinds is None or row['kind'] in kinds)
                   and (context_id is None or index.context_of(row['payload']['id']) == context_id)
                   and query.casefold() in canonical_json(row).casefold()]
        return {'snapshot': index.body['snapshot'], 'records': records, 'total': len(records),
                'filtered': True, 'complete_history': False, 'strategy_ids': ids}

    def status_cards(self, context_id, recent_plan_limit=5):
        if type(recent_plan_limit) is not int or recent_plan_limit < 0:
            raise MemoryError('recent_plan_limit must be a nonnegative integer')
        index = _Index(json.loads(self._body))
        context = index.context(context_id)
        states = index.states()
        runs = [r['payload'] for r in index.records if r['kind'] == 'Run' and r['payload']['context_id'] == context_id]

        def best(scope):
            item = next((r for r in index.body['projections']['best'] if r['context_id'] == context_id and r['scope'] == scope), None)
            if item is None:
                return None
            evaluation = index.expect(item['evaluation_id'], 'EvaluationBundle')
            run = index.expect(evaluation['run_id'], 'Run')
            return {'candidate_id': run['candidate_id'], 'run_id': run['id'], 'evaluation_id': evaluation['id'],
                    'latency_ms': evaluation['latency_ms'], 'strategy_id': index.owner(run['id']), 'context_id': context_id}

        cards = []
        for strategy in index.strategy_ids():
            owned = [r for r in index.records if index.owner(r['payload']['id']) == strategy]
            candidates = [r['payload'] for r in owned if r['kind'] == 'Candidate']
            branch_runs = [r for r in runs if index.owner(r['id']) == strategy]
            attempts = [(index.order[r['id']], {'candidate_id': r['candidate_id'], 'run_id': r['id'], **states[r['id']]}) for r in branch_runs]
            for candidate in candidates:
                if not any(r['kind'] == 'Run' and r['payload']['candidate_id'] == candidate['id'] for r in index.records):
                    attempts.append((index.order[candidate['id']], {'candidate_id': candidate['id'], 'run_id': None,
                                     'execution_status': 'not_run', 'scheduling_status': 'ready', 'event_id': None}))
            correct = [r['payload'] for r in owned if r['kind'] == 'EvaluationBundle'
                       and r['payload']['correctness'] == 'passed' and index.context_of(r['payload']['id']) == context_id]
            last_correct = None
            if correct:
                evaluation = correct[-1]
                run = index.expect(evaluation['run_id'], 'Run')
                last_correct = {'candidate_id': run['candidate_id'], 'run_id': run['id'], 'evaluation_id': evaluation['id']}
            decisions = [r['payload'] for r in owned if r['kind'] == 'Decision']
            pause = next((d for d in reversed(decisions) if d['kind'] in ('paused', 'resumed') and d['target_id'] == strategy), None)
            selection = next((d for d in reversed(decisions) if d['kind'] == 'note'
                              and d['details'].get('type') == 'parent_selection.v1'
                              and d['details']['context_id'] == context_id), None)
            current_parent = None
            if selection:
                parent = index.expect(selection['details']['parent_id'], 'ParentCandidate')
                current_parent = {'parent_id': parent['id'], 'candidate_id': parent['candidate_id'],
                                  'identity': parent['identity'], 'decision_id': selection['id']}
            deferred = [{'record_id': r['id'], 'kind': 'Run', 'reason': index.expect(states[r['id']]['event_id'])['reason'],
                         'event_id': states[r['id']]['event_id']} for r in branch_runs
                        if states[r['id']]['scheduling_status'] == 'deferred']
            seen_deferred = set()
            for decision in reversed(decisions):
                if decision['kind'] != 'deferred' or decision['target_id'] in seen_deferred:
                    continue
                target = index.by_id[decision['target_id']]
                seen_deferred.add(decision['target_id'])
                target_id = target['payload']['id']
                if target['kind'] == 'Run':
                    continue  # State events are authoritative for Run scheduling.
                target_candidates = [target_id] if target['kind'] == 'Candidate' else [c['id'] for c in candidates if c['plan_id'] == target_id]
                pending_candidates = []
                for candidate_id in target_candidates:
                    candidate_runs = [r['payload'] for r in index.records
                                      if r['kind'] == 'Run' and r['payload']['candidate_id'] == candidate_id]
                    if not candidate_runs or any(states[r['id']]['execution_status'] == 'not_run' for r in candidate_runs):
                        pending_candidates.append(candidate_id)
                if pending_candidates or not target_candidates:
                    deferred.append({'record_id': target_id, 'kind': target['kind'], 'reason': decision['reason'], 'decision_id': decision['id']})
                    for _, attempt in attempts:
                        if attempt['run_id'] is None and attempt['candidate_id'] in pending_candidates:
                            attempt['scheduling_status'] = 'deferred'
            plans = [r['payload'] for r in owned if r['kind'] == 'Plan']
            cards.append({'snapshot_id': index.body['snapshot']['id'], 'snapshot_version': index.body['snapshot']['version'],
                          'strategy_id': strategy, 'context_id': context_id, 'provenance': context['provenance'],
                          'latest_attempt': max(attempts, default=(0, None), key=lambda x: x[0])[1],
                          'last_correct_candidate': last_correct, 'branch_best': best('strategy:' + strategy),
                          'task_best': best('task'),
                          'frontier': [r['candidate_id'] for r in index.body['projections']['frontier']
                                       if r['context_id'] == context_id and index.owner(r['candidate_id']) == strategy],
                          'paused': bool(pause and pause['kind'] == 'paused'), 'pause_decision': pause,
                          'deferred': deferred, 'current_parent_candidate': current_parent,
                          'recent_plans': list(reversed(plans))[:recent_plan_limit], 'recent_plans_total': len(plans),
                          'recent_plans_complete': len(plans) <= recent_plan_limit,
                          'evidence_refs': [r['payload']['id'] for r in owned if r['kind'] == 'Evidence'
                                            and index.context_of(r['payload']['id']) == context_id]})
        return cards
