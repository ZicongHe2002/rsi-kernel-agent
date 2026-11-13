"""Knowledge/experiment relationships use existing append-only Decisions."""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rsi_kernel_agent.associations import MemoryWriter
from rsi_kernel_agent.contracts import (
    Candidate, CandidateIdentity, EvaluationContext, Evidence, ParentCandidate,
    Plan, Run, Strategy, Task, Workload,
)
from rsi_kernel_agent.knowledge import MethodRecord, SourceRef, method_note_id
from rsi_kernel_agent.store import ExperimentStore, StoreError, ConflictError


class AssociationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ExperimentStore(Path(self.temp.name) / 'db.sqlite', Path(self.temp.name) / 'artifacts')
        self.writer = MemoryWriter(self.store)
        identity = CandidateIdentity(*[sha256(x).hexdigest() for x in (b's', b'c', b'b')])
        for record in (
            Task('task', 'Synthetic task', '1'), Task('other', 'Other', '1'),
            Workload('work', 'task', {'shape': [2]}),
            EvaluationContext('ctx', 'task', 'work', {'cpu': 'fixture'}, {'timer': 'fixture'}, 'synthetic'),
            Strategy('A', 'task', 'A'), Strategy('B', 'task', 'B'),
            Strategy('X', 'other', 'X'),
            ParentCandidate('parent', 'task', identity), ParentCandidate('parent2', 'task', identity),
            ParentCandidate('foreign-parent', 'other', identity),
            Plan('plan-a', 'A', 'A proposal'), Plan('plan-b', 'B', 'B proposal'),
            Candidate('candidate-a', 'plan-a', 'parent', identity),
            Candidate('candidate-b', 'plan-b', 'parent2', identity),
            Run('run-a', 'candidate-a', 'ctx'), Run('run-b', 'candidate-b', 'ctx'),
            Evidence('evidence-a', 'run-a', 'source_claim', 'available', 'completed', 'hypothesis',
                     'Synthetic observation', source={'provenance': 'synthetic'}),
        ):
            self.store.append(record, record.id)
        self.method = MethodRecord(
            method_id='method-fixture', method_name='Synthetic tiling method',
            description='Source-supported fixture only',
            source_refs=(SourceRef('Sources/tiling.md', 'a' * 64, '## Tiling', 'Synthetic tiling text'),),
            source_revision={'Sources/tiling.md': 'a' * 64}, optimization_category='tiling',
            applicable_hardware=('synthetic CPU',), applicable_workloads=('matrix',),
            preconditions=('shape divisible',), implementation_requirements=('tile buffer',),
            expected_tradeoffs=('more local memory',), known_risks=('padding cost',),
            unknowns=('TPU effect unknown',), local_experiment_refs=(),
            validation_status='source_supported', mechanism='reuse',
        )

    def link(self, **overrides):
        arguments = dict(method=self.method, method_note_id=method_note_id(self.method.method_id),
                         method_note_revision='b' * 64, plan_id='plan-a', parent_id='parent',
                         reason='Try source mechanism', key='link', candidate_id='candidate-a',
                         run_id='run-a', evidence_ids=('evidence-a',))
        arguments.update(overrides)
        return self.writer.link_method(**arguments)

    def test_binding_and_link_preserve_source_without_promoting_claim(self):
        binding = self.writer.bind_plan('plan-a', 'parent', 'bind')
        decision = self.link()
        self.assertEqual(binding.details['type'], 'plan_parent.v1')
        self.assertEqual(decision.target_id, 'plan-a')
        self.assertEqual(decision.details['strategy_id'], 'A')
        self.assertEqual(decision.details['method_snapshot']['validation_status'], 'source_supported')
        self.assertEqual(decision.details['source_refs'][0]['revision'], 'a' * 64)
        self.assertEqual(decision.details['run_id'], 'run-a')
        self.assertEqual(decision.details['evidence_ids'], ['evidence-a'])
        self.assertIsNone(self.store.best('task', 'ctx'))
        self.assertEqual(self.link(), decision)
        self.assertEqual(sum(getattr(x, 'details', {}).get('type') == 'knowledge_link.v1'
                             for x in self.store.history('task')), 1)

    def test_plan_parent_must_match_existing_candidates_and_be_fixed(self):
        with self.assertRaises(StoreError):
            self.writer.bind_plan('plan-a', 'parent2', 'wrong')
        self.writer.bind_plan('plan-a', 'parent', 'right')
        with self.assertRaises(StoreError):
            self.writer.bind_plan('plan-a', 'foreign-parent', 'foreign')
        self.store.append(Plan('empty', 'A', 'no candidate yet'), 'empty')
        self.writer.bind_plan('empty', 'parent', 'empty-bind')
        with self.assertRaises(ConflictError):
            self.writer.bind_plan('empty', 'parent2', 'empty-conflict')

    def test_method_link_rejects_each_mismatched_reference_without_partial_record(self):
        self.writer.bind_plan('plan-a', 'parent', 'bind')
        before = len(self.store.history('task'))
        cases = (
            {'candidate_id': 'candidate-b'}, {'run_id': 'run-b'}, {'parent_id': 'parent2'},
            {'evidence_ids': ('missing',)}, {'candidate_id': None, 'run_id': 'run-a'},
            {'run_id': None, 'evidence_ids': ('evidence-a',)},
            {'method_note_revision': 'invalid'}, {'method_note_id': 'wrong.md'},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises((StoreError, ValueError)):
                self.link(**overrides)
        self.assertEqual(len(self.store.history('task')), before)

    def test_explicit_parent_selection_is_not_inferred_from_latest_or_best(self):
        selected = self.writer.select_parent('B', 'ctx', 'parent', 'Cross-route reuse', 'select')
        self.assertEqual(selected.target_id, 'B')
        self.assertEqual(selected.details, {'type': 'parent_selection.v1', 'strategy_id': 'B',
                                           'context_id': 'ctx', 'parent_id': 'parent'})
        self.assertEqual(self.writer.select_parent('B', 'ctx', 'parent', 'Cross-route reuse', 'select'), selected)
        with self.assertRaises(StoreError):
            self.writer.select_parent('X', 'ctx', 'parent', 'wrong task', 'bad-select')

    def test_method_payload_is_detached_and_conflicting_key_is_rejected(self):
        self.writer.bind_plan('plan-a', 'parent', 'bind')
        decision = self.link()
        self.method.source_revision['Sources/tiling.md'] = 'c' * 64
        self.assertEqual(self.store.get(decision.id).details['method_snapshot']['source_revision'],
                         {'Sources/tiling.md': 'a' * 64})
        changed = replace(self.method, source_revision={'Sources/tiling.md': 'a' * 64}, description='Different')
        with self.assertRaises(ConflictError):
            self.link(method=changed)

    def test_link_requires_explicit_plan_parent_binding(self):
        with self.assertRaises(StoreError):
            self.link()

    def test_repaired_candidate_retains_exact_parent_and_original_plan_base(self):
        original = self.store.get('candidate-a')
        self.store.append(ParentCandidate('repair-parent', 'task', original.identity, original.id), 'repair-parent')
        self.store.append(Candidate('repair', 'plan-a', 'repair-parent', original.identity, 'repair'), 'repair')
        self.writer.bind_plan('plan-a', 'parent', 'bind')
        decision = self.link(candidate_id='repair', run_id=None, evidence_ids=())
        self.assertEqual(decision.details['parent_id'], 'parent')
        self.assertEqual(decision.details['candidate_parent_id'], 'repair-parent')


if __name__ == '__main__':
    unittest.main()
