"""Local CPU checks for auxiliary admission/recovery; no live store or bridge."""
import ast
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
from phase3_fixtures import RealProtocolFixture
from rsi_kernel_agent.contracts import EvaluationBundle, RunEvent


def definitions(name):
    # Original helpers have top-level effects. Only definitions/imports are
    # evaluated in this probe, never admission, a live Store or a remote call.
    path = HERE / name
    tree = ast.parse(path.read_text('utf-8'), filename=str(path))
    body = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef))]
    scope = {'__file__': str(path)}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(path), 'exec'), scope)
    return scope


class AdmissionTests(unittest.TestCase):
    def test_publication_rejects_replacing_existing_recovery_input(self):
        write = definitions('admit_runtime2.py')['write_immutable_json']
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'job.json'
            write(path, {'run_id': 'old'})
            before = path.read_bytes()
            write(path, {'run_id': 'old'})
            with self.assertRaisesRegex(ValueError, 'differs'):
                write(path, {'run_id': 'new'})
            self.assertEqual(path.read_bytes(), before)

    def test_existing_config_must_match_observed_runtime(self):
        prepare = definitions('admit_runtime2.py')['prepare_config']
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            old = {'python': 'python.exe', 'session': 'session', 'runtime_id': 'old',
                   'nonce': 'old-nonce', 'remote_root': '/content/rsi-kernel-agent/old',
                   'cache': tmp, 'remote_python': '/content/rsi-kernel-agent/env/bin/python'}
            new = prepare(path, old, {'runtime_session_id': 'new'})
            self.assertEqual(new['runtime_id'], 'new')
            self.assertNotEqual(new['nonce'], old['nonce'])
            self.assertEqual(prepare(path, old, {'runtime_session_id': 'new'}), new)
            with self.assertRaisesRegex(ValueError, 'runtime'):
                prepare(path, old, {'runtime_session_id': 'third'})


class OracleFailureTests(RealProtocolFixture, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prepare(self.tmp.name)
        self.record = definitions('oracle_gate_aux.py')['record_oracle_gate_failure']

    def test_unexpected_pass_is_incomplete_with_receipts_and_without_consider(self):
        job, receipts = self.execute_fixture()
        receipts = {k: receipts[k] for k in ('generate', 'compile', 'correctness')}
        with patch.object(self.store, 'consider', side_effect=AssertionError('must not consider')):
            result = self.record(self.store, job, receipts)
            self.assertEqual(self.store.run_state(job['run_id'])['execution_status'], 'incomplete')
            self.assertEqual(result['classification'], 'oracle_gate_failure')
            self.assertEqual(result['correctness'], 'passed')
            self.assertEqual(set(result['receipt_refs']), set(receipts))
            count = len(self.store.history('task'))
            self.assertEqual(self.record(self.store, job, receipts), result)
            self.assertEqual(len(self.store.history('task')), count)
        self.assertFalse(any(type(r) is EvaluationBundle for r in self.store.history('task')))

    def test_recovery_after_gate_decision_before_terminal_event(self):
        job, receipts = self.execute_fixture()
        receipts = {k: receipts[k] for k in ('generate', 'compile', 'correctness')}
        with patch.object(self.store, 'transition', side_effect=RuntimeError('crash')):
            with self.assertRaisesRegex(RuntimeError, 'crash'):
                self.record(self.store, job, receipts)
        self.assertEqual(self.store.run_state(job['run_id'])['execution_status'], 'running')
        self.record(self.store, job, receipts)
        self.assertEqual(self.store.run_state(job['run_id'])['execution_status'], 'incomplete')

    def test_administratively_closed_run_cannot_resume_without_evaluation_intent(self):
        check = definitions('oracle_gate_aux.py')['require_resumable_run']
        job, _ = self.execute_fixture()
        previous = self.store.run_state(job['run_id'])['event_id']
        self.store.transition(RunEvent('lost', job['run_id'], 'incomplete', 'finished', 'Runtime lost'), previous, 'lost')
        with self.assertRaisesRegex(RuntimeError, 'Terminal Run'):
            check(self.store, job)

    def test_driver_scripts_parse_and_terminate_negatives_at_correctness(self):
        for name in ('admit_runtime2.py', 'advance_runtime2.py', 'run_oracle_negatives.py', 'oracle_gate_aux.py'):
            ast.parse((HERE / name).read_text('utf-8'), filename=name)
        code = (HERE / 'advance_runtime2.py').read_text('utf-8')
        self.assertIn("stages=('generate','compile','correctness') if negative", code)
        code = (HERE / 'run_oracle_negatives.py').read_text('utf-8')
        self.assertIn("for stage in ('generate', 'compile', 'correctness')", code)


if __name__ == '__main__':
    unittest.main(verbosity=2)
