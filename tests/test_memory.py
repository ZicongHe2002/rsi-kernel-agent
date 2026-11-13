"""Read-only Memory views over real Phase 2A SQLite snapshots; synthetic data."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from rsi_kernel_agent.contracts import (
    AnalysisBundle, Candidate, CandidateIdentity, Decision, EvaluationBundle,
    EvaluationContext, Evidence, ParentCandidate, Plan, ProfileBundle, Run,
    RunEvent, Strategy, Task, Workload,
)
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.knowledge import MethodRecord, SourceRef, method_note_id, method_to_dict
try:
    from rsi_kernel_agent.memory import MemoryReader, MemoryError, MemoryIncomplete
except ImportError:
    MemoryReader = None


def identity(name):
    return CandidateIdentity(*(hashlib.sha256((name + x).encode()).hexdigest()
                               for x in ('source', 'config', 'build')))


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(MemoryReader, 'Phase 2B Memory reader is not implemented')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'records.sqlite3'
        self.store = ExperimentStore(self.db, self.root / 'artifacts')
        for record in (
            Task('task', 'Synthetic infrastructure', 'v1'),
            Workload('workload', 'task', {'shape': [2, 2]}),
            EvaluationContext('context', 'task', 'workload', {'device': 'fixture'}, {'clock': 'fixture'}, 'synthetic'),
            ParentCandidate('baseline', 'task', identity('baseline')),
        ):
            self.add(record)
        for strategy in 'ABCDE':
            self.add(Strategy(strategy, 'task', 'Route ' + strategy))
            if strategy in 'AB':
                for number in '12':
                    plan = strategy + 'plan' + number
                    self.add(Plan(plan, strategy, 'Synthetic plan ' + number))
                    for variant in '12':
                        name = strategy + number + variant
                        self.add(Candidate(name, plan, 'baseline', identity(name)))

    def add(self, record):
        return self.store.append(record, 'append:' + record.id)

    def event(self, run, execution, scheduling, previous=None):
        event = RunEvent(run + ':' + execution + ':' + scheduling, run, execution, scheduling, 'synthetic reason')
        self.store.transition(event, previous, 'event:' + event.id)
        return event.id

    def evaluate(self, name, candidate, latency, correctness='passed', timing='valid', context='context'):
        run = 'run:' + name
        self.add(Run(run, candidate, context))
        started = self.event(run, 'running', 'active')
        self.event(run, 'succeeded', 'finished', started)
        artifact = self.store.artifacts.put(('synthetic timing ' + name).encode())
        evidence = 'evidence:' + name
        self.add(Evidence(evidence, run, 'measured_timing', 'available', 'completed', 'supported',
                          latency, 'ms', (artifact,), (), {'provenance': 'synthetic'}))
        self.add(ProfileBundle('profile:' + name, run, (evidence,), 'available'))
        self.add(AnalysisBundle('analysis:' + name, run, (evidence,), ('profile:' + name,), 'hypothesis', 'Unverified fixture hypothesis'))
        evaluation = 'eval:' + name
        self.add(EvaluationBundle(evaluation, run, correctness, timing, latency,
                                  correctness == 'passed' and timing == 'valid', (evidence,)))
        if correctness == 'passed' and timing == 'valid':
            self.store.consider(evaluation, 'consider:' + evaluation)
        return run

    def snapshot(self, key='snapshot'):
        return self.store.create_snapshot('task', key)

    def view(self, key='snapshot', page_size=3):
        return MemoryReader(self.store).open_snapshot(self.snapshot(key)['id'], page_size=page_size)

    def cards(self, view):
        return {card['strategy_id']: card for card in view.status_cards('context')}

    def method_details(self, candidate_id='A11', candidate_parent_id='baseline'):
        method = MethodRecord(
            method_id='method1', method_name='Synthetic tiling method', description='Captured source claim',
            source_refs=(SourceRef('Sources/tiling.md', '2' * 64, 'heading:Tiling', 'Synthetic source text'),),
            source_revision={'Sources/tiling.md': '2' * 64}, optimization_category='tiling',
            applicable_hardware=('synthetic CPU',), applicable_workloads=('matrix',), preconditions=(),
            implementation_requirements=(), expected_tradeoffs=(), known_risks=(),
            unknowns=('Local performance has not been established',), local_experiment_refs=(),
            validation_status='source_supported', mechanism='reuse',
        )
        payload = method_to_dict(method)
        return {'type': 'knowledge_link.v1', 'strategy_id': 'A', 'plan_id': 'Aplan1', 'parent_id': 'baseline',
                'candidate_id': candidate_id, 'candidate_parent_id': candidate_parent_id, 'run_id': None,
                'evidence_ids': [], 'method_id': method.method_id, 'method_note_id': method_note_id(method.method_id),
                'method_note_revision': '1' * 64, 'source_refs': payload['source_refs'], 'method_snapshot': payload}

    def test_all_cards_keep_latest_correct_best_frontier_and_parent_separate(self):
        self.evaluate('A-fast', 'A11', 10)
        self.evaluate('B-fast', 'B11', 8)
        self.evaluate('A-correct-bad-timer', 'A12', 12, timing='invalid')
        self.add(Run('run:A-failed', 'A21', 'context'))
        started = self.event('run:A-failed', 'running', 'active')
        self.event('run:A-failed', 'failed', 'finished', started)
        # Late evidence about an old attempt must not become the newest attempt.
        self.add(AnalysisBundle('late-analysis', 'run:A-fast', ('evidence:A-fast',), (), 'hypothesis', 'Late unverified explanation'))
        self.store.set_frontier('A22', 'context', True, 'keep exploring', 'frontier')
        cards = self.cards(self.view())
        self.assertEqual(set(cards), set('ABCDE'))
        a = cards['A']
        self.assertEqual(a['latest_attempt']['run_id'], 'run:A-failed')
        self.assertEqual(a['latest_attempt']['execution_status'], 'failed')
        self.assertEqual(a['last_correct_candidate']['candidate_id'], 'A12')
        self.assertEqual(a['branch_best']['candidate_id'], 'A11')
        self.assertEqual(a['task_best']['candidate_id'], 'B11')
        self.assertEqual(a['frontier'], ['A22'])
        self.assertIsNone(a['current_parent_candidate'])
        self.assertEqual(a['provenance'], 'synthetic')
        self.assertIsNone(cards['C']['latest_attempt'])
        self.assertFalse(cards['B']['paused'])
        self.assertEqual(cards['B']['branch_best']['candidate_id'], 'B11')

    def test_explicit_parent_pause_resume_and_unexecuted_deferred_candidate(self):
        self.add(ParentCandidate('parent:A11', 'task', identity('A11'), 'A11'))
        self.add(Decision('parent-choice', 'task', 'note', 'B', 'cross-route parent is explicit',
                          {'type': 'parent_selection.v1', 'strategy_id': 'B', 'context_id': 'context', 'parent_id': 'parent:A11'}))
        self.add(Decision('pause', 'task', 'paused', 'E', 'budget exhausted'))
        self.add(Decision('defer-candidate', 'task', 'deferred', 'B22', 'no slot'))
        self.add(Run('deferred-run', 'A22', 'context'))
        self.event('deferred-run', 'not_run', 'deferred')
        old_view = self.view('old')
        cards = self.cards(old_view)
        self.assertEqual(cards['B']['current_parent_candidate']['candidate_id'], 'A11')
        self.assertEqual(cards['B']['current_parent_candidate']['parent_id'], 'parent:A11')
        self.assertTrue(cards['E']['paused'])
        self.assertEqual(cards['B']['deferred'][0]['record_id'], 'B22')
        self.assertEqual(cards['A']['latest_attempt']['scheduling_status'], 'deferred')
        self.add(Decision('resume', 'task', 'resumed', 'E', 'new budget'))
        self.add(Run('b-start', 'B22', 'context'))
        self.event('b-start', 'running', 'active')
        current = self.cards(self.view('current'))
        self.assertFalse(current['E']['paused'])
        self.assertEqual(current['B']['deferred'], [])
        self.assertTrue(self.cards(old_view)['E']['paused'])

    def test_plan_deferral_keeps_pending_children_after_sibling_execution(self):
        self.add(Plan('partial-plan', 'A', 'multiple attempts in one Plan'))
        self.add(Candidate('A-complete', 'partial-plan', 'baseline', identity('complete')))
        completed_run = self.evaluate('completed-sibling', 'A-complete', 10)
        self.add(Candidate('A-pending', 'partial-plan', 'baseline', identity('pending')))
        self.add(Decision('partial-defer', 'task', 'deferred', 'partial-plan', 'budget for remaining candidate'))
        first = self.cards(self.view('partial-snapshot'))['A']
        self.assertIn('partial-plan', [item['record_id'] for item in first['deferred']])
        self.assertEqual(first['latest_attempt']['candidate_id'], 'A-pending')
        self.assertEqual(first['latest_attempt']['scheduling_status'], 'deferred')
        self.assertEqual(self.store.run_state(completed_run)['execution_status'], 'succeeded')
        self.add(Run('pending-run', 'A-pending', 'context'))
        waiting = self.cards(self.view('waiting-snapshot'))['A']
        self.assertIn('partial-plan', [item['record_id'] for item in waiting['deferred']])
        # An existing Run's explicit/implicit state remains authoritative.
        self.assertEqual(waiting['latest_attempt']['scheduling_status'], 'ready')
        started = self.event('pending-run', 'running', 'active')
        self.event('pending-run', 'failed', 'finished', started)
        final_view = self.view('all-attempted-snapshot')
        final = self.cards(final_view)['A']
        self.assertNotIn('partial-plan', [item['record_id'] for item in final['deferred']])
        self.assertEqual(final['latest_attempt']['execution_status'], 'failed')
        self.assertIn('partial-defer', [r['payload']['id'] for r in final_view.histories(('A',))['A']['records']])

    def test_each_participating_history_preserves_all_attempts_and_exact_dependencies(self):
        self.evaluate('A-parent', 'A11', 10)
        self.add(ParentCandidate('parent:A11', 'task', identity('A11'), 'A11'))
        self.add(Candidate('B-repair', 'Bplan1', 'parent:A11', identity('B-repair'), 'repair'))
        for state in ('failed', 'invalid', 'incomplete'):
            run = 'B-' + state
            self.add(Run(run, 'B-repair', 'context'))
            started = self.event(run, 'running', 'active')
            self.event(run, state, 'finished', started)
        self.add(Decision('B-deferred', 'task', 'deferred', 'B22', 'budget'))
        view = self.view()
        histories = view.histories(('A', 'B'))
        self.assertEqual(set(histories), {'A', 'B'})
        b = histories['B']
        ids = {record['payload']['id'] for record in b['records']}
        self.assertTrue({'Bplan1', 'Bplan2', 'B11', 'B12', 'B21', 'B22', 'B-repair',
                         'B-failed', 'B-invalid', 'B-incomplete', 'B-deferred'} <= ids)
        self.assertTrue({'parent:A11', 'A11', 'run:A-parent', 'eval:A-parent', 'evidence:A-parent'} <= set(b['dependency_ids']))
        self.assertNotIn('A22', ids)
        self.assertNotIn('B-repair', histories['A']['owned_ids'])
        self.assertTrue(b['complete'])
        self.assertEqual(len(b['records']), b['total'])
        self.assertEqual(len(b['owned_ids']), b['owned_total'])
        self.assertEqual(histories['A']['snapshot']['id'], b['snapshot']['id'])

    def test_history_pages_do_not_call_last_page_complete(self):
        view = self.view()
        first = view.history_page('A', limit=2)
        self.assertFalse(first['complete'])
        self.assertEqual(first['next_offset'], 2)
        tail = view.history_page('A', offset=2, limit=1000)
        self.assertIsNone(tail['next_offset'])
        self.assertFalse(tail['complete'])
        all_records = first['records'] + tail['records']
        self.assertEqual(len({r['payload']['id'] for r in all_records}), first['total'])
        with self.assertRaises(MemoryError):
            view.history_page('missing')
        with self.assertRaises(MemoryError):
            view.histories(('A', 'A'))

    def test_history_page_identity_lists_cover_only_returned_records(self):
        view = self.view()
        offset, ids, owned, dependencies = 0, [], [], []
        while True:
            page = view.history_page('A', offset=offset, limit=1)
            page_ids = [record['payload']['id'] for record in page['records']]
            self.assertEqual(len(page['owned_ids']) + len(page['dependency_ids']), len(page_ids))
            self.assertEqual(set(page['owned_ids']) | set(page['dependency_ids']), set(page_ids))
            ids.extend(page_ids)
            owned.extend(page['owned_ids'])
            dependencies.extend(page['dependency_ids'])
            if page['next_offset'] is None:
                break
            offset = page['next_offset']
        self.assertEqual(len(set(ids)), page['total'])
        self.assertEqual(len(set(owned)), page['owned_total'])
        self.assertEqual(len(set(dependencies)), page['dependency_total'])
        self.assertEqual(set(ids), set(owned) | set(dependencies))

    def test_snapshot_pagination_rejects_missing_duplicate_or_changed_coverage(self):
        snapshot = self.snapshot()
        actual = self.store
        class BrokenPages:
            def __init__(self, mode):
                self.mode = mode
            def snapshot_page(self, snapshot_id, offset=0, limit=100):
                page = actual.snapshot_page(snapshot_id, offset, limit)
                if self.mode == 'early-end':
                    page['next_offset'] = None
                elif offset > 0:
                    if self.mode == 'revision':
                        page['snapshot']['digest'] = '0' * 64
                    elif self.mode == 'duplicate':
                        page['records'][0] = actual.snapshot_page(snapshot_id, 0, 1)['records'][0]
                    elif self.mode == 'offset':
                        page['offset'] = 0
                    elif self.mode == 'counts':
                        page['snapshot']['strategy_counts']['A'] = 1
                return page
        for mode in ('early-end', 'revision', 'duplicate', 'offset', 'counts'):
            with self.subTest(mode=mode), self.assertRaises(MemoryIncomplete):
                MemoryReader(BrokenPages(mode)).open_snapshot(snapshot['id'], page_size=2)

    def test_read_views_are_detached_and_concurrent_reads_do_not_write(self):
        snapshot = self.snapshot()
        def counts():
            with closing(sqlite3.connect(self.db)) as con:
                return {table: con.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                        for table in ('records', 'requests', 'snapshots', 'links')}
        before = counts()
        view = MemoryReader(self.store).open_snapshot(snapshot['id'])
        cards = view.status_cards('context')
        cards[0]['recent_plans'][0]['description'] = 'mutated output'
        page = view.history_page('A', limit=1000)
        page['records'][0]['payload']['name'] = 'mutated output'
        page['snapshot']['record_count'] = 0
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: MemoryReader(self.store).open_snapshot(snapshot['id']).histories(('A', 'B')), range(8)))
        self.assertEqual(counts(), before)
        self.assertTrue(all(x['A']['complete'] and x['B']['complete'] for x in results))
        self.assertEqual(view.histories(('A',))['A']['records'][0]['payload']['name'], 'Synthetic infrastructure')
        self.assertNotEqual(view.status_cards('context')[0]['recent_plans'][0]['description'], 'mutated output')

    def test_status_cards_context_isolation_and_recent_plan_coverage(self):
        self.evaluate('original', 'A11', 10)
        self.add(EvaluationContext('other', 'task', 'workload', {'device': 'other-fixture'}, {'clock': 'fixture'}, 'synthetic'))
        self.evaluate('other', 'A12', 1, context='other')
        view = self.view()
        cards = {card['strategy_id']: card for card in view.status_cards('context', recent_plan_limit=1)}
        self.assertEqual(cards['A']['last_correct_candidate']['candidate_id'], 'A11')
        self.assertEqual(cards['A']['branch_best']['candidate_id'], 'A11')
        self.assertEqual(cards['A']['latest_attempt']['run_id'], 'run:original')
        self.assertEqual(cards['A']['recent_plans_total'], 2)
        self.assertFalse(cards['A']['recent_plans_complete'])
        with self.assertRaises(MemoryError):
            view.status_cards('no-context')

    def test_filtered_history_is_explicit_and_preserves_hypothesis_labels(self):
        self.evaluate('A', 'A11', 10)
        self.evaluate('B', 'B11', 8)
        result = self.view().search_history('unverified', strategy_ids=('A',), context_id='context', kinds=('AnalysisBundle',))
        self.assertTrue(result['filtered'])
        self.assertFalse(result['complete_history'])
        self.assertEqual([r['payload']['id'] for r in result['records']], ['analysis:A'])
        self.assertEqual(result['records'][0]['payload']['claim_status'], 'hypothesis')

    def test_parent_and_plan_link_references_are_retained(self):
        self.add(Decision('plan-parent', 'task', 'note', 'Aplan1', 'choose precise baseline',
                          {'type': 'plan_parent.v1', 'strategy_id': 'A', 'plan_id': 'Aplan1', 'parent_id': 'baseline'}))
        self.add(Decision('method-link', 'task', 'note', 'Aplan1', 'test source method',
                          self.method_details()))
        page = self.view().histories(('A',))['A']
        link = next(r['payload'] for r in page['records'] if r['payload']['id'] == 'method-link')
        self.assertEqual(link['details']['method_snapshot']['validation_status'], 'source_supported')
        self.assertIn('baseline', page['dependency_ids'])

    def test_bound_plan_history_retains_repairs_descended_from_its_initial_parent(self):
        self.add(ParentCandidate('parent:A11', 'task', identity('A11'), 'A11'))
        self.add(Candidate('A-repair', 'Aplan1', 'parent:A11', identity('repair'), 'repair'))
        self.add(Decision('plan-parent', 'task', 'note', 'Aplan1', 'initial base',
                          {'type': 'plan_parent.v1', 'strategy_id': 'A', 'plan_id': 'Aplan1', 'parent_id': 'baseline'}))
        self.add(Decision('repair-method-link', 'task', 'note', 'Aplan1', 'method used during repair',
                          self.method_details('A-repair', 'parent:A11')))
        records = self.view().histories(('A',))['A']['records']
        repair = next(r['payload'] for r in records if r['payload']['id'] == 'A-repair')
        self.assertEqual(repair['parent_id'], 'parent:A11')
        link = next(r['payload'] for r in records if r['payload']['id'] == 'repair-method-link')
        self.assertEqual(link['details']['parent_id'], 'baseline')
        self.assertEqual(link['details']['candidate_parent_id'], 'parent:A11')

    def test_unvalidated_method_decisions_cannot_supply_conflicting_provenance(self):
        cases = {
            'note-revision': {'method_note_revision': 'not-a-revision'},
            'method-schema': {'method_snapshot': {'validation_status': 'source_supported'}},
            'method-id': {'method_id': 'other-method'},
            'note-id': {'method_note_id': 'RSI/Methods/wrong.md'},
            'source-refs': {'source_refs': []},
        }
        for name, changes in cases.items():
            # Every malformed link gets its own real DB so one prior rejection
            # cannot make the remaining cases pass vacuously.
            with self.subTest(name=name):
                store = ExperimentStore(self.root / (name + '.sqlite3'), self.root / (name + '-artifacts'))
                for record in (
                    Task('task', 'Synthetic source boundary', 'v1'), Strategy('A', 'task', 'A'),
                    ParentCandidate('baseline', 'task', identity('baseline')), Plan('Aplan1', 'A', 'Plan'),
                    Decision('binding', 'task', 'note', 'Aplan1', 'base',
                             {'type': 'plan_parent.v1', 'strategy_id': 'A', 'plan_id': 'Aplan1', 'parent_id': 'baseline'}),
                ):
                    store.append(record, record.id)
                details = self.method_details(None, None)
                details.update(changes)
                store.append(Decision('bad-link', 'task', 'note', 'Aplan1', 'malformed import', details), 'bad-link')
                snapshot = store.create_snapshot('task', 'snap')
                with self.assertRaises(MemoryIncomplete):
                    MemoryReader(store).open_snapshot(snapshot['id'])

    def test_recognized_parent_binding_rejects_unknown_schema_fields(self):
        self.add(Decision('ambiguous-binding', 'task', 'note', 'Aplan1', 'unknown field',
                          {'type': 'plan_parent.v1', 'strategy_id': 'A', 'plan_id': 'Aplan1', 'parent_id': 'baseline',
                           'candidate_parent_id': 'A11'}))
        with self.assertRaises(MemoryIncomplete):
            self.view()

    def test_malformed_recognized_decision_cannot_claim_correct_parent(self):
        self.add(Decision('bad-parent', 'task', 'note', 'A', 'bad imported decision',
                          {'type': 'parent_selection.v1', 'strategy_id': 'A', 'context_id': 'context', 'parent_id': 'missing'}))
        with self.assertRaises(MemoryIncomplete):
            self.view()

    def test_restart_reconstructs_same_frozen_histories(self):
        self.evaluate('A', 'A11', 10)
        snapshot = self.snapshot()
        view = MemoryReader(self.store).open_snapshot(snapshot['id'])
        expected = view.histories(('A', 'B'))
        env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'), PYTHONIOENCODING='utf-8')
        code = ('import json,sys; from rsi_kernel_agent.store import ExperimentStore; '
                'from rsi_kernel_agent.memory import MemoryReader; '
                'store=ExperimentStore(sys.argv[1],sys.argv[2]); '
                'print(json.dumps(MemoryReader(store).open_snapshot(sys.argv[3],page_size=2).histories(("A","B"))))')
        result = subprocess.run([sys.executable, '-B', '-c', code, str(self.db), str(self.root / 'artifacts'), snapshot['id']],
                                capture_output=True, text=True, encoding='utf-8', env=env, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), expected)


if __name__ == '__main__':
    unittest.main()
