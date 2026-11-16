"""Real-protocol CPU receipts distinguish hardware-stage feedback from cancellation."""
from copy import deepcopy
import importlib
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / 'src'), str(HERE.parents[1] / 'tests')]
from phase3_fixtures import RealProtocolFixture
from rsi_kernel_agent.contracts import ArtifactRef, RunEvent
from rsi_kernel_agent.workflow.evaluation import integrate_result


class RecoveryFeedbackTests(RealProtocolFixture, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prepare(self.tmp.name)
        self.api = importlib.import_module('p35_prepare_diagnostic_round')

    def terminal(self, kind):
        job = self.job()
        job['round_id'] = 'real-runtime5-recovery-round:2'
        self.store.transition(RunEvent('run:start', 'run', 'running', 'active', 'CPU feedback fixture'),
                              None, 'run:start')
        outputs = self.outputs(job)
        receipts = {}
        if kind != 'administrative':
            receipts['generate'] = self.receipt(job, 'generate', outputs['generate'])
        if kind == 'compile_failed':
            receipts['compile'] = self.receipt(job, 'compile',
                {'error_type': 'CompileError', 'message': 'CPU fixture lowering failure'}, status='failed')
        elif kind == 'complete':
            receipts.update({stage: self.receipt(job, stage, outputs[stage])
                             for stage in ('compile', 'correctness', 'benchmark', 'profile')})
        result = integrate_result(self.store, job, receipts, cancelled=kind in ('administrative', 'generate_only'))
        job.update(state='completed' if result['confirmed'] else
                   'cancelled' if result['classification'] == 'cancelled' else 'failed',
                   receipts=receipts, result=result)
        return job

    def feedback(self, job, context_id='context'):
        helper = getattr(self.api, 'require_recovery_feedback', None)
        self.assertIsNotNone(helper, 'Current-context real compile feedback gate is missing')
        return helper(self.store, [job], context_id)

    def test_administrative_cancel_with_valid_workflow_result_is_not_feedback(self):
        job = self.terminal('administrative')
        self.assertEqual(job['result']['classification'], 'cancelled')
        with self.assertRaisesRegex(ValueError, 'compile feedback'):
            self.feedback(job)

    def test_generate_only_cancel_is_not_feedback(self):
        job = self.terminal('generate_only')
        with self.assertRaisesRegex(ValueError, 'compile feedback'):
            self.feedback(job)

    def test_real_compile_failure_is_feedback_without_correctness_or_win(self):
        job = self.terminal('compile_failed')
        self.assertEqual(job['result']['classification'], 'compile_failed')
        result = self.feedback(job)
        self.assertEqual([row['run_id'] for row in result], ['run'])
        self.assertIsNotNone(self.store.get(result[0]['compile_marker_id']))
        self.assertEqual(self.store.get(result[0]['result_marker_id']).details['result'], job['result'])

    def test_complete_real_execution_is_feedback(self):
        self.assertEqual(len(self.feedback(self.terminal('complete'))), 1)

    def test_wrong_context_or_synthetic_label_cannot_supply_feedback(self):
        job = self.terminal('compile_failed')
        with self.assertRaisesRegex(ValueError, 'Context'):
            self.feedback(job, context_id='another-context')
        changed = deepcopy(job)
        changed['contract']['context']['provenance'] = 'synthetic'
        with self.assertRaisesRegex(ValueError, 'real'):
            self.feedback(changed)

    def test_forged_compile_dictionary_without_durable_receipt_is_rejected(self):
        job = self.terminal('generate_only')
        job['result']['receipt_refs']['compile'] = job['result']['receipt_refs']['generate']
        with self.assertRaises(ValueError):
            self.feedback(job)

    def test_corrupted_compile_artifact_is_rejected(self):
        job = self.terminal('compile_failed')
        ref = ArtifactRef(**job['result']['receipt_refs']['compile'])
        path = self.store.artifacts.path_for(ref)
        path.write_bytes(b'corrupted fixture')
        with self.assertRaises((ValueError, OSError)):
            self.feedback(job)


if __name__ == '__main__':
    unittest.main(verbosity=2)
