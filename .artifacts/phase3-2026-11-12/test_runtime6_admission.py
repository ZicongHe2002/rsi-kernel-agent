"""R5 zero-submission loss/R6 readmission fixtures; no production Store or TPU."""
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / 'src'), str(HERE.parents[1] / 'tests')]
import test_p35_archive_unsubmitted_runtime_loss as original_archive_tests
from rsi_kernel_agent.contracts import Decision
from rsi_kernel_agent.workflow.journal import WorkflowJournal


class Runtime5LossTests(unittest.TestCase):
    def fixture(self):
        original = original_archive_tests.ArchiveTests()
        data = original.fixture()
        self.addCleanup(original.doCleanups)
        directory, store, expected, observation, state, active = data
        (directory / 'real-round-2').rename(directory / 'real-round-2-runtime5')
        (directory / 'real-round-2-runtime5/runtime-binding.json').write_text('{"generation":"runtime5"}')
        (directory / 'colab-config-runtime5.json').write_text(json.dumps({'runtime_id': expected['runtime_id']}))
        expected = {**expected, 'prior_job_count': 9}
        self.assertIsNotNone(importlib.util.find_spec('p35_archive_runtime5_round_loss'),
                             'Runtime5 zero-submission archival helper is not implemented')
        api = importlib.import_module('p35_archive_runtime5_round_loss')
        scoped = patch.multiple(api, EXPECTED=expected,
            SPENT={'executions': 10, 'tokens': 0, 'time_s': 18000.0},
            PRIOR_SPENT={'executions': 9, 'tokens': 0, 'time_s': 16200.0})
        scoped.start()
        self.addCleanup(scoped.stop)
        return api, directory, store, observation, state, active

    def test_archive_preserves_operation_budget_old_jobs_and_unstarted_runs(self):
        api, directory, store, observation, before, active = self.fixture()
        out = api.archive_runtime5_loss(store, directory, observation)
        after = WorkflowJournal(store, 'task').load()
        self.assertEqual(after['spent'], before['spent'])
        self.assertEqual(after['limits'], before['limits'])
        self.assertFalse(after['cancelled'])
        self.assertEqual(after['jobs'][active]['operation'], before['jobs'][active]['operation'])
        self.assertEqual(after['jobs'][active]['result']['classification'], 'cancelled')
        self.assertEqual(store.run_state(after['jobs'][active]['run_id'])['execution_status'], 'incomplete')
        for key, job in before['jobs'].items():
            if job['round_id'] != api.EXPECTED['round_id']:
                self.assertEqual(after['jobs'][key], job)
            elif key != active:
                self.assertEqual(store.run_state(job['run_id'])['execution_status'], 'not_run')
                self.assertIsNone(after['jobs'][key]['result'])
        count = len(store.history('task'))
        self.assertEqual(api.archive_runtime5_loss(store, directory, observation), out)
        self.assertEqual(len(store.history('task')), count)

    def test_durable_or_orphan_request_refuses_before_archive(self):
        api, directory, store, observation, before, active = self.fixture()
        job = before['jobs'][active]
        store.append(Decision('orphan', 'task', 'note', 'task', 'Fixture orphan request',
            {'type': 'colab_execution_request.v1', 'request': job['operation']}), 'orphan')
        count = len(store.history('task'))
        with self.assertRaises(ValueError): api.archive_runtime5_loss(store, directory, observation)
        self.assertEqual(len(store.history('task')), count)

    def test_stale_journal_after_intent_refuses_before_evaluation(self):
        api, directory, store, observation, before, active = self.fixture()
        original = store._write
        def first_write(*args, **kwargs):
            value = original(*args, **kwargs)
            raise RuntimeError('stop after durable intent')
        with patch.object(store, '_write', side_effect=first_write):
            with self.assertRaises(RuntimeError): api.archive_runtime5_loss(store, directory, observation)
        journal = WorkflowJournal(store, 'task')
        state = journal.load()
        state['jobs'][active]['reason'] = 'Concurrent change must survive'
        journal.save(state, 'CPU concurrent state')
        count = len(store.history('task'))
        with patch.object(api, 'integrate_result', wraps=api.integrate_result) as integrate:
            with self.assertRaises(ValueError): api.archive_runtime5_loss(store, directory, observation)
            integrate.assert_not_called()
        self.assertEqual(len(store.history('task')), count)

    def test_preexisting_evaluation_intent_refuses_before_archival(self):
        api, directory, store, observation, before, active = self.fixture()
        job = before['jobs'][active]
        identity = api.stable_id('workflow-evaluation-intent', job['run_id'])
        store.append(Decision(identity, 'task', 'note', job['run_id'], 'CPU preexisting evaluation',
            {'type': 'workflow_evaluation_intent.v1', 'job_id': active,
             'request_digest': job['operation']['request_digest'], 'receipt_refs': {}, 'cancelled': True}), identity)
        count = len(store.history('task'))
        with self.assertRaises(ValueError): api.archive_runtime5_loss(store, directory, observation)
        self.assertEqual(len(store.history('task')), count)

    def test_snapshot_must_contain_exact_terminal_journal(self):
        api, directory, store, observation, before, active = self.fixture()
        api.archive_runtime5_loss(store, directory, observation)
        original = store.snapshot_page
        def omit_journal(*args, **kwargs):
            page = original(*args, **kwargs)
            page['records'] = [r for r in page['records'] if not
                (type(r) is Decision and r.details.get('type') == 'workflow_state.v1')]
            return page
        with patch.object(store, 'snapshot_page', side_effect=omit_journal):
            with self.assertRaises(ValueError): api.require_archived_runtime5_loss(store, directory)

    def test_cli_help_without_pythonpath_or_store(self):
        environment = {k: v for k, v in os.environ.items() if k.upper() != 'PYTHONPATH'}
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8',
            str(HERE / 'p35_archive_runtime5_round_loss.py'), '--help'],
            cwd=HERE, env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--observation', result.stdout)

    def test_crash_after_each_durable_effect_resumes_idempotently(self):
        for boundary in range(1, 15):
            api, directory, store, observation, before, active = self.fixture()
            original, calls = store._write, [0]
            def crash(*args, **kwargs):
                value = original(*args, **kwargs)
                calls[0] += 1
                if calls[0] == boundary: raise RuntimeError('injected loss')
                return value
            with patch.object(store, '_write', side_effect=crash):
                try: api.archive_runtime5_loss(store, directory, observation)
                except RuntimeError: pass
            out = api.archive_runtime5_loss(store, directory, observation)
            count = len(store.history('task'))
            self.assertEqual(api.archive_runtime5_loss(store, directory, observation), out)
            self.assertEqual(len(store.history('task')), count)
            self.assertEqual(WorkflowJournal(store, 'task').load()['spent'], before['spent'])

    def test_changed_observation_live_runtime_and_refund_refuse(self):
        api, directory, store, observation, before, active = self.fixture()
        changed = deepcopy(observation)
        changed['assignments'] = [{'runtime_id': api.EXPECTED['runtime_id'],
            'session_names': [], 'accelerator': 'TPU', 'variant': 'v6e'}]
        with self.assertRaises(ValueError): api.archive_runtime5_loss(store, directory, changed)
        out = api.archive_runtime5_loss(store, directory, observation)
        changed = deepcopy(observation)
        changed['reason'] = 'changed'
        with self.assertRaises(ValueError): api.archive_runtime5_loss(store, directory, changed)
        state = WorkflowJournal(store, 'task').load()
        state['spent']['executions'] -= 1
        WorkflowJournal(store, 'task').save(state, 'CPU refund rejection fixture')
        with self.assertRaises(ValueError): api.require_archived_runtime5_loss(store, directory)


class Runtime6Tests(unittest.TestCase):
    def fixture(self):
        from test_runtime4_admission import Runtime4Tests
        f = Runtime4Tests(methodName='test_four_new_runs_preserve_sources_and_are_idempotent')
        f.setUp()
        self.addCleanup(f.doCleanups)
        return f

    def test_runtime6_requires_r5_loss_and_preserves_prior_generation_files(self):
        f = self.fixture()
        self.assertIsNotNone(importlib.util.find_spec('p35_archive_runtime5_round_loss'))
        with patch.object(f.api, 'require_runtime4_archive', return_value=f.state), \
             patch('p35_archive_runtime4_prerequisite_loss.require_archived_runtime4_loss', return_value={}), \
             patch('p35_archive_runtime5_round_loss.require_archived_runtime5_loss', side_effect=ValueError('missing R5 archive')):
            with self.assertRaisesRegex(ValueError, 'missing R5 archive'):
                f.api.prepare_prerequisite_jobs(f.store, f.root,
                    {**f.environment4, 'runtime_session_id': 'runtime-six'}, generation='runtime6')
        old = f.root / 'baseline-b-runtime5-job.json'
        old.write_text('{"preserved":true}')
        raw = old.read_bytes()
        with patch.object(f.api, 'require_generation_archive', return_value=f.state), \
             patch.object(f.api, 'validate_origins', return_value=f.origins):
            jobs = f.api.prepare_prerequisite_jobs(f.store, f.root,
                {**f.environment4, 'runtime_session_id': 'runtime-six'}, generation='runtime6')
            count = len(f.store.history('task'))
            self.assertEqual(jobs, f.api.prepare_prerequisite_jobs(f.store, f.root,
                {**f.environment4, 'runtime_session_id': 'runtime-six'}, generation='runtime6'))
        self.assertEqual(len(f.store.history('task')), count)
        self.assertEqual(old.read_bytes(), raw)
        self.assertEqual(len({j['run_id'] for j in jobs.values()}), 4)
        for name, job in jobs.items():
            self.assertEqual(job['variant']['source_ref'], f.origins[name]['variant']['source_ref'])

    def test_runtime6_observation_uses_runtime5_as_prior(self):
        f = self.fixture()
        manifests, env, report = f.observation_files()
        env['runtime_session_id'] = 'runtime-six'
        report['environment'] = env
        for stem in ('environment-isolated', 'isolated-environment-setup', 'isolated-probe'):
            value = json.loads((manifests / (stem + '-runtime4.json')).read_text())
            if stem == 'environment-isolated': value = env
            if stem == 'isolated-probe': value['stdout'] = 'RSI_PROBE:' + json.dumps(report)
            (manifests / (stem + '-runtime6.json')).write_text(json.dumps(value))
        (f.root / 'observation-runtime6.json').write_text('{"runtime_id":"runtime-six"}')
        (f.root / 'allocation-intent.json').write_text('{"status":"allocated","runtime_id":"runtime-six","session":"s6"}')
        (f.root / 'colab-config-runtime5.json').write_text('{"runtime_id":"runtime-five","nonce":"five","session":"s5"}')
        observed, config = f.api.observed_inputs(f.root, manifests, generation='runtime6')
        self.assertEqual(observed['runtime_session_id'], 'runtime-six')
        self.assertNotEqual(config['nonce'], 'five')
        self.assertEqual(config['remote_root'], '/content/rsi-kernel-agent/phase3-20261112-runtime6')


if __name__ == '__main__': unittest.main(verbosity=2)
