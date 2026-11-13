"""Local CLI checks; no Colab account or network operation is performed."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from phase3_fixtures import make_real_fixture


class RealCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = make_real_fixture(self.root)
        self.store = self.fixture['store']
        self.command = [sys.executable, '-B', '-m', 'rsi_kernel_agent.workflow', '--db', str(self.root / 'db.sqlite'),
                        '--artifacts', str(self.root / 'artifacts')]
        self.env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src'), 'PYTHONUTF8': '1'}

    def run_cli(self, *args):
        return subprocess.run([*self.command, *args], env=self.env, capture_output=True, text=True, timeout=30)

    def task_spec(self, provenance='real'):
        spec = {key: asdict(self.store.get(rid)) for key, rid in
                (('task', 'task'), ('workload', 'workload'), ('context', 'context'))}
        spec['strategies'] = [asdict(self.store.get('A'))]
        spec['context']['provenance'] = provenance
        path = self.root / 'task.json'
        path.write_text(json.dumps(spec), encoding='utf-8')
        return path

    def test_create_real_context_reuses_existing_fixed_contract_and_artifacts(self):
        result = self.run_cli('create', '--spec', str(self.task_spec()))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['provenance'], 'real')

    def test_real_run_without_connection_configuration_cannot_fall_back_to_synthetic(self):
        result = self.run_cli('run', '--task', 'task')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--colab-config', result.stderr)

    def test_status_does_not_load_or_contact_an_unavailable_colab_configuration(self):
        result = self.run_cli('--colab-config', str(self.root / 'does-not-exist.json'), 'status', '--task', 'task')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['task_id'], 'task')

    def test_saved_context_is_readable_history_but_not_an_executable_task_spec(self):
        result = self.run_cli('create', '--spec', str(self.task_spec('saved')))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('saved', result.stderr)

    def test_bootstrap_is_an_explicit_command_and_requires_config(self):
        result = self.run_cli('colab-bootstrap')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--colab-config', result.stderr)


if __name__ == '__main__':
    unittest.main()
