"""Temporary Store checks for the P3.5 B baseline control; no remote execution."""
from copy import deepcopy
from contextlib import redirect_stdout
import importlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / 'src'), str(HERE.parents[1] / 'tests')]
from phase3_fixtures import RealProtocolFixture
from rsi_kernel_agent.contracts import Candidate, Decision, Plan, Run, Strategy, canonical_json
from rsi_kernel_agent.workflow.execution import stable_id
from runtime_readmission import remeasure
from test_runtime2_aux import definitions


class ControlAdmissionTests(RealProtocolFixture, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.prepare(self.temp.name)
        self.api = importlib.import_module('real_round_aux')
        self.admission = importlib.import_module('admit_runtime3')
        self.env = {**self.environment, 'runtime_session_id': 'new-fixture-session'}
        self.store.append(Strategy('B', 'task', 'B'), 'B')
        self.old = {'baseline-repair': self.job()}
        for name, strategy in [('baseline-b', 'B'), ('treatment', 'B'),
                               ('oracle-negative-attention', 'A'), ('oracle-negative-cache', 'A')]:
            plan = Plan('plan:' + name, strategy, 'CPU admission fixture')
            candidate = Candidate('candidate:' + name, plan.id, 'parent', self.identity)
            run = Run('run:' + name, candidate.id, 'context')
            for record in (plan, candidate, run):
                self.store.append(record, record.id)
            job = deepcopy(self.job())
            job.update(id='job:' + name, plan_id=plan.id, strategy_id=strategy,
                       candidate_id=candidate.id, run_id=run.id)
            self.old[name] = job
            if name.startswith('oracle-negative-'):
                response = self.put({'purpose': 'known-wrong real-oracle validation; not optimization',
                    'source_sha256': job['variant']['source_ref']['digest'],
                    'mechanism': {'output': name.removeprefix('oracle-negative-')}})
                note = Decision(stable_id('oracle-negative-admission', candidate.id), 'task', 'note', candidate.id,
                    'CPU gate boundary fixture', {'type': 'oracle_negative_admission.v1', 'response_ref': response})
                self.store.append(note, note.id)
        # Admission selects the explicit completed delayed-store-wait identity.
        treatment = self.old['treatment']
        original = self.store.get(treatment['candidate_id'])
        frozen = Candidate('wf-c:afa5b476cde0e73bb0bedb604b2d5c51', original.plan_id, original.parent_id, original.identity)
        self.store.append(frozen, frozen.id)
        frozen_run = Run('run:delayed', frozen.id, 'context')
        self.store.append(frozen_run, frozen_run.id)
        treatment.update(candidate_id=frozen.id, run_id=frozen_run.id, state='completed')
        for name, job in self.old.items():
            if name != 'treatment':
                self.write(name + '-runtime2-job.json', job)
        self.write('p35-confirmation-policy.json', {'version': 'p35-abba-v1',
            'min_paired_reduction': 0.02, 'max_within_role_spread': 0.05})
        self.write('colab-config-runtime3.json', {'runtime_id': self.env['runtime_session_id']})

    def write(self, name, value):
        (self.root / name).write_text(canonical_json(value), encoding='utf-8')

    def gate_jobs(self):
        jobs = {}
        for name in ('oracle-negative-attention', 'oracle-negative-cache', 'baseline-repair', 'baseline-b'):
            original = self.old['treatment'] if name == 'baseline-b' else self.old[name]
            jobs[name] = remeasure(self.store, original, self.env)
            self.write(name + '-runtime3-job.json', jobs[name])
        jobs['confirmation-control'] = remeasure(self.store, self.old['baseline-b'], self.env)
        self.write('confirmation-control-runtime3-job.json', jobs['confirmation-control'])
        return jobs

    def verify_gates(self):
        # Existing receipt verifier is separately exercised with real protocol
        # receipts; this fixture isolates control selection and identity binding.
        with patch.object(self.api, 'verified_gate_result', return_value={'confirmed': True}):
            return self.api.require_gates(self.store, self.root, {'generation': 'runtime3'})

    def test_admission_remeasures_five_distinct_runs_and_restores_same_control(self):
        if not hasattr(self.admission, 'prepare_prerequisite_jobs'):
            self.fail('Callable Runtime3 prerequisite admission is missing')
        state = {'jobs': {'treatment': self.old['treatment']}}
        with patch.object(self.admission, 'verified_gate_result', return_value={'confirmed': True}):
            jobs = self.admission.prepare_prerequisite_jobs(self.store, self.root, self.env, state)
            count = len(self.store.history('task'))
            restored = self.admission.prepare_prerequisite_jobs(self.store, self.root, self.env, state)
        self.assertEqual(jobs, restored)
        self.assertEqual(len(self.store.history('task')), count)
        self.assertEqual(len({job['run_id'] for job in jobs.values()}), 5)
        control = jobs['confirmation-control']
        self.assertEqual(control['candidate_id'], self.old['baseline-b']['candidate_id'])
        self.assertEqual(jobs['baseline-b']['candidate_id'], self.old['treatment']['candidate_id'])
        self.assertEqual(control['strategy_id'], 'B')
        self.assertEqual(control['context_id'], jobs['baseline-repair']['context_id'])
        self.assertNotEqual(control['run_id'], self.old['baseline-b']['run_id'])
        self.assertEqual(control['variant']['cost'], self.old['baseline-b']['variant']['cost'])

    def test_policy_gate_returns_independent_b_control(self):
        expected = self.gate_jobs()
        actual, results = self.verify_gates()
        self.assertEqual(set(actual), set(expected))
        self.assertEqual(set(results), set(expected))
        self.assertEqual(actual['confirmation-control'], expected['confirmation-control'])

    def test_policy_gate_rejects_missing_control(self):
        self.gate_jobs()
        (self.root / 'confirmation-control-runtime3-job.json').unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.verify_gates()

    def test_policy_gate_rejects_control_origin_or_identity_substitution(self):
        jobs = self.gate_jobs()
        original = jobs['confirmation-control']
        changes = [('strategy_id', 'A'), ('context_id', 'context'),
                   ('candidate_id', jobs['baseline-repair']['candidate_id']),
                   ('candidate_id', jobs['baseline-b']['candidate_id'])]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                changed = deepcopy(original)
                changed[field] = value
                self.write('confirmation-control-runtime3-job.json', changed)
                with self.assertRaises(ValueError):
                    self.verify_gates()
        for key in ('source_ref', 'config_ref', 'build_ref', 'source_bundle_ref'):
            with self.subTest(artifact=key):
                changed = deepcopy(original)
                changed['variant'][key] = self.put(b'substituted fixture artifact')
                self.write('confirmation-control-runtime3-job.json', changed)
                with self.assertRaises(ValueError):
                    self.verify_gates()

    def test_legacy_gate_without_policy_keeps_original_four_prerequisites(self):
        self.gate_jobs()
        (self.root / 'p35-confirmation-policy.json').unlink()
        (self.root / 'confirmation-control-runtime3-job.json').unlink()
        jobs, results = self.verify_gates()
        self.assertEqual(len(jobs), 4)
        self.assertEqual(len(results), 4)

    def test_policy_does_not_rewrite_pinned_runtime2_prerequisites(self):
        self.write('colab-config-runtime2.json', {'runtime_id': self.environment['runtime_session_id']})
        with patch.object(self.api, 'verified_gate_result', return_value={'confirmed': True}):
            jobs, results = self.api.require_gates(self.store, self.root, {'generation': 'runtime2'})
        self.assertEqual(len(jobs), 4)
        self.assertEqual(len(results), 4)
        self.assertNotIn('confirmation-control', jobs)

    def test_prerequisite_driver_executes_fifth_control_with_separate_reservation(self):
        scope = definitions('advance_runtime2.py')
        if 'advance' not in scope:
            self.fail('Callable prerequisite driver is missing')
        jobs = self.gate_jobs()
        operations = {stable_id('operation', job['run_id'], stage): (name, job, stage)
                      for name, job in jobs.items()
                      for stage in ('generate', 'compile', 'correctness', 'benchmark', 'profile')}
        owner = self
        class FixtureBackend:
            def lookup(self, operation_id):
                name, job, stage = operations[operation_id]
                output = owner.outputs(job)[stage]
                if 'environment' in output:
                    output['environment'] = owner.env
                    output['identity']['runtime_session_id'] = owner.env['runtime_session_id']
                if 'lease' in output:
                    output['lease']['runtime_session_id'] = owner.env['runtime_session_id']
                status = 'succeeded'
                if name.startswith('oracle-negative-') and stage == 'correctness':
                    status = 'failed'
                    output['correctness'] = 'failed'
                    output['cases'][0]['passed'] = False
                    output['cases'][0]['outputs'][0].update(passed=False, max_abs=1., max_rel=1., rms=1., tolerance_violations=1)
                return {'status': 'completed', 'receipt': owner.receipt(job, stage, output, status)}
        scope.update(ExperimentStore=lambda *args: self.store, ColabBackend=lambda *args: FixtureBackend(),
                     colab_remote=lambda *args: None)
        with redirect_stdout(io.StringIO()):
            scope['advance'](self.root, 'runtime3')
        reservations = [record for record in self.store.history('task') if type(record) is Decision
                        and record.details.get('type') == 'prerequisite_reservation.v1']
        self.assertEqual({record.target_id for record in reservations}, {job['run_id'] for job in jobs.values()})
        self.assertEqual(sum(record.details['cost']['executions'] for record in reservations), 5)
        self.assertEqual(self.store.run_state(jobs['confirmation-control']['run_id'])['execution_status'], 'succeeded')
        self.assertTrue(self.api.verified_gate_result(self.store, jobs['confirmation-control'])['confirmed'])
        self.assertFalse(any(type(record) is Decision and record.details.get('type') == 'workflow_state.v1'
                             for record in self.store.history('task')))


if __name__ == '__main__':
    unittest.main(verbosity=2)
