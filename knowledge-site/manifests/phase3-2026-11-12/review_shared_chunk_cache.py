"""Independent local CPU probes; no real bridge, runtime, or network access."""
from hashlib import sha256
import io
import os
from pathlib import Path
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

from rsi_kernel_agent.workflow import colab_remote as module
from test_phase3_remote import RemoteTests


class SharedCacheReview(unittest.TestCase):
    setUp = RemoteTests.setUp
    _configure_chunk_download = RemoteTests._configure_chunk_download

    def test_legacy_parent_name_does_not_authorize_corrupt_bytes(self):
        expected = b'abcdefgh'
        legacy = self.remote.local_cache / 'parts' / sha256(expected).hexdigest()
        legacy.mkdir(parents=True)
        (legacy / '0').write_bytes(b'WRNG')
        (legacy / '1').write_bytes(b'efgh')
        artifact, calls = self._configure_chunk_download(expected, expected)
        self.assertEqual(self.remote.read_artifact(artifact), expected)
        self.assertEqual(calls, [0])

    def test_staging_change_before_assembly_rejects_then_recovers_without_get(self):
        artifact, calls = self._configure_chunk_download(b'abcdefgh', b'abcdefgh')
        original = module._copy_chunk
        parts = self.remote.local_cache / 'parts' / artifact['ref']['digest']
        def copy_then_corrupt(path, raw):
            original(path, raw)
            if path == parts / '1':
                (parts / '0').write_bytes(b'WRNG')
        with patch.object(module, '_copy_chunk', copy_then_corrupt):
            with self.assertRaisesRegex(ValueError, 'staging changed'):
                self.remote.read_artifact(artifact)
        self.assertFalse((self.remote.local_cache / artifact['ref']['digest']).exists())
        self.assertEqual(list(self.remote.local_cache.glob('.assembled-*')), [])
        calls.clear()
        self.assertEqual(self.remote.read_artifact(artifact), b'abcdefgh')
        self.assertEqual(calls, [])

    def test_default_four_workers_never_exceed_four(self):
        expected = b''.join(bytes([index]) * 4 for index in range(12))
        artifact, calls = self._configure_chunk_download(expected, expected)
        self.remote.transfer_workers = 4
        original = self.bridge.get
        lock = threading.Lock()
        counts = {'active': 0, 'peak': 0}
        def measured(remote, local):
            with lock:
                counts['active'] += 1
                counts['peak'] = max(counts['peak'], counts['active'])
            try:
                time.sleep(.02)
                original(remote, local)
            finally:
                with lock:
                    counts['active'] -= 1
        self.bridge.get = measured
        self.assertEqual(self.remote.read_artifact(artifact), expected)
        self.assertEqual(counts['peak'], 4)
        self.assertEqual(len(calls), 12)

    def test_invalid_worker_counts_rejected_before_get(self):
        artifact, calls = self._configure_chunk_download(b'abcdefgh', b'abcdefgh')
        for workers in (0, 5, True, 1.5):
            with self.subTest(workers=workers):
                self.remote.transfer_workers = workers
                with self.assertRaisesRegex(ValueError, '1..4'):
                    self.remote.read_artifact(artifact)
        self.assertEqual(calls, [])

    def test_duplicate_chunks_replace_corrupt_shared_bytes_with_one_download(self):
        artifact, calls = self._configure_chunk_download(b'abcdabcdabcd', b'abcdabcdabcd')
        self.remote.transfer_workers = 4
        shared = self.remote.local_cache / 'chunks'
        shared.mkdir()
        (shared / sha256(b'abcd').hexdigest()).write_bytes(b'WRNG')
        self.assertEqual(self.remote.read_artifact(artifact), b'abcdabcdabcd')
        self.assertEqual(len(calls), 1)

    def test_shared_file_mutation_cannot_modify_staging_copies(self):
        artifact, calls = self._configure_chunk_download(b'abcdefgh', b'abcdefgh')
        self.remote.read_artifact(artifact)
        shared = self.remote.local_cache / 'chunks' / sha256(b'abcd').hexdigest()
        shared.write_bytes(b'WRNG')
        staged = self.remote.local_cache / 'parts' / artifact['ref']['digest'] / '0'
        self.assertEqual(staged.read_bytes(), b'abcd')
        calls.clear()
        self.assertEqual(self.remote.read_artifact(artifact), b'abcdefgh')
        self.assertEqual(calls, [])
        self.assertEqual(shared.read_bytes(), b'abcd')

    @unittest.skipUnless(os.name == 'nt', 'Windows junction probe')
    def test_legacy_directory_junction_rejected_without_reading_outside(self):
        artifact, calls = self._configure_chunk_download(b'abcdefgh', b'abcdefgh')
        parts = self.remote.local_cache / 'parts'
        parts.mkdir()
        outside = self.root / 'outside-legacy'
        outside.mkdir()
        (outside / '0').write_bytes(b'abcd')
        junction = parts / 'legacy'
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(outside)],
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        try:
            with self.assertRaisesRegex(ValueError, 'links'):
                self.remote.read_artifact(artifact)
            self.assertEqual(calls, [])
            self.assertEqual((outside / '0').read_bytes(), b'abcd')
        finally:
            junction.rmdir()


if __name__ == '__main__':
    suite = unittest.TestSuite([
        unittest.defaultTestLoader.loadTestsFromName('test_phase3_remote'),
        unittest.defaultTestLoader.loadTestsFromName('test_phase3_backend'),
        unittest.defaultTestLoader.loadTestsFromTestCase(SharedCacheReview),
    ])
    output = io.StringIO()
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    report = output.getvalue()
    Path(__file__).with_name('independent-review-shared-chunks.txt').write_text(report, encoding='utf-8')
    print(report, end='')
    raise SystemExit(0 if result.wasSuccessful() else 1)
