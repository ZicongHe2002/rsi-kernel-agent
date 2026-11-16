"""CPU fixtures for preserving an unknown submitted prerequisite and readmitting."""
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / 'src'), str(HERE.parents[1] / 'tests')]
from phase3_fixtures import RealProtocolFixture
from rsi_kernel_agent.contracts import Candidate, Decision, Run, RunEvent
from rsi_kernel_agent.workflow.execution import make_request, stable_id
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from oracle_gate_aux import require_resumable_run


class Runtime4UnknownLossTests(RealProtocolFixture, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.prepare(self.temp.name)
        try:
            self.api = importlib.import_module('p35_archive_runtime4_prerequisite_loss')
        except ImportError:
            self.fail('Submitted Runtime4 loss archival is not implemented')
        self.jobs = {}
        self.config4 = {'runtime_id': self.environment['runtime_session_id'], 'nonce': 'nonce-four',
                        'remote_root': '/content/runtime-four'}
        (self.root / 'colab-config-runtime4.json').write_text(json.dumps(self.config4))
        for name in self.api.NAMES:
            candidate, run = 'c-' + name, 'r-' + name
            self.store.append(Candidate(candidate, 'plan', 'parent', self.identity), candidate)
            self.store.append(Run(run, candidate, 'context'), run)
            job = deepcopy(self.job())
            job.update(id='j-' + name, candidate_id=candidate, run_id=run)
            self.jobs[name] = job
            (self.root / (name + '-runtime4-job.json')).write_text(json.dumps(job))
        self.active = self.jobs['oracle-negative-attention']
        self.store.transition(RunEvent('active-start', self.active['run_id'], 'running', 'active', 'fixture'),
                              None, 'active-start')
        self.outputs4 = self.outputs(self.active)
        self.receipts = {}
        for stage in ('generate', 'compile', 'correctness'):
            request = make_request(self.store, self.active, stage)
            rid = stable_id('execution-request', request['operation_id'])
            self.store.append(Decision(rid, 'task', 'note', self.active['run_id'], 'fixture request',
                {'type': 'colab_execution_request.v1', 'request': request, **self.config4}), rid)
            if stage != 'correctness':
                self.receipts[stage] = self.receipt(self.active, stage, self.outputs4[stage])
        self.journal = WorkflowJournal(self.store, 'task')
        self.state = self.journal.save({'task_id': 'task', 'revision': 0, 'cancelled': False, 'jobs': {},
            'limits': {'executions': 16, 'tokens': 100000, 'time_s': 28800.0, 'max_concurrency': 1,
                       'max_repairs': 1, 'max_retries': 1, 'weights': {}},
            'spent': {'executions': 10, 'tokens': 0, 'time_s': 18000.0}}, 'fixture budget')
        self.observation = {'schema_version': 1, 'runtime_id': self.config4['runtime_id'],
            'observed_at_utc': '2026-11-13T12:40:45Z', 'control_plane_reachable': True,
            'assignments': [], 'driver_processes_stopped': True,
            'reason': 'Control plane absent, original correctness lookup unavailable; result unknown'}

    def archive(self):
        with patch.object(self.api, 'require_prior_archive', return_value=self.state):
            return self.api.archive_runtime4_loss(self.store, self.root, self.observation)

    def test_unknown_submission_preserved_no_evaluation_or_budget_change_and_replay(self):
        before = {r.id: r for r in self.store.history('task')}
        output = self.archive()
        after = {r.id: r for r in self.store.history('task')}
        self.assertTrue(all(after[key] == record for key, record in before.items()))
        self.assertEqual(self.journal.load(), self.state)
        self.assertEqual(self.store.run_state(self.active['run_id'])['execution_status'], 'incomplete')
        self.assertEqual(output['summary']['remote_correctness_outcome'], 'unknown')
        self.assertEqual(output['summary']['durable_request_stages'], ['generate', 'compile', 'correctness'])
        self.assertEqual(output['summary']['receipt_stages'], ['generate', 'compile'])
        self.assertEqual({type(r).__name__ for key, r in after.items() if key not in before}, {'Decision', 'RunEvent'})
        for name in self.api.NAMES[1:]:
            self.assertEqual(self.store.run_state(self.jobs[name]['run_id'])['execution_status'], 'not_run')
        with self.assertRaisesRegex(RuntimeError, 'Terminal Run'):
            require_resumable_run(self.store, self.active)
        count = len(after)
        self.assertEqual(self.archive(), output)
        self.assertEqual(len(self.store.history('task')), count)
        with patch.object(self.store, '_write', side_effect=AssertionError('read-only guard wrote')):
            self.assertEqual(self.api.require_archived_runtime4_loss(self.store, self.root), output)

    def test_completed_correctness_rejected_before_any_write(self):
        self.receipt(self.active, 'correctness', self.outputs4['correctness'])
        before = len(self.store.history('task'))
        with self.assertRaises(ValueError):
            self.archive()
        self.assertEqual(len(self.store.history('task')), before)

    def test_live_runtime_rejected_before_write(self):
        self.observation['assignments'] = [{'runtime_id': self.config4['runtime_id'],
            'session_names': ['old'], 'accelerator': 'V6E1', 'variant': 'TPU'}]
        before = len(self.store.history('task'))
        with self.assertRaises(ValueError):
            self.archive()
        self.assertEqual(len(self.store.history('task')), before)

    def test_crash_after_each_durable_effect_recovers_without_duplicate_records(self):
        # Intent, original RunEvent transition, result marker: each must resume.
        for boundary in (1, 2, 3):
            for after in (False, True):
                with self.subTest(boundary=boundary, after=after):
                    fixture = Runtime4UnknownLossTests(methodName='test_unknown_submission_preserved_no_evaluation_or_budget_change_and_replay')
                    fixture.setUp()
                    self.addCleanup(fixture.doCleanups)
                    original = fixture.store._write
                    calls = 0
                    def fail(operation, value, key, action):
                        nonlocal calls
                        calls += 1
                        if calls == boundary and not after:
                            raise RuntimeError('injected archive effect')
                        result = original(operation, value, key, action)
                        if calls == boundary and after:
                            raise RuntimeError('injected archive effect')
                        return result
                    with patch.object(fixture.store, '_write', side_effect=fail):
                        with self.assertRaisesRegex(RuntimeError, 'injected'):
                            fixture.archive()
                    output = fixture.archive()
                    count = len(fixture.store.history('task'))
                    self.assertEqual(fixture.archive(), output)
                    self.assertEqual(len(fixture.store.history('task')), count)

    def test_output_file_crash_recovers_and_late_receipt_invalidates_readonly_guard(self):
        with patch.object(self.api, 'write_once', side_effect=RuntimeError('output crash')):
            with self.assertRaisesRegex(RuntimeError, 'output crash'):
                self.archive()
        self.archive()
        self.receipt(self.active, 'correctness', self.outputs4['correctness'])
        before = len(self.store.history('task'))
        with self.assertRaisesRegex(ValueError, 'Correctness has completed'):
            self.api.require_archived_runtime4_loss(self.store, self.root)
        self.assertEqual(len(self.store.history('task')), before)

    def test_changed_nonce_and_started_other_run_reject_without_archive_writes(self):
        self.config4['nonce'] = 'changed'
        (self.root / 'colab-config-runtime4.json').write_text(json.dumps(self.config4))
        before = len(self.store.history('task'))
        with self.assertRaisesRegex(ValueError, 'ledger mapping'):
            self.archive()
        self.assertEqual(len(self.store.history('task')), before)
        self.config4['nonce'] = 'nonce-four'
        (self.root / 'colab-config-runtime4.json').write_text(json.dumps(self.config4))
        other = self.jobs['oracle-negative-cache']['run_id']
        self.store.transition(RunEvent('other-start', other, 'running', 'active', 'fixture'), None, 'other-start')
        before = len(self.store.history('task'))
        with self.assertRaisesRegex(ValueError, 'was started'):
            self.archive()
        self.assertEqual(len(self.store.history('task')), before)

    def test_misbound_request_nested_run_is_refused(self):
        request = make_request(self.store, self.active, 'benchmark')
        request['operation_id'] = 'misbound-operation'
        record = Decision('orphan-request', 'task', 'note', 'task', 'fixture orphan',
                          {'type': 'colab_execution_request.v1', 'request': request})
        self.store.append(record, record.id)
        before = len(self.store.history('task'))
        with self.assertRaisesRegex(ValueError, 'orphan'):
            self.archive()
        self.assertEqual(len(self.store.history('task')), before)


class Runtime5ObservationTests(unittest.TestCase):
    def test_runtime5_jobs_keep_old_files_and_sources_and_require_loss_gate(self):
        from test_runtime4_admission import Runtime4Tests
        fixture = Runtime4Tests(methodName='test_four_new_runs_preserve_sources_and_are_idempotent')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        old_file = fixture.root / 'baseline-b-runtime4-job.json'
        old_file.write_text('{"preserved":"runtime4"}')
        before = old_file.read_bytes()
        environment5 = {**fixture.environment4, 'runtime_session_id': 'runtime-five'}
        with patch.object(fixture.api, 'require_runtime4_archive', return_value=fixture.state), \
             patch('p35_archive_runtime4_prerequisite_loss.require_archived_runtime4_loss', side_effect=ValueError('missing Runtime4 loss')):
            count = len(fixture.store.history('task'))
            with self.assertRaisesRegex(ValueError, 'missing Runtime4 loss'):
                fixture.api.prepare_prerequisite_jobs(fixture.store, fixture.root, environment5, generation='runtime5')
            self.assertEqual(len(fixture.store.history('task')), count)
        with patch.object(fixture.api, 'require_generation_archive', return_value=fixture.state), \
             patch.object(fixture.api, 'validate_origins', return_value=fixture.origins):
            jobs = fixture.api.prepare_prerequisite_jobs(fixture.store, fixture.root, environment5, generation='runtime5')
            count = len(fixture.store.history('task'))
            self.assertEqual(jobs, fixture.api.prepare_prerequisite_jobs(fixture.store, fixture.root, environment5, generation='runtime5'))
        self.assertEqual(len(fixture.store.history('task')), count)
        self.assertEqual(old_file.read_bytes(), before)
        for name, job in jobs.items():
            self.assertTrue((fixture.root / (name + '-runtime5-job.json')).is_file())
            self.assertEqual(job['variant']['source_ref'], fixture.origins[name]['variant']['source_ref'])
            self.assertNotEqual(job['run_id'], fixture.origins[name]['run_id'])
            self.assertNotEqual(job['context_id'], fixture.origins[name]['context_id'])

    def test_runtime5_uses_separate_pin_config_and_nonce(self):
        from test_runtime4_admission import Runtime4Tests
        fixture = Runtime4Tests(methodName='test_four_new_runs_preserve_sources_and_are_idempotent')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        manifests, env, report = fixture.observation_files()
        env['runtime_session_id'] = 'runtime-five'
        report['environment'] = env
        for stem in ('environment-isolated', 'isolated-environment-setup', 'isolated-probe'):
            source = manifests / (stem + '-runtime4.json')
            value = json.loads(source.read_text())
            if stem == 'environment-isolated': value = env
            if stem == 'isolated-probe': value['stdout'] = 'RSI_PROBE:' + json.dumps(report)
            (manifests / (stem + '-runtime5.json')).write_text(json.dumps(value))
        (fixture.root / 'observation-runtime5.json').write_text('{"runtime_id":"runtime-five"}')
        (fixture.root / 'allocation-intent.json').write_text('{"status":"allocated","runtime_id":"runtime-five","session":"session-five"}')
        (fixture.root / 'colab-config-runtime4.json').write_text('{"runtime_id":"runtime-four","nonce":"four","session":"session"}')
        observed, config = fixture.api.observed_inputs(fixture.root, manifests, generation='runtime5')
        self.assertEqual(observed['runtime_session_id'], 'runtime-five')
        self.assertEqual(config['remote_root'], '/content/rsi-kernel-agent/phase3-20261112-runtime5')
        self.assertNotEqual(config['nonce'], 'four')
        self.assertEqual(json.loads((fixture.root / 'observation-runtime4.json').read_text()), {'runtime_id': 'runtime-four'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
