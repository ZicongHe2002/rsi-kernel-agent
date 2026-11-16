"""Post-B2 read-only reconciliation with temporary original protocol/grant fixtures."""
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_p35_continuation_round as chain
from phase3_fixtures import RealProtocolFixture
from test_analyzer import proposal
from rsi_kernel_agent.contracts import (Candidate, Decision, EvaluationContext, Plan, Run, RunEvent,
    Strategy, Workload, canonical_json)
from rsi_kernel_agent.profiler.session import submit_response
from rsi_kernel_agent.workflow.controller import Controller, stable
from rsi_kernel_agent.workflow.evaluation import integrate_result
from rsi_kernel_agent.workflow.execution import execution_contract
from real_round_aux import LIMITS
from verify_real_round import ReadOnlyStore, verify_job

ROUND = 'real-runtime7-b2-continuation-round:2'
GRANT_KEY = 'p35-runtime7-b2-after-a2-diagnostic-v1'
COST = {'executions': 1, 'tokens': 0, 'time_s': 1800}


class PostB2MemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            self.api = importlib.import_module('verify_p35_b2_final_memory')
        except ImportError:
            self.fail('Post-B2 final Memory verifier is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fx = RealProtocolFixture()
        self.fx.prepare(self.root)
        self.store = self.fx.store
        self.store.db_path.rename(self.root / 'experiments.sqlite3')
        self.store.db_path = self.root / 'experiments.sqlite3'
        self.fx.variant['cost'] = deepcopy(COST)
        self.store.append(Strategy('B', 'task', 'B'), 'B')
        self.store.append(Workload('diag-workload', 'task', {'kernel': 'fixture', 'cases': [self.fx.case]}), 'diag-workload')
        self.store.append(EvaluationContext('diag-context', 'task', 'diag-workload', self.fx.environment,
            self.fx.protocol, 'real'), 'diag-context')
        self.controller = Controller(self.store, 'task', None, limits=LIMITS)
        self.old = {}
        for index, label in enumerate(('length-row', 'length-smem', 'diagnostic-baseline', 'diagnostic-single-tile')):
            context = 'context' if index < 2 else 'diag-context'
            round_id = 'real-runtime7-recovery-round:2' if index < 2 else 'p35-runtime7-single-tile-diagnostic:1'
            job = self.completed_origin(label, context, round_id)
            self.old[job['id']] = job
        self.parent_b = self.completed_origin('baseline-b', 'context', 'prerequisite', strategy='B')
        state = self.controller.journal.load()
        # Keep the historical reservations as complete journal job records.
        # Controller.accept_intake revalidates every existing job's candidate
        # inputs, so a cost-only placeholder would fail its original contract
        # boundary even though it is already terminal.
        historical = {}
        for i in range(11):
            jid = f'old-{i}'
            historical[jid] = {**self.fx.job(), 'id': jid, 'round_id': 'old',
                # Reuse the fixture's persisted Plan; the historical rows are
                # journal reservations, not new Plan publications.
                'plan_id': 'plan', 'candidate_id': stable('wf-c', jid),
                'run_id': stable('wf-r', jid + ':0'), 'state': 'cancelled',
                'stage': 'evaluate', 'receipts': {}, 'result': None,
                'reserved': True, 'variant': {**deepcopy(self.fx.variant), 'label': jid}}
        state['jobs'] = {**self.old, **historical}
        state['spent'] = {'executions': 15, 'tokens': 0, 'time_s': 27000}
        state = self.controller.journal.save(state, 'CPU prior fifteen preserved reservations')
        self.prior_snapshot = self.store.create_snapshot('task', 'pre-b2')
        reader = ReadOnlyStore(self.store.db_path, self.root / 'artifacts')
        rows = [verify_job(reader, job, recompute_npz=False) for job in self.old.values()]
        # The fixture has tiny protocol JSON, not arrays. This saved verifier
        # assertion is a trusted-input test seam; receipt binding is real.
        for row in rows:
            row['npz_comparison_status'] = 'recomputed_from_raw_npz'
        source = self.write(self.root / 'prior-verification.json', {'jobs': rows, 'status': 'selected_terminal_jobs_verified'})
        self.prior_report = self.write(self.root / 'prior-final-memory.json', {'status': 'verified',
            'task_id': 'task', 'runtime_generation': 'runtime7', 'final_snapshot': self.prior_snapshot,
            'verified_runs': [{'run_id': j['run_id']} for j in self.old.values()],
            'missing_verified_runs': [], 'missing_verification_reports': [],
            'verification_sources': [{'path': str(source), 'sha256': sha256(source.read_bytes()).hexdigest()}]})
        rows = []
        for rid in ('real-runtime7-recovery-round:2', 'p35-runtime7-single-tile-diagnostic:1'):
            jobs = [j for j in self.old.values() if j['round_id'] == rid]
            rows.append({'round_id': rid, 'context_id': jobs[0]['context_id'], 'run_ids': [j['run_id'] for j in jobs]})
        grant = {'schema_version': 1, 'task_id': 'task', 'expected_revision': state['revision'],
            'expected_state_digest': sha256(canonical_json(state).encode()).hexdigest(), 'old_limits': deepcopy(state['limits']),
            'new_limits': {**state['limits'], 'executions': 18, 'time_s': 32400}, 'spent': deepcopy(state['spent']),
            'next_batch': {'round_id': ROUND, 'purpose': 'Finish two B routes',
                           'cost': {'executions': 2, 'tokens': 0, 'time_s': 3600}},
            'authorization': {'source': 'CPU user source', 'quote': 'Continue iterating.',
                              'artifact_ref': self.fx.put(b'Continue iterating.')},
            'milestone': {'snapshot': self.prior_snapshot, 'rounds': rows,
                          'successful_round_id': 'real-runtime7-recovery-round:2'}}
        entry = self.controller.extend_budget(grant, GRANT_KEY)
        self.spec = {'schema_version': 1, 'task_id': 'task', 'round_id': ROUND, 'context_id': 'context',
            'runtime_generation': 'runtime7', 'runtime_id': self.fx.environment['runtime_session_id'],
            'strategies': ['B'], 'grant_key': GRANT_KEY, 'grant_digest': entry['digest'],
            'milestone': grant['milestone'], 'knowledge': [], 'milestone_validation': {'final_memory_report': {
                'path': str(self.prior_report), 'sha256': sha256(self.prior_report.read_bytes()).hexdigest()}}}
        self.write(self.root / 'colab-config-runtime7.json', {'runtime_id': self.spec['runtime_id']})
        self.chain = importlib.import_module('p35_continuation_round')
        for name, value in (('require_gates', ({'baseline-repair': next(iter(self.old.values())), 'baseline-b': self.parent_b}, {})),
                            ('validate_p35_milestone', None)):
            patched = patch.object(self.chain, name, return_value=value)
            patched.start(); self.addCleanup(patched.stop)
        prepared = await self.chain.prepare(self.store, self.root, self.spec, None)
        self.target = self.chain.target_for(self.root, self.spec)
        response = self.target / 'response-fixture.json'
        self.write(response, {'tool_calls': [
            {'id': 'source', 'name': 'source', 'arguments': {'strategy_id': 'B'}},
            {'id': 'history', 'name': 'history', 'arguments': {'strategy_id': 'B'}}]})
        submit_response(self.store, self.target, response)
        answer = proposal()
        answer['code_understanding'] = [answer['code_understanding'][1]]
        for row in answer['observations'] + answer['hypotheses']:
            row['strategy_id'] = 'B'
            row['refs'] = ['source:B']
        answer['plans'] = answer['plans'][-2:]
        self.variants = {}
        for plan, label in zip(answer['plans'], ('query-in-attention', 'b1-fused-prepare')):
            plan['strategy_id'] = 'B'
            plan['parent_id'] = prepared['request']['strategies'][0]['parent_id']
            plan['variants'] = [label]
            self.variants[plan['id']] = [{**deepcopy(self.fx.variant), 'label': label}]
        self.write(response, {'final': answer})
        submit_response(self.store, self.target, response)

    def write(self, path, body):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical_json(body), encoding='utf-8')
        return path

    def completed_origin(self, label, context, round_id, strategy='A'):
        jid = 'origin-' + label
        cid, rid = stable('wf-c', jid), stable('wf-r', jid + ':0')
        plan = 'plan-' + label
        for obj in (Plan(plan, strategy, 'CPU fixture ' + label), Candidate(cid, plan, 'parent', self.fx.identity), Run(rid, cid, context)):
            self.store.append(obj, obj.id)
        contract, contract_digest = execution_contract(self.store, context)
        job = {**self.fx.job(), 'id': jid, 'round_id': round_id, 'strategy_id': strategy, 'plan_id': plan,
            'candidate_id': cid, 'run_id': rid, 'context_id': context, 'contract': contract,
            'contract_digest': contract_digest, 'variant': {**deepcopy(self.fx.variant), 'label': label}}
        self.store.transition(RunEvent(rid+':start', rid, 'running', 'active', 'CPU fixture'), None, rid+':start')
        receipts = {stage: self.fx.receipt(job, stage, output) for stage, output in self.fx.outputs(job).items()}
        job.update(state='completed', stage='evaluate', reserved=True, attempt=0, repair_depth=0,
            receipts=receipts, result=integrate_result(self.store, job, receipts))
        return job

    async def finish(self, mode='success'):
        owner = self
        class Backend(chain.FixtureBackend):
            def submit(self, request):
                job, stage = request['job'], request['stage']
                if job['variant']['label'] == 'query-in-attention' and mode == stage:
                    output = {'error_type': 'FixtureCompile', 'message': 'Real protocol failure fixture'}
                    if stage == 'correctness':
                        output = owner.fx.outputs(job)[stage]
                        output['correctness'] = 'failed'
                        output['cases'][0]['passed'] = False
                        output['cases'][0]['outputs'][0].update(passed=False, tolerance_violations=1, max_abs=1.)
                    receipt = owner.fx.receipt(job, stage, output, 'failed')
                    return {'status': 'completed', 'receipt': receipt}
                return super().submit(request)
        await self.chain.run(self.store, self.root, self.spec, self.variants, None, Backend(self.fx), steps=30)
        state = self.controller.journal.load()
        jobs = [j for j in state['jobs'].values() if j['round_id'] == ROUND]
        reader = ReadOnlyStore(self.store.db_path, self.root / 'artifacts')
        rows = [verify_job(reader, job, recompute_npz=False) for job in jobs]
        for row in rows:
            if 'correctness' in row['receipt_refs']:
                row['npz_comparison_status'] = 'recomputed_from_raw_npz'
            else:
                row['npz_comparison_status'] = 'unavailable_no_correctness_arrays'
        completed = json.loads((self.target / 'completed.json').read_bytes())
        self.b2_report = self.write(self.root / 'b2-verification.json', {'schema_version': 1,
            'status': 'selected_terminal_jobs_verified', 'runtime_generation': 'runtime7', 'round_id': ROUND,
            'recompute_npz_requested': True, 'snapshot': completed['snapshot'], 'jobs': rows,
            'counts': {'verified': 2, 'pending': 0, 'failed_verification': 0}})
        return state

    def call(self):
        return self.api.verify_b2_final_memory(self.root, self.spec, self.b2_report)

    async def test_actual_completed_pair_reconciles_six_runs_all_contexts_and_grant_budget_without_writes(self):
        await self.finish()
        before = self.store.db_path.read_bytes()
        report = self.call()
        self.assertEqual(report['status'], 'verified')
        self.assertTrue(report['original_second_round_scope_complete'])
        self.assertEqual(len(report['verified_runs']), 6)
        self.assertEqual(report['final_budget']['spent'], {'executions': 17, 'tokens': 0, 'time_s': 30600})
        self.assertEqual(report['final_budget']['limits']['executions'], 18)
        self.assertEqual(report['final_budget']['remaining']['executions'], 1)
        self.assertEqual(set(report['contexts']), {'context', 'diag-context'})
        self.assertEqual(set(report['participating_histories']), {'A', 'B'})
        self.assertEqual(report['current']['projection_comparison'], 'equal_at_same_watermark')
        self.assertEqual(self.store.db_path.read_bytes(), before)

    async def test_real_compile_failure_remains_complete_feedback_without_claiming_success(self):
        await self.finish('compile')
        report = self.call()
        self.assertEqual(report['status'], 'verified')
        self.assertTrue(report['original_second_round_scope_complete'])
        self.assertIn('compile_failed', {r['classification'] for r in report['verified_runs']})
        self.assertEqual(report['b2_successful_runs'], 1)

    async def test_real_correctness_failure_is_preserved_and_bound_to_original_arrays_verification(self):
        await self.finish('correctness')
        report = self.call()
        self.assertEqual(report['status'], 'verified')
        self.assertTrue(report['original_second_round_scope_complete'])
        self.assertIn('incorrect', {r['classification'] for r in report['verified_runs']})

    async def test_generation_failure_does_not_count_as_executed_scope(self):
        await self.finish('generate')
        report = self.call()
        self.assertEqual(report['status'], 'incomplete_execution_feedback')
        self.assertFalse(report['original_second_round_scope_complete'])
        self.assertEqual(report['pending_recovery_candidates'], ['query-in-attention'])

    async def test_changed_local_completion_intake_spec_report_or_receipt_rejects_without_writes(self):
        await self.finish()
        job = next(j for j in self.controller.journal.load()['jobs'].values() if j['round_id'] == ROUND)
        from rsi_kernel_agent.contracts import ArtifactRef
        paths = [self.target / name for name in ('completed.json', 'intake.json', 'spec.json', 'execution-input.json', 'checkpoint.json')]
        paths += [self.prior_report, self.root / 'prior-verification.json', self.b2_report,
                  self.store.artifacts.path_for(ArtifactRef(**job['result']['receipt_refs']['compile']))]
        before = self.store.db_path.read_bytes()
        for path in paths:
            saved = path.read_bytes()
            try:
                with self.subTest(path=path.name):
                    path.write_bytes(b'{}')
                    with self.assertRaises((ValueError, KeyError, TypeError)):
                        self.call()
                    self.assertEqual(self.store.db_path.read_bytes(), before)
            finally:
                path.write_bytes(saved)

    async def test_later_live_note_does_not_replace_final_snapshot_watermark_or_budget(self):
        await self.finish()
        self.store.append(Decision('later', 'task', 'note', 'task', 'Later unrelated observed record'), 'later')
        report = self.call()
        self.assertEqual(report['status'], 'verified')
        self.assertFalse(report['current']['same_high_watermark'])
        self.assertEqual(report['current']['projection_comparison'], 'different_timepoints_not_equated')
        self.assertEqual(report['final_budget']['spent']['executions'], 17)


if __name__ == '__main__':
    unittest.main(verbosity=2)
