"""Local protocol fixtures for the P3.5 round2 gate; no TPU or live Store."""
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / 'src'), str(ROOT / 'tests')]
from phase3_fixtures import RealProtocolFixture
from rsi_kernel_agent.contracts import ArtifactRef, Candidate, CandidateIdentity, Decision, EvaluationContext, Plan, Run, RunEvent, Strategy, canonical_json
from rsi_kernel_agent.memory import MemoryReader
from rsi_kernel_agent.workflow.execution import execution_contract, stable_id
from rsi_kernel_agent.workflow.evaluation import integrate_result
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from real_round_aux import LIMITS
from p35_confirmation_statistics import analyze_abba

POLICY = {'version': 'p35-abba-v1', 'min_paired_reduction': .02, 'max_within_role_spread': .05}


class ConfirmationGateTests(RealProtocolFixture, unittest.TestCase):
    def setUp(self):
        self.prepare_confirmation_fixture()

    def prepare_confirmation_fixture(self, *, intake_before_policy=False):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prepare(self.tmp.name)
        try:
            self.api = importlib.import_module('p35_round2_gate')
        except ImportError:
            self.fail('P3.5 round2 confirmation gate is not implemented')
        self.directory = Path(self.tmp.name)
        self.policy_path = self.directory / 'p35-confirmation-policy.json'
        self.policy_path.write_text(canonical_json(POLICY), encoding='utf-8')
        self.protocol['benchmark'].update(version='mla-contiguous-host-sync-v1', warmup=5, measurements=20)
        self.store.append(EvaluationContext('p35-context', 'task', 'workload', self.environment, self.protocol, 'real'), 'p35-context')
        self.store.append(Strategy('B', 'task', 'B'), 'B')
        self.store.append(Plan('confirmation-plan', 'B', 'Explicit fixed-byte ABBA replication'), 'confirmation-plan')
        self.jobs = []
        self.rows = []
        self.selection = {'schema_version': 1, 'task_id': 'task', 'context_id': 'p35-context', 'strategy_id': 'B',
            'candidate_id': 'origin-treatment', 'baseline_candidate_id': 'origin-control',
            'round_id': 'confirmation:p35', 'previous_round_ids': ['real-runtime2-round:1'], 'knowledge': [],
            'plan_roles': {'baseline': 'control', 'candidate': 'repeat'},
            'sequence': [{'role': role, 'label': 'replica-' + str(index), 'priority': 40 - 10 * index}
                         for index, role in enumerate(('baseline', 'candidate', 'candidate', 'baseline'))]}
        for cid in ('origin-control', 'origin-treatment'):
            self.store.append(Candidate(cid, 'confirmation-plan', 'parent', self.identity), cid)
        self.policy_ref = self.put(POLICY)
        policy_id = stable_id('p35-confirmation-policy', 'task', self.selection['round_id'])
        policy_note = Decision(policy_id, 'task', 'note', 'task', 'Predeclared local fixture policy',
            {'type': 'p35_confirmation_policy.v1', 'selection': self.selection, 'policy_ref': self.policy_ref})
        if intake_before_policy:
            intake = Decision('confirmation-intake', 'task', 'note', 'task', 'Local pre-policy intake',
                {'type': 'workflow_intake.v1', 'result': {'round_id': self.selection['round_id']}})
            self.store.append(intake, intake.id)
        self.store.append(policy_note, policy_note.id)
        for index, role in enumerate(('control', 'treatment', 'treatment', 'control')):
            cid, rid = 'replica-' + str(index), 'replica-run-' + str(index)
            self.store.append(Candidate(cid, 'confirmation-plan', 'parent', self.identity), cid)
            self.store.append(Run(rid, cid, 'p35-context'), rid)
            contract, digest = execution_contract(self.store, 'p35-context')
            job = {**self.job(), 'id': 'replica-job-' + str(index), 'round_id': self.selection['round_id'],
                'strategy_id': 'B', 'plan_id': 'confirmation-plan', 'candidate_id': cid, 'run_id': rid,
                'context_id': 'p35-context', 'contract': contract, 'contract_digest': digest,
                'variant': {**deepcopy(self.variant), 'label': 'replica-' + str(index)},
                'state': 'completed', 'reserved': True}
            self.store.transition(RunEvent(rid + ':start', rid, 'running', 'active', 'Local protocol fixture'), None, rid + ':start')
            outputs = self.outputs(job)
            outputs['benchmark'].update(samples_ms=[3.] * 20, latency_ms=3., warmup_count=5)
            for stage in ('benchmark', 'profile'):
                outputs[stage]['lease']['started_ns'] += 100 * index
                outputs[stage]['lease']['finished_ns'] += 100 * index
            report = json.loads(self.store.artifacts.read(ArtifactRef(**outputs['profile']['report_ref'])))
            original = report['groups'][0]
            report['calls'] = 20
            report['groups'] = []
            for kind, value in (('program', 6.), ('prepare', .7), ('attention', 5.)):
                samples = [{'duration_ps': value * 1e6, 'device_offset_ps': n * 10000000, 'step_num': n, 'run_id': n} for n in range(20)]
                report['groups'].append({**deepcopy(original), 'kind': 'program' if kind == 'program' else 'kernel',
                    'name': kind, 'count': 20, 'mean_us': value, 'median_us': value, 'p95_us': value,
                    'min_us': value, 'max_us': value, 'samples': samples})
            outputs['profile']['report_ref'] = self.put(report)
            receipts = {stage: self.receipt(job, stage, output) for stage, output in outputs.items()}
            job['result'] = integrate_result(self.store, job, receipts)
            job['receipts'] = receipts
            self.jobs.append(job)
            self.rows.append({'role': role, 'job_id': job['id'], 'label': job['variant']['label'],
                'candidate_id': cid, 'run_id': rid, 'context_id': 'p35-context',
                'runtime_id': self.environment['runtime_session_id'], 'protocol_version': 'mla-contiguous-host-sync-v1',
                'input_digest': outputs['correctness']['input_digest'], 'source_digest': self.identity.source_digest,
                'config_digest': self.identity.config_digest, 'build_digest': self.identity.build_digest,
                'raw_samples_ms': [3.] * 20, 'profile_samples_us': {'program': [6.] * 20, 'prepare': [.7] * 20, 'attention': [5.] * 20},
                'profile_run_id': rid, 'profile_capture_operation_id': outputs['profile']['capture_operation_id'],
                'benchmark_started_ns': 10 + 100 * index, 'benchmark_finished_ns': 20 + 100 * index,
                'profile_started_ns': 20 + 100 * index, 'profile_finished_ns': 30 + 100 * index})
        state = {'task_id': 'task', 'revision': 0, 'limits': LIMITS, 'jobs': {j['id']: j for j in self.jobs}}
        WorkflowJournal(self.store, 'task').save(state, 'Local confirmed fixture jobs')

    def publish(self, *, rows=None, summary=None):
        rows = self.rows if rows is None else rows
        stats = analyze_abba(rows, POLICY)
        report = {'schema_version': 1, 'selection': self.selection, 'policy': POLICY,
                  'statistics': stats, 'rows': rows, 'verification_limits': ['Local protocol fixture only']}
        ref = self.put(report)
        policy_ref = self.put(POLICY)
        marker = Decision('confirmation-result', 'task', 'note', 'task', 'Local confirmation fixture',
            {'type': 'p35_confirmation_result.v1', 'artifact_ref': ref, 'policy_ref': policy_ref,
             'context_id': 'p35-context', 'round_id': self.selection['round_id'], 'run_ids': [r['run_id'] for r in rows]})
        self.store.append(marker, marker.id)
        associations = {}
        for sid in ('A', 'B'):
            note = Decision('confirmation-association-' + sid, 'task', 'note', sid, 'Shared confirmation conclusion',
                {'type': 'p35_confirmation_association.v1', 'confirmation_id': marker.id,
                 'artifact_ref': ref, 'context_id': 'p35-context', 'summary': stats if summary is None else summary})
            self.store.append(note, note.id)
            associations[sid] = note.id
        pointer = {'decision_id': marker.id, 'artifact_ref': ref, 'strategy_note_ids': associations}
        (self.directory / 'p35-confirmation-result.json').write_text(canonical_json(pointer), encoding='utf-8')
        return report

    def frozen(self):
        snap = self.store.create_snapshot('task', 'p35:prepared')
        view = MemoryReader(self.store).open_snapshot(snap['id'])
        return {'snapshot': snap, 'histories': view.histories(('A', 'B'))}

    def gate(self, prepared=None):
        return self.api.require_confirmation(self.store, self.directory, 'task', 'p35-context', ['A', 'B'], prepared=prepared)

    def test_absent_policy_cannot_bypass_round2_confirmation(self):
        self.policy_path.unlink()
        before = len(self.store.history('task'))
        with self.assertRaisesRegex(ValueError, 'policy'):
            self.gate()
        self.assertEqual(len(self.store.history('task')), before)

    def test_missing_result_blocks_without_new_records_or_snapshot(self):
        before = len(self.store.history('task'))
        with self.assertRaisesRegex(ValueError, 'confirmation'):
            self.gate()
        self.assertEqual(len(self.store.history('task')), before)
        with self.store._connection() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0], 0)

    def test_receipt_row_reads_exact_original_measurements(self):
        self.assertEqual(self.api.receipt_row(self.store, self.jobs[0], 'control'), self.rows[0])

    def test_complete_not_confirmed_result_is_admissible_and_shared_in_history(self):
        report = self.publish()
        self.assertEqual(report['statistics']['outcome'], 'neither_confirmed')
        before = len(self.store.history('task'))
        self.gate()
        self.assertEqual(len(self.store.history('task')), before)
        prepared = self.frozen()
        self.gate(prepared)
        for sid in ('A', 'B'):
            notes = [r['payload'] for r in prepared['histories'][sid]['records'] if r['payload']['id'] == 'confirmation-association-' + sid]
            self.assertEqual(notes[0]['details']['summary']['outcome'], 'neither_confirmed')

    def test_snapshot_before_confirmation_cannot_be_reused(self):
        prepared = self.frozen()
        self.publish()
        with self.assertRaisesRegex(ValueError, 'snapshot'):
            self.gate(prepared)

    def test_missing_strategy_history_association_rejected(self):
        self.publish()
        prepared = self.frozen()
        prepared['histories']['A']['records'] = [r for r in prepared['histories']['A']['records'] if r['payload']['id'] != 'confirmation-association-A']
        with self.assertRaisesRegex(ValueError, 'history'):
            self.gate(prepared)

    def test_forged_summary_rejected(self):
        self.publish(summary={'outcome': 'host_and_program'})
        with self.assertRaisesRegex(ValueError, 'summary'):
            self.gate()

    def test_forged_raw_measurement_rejected_against_receipts(self):
        rows = deepcopy(self.rows)
        rows[0]['raw_samples_ms'] = [2.] * 20
        self.publish(rows=rows)
        with self.assertRaisesRegex(ValueError, 'receipt'):
            self.gate()

    def test_changed_policy_rejected(self):
        self.publish()
        self.policy_path.write_text(canonical_json({**POLICY, 'min_paired_reduction': .01}), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'policy'):
            self.gate()

    def test_pending_job_rejected(self):
        self.publish()
        journal = WorkflowJournal(self.store, 'task')
        state = journal.load()
        state['jobs'][self.jobs[1]['id']]['state'] = 'blocked'
        journal.save(state, 'Local interrupted fixture')
        with self.assertRaisesRegex(ValueError, 'completed'):
            self.gate()

    def test_controller_result_must_match_immutable_completion(self):
        self.publish()
        journal = WorkflowJournal(self.store, 'task')
        state = journal.load()
        state['jobs'][self.jobs[0]['id']]['result']['classification'] = 'incorrect'
        journal.save(state, 'Local inconsistent completion projection')
        with self.assertRaisesRegex(ValueError, 'result'):
            self.gate()

    def test_later_policy_note_cannot_replace_predeclared_policy(self):
        self.publish()
        note = Decision('late-policy', 'task', 'note', 'task', 'Too late local policy',
            {'type': 'p35_confirmation_policy.v1', 'selection': self.selection, 'policy_ref': self.policy_ref})
        self.store.append(note, note.id)
        path = self.directory / 'p35-confirmation-result.json'
        pointer = json.loads(path.read_bytes())
        pointer['policy_decision_id'] = note.id
        path.write_text(canonical_json(pointer), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'policy'):
            self.gate()

    def test_policy_after_intake_but_before_runs_is_rejected(self):
        self.prepare_confirmation_fixture(intake_before_policy=True)
        self.publish()
        before = len(self.store.history('task'))
        with self.assertRaisesRegex(ValueError, 'policy.*intake'):
            self.gate()
        self.assertEqual(len(self.store.history('task')), before)

    def test_raw_receipt_corruption_blocks_confirmation(self):
        self.publish()
        ref = ArtifactRef(**self.jobs[0]['result']['receipt_refs']['benchmark'])
        self.store.artifacts.path_for(ref).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            self.gate()

    def test_role_origin_swap_with_same_labels_is_rejected(self):
        changed_source = self.put(b'def different_kernel(x): return x + 1\n')
        different = CandidateIdentity(changed_source['digest'], self.identity.config_digest, self.identity.build_digest)
        self.store.append(Candidate('different-origin', 'confirmation-plan', 'parent', different), 'different-origin')
        selection = {**self.selection, 'baseline_candidate_id': 'different-origin'}
        if not hasattr(self.api, 'validate_origin'):
            self.fail('Confirmation role origin identity is not validated')
        with self.assertRaisesRegex(ValueError, 'origin'):
            self.api.validate_origin(self.store, self.jobs[0], selection, 'control')
        self.api.validate_origin(self.store, self.jobs[1], selection, 'treatment')

    def test_role_origin_cannot_borrow_other_strategy_candidate(self):
        selection = {**self.selection, 'baseline_candidate_id': 'candidate'}
        if not hasattr(self.api, 'validate_origin'):
            self.fail('Confirmation origin strategy is not validated')
        with self.assertRaisesRegex(ValueError, 'strategy'):
            self.api.validate_origin(self.store, self.jobs[0], selection, 'control')

    def test_confirmation_cannot_omit_an_extra_same_round_job(self):
        self.publish()
        journal = WorkflowJournal(self.store, 'task')
        state = journal.load()
        extra = deepcopy(self.jobs[0])
        extra.update(id='extra-unreported-job', state='blocked')
        state['jobs'][extra['id']] = extra
        journal.save(state, 'Local extra unreported confirmation attempt')
        with self.assertRaisesRegex(ValueError, 'jobs'):
            self.gate()


class MissingResultDriverTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_and_publish_round2_reject_missing_confirmation_before_writes(self):
        from phase2d_fixtures import make_workflow_fixture
        with tempfile.TemporaryDirectory() as root:
            data = await make_workflow_fixture(root)
            directory = Path(root)
            (directory / 'p35-confirmation-policy.json').write_text(canonical_json(POLICY), encoding='utf-8')
            gates = {'baseline-repair': {'task_id': 'task', 'context_id': 'context', 'strategy_id': 'A'},
                     'baseline-b': {'task_id': 'task', 'context_id': 'context', 'strategy_id': 'B'}}
            store = data['store']
            before = len(store.history('task'))
            for name in ('prepare_real_round', 'run_real_round'):
                script = importlib.import_module(name)
                with patch.object(script, 'require_gates', return_value=(gates, {})):
                    with self.assertRaisesRegex(ValueError, 'confirmation'):
                        if name == 'prepare_real_round':
                            await script.prepare(2, directory)
                        else:
                            await script.run(2, directory / 'absent-variants.json', directory=directory)
                self.assertEqual(len(store.history('task')), before)
                self.assertFalse((directory / 'real-round-2').exists())

    async def test_round2_missing_policy_cannot_bypass_gate_before_writes(self):
        from phase2d_fixtures import make_workflow_fixture
        with tempfile.TemporaryDirectory() as root:
            data = await make_workflow_fixture(root)
            directory = Path(root)
            gates = {'baseline-repair': {'task_id': 'task', 'context_id': 'context', 'strategy_id': 'A'},
                     'baseline-b': {'task_id': 'task', 'context_id': 'context', 'strategy_id': 'B'}}
            before = len(data['store'].history('task'))
            for name in ('prepare_real_round', 'run_real_round'):
                script = importlib.import_module(name)
                with patch.object(script, 'require_gates', return_value=(gates, {})):
                    with self.assertRaisesRegex(ValueError, 'policy'):
                        if name == 'prepare_real_round':
                            await script.prepare(2, directory)
                        else:
                            await script.run(2, directory / 'absent-variants.json', directory=directory)
                self.assertEqual(len(data['store'].history('task')), before)
                self.assertFalse((directory / 'real-round-2').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
