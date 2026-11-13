"""CPU tests of remote command construction and immutable installation."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from rsi_kernel_agent.workflow.colab_remote import ColabRemote


class MemoryBridge:
    runtime_id = 'runtime-test'
    def __init__(self):
        self.calls = []
    def execute(self, code):
        self.calls.append(('execute', code))
        return [{'output_type': 'stream', 'text': 'RSI_RESULT:{"status":"definitely_not_started","receipt":null}\n'}]
    def put(self, local, remote):
        self.calls.append(('put', str(local), remote))
    def get(self, remote, local):
        self.calls.append(('get', remote, str(local)))
        Path(local).write_bytes(b'payload')


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.worker, self.runner = self.root / 'worker.py', self.root / 'runner.py'
        self.worker.write_text('# fixed worker', encoding='utf-8')
        self.runner.write_text('# fixed runner', encoding='utf-8')
        self.bridge = MemoryBridge()
        self.remote = ColabRemote(self.bridge, '/content/rsi-kernel-agent/test', 'fixed-nonce',
                                  self.worker, self.runner, self.root / 'cache')
        self.remote.transfer_workers = 1

    def test_lookup_never_recreates_a_missing_ledger(self):
        self.remote.lookup('operation:' + 'a' * 64)
        self.assertEqual(len(self.bridge.calls), 1)
        code = self.bridge.calls[0][1]
        self.assertNotIn('mkdir', code)
        self.assertNotIn('bootstrap', code)
        self.assertIn('lookup', code)
        self.assertIn('fixed-nonce', code)

    def test_artifact_download_rejects_paths_outside_pinned_spool(self):
        for path in ('/content/user-file', '/content/rsi-kernel-agent/test/../outside',
                     '/content/rsi-kernel-agent/test2/artifacts/file'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.remote.read_artifact({'ref': {'digest': 'a' * 64, 'size': 7}, 'path': path})
        self.assertEqual(self.bridge.calls, [])

    def test_artifact_download_uses_digest_named_local_path(self):
        from hashlib import sha256
        digest = sha256(b'payload').hexdigest()
        result = self.remote.read_artifact({'ref': {'digest': digest, 'size': 7},
            'path': '/content/rsi-kernel-agent/test/artifacts/' + digest})
        self.assertEqual(result, b'payload')
        self.assertEqual(Path(self.bridge.calls[0][2]).name, digest)

    def test_invalid_remote_root_is_rejected(self):
        for root in ('/tmp', '/content/rsi-kernel-agent/../user', '/content/rsi-kernel-agent/test\\escape'):
            with self.assertRaises(ValueError):
                ColabRemote(self.bridge, root, 'nonce', self.worker, self.runner, self.root / 'cache')

    def test_bootstrap_pins_worker_and_runner_content_hash(self):
        from hashlib import sha256
        self.remote.bootstrap()
        code = self.bridge.calls[0][1]
        self.assertIn(sha256(self.worker.read_bytes()).hexdigest(), code)
        self.assertIn(sha256(self.runner.read_bytes()).hexdigest(), code)
        self.assertIn('bootstrap', code)

    def test_explicit_remote_python_runs_worker_outside_notebook_process(self):
        remote = ColabRemote(self.bridge, '/content/rsi-kernel-agent/test', 'fixed-nonce',
                             self.worker, self.runner, self.root / 'cache',
                             remote_python='/content/rsi-kernel-agent/env-20261112/venv/bin/python')
        remote.lookup('operation:test')
        code = self.bridge.calls[-1][1]
        self.assertIn('/content/rsi-kernel-agent/env-20261112/venv/bin/python', code)
        self.assertIn('subprocess.run', code)
        self.assertNotIn('spec_from_file_location', code)

    def test_tool_updates_install_distinct_paths_without_overwriting_pending_workers(self):
        previous = self.remote._code(self.remote._command('lookup', operation_id='op'))
        self.worker.write_text('# revised worker', encoding='utf-8')
        changed = ColabRemote(self.bridge, self.remote.root, self.remote.nonce,
                              self.worker, self.runner, self.root / 'cache')
        self.assertNotEqual(self.remote.worker_remote_path, changed.worker_remote_path)
        self.assertIn(self.remote.worker_digest, self.remote.worker_remote_path)
        self.assertIn(self.remote.worker_remote_path, previous)

    def test_large_artifact_resumes_verified_chunks_and_checks_whole_digest(self):
        from hashlib import sha256
        from rsi_kernel_agent.workflow.colab_bridge import BridgeUnavailable
        data=b'abcdefghij'
        digest=sha256(data).hexdigest()
        artifact={'ref':{'digest':digest,'size':len(data)},'path':self.remote.root+'/blobs/'+digest}
        chunks=[data[i:i+4] for i in range(0,len(data),4)]
        manifest={'digest':digest,'size':len(data),'chunks':[
            {'path':self.remote.root+'/transfers/'+digest+'/'+str(i), 'digest':sha256(chunk).hexdigest(),
             'size':len(chunk),'offset':i*4} for i,chunk in enumerate(chunks)]}
        self.bridge.execute=lambda code:[{'output_type':'stream','text':'RSI_RESULT:'+json.dumps(manifest)}]
        calls=[]
        fail=[True]
        def get(remote,local):
            index=int(remote.rsplit('/',1)[1]);calls.append(index)
            if index==1 and fail[0]:
                fail[0]=False
                raise BridgeUnavailable('lost transfer')
            Path(local).write_bytes(chunks[index])
        self.bridge.get=get
        self.remote.chunk_bytes=4
        with self.assertRaises(BridgeUnavailable):self.remote.read_artifact(artifact)
        self.assertEqual(self.remote.read_artifact(artifact),data)
        self.assertEqual(calls,[0,1,1,2])

    def test_invalid_chunk_manifest_is_rejected_before_download(self):
        self.remote.chunk_bytes=1
        self.bridge.execute=lambda code:[{'output_type':'stream','text':'RSI_RESULT:'+json.dumps(
            {'digest':'a'*64,'size':2,'chunks':[{'path':'/content/user-file','digest':'b'*64,'size':2,'offset':0}]})}]
        with self.assertRaises(ValueError):
            self.remote.read_artifact({'ref':{'digest':'a'*64,'size':2},'path':self.remote.root+'/blobs/'+'a'*64})

    def _configure_chunk_download(self, expected, downloaded):
        from hashlib import sha256
        self.remote.chunk_bytes = 4
        digest = sha256(expected).hexdigest()
        artifact = {'ref': {'digest': digest, 'size': len(expected)},
                    'path': self.remote.root + '/blobs/' + digest}
        chunks = [downloaded[i:i + 4] for i in range(0, len(downloaded), 4)]
        manifest = {'digest': digest, 'size': len(expected), 'chunks': [
            {'path': self.remote.root + '/transfers/' + digest + '/' + str(index),
             'digest': sha256(chunk).hexdigest(), 'size': len(chunk), 'offset': index * 4}
            for index, chunk in enumerate(chunks)]}
        self.bridge.execute = lambda code: [{'output_type': 'stream',
                                             'text': 'RSI_RESULT:' + json.dumps(manifest)}]
        calls = []
        def get(remote, local):
            index = int(remote.rsplit('/', 1)[1])
            calls.append(index)
            Path(local).write_bytes(chunks[index])
        self.bridge.get = get
        return artifact, calls

    def test_valid_chunk_hashes_cannot_mask_a_wrong_whole_artifact(self):
        artifact, calls = self._configure_chunk_download(b'abcdefgh', b'abcdXXXX')
        with self.assertRaisesRegex(ValueError, 'Assembled artifact'):
            self.remote.read_artifact(artifact)
        self.assertEqual(calls, [0, 1])
        self.assertFalse((self.remote.local_cache / artifact['ref']['digest']).exists())
        self.assertEqual(list(self.remote.local_cache.glob('.assembled-*')), [])

    def test_corrupt_staged_chunk_is_restored_from_independent_verified_cache(self):
        artifact, calls = self._configure_chunk_download(b'abcdefgh', b'abcdefgh')
        self.assertEqual(self.remote.read_artifact(artifact), b'abcdefgh')
        cached = self.remote.local_cache / 'parts' / artifact['ref']['digest'] / '0'
        cached.write_bytes(b'WRNG')
        calls.clear()
        self.assertEqual(self.remote.read_artifact(artifact), b'abcdefgh')
        self.assertEqual(calls, [])
        self.assertEqual(cached.read_bytes(), b'abcd')

    def test_distinct_whole_artifacts_download_only_changed_chunks_after_restart(self):
        first, calls = self._configure_chunk_download(b'abcdefghijkl', b'abcdefghijkl')
        self.assertEqual(self.remote.read_artifact(first), b'abcdefghijkl')
        self.assertEqual(calls, [0, 1, 2])
        self.remote = ColabRemote(self.bridge, self.remote.root, self.remote.nonce,
                                  self.worker, self.runner, self.remote.local_cache)
        self.remote.transfer_workers = 1
        second, calls = self._configure_chunk_download(b'XXXXefghijkl', b'XXXXefghijkl')
        self.assertNotEqual(first['ref']['digest'], second['ref']['digest'])
        self.assertEqual(self.remote.read_artifact(second), b'XXXXefghijkl')
        self.assertEqual(calls, [0])
        self.assertEqual((self.remote.local_cache / first['ref']['digest']).read_bytes(), b'abcdefghijkl')

    def test_legacy_parts_from_another_artifact_are_verified_and_reused(self):
        from hashlib import sha256
        legacy = self.remote.local_cache / 'parts' / sha256(b'abcdefgh').hexdigest()
        legacy.mkdir(parents=True)
        (legacy / '0').write_bytes(b'abcd')
        (legacy / '1').write_bytes(b'efgh')
        artifact, calls = self._configure_chunk_download(b'XXXXefgh', b'XXXXefgh')
        self.assertEqual(self.remote.read_artifact(artifact), b'XXXXefgh')
        self.assertEqual(calls, [0])
        # Staging must be a copy, so later corruption cannot mutate legacy bytes.
        staged = self.remote.local_cache / 'parts' / artifact['ref']['digest'] / '1'
        staged.write_bytes(b'WRNG')
        self.assertEqual((legacy / '1').read_bytes(), b'efgh')

    def test_corrupt_shared_and_staged_chunks_require_a_verified_download(self):
        from hashlib import sha256
        artifact, calls = self._configure_chunk_download(b'abcdefgh', b'abcdefgh')
        self.assertEqual(self.remote.read_artifact(artifact), b'abcdefgh')
        (self.remote.local_cache / 'parts' / artifact['ref']['digest'] / '0').write_bytes(b'WRNG')
        shared = self.remote.local_cache / 'chunks' / sha256(b'abcd').hexdigest()
        self.assertTrue(shared.is_file(), 'Verified chunks must be reusable independently of a whole artifact')
        shared.write_bytes(b'WRNG')
        calls.clear()
        self.assertEqual(self.remote.read_artifact(artifact), b'abcdefgh')
        self.assertEqual(calls, [0])
        self.assertEqual((self.remote.local_cache / 'chunks' / sha256(b'abcd').hexdigest()).read_bytes(), b'abcd')

    def test_duplicate_chunk_downloads_are_coalesced_with_parallel_workers(self):
        artifact, calls = self._configure_chunk_download(b'abcdabcdabcd', b'abcdabcdabcd')
        self.remote.transfer_workers = 4
        self.assertEqual(self.remote.read_artifact(artifact), b'abcdabcdabcd')
        self.assertEqual(len(calls), 1)

    def test_shared_chunk_symlink_is_rejected_without_following_it(self):
        from hashlib import sha256
        artifact, calls = self._configure_chunk_download(b'abcdefgh', b'abcdefgh')
        chunks = self.remote.local_cache / 'chunks'
        chunks.mkdir()
        outside = self.root / 'outside-chunk'
        outside.write_bytes(b'abcd')
        try:
            (chunks / sha256(b'abcd').hexdigest()).symlink_to(outside)
        except OSError as error:
            self.skipTest(f'Platform symlink permission unavailable: {error}')
        with self.assertRaisesRegex(ValueError, 'links'):
            self.remote.read_artifact(artifact)
        self.assertEqual(calls, [])
        self.assertEqual(outside.read_bytes(), b'abcd')

    @unittest.skipUnless(os.name == 'nt', 'Windows junction behavior')
    def test_shared_chunk_directory_junction_is_rejected(self):
        artifact, calls = self._configure_chunk_download(b'abcdefgh', b'abcdefgh')
        outside = self.root / 'outside-chunks'
        outside.mkdir()
        junction = self.remote.local_cache / 'chunks'
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(outside)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        try:
            with self.assertRaisesRegex(ValueError, 'links'):
                self.remote.read_artifact(artifact)
            self.assertEqual(calls, [])
            self.assertEqual(list(outside.iterdir()), [])
        finally:
            junction.rmdir()

    def test_corrupt_downloaded_chunk_cannot_publish_full_artifact(self):
        artifact, calls = self._configure_chunk_download(b'abcdefgh', b'abcdefgh')
        self.bridge.get = lambda remote, local: Path(local).write_bytes(b'WRNG')
        with self.assertRaisesRegex(ValueError, 'Downloaded chunk'):
            self.remote.read_artifact(artifact)
        self.assertFalse((self.remote.local_cache / artifact['ref']['digest']).exists())

    def test_chunk_download_concurrency_is_bounded_and_assembly_order_is_fixed(self):
        import threading,time
        artifact,calls=self._configure_chunk_download(b'abcdefghijklmnop',b'abcdefghijklmnop')
        original=self.bridge.get
        active=[0];peak=[0];lock=threading.Lock()
        def get(remote,local):
            with lock:
                active[0]+=1;peak[0]=max(peak[0],active[0])
            time.sleep(.02)
            try:original(remote,local)
            finally:
                with lock:active[0]-=1
        self.bridge.get=get
        self.remote.transfer_workers=2
        self.assertEqual(self.remote.read_artifact(artifact),b'abcdefghijklmnop')
        self.assertEqual(peak[0],2)


if __name__ == '__main__':
    unittest.main()
