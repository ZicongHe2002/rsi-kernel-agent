"""Synthetic SQLite fixtures for read-only milestone verification, never TPU evidence."""
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import importlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(Path(__file__).resolve().parent)]
from rsi_kernel_agent.contracts import (Candidate, CandidateIdentity, Decision, EvaluationBundle,
    EvaluationContext, Evidence, ParentCandidate, Plan, Run, RunEvent, Strategy, Task, Workload, canonical_json)
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.workflow.execution import stable_id
from rsi_kernel_agent.workflow.controller import stable
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from rsi_kernel_agent.workflow.scheduling import validate_limits
from real_round_aux import LIMITS
from p35_prepare_diagnostic_round import target_for

MODULE = Path(__file__).with_name('verify_p35_final_memory.py')


def candidate_id(label):
    return stable('wf-c', 'job:' + label)


def run_id(label):
    return stable('wf-r', 'job:' + label + ':0')


class FinalMemoryTests(unittest.TestCase):
    generation = 'runtime5'
    prior_count = 10
    recovery_strategies = ('A', 'A', 'B', 'B')

    def setUp(self):
        self.assertTrue(MODULE.is_file(), 'Read-only final Memory verifier is not implemented')
        self.verify = importlib.import_module('verify_p35_final_memory')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, path, body):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical_json(body), encoding='utf-8')
        return path

    def fixture(self):
        self.store = ExperimentStore(self.root / 'experiments.sqlite3', self.root / 'artifacts')
        self.add = lambda r: self.store.append(r, r.id)
        self.add(Task('task', 'Synthetic final verification fixture', 'v1'))
        self.add(Workload('workload', 'task', {'synthetic_fixture': True}))
        self.add(Workload('diagnostic-workload', 'task', {'synthetic_fixture': True, 'zero_length': True}))
        self.contexts = {}
        for cid, runtime in (('c3', 'r3'), ('c4', 'r4'), ('c5', 'r5'), ('diag', 'r5')):
            context = EvaluationContext(cid, 'task', 'diagnostic-workload' if cid == 'diag' else 'workload',
                                        {'runtime_session_id': runtime}, {'fixture': True}, 'real')
            self.add(context)
            self.contexts[cid] = context
        for strategy in ('A', 'B', 'idle'):
            self.add(Strategy(strategy, 'task', strategy))
        identity = CandidateIdentity(*[sha256(x.encode()).hexdigest() for x in ('source', 'config', 'build')])
        self.add(ParentCandidate('baseline', 'task', identity))
        self.identity = identity
        self.selection = {'schema_version': 2, 'runtime_generation': self.generation, 'task_id': 'task',
                          'context_id': 'c5', 'strategy_id': 'A',
                          'round_id': 'p35-' + self.generation + '-single-tile-diagnostic:1', 'knowledge': []}
        self.rec_id = 'real-' + self.generation + '-recovery-round:2'
        self.rec_target = self.root / ('real-round-2-' + self.generation)
        self.diag_target = target_for(self.root, self.selection)
        self.write(self.root / ('p35-diagnostic-selection-' + self.generation + '.json'), self.selection)
        self.write(self.diag_target / 'selection.json', self.selection)
        self.write(self.diag_target / 'diagnostic-packet.json',
                   {'original_context_id': 'c5', 'context': asdict(self.contexts['diag'])})
        self.write(self.root / ('colab-config-' + self.generation + '.json'), {'runtime_id': 'r5'})
        for target in (self.rec_target, self.diag_target):
            self.write(target / 'runtime-binding.json', {'generation': self.generation})
        self.state = {'task_id': 'task', 'revision': 0, 'cancelled': False,
                      'limits': validate_limits(LIMITS),
                      'spent': {'executions': self.prior_count, 'tokens': 0, 'time_s': 1800 * self.prior_count},
                      'jobs': {f'old-{i}': {'id': f'old-{i}', 'round_id': 'old', 'state': 'completed',
                                'reserved': True, 'variant': {'cost': {'executions': 1, 'tokens': 0, 'time_s': 1800}}}
                               for i in range(self.prior_count)}}
        self.journal = WorkflowJournal(self.store, 'task')
        self.state = self.journal.save(self.state, 'Synthetic prior milestone')
        # Historical Contexts retain independent Best and Frontier values.
        self.make_job('historical3', 'c3', 'A', 0.5, round_id='historical')
        self.make_job('historical4', 'c4', 'B', 0.7, round_id='historical')
        self.store.set_frontier(candidate_id('historical3'), 'c3', True, 'fixture', 'frontier-old')
        self.rows = []
        for index, strategy in enumerate(self.recovery_strategies):
            job, row = self.make_job('recovery' + str(index), 'c5', strategy, 10 + index,
                                     round_id=self.rec_id)
            self.state['jobs'][job['id']] = job
            self.rows.append(row)
        recovery_spent = self.prior_count + len(self.recovery_strategies)
        self.state['spent'] = {'executions': recovery_spent, 'tokens': 0, 'time_s': 1800 * recovery_spent}
        self.state = self.journal.save(self.state, 'Synthetic recovery completion')
        self.rec = self.complete(self.rec_target, self.rec_id)
        for index in range(2):
            job, row = self.make_job('diagnostic' + str(index), 'diag', 'A', 20 + index,
                                     round_id=self.selection['round_id'])
            self.state['jobs'][job['id']] = job
            self.rows.append(row)
        self.add(ParentCandidate('selected-parent', 'task', self.identity, candidate_id('recovery0')))
        self.add(Decision('parent-choice', 'task', 'note', 'A', 'fixture explicit parent',
            {'type': 'parent_selection.v1', 'strategy_id': 'A', 'context_id': 'c5', 'parent_id': 'selected-parent'}))
        self.store.set_frontier(candidate_id('recovery1'), 'c5', True, 'fixture', 'frontier-new')
        self.final_count = recovery_spent + 2
        self.state['spent'] = {'executions': self.final_count, 'tokens': 0, 'time_s': 1800 * self.final_count}
        self.state = self.journal.save(self.state, 'Synthetic diagnostic completion')
        self.diag = self.complete(self.diag_target, self.selection['round_id'], context='diag')
        self.reports = self.write(self.root / 'verified.json', {'status': 'selected_terminal_jobs_verified', 'jobs': self.rows})

    def make_job(self, label, context, strategy, latency, *, round_id):
        plan, cid, rid = 'plan:' + label, candidate_id(label), run_id(label)
        self.add(Plan(plan, strategy, 'Synthetic proposal'))
        self.add(Candidate(cid, plan, 'baseline', self.identity))
        self.add(Run(rid, cid, context))
        self.store.transition(RunEvent(rid + ':start', rid, 'running', 'active', 'fixture'), None, rid + ':start')
        self.store.transition(RunEvent(rid + ':end', rid, 'succeeded', 'finished', 'fixture'), rid + ':start', rid + ':end')
        ref = self.store.artifacts.put(b'Synthetic timing evidence')
        eid = 'evidence:' + label
        self.add(Evidence(eid, rid, 'measured_timing', 'available', 'completed', 'supported', latency,
                          'ms', (ref,), (), {'synthetic_fixture': True, 'provenance': 'real'}))
        ev = EvaluationBundle('eval:' + label, rid, 'passed', 'valid', latency, True, (eid,))
        self.add(ev)
        promotion = self.store.consider(ev.id, 'consider:' + label)
        op = stable_id('operation', rid, 'compile')
        contract = {'context': asdict(self.contexts[context])}
        contract_digest = sha256(canonical_json(contract).encode()).hexdigest()
        receipt = {'operation_id': op, 'request_digest': sha256(label.encode()).hexdigest(), 'stage': 'compile',
                   'run_id': rid, 'candidate_id': cid, 'context_id': context, 'parent_id': 'baseline',
                   'candidate_identity': asdict(self.identity), 'parent_identity': asdict(self.identity),
                   'contract_digest': contract_digest,
                   'status': 'succeeded', 'provenance': 'real', 'artifact_refs': [], 'outputs': {'synthetic_fixture': True}}
        receipt_ref = asdict(self.store.artifacts.put(canonical_json(receipt).encode()))
        self.add(Decision(stable_id('execution-receipt', op), 'task', 'note', rid, 'Synthetic receipt',
                          {'type': 'execution_receipt.v1', 'operation_id': op,
                           'request_digest': receipt['request_digest'], 'artifact_ref': receipt_ref}))
        result = {'classification': 'correct_inconclusive', 'provenance': 'real', 'run_id': rid,
                  'candidate_id': cid, 'evaluation_id': ev.id, 'promotion_id': promotion.id,
                  'correctness': 'passed', 'timing_validity': 'valid', 'latency_ms': latency, 'confirmed': True,
                  'parent_latency_ms': None, 'receipt_refs': {'compile': receipt_ref}}
        jid = 'job:' + label
        marker = stable_id('workflow-result', rid)
        self.add(Decision(marker, 'task', 'note', rid, 'Synthetic result',
                          {'type': 'workflow_result.v1', 'job_id': jid, 'result': result, 'cancelled': False}))
        job = {'id': jid, 'task_id': 'task', 'round_id': round_id, 'strategy_id': strategy, 'plan_id': plan,
               'candidate_id': cid, 'parent_id': 'baseline', 'parent_evaluation_id': None,
               'run_id': rid, 'context_id': context, 'state': 'completed', 'reserved': True,
               'attempt': 0, 'repair_depth': 0,
               'variant': {'label': label, 'cost': {'executions': 1, 'tokens': 0, 'time_s': 1800}},
               'contract': contract, 'contract_digest': contract_digest, 'result': result}
        row = {key: job[key] for key in ('id', 'strategy_id', 'plan_id', 'candidate_id', 'parent_id',
                                         'parent_evaluation_id', 'run_id', 'context_id')}
        row.update(verification='verified', result_marker_id=marker,
                   result_digest=sha256(canonical_json(result).encode()).hexdigest(),
                   receipt_refs=result['receipt_refs'], runtime_id=self.contexts[context].environment['runtime_session_id'],
                   npz_comparison_status='recomputed_from_raw_npz',
                   all_stage_artifacts_reachable_from_evaluation=True)
        return job, row

    def complete(self, target, round_id, context=None):
        pub, intake_id = round_id + ':publication', round_id + ':intake'
        manifest = asdict(self.store.artifacts.put(('Synthetic manifest ' + round_id).encode()))
        self.write(target / 'manifest-ref.json', manifest)
        self.add(Decision(pub, 'task', 'note', 'task', 'Synthetic publication',
                          {'type': 'analysis_result.v1', 'manifest': manifest}))
        jobs = [j for j in self.state['jobs'].values() if j['round_id'] == round_id]
        raw_jobs = [{key: value for key, value in job.items() if key not in
                     ('candidate_id', 'run_id', 'state', 'reserved', 'attempt', 'repair_depth', 'result')}
                    for job in jobs]
        intake = {'id': intake_id, 'task_id': 'task', 'round_id': round_id, 'publication_id': pub,
                  'items': [{'status': 'accepted', 'jobs': raw_jobs}]}
        self.add(Decision(intake_id, 'task', 'note', 'task', 'Synthetic intake',
                          {'type': 'workflow_intake.v1', 'result': intake}))
        body = {'round_id': round_id, 'snapshot': self.store.create_snapshot('task', round_id + ':done'),
                'publication_id': pub, 'intake_id': intake_id, 'results': {j['id']: j['result'] for j in jobs}}
        if context:
            body.update(context_id=context, charged_cost={'executions': 2, 'tokens': 0, 'time_s': 3600})
        self.write(target / 'completed.json', body)
        return body

    def call(self, **kwargs):
        return self.verify.verify_final_memory(self.root, self.rec_target / 'completed.json',
            self.diag_target / 'completed.json', generation=self.generation, verification_reports=(self.reports,), **kwargs)

    def test_missing_completion_is_pending_without_opening_database(self):
        report = self.verify.verify_final_memory(self.root, self.root / 'missing-recovery.json', self.root / 'missing-diagnostic.json')
        self.assertEqual(report['status'], 'pending_completion')
        self.assertFalse((self.root / 'experiments.sqlite3').exists())

    def test_verified_milestone_preserves_contexts_and_original_memory_semantics(self):
        self.fixture()
        before = (self.root / 'experiments.sqlite3').read_bytes()
        report = self.call()
        self.assertEqual(report['status'], 'verified')
        self.assertEqual(report['milestone_budget']['spent']['executions'], self.final_count)
        contexts = report['contexts']
        self.assertEqual(set(contexts), {'c3', 'c4', 'c5', 'diag'})
        cards = {c['strategy_id']: c for c in contexts['c5']['cards']}
        self.assertEqual(cards['A']['branch_best']['candidate_id'], candidate_id('recovery0'))
        self.assertEqual(cards['A']['last_correct_candidate']['candidate_id'], candidate_id('recovery1'))
        self.assertEqual(cards['A']['latest_attempt']['run_id'], run_id('recovery1'))
        self.assertEqual(cards['A']['current_parent_candidate']['parent_id'], 'selected-parent')
        self.assertEqual(cards['A']['frontier'], [candidate_id('recovery1')])
        self.assertIsNone(cards['idle']['branch_best'])
        self.assertEqual(contexts['c3']['task_best']['candidate_id'], candidate_id('historical3'))
        self.assertEqual(before, (self.root / 'experiments.sqlite3').read_bytes())
        self.assertTrue(all(x['complete'] for x in report['participating_histories'].values()))

    def test_later_live_journal_does_not_relabel_milestone_revision(self):
        self.fixture()
        invalid = deepcopy(self.state)
        invalid['limits']['executions'] = 20
        invalid['limits']['time_s'] = 36000
        with self.assertRaisesRegex(ValueError, 'budget grant'):
            self.journal.save(invalid, 'Synthetic unauthorized extension rejected')
        self.state = self.journal.save(self.state, 'Synthetic later observation')
        report = self.call()
        self.assertEqual(report['status'], 'verified')
        self.assertFalse(report['current']['same_high_watermark'])
        self.assertGreater(report['current']['journal']['revision'], report['milestone_budget']['revision'])
        self.assertEqual(report['milestone_budget']['limits']['executions'], 16)

    def test_completed_snapshot_metadata_mismatch_is_rejected(self):
        self.fixture()
        self.rec['snapshot']['digest'] = 'f' * 64
        self.write(self.rec_target / 'completed.json', self.rec)
        with self.assertRaisesRegex(ValueError, 'Snapshot'):
            self.call()

    def test_wrong_runtime_binding_is_rejected(self):
        self.fixture()
        self.write(self.rec_target / 'runtime-binding.json', {'generation': 'runtime4'})
        with self.assertRaisesRegex(ValueError, 'binding'):
            self.call()

    def test_budget_refund_in_completion_snapshot_is_rejected(self):
        self.fixture()
        self.state['spent']['executions'] = self.final_count - 1
        self.state = self.journal.save(self.state, 'Synthetic erroneous refund')
        self.diag['snapshot'] = self.store.create_snapshot('task', 'refund')
        self.write(self.diag_target / 'completed.json', self.diag)
        with self.assertRaisesRegex(ValueError, 'budget|spending|reservation'):
            self.call()

    def test_missing_execution_verification_stays_pending(self):
        self.fixture()
        self.write(self.reports, {'jobs': self.rows[:-1]})
        report = self.call()
        self.assertEqual(report['status'], 'pending_verification')
        self.assertEqual(report['missing_verified_runs'], [run_id('diagnostic1')])

    def test_missing_verification_report_path_stays_pending(self):
        self.fixture()
        self.reports.unlink()
        report = self.call()
        self.assertEqual(report['status'], 'pending_verification')
        self.assertEqual(report['missing_verification_reports'], [str(self.reports)])
        self.assertEqual(len(report['missing_verified_runs']), len(self.recovery_strategies) + 2)

    def test_receipt_contract_digest_is_rebound_to_job(self):
        self.fixture()
        job = deepcopy(self.state['jobs']['job:recovery0'])
        job['contract_digest'] = 'f' * 64
        with self.verify.TransactionStore(self.root / 'experiments.sqlite3', self.root / 'artifacts') as store:
            records = store.snapshot_page(self.diag['snapshot']['id'], limit=self.diag['snapshot']['record_count'])['records']
            frozen = self.verify.SnapshotRecords(store, records)
            with self.assertRaisesRegex(ValueError, 'contract|receipt'):
                self.verify.bind_verification(store, frozen, job, self.rows[0])

    def test_original_verify_job_compile_failure_report_can_be_rebound(self):
        # Full original real-protocol receipt shape, with explicitly synthetic data.
        sys.path.insert(0, str(ROOT / 'tests'))
        from phase3_fixtures import RealProtocolFixture
        from rsi_kernel_agent.workflow.evaluation import integrate_result
        from verify_real_round import verify_job
        fixture = RealProtocolFixture()
        fixture.prepare(self.root)
        job = fixture.job()
        fixture.store.transition(RunEvent('run:start', 'run', 'running', 'active', 'CPU fixture'), None, 'run:start')
        receipts = {'generate': fixture.receipt(job, 'generate', fixture.outputs(job)['generate']),
                    'compile': fixture.receipt(job, 'compile', {'error_type': 'FixtureError', 'message': 'synthetic failure'}, 'failed')}
        result = integrate_result(fixture.store, job, receipts)
        job.update(state='failed', stage='compile', result=result)
        row = verify_job(fixture.store, job, recompute_npz=True)
        with self.verify.TransactionStore(fixture.store.db_path, self.root / 'artifacts') as store:
            frozen = self.verify.SnapshotRecords(store, store.history('task'))
            bound = self.verify.bind_verification(store, frozen, job, row)
        self.assertEqual(bound['classification'], 'compile_failed')
        self.assertEqual(bound['npz_comparison_status'], 'unavailable_no_correctness_arrays')

    def test_wrong_report_identity_or_digest_is_rejected(self):
        self.fixture()
        for field, value in (('context_id', 'c3'), ('run_id', run_id('historical3')),
                             ('result_digest', 'f' * 64), ('receipt_refs', {})):
            with self.subTest(field=field):
                rows = deepcopy(self.rows)
                rows[0][field] = value
                self.write(self.reports, {'jobs': rows})
                with self.assertRaises(ValueError):
                    self.call()

    def test_corrupt_small_receipt_is_rejected_even_with_old_verification_report(self):
        self.fixture()
        ref = self.rows[0]['receipt_refs']['compile']
        path = self.root / 'artifacts' / 'sha256' / ref['digest'][:2] / ref['digest']
        path.write_bytes(b'corrupt receipt')
        with self.assertRaises(ValueError):
            self.call()

    def test_transaction_enforces_read_only_database_access(self):
        self.fixture()
        with self.verify.TransactionStore(self.root / 'experiments.sqlite3', self.root / 'artifacts') as store:
            with store._connection() as connection:
                self.assertEqual(connection.execute('PRAGMA query_only').fetchone()[0], 1)
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute('DELETE FROM records')


class Runtime6FinalMemoryTests(FinalMemoryTests):
    generation = 'runtime6'
    prior_count = 11
    recovery_strategies = ('A', 'A')

    def test_runtime6_preserves_repair_reserve_and_marks_original_B_goal_pending(self):
        self.fixture()
        report = self.call()
        self.assertEqual(report['milestone_budget']['spent']['executions'], 15)
        self.assertEqual(report['milestone_budget']['remaining']['executions'], 1)
        self.assertEqual(report['milestone_budget']['new_reserved_runs'], 4)
        self.assertFalse(report['original_second_round_scope_complete'])
        self.assertEqual(report['pending_recovery_candidates'], ['query-in-attention', 'b1-fused-prepare'])


class Runtime7FinalMemoryTests(Runtime6FinalMemoryTests):
    generation = 'runtime7'


if __name__ == '__main__':
    unittest.main()
