"""Validated Knowledge/Memory links using Phase 2A append-only Decisions.

No schema migration and no knowledge database: source content remains in the
knowledge site. Decisions capture the exact method revision used by a Plan.
"""
from hashlib import sha256
import re

from .contracts import Candidate, Decision, EvaluationContext, Evidence, ParentCandidate, Plan, Run, Strategy
from .knowledge import method_note_id as expected_method_note_id, method_to_dict
from .store import StoreError


def _id(prefix, value):
    if not isinstance(value, str) or not value.strip():
        raise StoreError('A nonempty stable key is required')
    return prefix + ':' + sha256(value.encode('utf-8')).hexdigest()


def plan_binding_id(plan_id):
    """One immutable parent binding identity per Plan."""
    return _id('plan-parent', plan_id)


class MemoryWriter:
    def __init__(self, store):
        self.store = store

    def _get(self, record_id, kind):
        record = self.store.get(record_id)
        if type(record) is not kind:
            raise StoreError(f'{record_id} must reference {kind.__name__}')
        return record

    def _plan(self, plan_id, parent_id):
        plan = self._get(plan_id, Plan)
        strategy = self._get(plan.strategy_id, Strategy)
        parent = self._get(parent_id, ParentCandidate)
        if parent.task_id != strategy.task_id:
            raise StoreError('Plan and ParentCandidate belong to different tasks')
        return plan, strategy, parent

    def _append(self, decision, key):
        return self.store.get(self.store.append(decision, key))

    def _descends_from(self, candidate, parent_id):
        seen = set()
        while candidate.id not in seen:
            seen.add(candidate.id)
            if candidate.parent_id == parent_id:
                return True
            parent = self._get(candidate.parent_id, ParentCandidate)
            if parent.candidate_id is None:
                return False
            candidate = self._get(parent.candidate_id, Candidate)
        raise StoreError('Cyclic candidate lineage')

    def bind_plan(self, plan_id, parent_id, key):
        """Fix one exact parent for this Plan; existing candidates must agree.

        Call before creating candidates. Later writes through the low-level 2A
        API remain possible; Memory rejects conflicting typed bindings on read.
        """
        plan, strategy, parent = self._plan(plan_id, parent_id)
        for record in self.store.history(strategy.task_id):
            if isinstance(record, Candidate) and record.plan_id == plan_id and not self._descends_from(record, parent_id):
                raise StoreError('Existing candidate disagrees with Plan parent')
        decision = Decision(plan_binding_id(plan.id), strategy.task_id, 'note', plan.id,
                            'Explicit Plan parent binding',
                            {'type': 'plan_parent.v1', 'strategy_id': strategy.id,
                             'plan_id': plan.id, 'parent_id': parent.id})
        return self._append(decision, key)

    def select_parent(self, strategy_id, context_id, parent_id, reason, key):
        """Record an explicit current parent; never infer it from any best pointer."""
        strategy = self._get(strategy_id, Strategy)
        context = self._get(context_id, EvaluationContext)
        parent = self._get(parent_id, ParentCandidate)
        if strategy.task_id != context.task_id or strategy.task_id != parent.task_id:
            raise StoreError('Parent selection references different tasks')
        decision = Decision(_id('parent-select', key), strategy.task_id, 'note', strategy.id, reason,
                            {'type': 'parent_selection.v1', 'strategy_id': strategy.id,
                             'context_id': context.id, 'parent_id': parent.id})
        return self._append(decision, key)

    def link_method(self, method, method_note_id, method_note_revision, plan_id, parent_id,
                    reason, key, candidate_id=None, run_id=None, evidence_ids=()):
        """Append exact source/method provenance and validated experiment references.

        The caller obtains the method and its note revision through KnowledgeClient.
        Source text is reference data: linking it neither measures nor promotes it.
        """
        payload = method_to_dict(method)
        if method_note_id != expected_method_note_id(method.method_id):
            raise StoreError('Method note ID does not match the deterministic method location')
        if not isinstance(method_note_revision, str) or not re.fullmatch(r'[0-9a-f]{64}', method_note_revision):
            raise StoreError('Method note revision must be a SHA-256 identifier')
        plan, strategy, parent = self._plan(plan_id, parent_id)
        binding = self._get(plan_binding_id(plan.id), Decision)
        if (binding.kind != 'note' or binding.target_id != plan.id or binding.details != {
                'type': 'plan_parent.v1', 'strategy_id': strategy.id, 'plan_id': plan.id, 'parent_id': parent.id}):
            raise StoreError('Method link requires the exact explicit Plan parent binding')
        candidate_parent_id = None
        if candidate_id is not None:
            candidate = self._get(candidate_id, Candidate)
            if candidate.plan_id != plan_id or not self._descends_from(candidate, parent_id):
                raise StoreError('Candidate does not match the Plan and parent')
            candidate_parent_id = candidate.parent_id
        if run_id is not None:
            if candidate_id is None or self._get(run_id, Run).candidate_id != candidate_id:
                raise StoreError('Run must belong to the specified Candidate')
        if not isinstance(evidence_ids, (tuple, list)) or len(set(evidence_ids)) != len(evidence_ids):
            raise StoreError('Evidence references must be a sequence of distinct IDs')
        for evidence_id in evidence_ids:
            if run_id is None or self._get(evidence_id, Evidence).run_id != run_id:
                raise StoreError('Evidence must belong to the specified Run')
        details = {'type': 'knowledge_link.v1', 'strategy_id': strategy.id,
                   'plan_id': plan.id, 'parent_id': parent.id,
                   'candidate_id': candidate_id, 'candidate_parent_id': candidate_parent_id,
                   'run_id': run_id, 'evidence_ids': list(evidence_ids),
                   'method_id': method.method_id, 'method_note_id': method_note_id,
                   'method_note_revision': method_note_revision,
                   'source_refs': payload['source_refs'], 'method_snapshot': payload}
        return self._append(Decision(_id('knowledge-link', key), strategy.task_id, 'note', plan.id, reason, details), key)
