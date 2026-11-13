"""Detached wrapper tests; never opens the live Store or contacts hardware."""
from copy import deepcopy
from hashlib import sha256
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


class DiagnosticVerificationTests(unittest.TestCase):
    def setUp(self):
        self.api = importlib.import_module('verify_p35_diagnostic')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.selection = {'schema_version': 1, 'task_id': 'task', 'context_id': 'original-context',
                          'strategy_id': 'strategy', 'round_id': 'p35-zero-diagnostic', 'knowledge': []}
        self.selection_path = self.directory / 'selected.json'
        self.selection_path.write_text(json.dumps(self.selection), encoding='utf-8')
        self.target = self.api.target_for(self.directory, self.selection)

    def frozen(self, jobs):
        from rsi_kernel_agent.contracts import Decision, canonical_json
        self.target.mkdir()
        (self.target / 'selection.json').write_text(json.dumps(self.selection), encoding='utf-8')
        (self.target / 'diagnostic-packet.json').write_text(
            json.dumps({'context': {'id': 'diagnostic-context'}}), encoding='utf-8')
        state = {'task_id': 'task', 'revision': 7, 'jobs': jobs}
        note = Decision('journal', 'task', 'note', 'task', 'frozen test journal', {
            'type': 'workflow_state.v1', 'state': deepcopy(state),
            'state_digest': sha256(canonical_json(state).encode()).hexdigest()})
        state['workflow_state'] = 'active'
        (self.target / 'controller-status.json').write_text(json.dumps(state), encoding='utf-8')

        class Store:
            def __init__(self, *_):
                self.artifacts = type('Artifacts', (), {'checked': {}})()

            def get(self, _):
                return note

        return Store, note

    def job(self, label, state='active'):
        return {'id': label, 'state': state, 'stage': 'compile', 'reason': '',
                'task_id': 'task', 'strategy_id': 'strategy', 'round_id': self.selection['round_id'],
                'context_id': 'diagnostic-context', 'run_id': 'run-' + label,
                'variant': {'label': label}, 'result': None}

    def test_missing_status_is_pending_without_store_or_directory_creation(self):
        with patch.object(self.api, 'ReadOnlyStore', side_effect=AssertionError('must not open')):
            report = self.api.verify_diagnostic(self.selection_path, directory=self.directory)
        self.assertEqual(report['status'], 'pending_status_file')
        self.assertFalse(self.target.exists())

    def test_original_pending_semantics_and_missing_second_role_remain_pending(self):
        job = self.job('diagnostic-baseline')
        store, _ = self.frozen({job['id']: job})
        with patch.object(self.api, 'ReadOnlyStore', store):
            report = self.api.verify_diagnostic(self.selection_path, directory=self.directory)
        self.assertEqual(report['status'], 'pending')
        self.assertEqual(report['jobs'][0]['verification'], 'pending')
        self.assertEqual(report['missing_labels'], ['diagnostic-single-tile'])

    def test_tampered_saved_status_cannot_reach_job_verification(self):
        job = self.job('diagnostic-baseline')
        store, _ = self.frozen({job['id']: job})
        path = self.target / 'controller-status.json'
        state = json.loads(path.read_text())
        state['jobs'][job['id']]['state'] = 'completed'
        path.write_text(json.dumps(state), encoding='utf-8')
        with patch.object(self.api, 'ReadOnlyStore', store), patch.object(self.api, 'verify_job') as verify:
            with self.assertRaisesRegex(ValueError, 'persisted journal'):
                self.api.verify_diagnostic(self.selection_path, directory=self.directory)
        verify.assert_not_called()

    def test_both_roles_delegate_and_retain_failed_verification(self):
        jobs = {label: self.job(label, 'completed') for label in self.api.PLAN_ROLES.values()}
        store, _ = self.frozen(jobs)

        def verified(reader, job, **kwargs):
            self.assertFalse(kwargs['recompute_npz'])
            if job['variant']['label'] == 'diagnostic-single-tile':
                raise ValueError('bad immutable receipt')
            return {'id': job['id'], 'label': job['variant']['label'], 'verification': 'verified'}

        with patch.object(self.api, 'ReadOnlyStore', store), patch.object(self.api, 'verify_job', verified):
            report = self.api.verify_diagnostic(self.selection_path, directory=self.directory, recompute_npz=False)
        self.assertEqual(report['status'], 'verification_failed')
        self.assertEqual(report['counts']['verified'], 1)
        self.assertEqual(report['counts']['failed_verification'], 1)

    def test_cli_pending_writes_unique_reports_without_database(self):
        reports = []
        for _ in range(2):
            result = subprocess.run(
                [sys.executable, '-B', str(Path(self.api.__file__)), str(self.selection_path),
                 '--directory', str(self.directory)], capture_output=True, text=True, check=True)
            message = json.loads(result.stdout)
            self.assertEqual(message['status'], 'pending_status_file')
            output = Path(message['report'])
            self.assertTrue(output.is_file())
            self.assertEqual(json.loads(output.read_text())['store_writes'], 0)
            reports.append(output)
        self.assertNotEqual(*reports)
        self.assertFalse((self.directory / 'experiments.sqlite3').exists())

    def test_cli_existing_status_missing_database_saves_failure_report(self):
        job = self.job('diagnostic-baseline')
        self.frozen({job['id']: job})
        result = subprocess.run(
            [sys.executable, '-B', str(Path(self.api.__file__)), str(self.selection_path),
             '--directory', str(self.directory)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stdout.strip(), result.stderr)
        message = json.loads(result.stdout)
        self.assertEqual(message['status'], 'verification_failed')
        report = json.loads(Path(message['report']).read_text())
        self.assertEqual(report['error_type'], 'OperationalError')
        self.assertFalse((self.directory / 'experiments.sqlite3').exists())

    def test_v2_verifier_rejects_wrong_runtime_binding_before_opening_store(self):
        self.selection.update(schema_version=2, runtime_generation='runtime4')
        self.selection_path.write_text(json.dumps(self.selection), encoding='utf-8')
        self.target = self.api.target_for(self.directory, self.selection)
        job = self.job('diagnostic-baseline')
        self.frozen({job['id']: job})
        (self.target / 'runtime-binding.json').write_text('{"generation":"runtime3"}')
        with patch.object(self.api, 'ReadOnlyStore', side_effect=AssertionError('must not open')):
            with self.assertRaisesRegex(ValueError, 'Runtime binding'):
                self.api.verify_diagnostic(self.selection_path, directory=self.directory)

    def test_v2_verifier_uses_new_runtime_and_preserves_pending_semantics(self):
        self.selection.update(schema_version=2, runtime_generation='runtime4')
        self.selection_path.write_text(json.dumps(self.selection), encoding='utf-8')
        self.target = self.api.target_for(self.directory, self.selection)
        job = self.job('diagnostic-baseline')
        store, _ = self.frozen({job['id']: job})
        (self.target / 'runtime-binding.json').write_text('{"generation":"runtime4"}')
        (self.directory / 'colab-config-runtime4.json').write_text('{"runtime_id":"runtime-four"}')
        (self.target / 'diagnostic-packet.json').write_text(json.dumps({
            'original_context_id': 'original-context', 'context': {'id': 'diagnostic-context',
                'environment': {'runtime_session_id': 'runtime-four'}}}))
        with patch.object(self.api, 'ReadOnlyStore', store):
            report = self.api.verify_diagnostic(self.selection_path, directory=self.directory)
        self.assertEqual(report['status'], 'pending')


if __name__ == '__main__':
    unittest.main(verbosity=2)
