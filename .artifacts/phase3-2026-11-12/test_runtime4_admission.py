"""Runtime 4 auxiliary fixtures; no live Store, bridge or TPU access."""
from contextlib import contextmanager
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
from rsi_kernel_agent.contracts import Candidate, Run


class ObservationArgumentsTests(unittest.TestCase):
    def test_generations_and_legacy_flags(self):
        api = importlib.import_module('runtime_readmission')
        self.assertTrue(hasattr(api, 'parse_observation_args'))
        for argv, suffix in [([], ''), (['--runtime2'], '-runtime2'),
                             (['--runtime3'], '-runtime3'), (['--generation', 'runtime4'], '-runtime4')]:
            self.assertEqual(api.parse_observation_args(argv).suffix, suffix)
        with self.assertRaises(SystemExit):
            api.parse_observation_args(['--runtime3', '--generation', 'runtime4'])
        with self.assertRaises(SystemExit):
            api.parse_observation_args(['--generation', '../runtime3'])

    def test_runtime4_pin_does_not_overwrite_runtime3(self):
        api = importlib.import_module('runtime_readmission')
        with tempfile.TemporaryDirectory() as temp:
            d = Path(temp)
            (d / 'observation-runtime3.json').write_text('{"runtime_id":"old"}')
            before = (d / 'observation-runtime3.json').read_bytes()
            api.pin_observation_runtime(d, '-runtime4', {'status': 'allocated', 'runtime_id': 'new'})
            self.assertEqual((d / 'observation-runtime3.json').read_bytes(), before)
            self.assertEqual(json.loads((d / 'observation-runtime4.json').read_text()), {'runtime_id': 'new'})


class Runtime4Tests(RealProtocolFixture, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.prepare(self.temp.name)
        try:
            self.api = importlib.import_module('admit_runtime4')
        except ImportError:
            self.fail('Runtime 4 admission is not implemented')
        self.environment4 = {**self.environment, 'runtime_session_id': 'runtime-four'}
        self.state = {'task_id': 'task', 'cancelled': False, 'jobs': {}}
        self.origins = {}
        for name in self.api.NAMES:
            candidate_id, run_id = 'candidate-' + name, 'run-' + name
            self.store.append(Candidate(candidate_id, 'plan', 'parent', self.identity), candidate_id)
            self.store.append(Run(run_id, candidate_id, 'context'), run_id)
            old = deepcopy(self.job())
            old.update(id='job-' + name, candidate_id=candidate_id, run_id=run_id)
            self.origins[name] = old
            (self.root / (name + '-runtime3-job.json')).write_text(json.dumps(old))

    def test_missing_archive_and_unfinished_state_reject_before_remeasure(self):
        for archive, state in [(ValueError('missing archive'), self.state),
                               ({'summary': {}}, {**self.state, 'jobs': {'x': {'state': 'active'}}})]:
            before = len(self.store.history('task'))
            with patch.object(self.api, 'archived_runtime_loss', side_effect=archive if isinstance(archive, Exception) else None,
                              return_value=archive), patch.object(self.api, 'load_task_state', return_value=state):
                with self.assertRaises(ValueError):
                    self.api.prepare_prerequisite_jobs(self.store, self.root, self.environment4)
            self.assertEqual(len(self.store.history('task')), before)

    def test_all_origins_validated_before_any_new_record(self):
        before = len(self.store.history('task'))
        with patch.object(self.api, 'require_runtime4_archive', return_value=self.state), \
             patch.object(self.api, 'validate_origins', side_effect=ValueError('last source mismatch')):
            with self.assertRaises(ValueError):
                self.api.prepare_prerequisite_jobs(self.store, self.root, self.environment4)
        self.assertEqual(len(self.store.history('task')), before)

    def test_four_new_runs_preserve_sources_and_are_idempotent(self):
        with patch.object(self.api, 'require_runtime4_archive', return_value=self.state), \
             patch.object(self.api, 'validate_origins', return_value=self.origins):
            jobs = self.api.prepare_prerequisite_jobs(self.store, self.root, self.environment4)
            count = len(self.store.history('task'))
            self.assertEqual(jobs, self.api.prepare_prerequisite_jobs(self.store, self.root, self.environment4))
        self.assertEqual(len(self.store.history('task')), count)
        self.assertEqual(len({job['run_id'] for job in jobs.values()}), 4)
        for name, job in jobs.items():
            old = self.origins[name]
            self.assertNotEqual(job['run_id'], old['run_id'])
            self.assertNotEqual(job['context_id'], old['context_id'])
            for key in ('source_ref', 'config_ref', 'build_ref', 'source_bundle_ref'):
                self.assertEqual(job['variant'][key], old['variant'][key])
            self.assertIsNone(job['result'])
            self.assertEqual(job['receipts'], {})

    def test_last_saved_job_conflict_rejects_before_new_records(self):
        before = len(self.store.history('task'))
        with patch.object(self.api, 'require_runtime4_archive', return_value=self.state), \
             patch.object(self.api, 'validate_origins', return_value=self.origins):
            _, jobs = self.api.preflight_jobs(self.store, self.root, self.environment4)
            wrong = deepcopy(jobs['baseline-b'])
            wrong['reason'] = 'changed admission'
            (self.root / 'baseline-b-runtime4-job.json').write_text(json.dumps(wrong))
            with self.assertRaisesRegex(ValueError, 'Saved Runtime 4 job differs'):
                self.api.prepare_prerequisite_jobs(self.store, self.root, self.environment4)
        self.assertEqual(len(self.store.history('task')), before)

    def observation_files(self):
        manifests = self.root / 'manifests'
        manifests.mkdir()
        environment = {'runtime_session_id': 'runtime-four', 'python': '3.11', 'jax': '0.8.1',
                       'jaxlib': '0.8.1', 'libtpu': '0.0.30', 'tpu_model': 'TPU v6e',
                       'device_count': 1, 'process_count': 1,
                       'devices': [{'platform': 'tpu', 'device_kind': 'TPU v6e'}]}
        report = {'environment': environment, 'result': 140.0, 'synchronized': True, 'xplane_parser_import': True}
        values = {
            self.root / 'allocation-intent.json': {'status': 'allocated', 'runtime_id': 'runtime-four', 'session': 'session'},
            self.root / 'observation-runtime4.json': {'runtime_id': 'runtime-four'},
            self.root / 'colab-config-runtime3.json': {'runtime_id': 'runtime-three', 'nonce': 'old', 'session': 'session'},
            manifests / 'environment-isolated-runtime4.json': environment,
            manifests / 'isolated-environment-setup-runtime4.json': {'status': 'ready'},
            manifests / 'isolated-probe-runtime4.json': {'returncode': 0, 'stdout': 'RSI_PROBE:' + json.dumps(report)},
        }
        for path, value in values.items():
            path.write_text(json.dumps(value))
        return manifests, environment, report

    def test_observation_requires_synchronized_success_and_exact_environment(self):
        manifests, environment, report = self.observation_files()
        observed, config = self.api.observed_inputs(self.root, manifests)
        self.assertEqual(observed, environment)
        self.assertEqual(config['runtime_id'], 'runtime-four')
        self.assertEqual(config['remote_root'], self.api.REMOTE_ROOT)
        self.assertNotEqual(config['nonce'], 'old')
        self.assertFalse((self.root / 'colab-config-runtime4.json').exists())
        report['synchronized'] = False
        (manifests / 'isolated-probe-runtime4.json').write_text(json.dumps(
            {'returncode': 0, 'stdout': 'RSI_PROBE:' + json.dumps(report)}))
        with self.assertRaisesRegex(ValueError, 'measured probe'):
            self.api.observed_inputs(self.root, manifests)

    def test_old_runtime_and_saved_config_session_change_reject_without_writes(self):
        manifests, _, _ = self.observation_files()
        _, config = self.api.observed_inputs(self.root, manifests)
        config['session'] = 'wrong-session'
        path = self.root / 'colab-config-runtime4.json'
        path.write_text(json.dumps(config))
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'Saved Runtime 4 config differs'):
            self.api.observed_inputs(self.root, manifests)
        self.assertEqual(path.read_bytes(), before)
        old = self.root / 'colab-config-runtime3.json'
        old.write_text('{"runtime_id":"runtime-four","nonce":"old"}')
        with self.assertRaisesRegex(ValueError, 'new pinned Runtime'):
            self.api.observed_inputs(self.root, manifests)


class AdvanceGuardTests(unittest.TestCase):
    def test_guard_runs_under_lock_before_backend_creation(self):
        api = importlib.import_module('advance_runtime2')
        events = []

        class Journal:
            def __init__(self, *_):
                pass

            @contextmanager
            def lock(self):
                events.append('locked')
                yield

        def guard(store, directory, generation):
            self.assertEqual(events, ['locked'])
            self.assertEqual(generation, 'runtime4')
            raise ValueError('guard refused')

        with tempfile.TemporaryDirectory() as temp:
            d = Path(temp)
            (d / 'oracle-negative-attention-runtime4-job.json').write_text('{"task_id":"task"}')
            with patch.object(api, 'ExperimentStore'), patch.object(api, 'WorkflowJournal', Journal), \
                 patch.object(api, 'colab_remote') as remote, patch.object(api, 'ColabBackend') as backend:
                with self.assertRaisesRegex(ValueError, 'guard refused'):
                    api.advance(d, 'runtime4', guard=guard)
            remote.assert_not_called()
            backend.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
