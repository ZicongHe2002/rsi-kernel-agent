"""CPU-only verification-helper regressions; never opens the live experiment DB."""
import importlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zipfile
from copy import deepcopy
from hashlib import sha256

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / 'src'), str(ROOT / 'tests')]
from phase3_fixtures import RealProtocolFixture
from rsi_kernel_agent.contracts import ArtifactRef
from rsi_kernel_agent.workflow.evaluation import integrate_result


def npy(raw, shape, descr):
    header = repr({'descr': descr, 'fortran_order': False, 'shape': tuple(shape)}).encode('ascii')
    header += b' ' * ((-10 - len(header) - 1) % 64) + b'\n'
    return b'\x93NUMPY\x01\x00' + struct.pack('<H', len(header)) + header + raw


def write_npz(path, arrays, corrupt_metadata=False):
    metadata = [{'name': 'array_' + str(i), 'shape': [len(words)], 'dtype': 'bfloat16',
                 'storage_dtype': 'uint16', 'sha256': sha256(struct.pack('<' + 'H' * len(words), *words)).hexdigest()}
                for i, words in enumerate(arrays)]
    if corrupt_metadata:
        metadata[0]['sha256'] = '0' * 64
    with zipfile.ZipFile(path, 'w') as z:
        for i, words in enumerate(arrays):
            z.writestr('array_' + str(i) + '.npy', npy(struct.pack('<' + 'H' * len(words), *words), [len(words)], '<u2'))
        body = json.dumps(metadata).encode()
        z.writestr('metadata.npy', npy(body, [len(body)], '|u1'))


class VerificationTests(RealProtocolFixture, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prepare(self.tmp.name)
        try:
            self.api = importlib.import_module('verify_real_round')
        except ImportError:
            self.fail('Read-only round verifier is not implemented')

    def reader(self):
        return self.api.ReadOnlyStore(self.store.db_path, self.store.artifacts.root)

    def terminal(self, **kwargs):
        job, receipts = self.execute_fixture(**kwargs)
        result = integrate_result(self.store, job, receipts)
        job.update(state='completed' if result['confirmed'] else 'failed', result=result, receipts=receipts)
        return job

    def test_pending_never_needs_result_or_store(self):
        job = self.job()
        job.update(state='active', stage='compile', result=None)
        row = self.api.verify_job(None, job, recompute_npz=True)
        self.assertEqual(row['verification'], 'pending')
        self.assertEqual(row['stage'], 'compile')

    def test_missing_status_remains_pending_without_creating_database(self):
        report = self.api.verify_round(Path(self.tmp.name) / 'missing', 1)
        self.assertEqual(report['status'], 'pending_status_file')
        self.assertFalse((Path(self.tmp.name) / 'missing').exists())

    def test_terminal_requires_original_completion_marker(self):
        job, receipts = self.execute_fixture()
        job.update(state='completed', receipts=receipts, result={})
        with self.assertRaises((ValueError, KeyError)):
            self.api.verify_job(self.reader(), job, recompute_npz=False)

    def test_success_recomputes_statistics_and_preserves_database(self):
        job = self.terminal()
        before = len(self.store.history('task'))
        row = self.api.verify_job(self.reader(), job, recompute_npz=False)
        self.assertEqual(row['verification'], 'verified')
        self.assertEqual(row['benchmark_statistics']['median_ms'], 3.0)
        self.assertEqual(row['profile_medians_us']['program'], [2000.0])
        self.assertTrue(row['all_stage_artifacts_reachable_from_evaluation'])
        self.assertEqual(row['loaded_identities']['compile']['module_path'], '/content/rsi/candidate/kernel.py')
        self.assertEqual(len(self.store.history('task')), before)

    def test_profile_failure_preserves_benchmark_without_claiming_available_profile(self):
        row = self.api.verify_job(self.reader(), self.terminal(profile_failed=True), recompute_npz=False)
        self.assertEqual(row['verification'], 'verified')
        self.assertEqual(row['classification'], 'profile_failed')
        self.assertEqual(row['benchmark_statistics']['median_ms'], 3.0)
        self.assertIsNone(row['profile_normalization_quality'])

    def test_failed_result_cannot_be_relabelled_completed(self):
        job = self.terminal(profile_failed=True)
        job['state'] = 'completed'
        with self.assertRaises(ValueError):
            self.api.verify_job(self.reader(), job, recompute_npz=False)

    def test_unstarted_cancellation_does_not_require_execution_marker(self):
        job = self.job()
        job.update(state='cancelled', result=None, receipts={}, reserved=False)
        row = self.api.verify_job(self.reader(), job, recompute_npz=True)
        self.assertEqual(row['verification'], 'verified_control_only')
        self.assertIsNone(row['result_marker_id'])
        self.assertEqual(row['run_state']['execution_status'], 'not_run')

    def test_runtime_input_mutation_rejection_is_not_changed_by_matching_output_npz(self):
        measured = {'name': 'cache', 'exact_raw_bytes_equal': True, 'mismatching_bf16_words': 0,
                    'tolerance_violations': 0, 'passed': True}
        recorded = {'name': 'cache', 'passed': False, 'tolerance_violations': 1,
                    'metric_reason': 'Candidate mutated the input cache'}
        result = self.api.reconcile_comparison(measured, recorded)
        self.assertTrue(result['passed'])  # Only independently recomputed output bytes.
        self.assertFalse(result['recorded_passed'])  # Runtime rejection is retained.
        self.assertTrue(result['input_mutation_not_independently_reverified'])

    def test_corrupted_raw_artifact_and_relabelled_result_are_rejected(self):
        job = self.terminal()
        wrong = deepcopy(job)
        wrong['candidate_id'] = 'someone-else'
        with self.assertRaises(ValueError):
            self.api.verify_job(self.reader(), wrong, recompute_npz=False)
        raw = ArtifactRef(**job['receipts']['correctness']['outputs']['raw_artifact_refs'][0])
        self.store.artifacts.path_for(raw).write_bytes(b'bad bytes')
        with self.assertRaises((ValueError, AssertionError)):
            self.api.verify_job(self.reader(), job, recompute_npz=False)

    def test_streamed_npz_compares_numeric_attention_and_opaque_nan_cache(self):
        actual, expected = Path(self.tmp.name) / 'actual.npz', Path(self.tmp.name) / 'expected.npz'
        write_npz(actual, [[0x3f80, 0x4000], [0x7fc0, 0x3f80]])
        write_npz(expected, [[0x3f80, 0x3f80], [0x7fc0, 0x3f80]])
        specs = [{'name': 'attention', 'shape': [2], 'dtype': 'bfloat16', 'comparison': 'allclose'},
                 {'name': 'cache', 'shape': [2], 'dtype': 'bfloat16', 'comparison': 'exact'}]
        rows = self.api.compare_npz(actual, expected, specs, atol=.01, rtol=.01)
        self.assertEqual(rows[0]['tolerance_violations'], 1)
        self.assertEqual(rows[0]['max_abs'], 1.0)
        self.assertAlmostEqual(rows[0]['rms'], 2 ** -.5)
        self.assertTrue(rows[1]['exact_raw_bytes_equal'])
        self.assertEqual(rows[1]['mismatching_bf16_words'], 0)

    def test_streamed_npz_rejects_wrong_internal_digest(self):
        actual, expected = Path(self.tmp.name) / 'actual.npz', Path(self.tmp.name) / 'expected.npz'
        write_npz(actual, [[0x3f80]], corrupt_metadata=True)
        write_npz(expected, [[0x3f80]])
        with self.assertRaises(ValueError):
            self.api.compare_npz(actual, expected,
                [{'name': 'attention', 'shape': [1], 'dtype': 'bfloat16', 'comparison': 'allclose'}], atol=.01, rtol=.01)

    def test_numeric_tolerance_preserves_reference_actual_direction(self):
        actual, expected = Path(self.tmp.name) / 'actual.npz', Path(self.tmp.name) / 'expected.npz'
        write_npz(actual, [[0x42c8]])  # 100 exactly in BF16
        write_npz(expected, [[0x42c6]])  # 99 exactly in BF16
        rows = self.api.compare_npz(actual, expected,
            [{'name': 'attention', 'shape': [1], 'dtype': 'bfloat16', 'comparison': 'allclose'}], atol=0, rtol=.01)
        self.assertTrue(rows[0]['passed'])  # 1 <= .01 * actual(100); reference scale would wrongly reject.


if __name__ == '__main__':
    unittest.main(verbosity=2)
