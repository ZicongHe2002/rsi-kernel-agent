"""Real backend protocol exercised with local transport fixtures, not a TPU."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest

from phase3_fixtures import make_real_fixture
from rsi_kernel_agent.contracts import ArtifactRef, RunEvent, canonical_json
from rsi_kernel_agent.workflow.colab import ColabBackend
from rsi_kernel_agent.workflow.colab_bridge import BridgeUnavailable
from rsi_kernel_agent.workflow.execution import make_request


class RemoteFixture:
    runtime_id = 'runtime-1'
    nonce = 'fixture-nonce'
    root = '/content/rsi-fixture'

    def __init__(self):
        self.operations = {}
        self.calls = 0
        self.lose_response = False
        self.unavailable = False

    def lookup(self, operation_id):
        if self.unavailable:
            raise BridgeUnavailable('transport unavailable')
        return deepcopy(self.operations.get(operation_id, {'status': 'definitely_not_started', 'receipt': None}))

    def submit(self, request, payload):
        self.calls += 1
        job = request['job']
        body = {k: request[k] for k in ('schema_version', 'backend_id', 'operation_id', 'request_digest',
                                       'stage', 'candidate_identity', 'parent_identity', 'provenance')}
        body.update({k: job[k] for k in ('contract_digest', 'run_id', 'candidate_id', 'parent_id', 'context_id')})
        body.update(status='succeeded', outputs={'workspace': '/content/rsi-fixture/candidate',
                    **request['candidate_identity'], 'source_bundle_digest': job['variant']['source_bundle_ref']['digest']},
                    cost=job['variant']['cost'])
        body['artifact_refs'] = []
        self.operations[request['operation_id']] = {'status': 'completed', 'receipt': body, 'artifacts': []}
        if self.lose_response:
            raise BridgeUnavailable('response lost after remote commit')
        return self.lookup(request['operation_id'])


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        data = make_real_fixture(self.temp.name)
        self.fixture, self.store, self.job = data['fixture'], data['store'], data['job']
        self.remote = RemoteFixture()
        self.remote.runtime_id = self.job['contract']['context']['environment']['runtime_session_id']
        self.backend = ColabBackend(self.store, self.remote)
        run_id = self.job['run_id']
        self.store.transition(RunEvent(run_id + ':start', run_id, 'running', 'active', 'Local transport test'),
                              None, 'test:start')
        self.request = make_request(self.store, self.job, 'generate')

    def test_lost_response_recovers_durable_remote_result_without_reexecution(self):
        self.remote.lose_response = True
        self.assertEqual(self.backend.submit(self.request)['status'], 'unknown')
        rebuilt = ColabBackend(self.store, self.remote)
        response = rebuilt.lookup(self.request['operation_id'])
        self.assertEqual(response['status'], 'completed')
        self.assertEqual(response['receipt']['run_id'], self.job['run_id'])
        self.assertEqual(rebuilt.submit(self.request), response)
        self.assertEqual(self.remote.calls, 1)

    def test_unknown_lookup_never_causes_a_submit(self):
        self.remote.unavailable = True
        self.assertEqual(self.backend.submit(self.request)['status'], 'unknown')
        self.assertEqual(self.remote.calls, 0)

    def test_remote_receipt_for_wrong_source_is_rejected_before_publication(self):
        self.remote.submit(self.request, {})
        self.remote.operations[self.request['operation_id']]['receipt']['candidate_identity']['source_digest'] = 'f' * 64
        with self.assertRaises(ValueError):
            self.backend.submit(self.request)
        self.assertEqual(self.remote.calls, 1)

    def test_changed_runtime_mapping_is_rejected(self):
        self.remote.runtime_id = 'another-runtime'
        with self.assertRaises(ValueError):
            self.backend.submit(self.request)
        self.assertEqual(self.remote.calls, 0)

    def test_all_source_and_oracle_bytes_are_in_payload(self):
        payload = self.backend.payload(self.request)
        import base64
        from hashlib import sha256
        for digest, encoded in payload['blobs'].items():
            self.assertEqual(sha256(base64.b64decode(encoded)).hexdigest(), digest)
        self.assertIn(self.job['variant']['config_ref']['digest'], payload['blobs'])
        import json
        bundle = json.loads(self.store.artifacts.read(ArtifactRef(**self.fixture.bundle)))
        oracle = json.loads(self.store.artifacts.read(ArtifactRef(**self.fixture.oracle_bundle)))
        self.assertEqual(payload['source_bundle']['entry'], bundle['entry'])
        self.assertEqual(payload['oracle_bundle']['entry'], oracle['entry'])

    def test_all_remote_logs_are_bound_and_reverified_on_local_recovery(self):
        from hashlib import sha256
        raw = b'original compiler log'
        artifact = {'ref': {'digest': sha256(raw).hexdigest(), 'size': len(raw)},
                    'path': '/content/rsi-fixture/blobs/' + sha256(raw).hexdigest()}
        self.remote.submit(self.request, {})
        state = self.remote.operations[self.request['operation_id']]
        state['receipt']['artifact_refs'] = [artifact['ref']]
        state['artifacts'] = [artifact]
        self.remote.read_artifact = lambda item: raw
        result = self.backend.submit(self.request)
        self.assertEqual(result['receipt']['artifact_refs'], [artifact['ref']])
        self.store.artifacts.path_for(ArtifactRef(**artifact['ref'])).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            self.backend.lookup(self.request['operation_id'])

    def test_partial_chunk_transfer_does_not_publish_receipt_or_repeat_execution(self):
        from hashlib import sha256
        import json
        from types import SimpleNamespace
        from rsi_kernel_agent.workflow.colab_remote import ColabRemote

        raw = b'abcdefghij'
        digest = sha256(raw).hexdigest()
        self.remote.root = '/content/rsi-kernel-agent/transfer-fixture'
        artifact = {'ref': {'digest': digest, 'size': len(raw)},
                    'path': self.remote.root + '/blobs/' + digest}
        chunks = [raw[i:i + 4] for i in range(0, len(raw), 4)]
        manifest = {'digest': digest, 'size': len(raw), 'chunks': [
            {'path': self.remote.root + '/transfers/' + digest + '/' + str(index),
             'digest': sha256(chunk).hexdigest(), 'size': len(chunk), 'offset': index * 4}
            for index, chunk in enumerate(chunks)]}
        calls = []
        interrupted = [False]
        def get(remote, local):
            index = int(remote.rsplit('/', 1)[1])
            calls.append(index)
            if index == 1 and not interrupted[0]:
                interrupted[0] = True
                raise BridgeUnavailable('CPU fixture interrupted transfer')
            Path(local).write_bytes(chunks[index])
        bridge = SimpleNamespace(runtime_id=self.remote.runtime_id, get=get,
            execute=lambda code: [{'output_type': 'stream', 'text': 'RSI_RESULT:' + json.dumps(manifest)}])
        downloader = ColabRemote(bridge, self.remote.root, self.remote.nonce, __file__, __file__,
                                 Path(self.temp.name) / 'download-cache')
        downloader.chunk_bytes = 4
        downloader.transfer_workers = 1
        self.remote.read_artifact = downloader.read_artifact
        self.remote.submit(self.request, {})
        state = self.remote.operations[self.request['operation_id']]
        state['receipt']['artifact_refs'] = [artifact['ref']]
        state['artifacts'] = [artifact]

        self.assertEqual(self.backend.submit(self.request)['status'], 'unknown')
        self.assertFalse(any(getattr(record, 'details', {}).get('type') == 'execution_receipt.v1'
                             for record in self.store.history(self.job['task_id'])))
        self.assertFalse(self.store.artifacts.path_for(ArtifactRef(**artifact['ref'])).exists())
        recovered = self.backend.submit(self.request)
        self.assertEqual(recovered['status'], 'completed')
        self.assertEqual(self.store.artifacts.read(ArtifactRef(**artifact['ref'])), raw)
        self.assertEqual(calls, [0, 1, 1, 2])
        self.assertEqual(self.remote.calls, 1)


if __name__ == '__main__':
    unittest.main()
