"""Independent interrupted local-output recovery regression; temporary fixtures."""
from contextlib import redirect_stdout
import io
import json
import unittest
from unittest.mock import patch

from test_resume_p35_diagnostic import DiagnosticResumeTests
from rsi_kernel_agent.workflow.controller import Controller


class DiagnosticResumeOutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_config_change_during_admission_cannot_be_accepted_as_new_pin(self):
        fixture = DiagnosticResumeTests('test_config_change_during_polling_blocks_next_step')
        await fixture.asyncSetUp()
        self.addCleanup(fixture.doCleanups)
        f = fixture.f
        config_path = f.directory / 'colab-config-runtime7.json'
        checked = fixture.resume.checked_artifact
        changed = False

        def mutate_after_original_read(store, ref):
            nonlocal changed
            checked(store, ref)
            if not changed:
                config = json.loads(config_path.read_bytes())
                config['runtime_id'] = 'different-runtime-during-admission'
                fixture.write(config_path, config)
                changed = True

        submissions = list(f.backend.submissions)
        with patch.object(fixture.resume, 'checked_artifact', side_effect=mutate_after_original_read):
            with self.assertRaisesRegex(ValueError, 'config|Config|frozen|Runtime'):
                fixture.call(steps=1)
        self.assertEqual(f.backend.submissions, submissions)

    async def test_terminal_journal_recovers_missing_final_status_before_completion(self):
        fixture = DiagnosticResumeTests('test_unknown_recovery_keeps_original_operations_two_charges_and_idempotent_completion')
        await fixture.asyncSetUp()
        self.addCleanup(fixture.doCleanups)
        f = fixture.f
        stale = json.loads((f.target / 'controller-status.json').read_bytes())
        f.backend.unknown = False
        controller = Controller(f.store, 'task', f.backend)
        for _ in range(20):
            state = controller.status()
            jobs = [job for job in state['jobs'].values() if job['round_id'] == f.spec['round_id']]
            if all(job['state'] in ('completed', 'failed', 'cancelled') for job in jobs):
                break
            controller.step()
        self.assertTrue(all(job['state'] == 'completed' for job in jobs))
        self.assertGreater(state['revision'], stale['revision'])
        self.assertFalse((f.target / 'completed.json').exists())
        submissions = list(f.backend.submissions)
        with redirect_stdout(io.StringIO()):
            result = fixture.call(steps=1)
        self.assertTrue(result['completed'])
        self.assertEqual(f.backend.submissions, submissions)
        saved = json.loads((f.target / 'controller-status.json').read_bytes())
        self.assertEqual(saved['revision'], state['revision'])
        self.assertTrue(all(job['state'] == 'completed' for job in saved['jobs'].values()
                            if job['round_id'] == f.spec['round_id']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
