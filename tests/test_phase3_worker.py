"""Local CPU subprocess tests for remote-spool safety; no JAX/TPU is run."""
from base64 import b64encode
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
MODULE = 'rsi_kernel_agent.workflow.colab_worker'


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


RUNNER = '''import json, os, sys, time
from pathlib import Path
def run_mla_stage(request, workspace, oracle_workspace, output_dir, **kwargs):
    print('CPU fixture stdout',flush=True)
    print('CPU fixture stderr',file=sys.stderr,flush=True)
    config=json.loads(kwargs['config_path'].read_text())
    counter=Path(config['counter'])
    with counter.open('a') as f:
        f.write(request['operation_id']+' start\\n'); f.flush(); os.fsync(f.fileno())
    if config.get('crash'): os._exit(73)
    if config.get('fail'):
        (output_dir/'partial.xplane.pb').write_bytes(b'partial CPU fixture trace')
        raise ValueError('deliberate CPU fixture failure')
    if config.get('barrier'):
        barrier=Path(config['barrier'])
        while not barrier.exists(): time.sleep(.02)
    time.sleep(config.get('delay',0))
    artifact=output_dir/'raw.bin'; artifact.write_bytes(b'raw CPU fixture output')
    with counter.open('a') as f:
        f.write(request['operation_id']+' end\\n'); f.flush(); os.fsync(f.fileno())
    return {'raw_artifact_paths':[str(artifact)], 'fixture':True}
'''


class WorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker = None
        if importlib.util.find_spec(MODULE) is not None:
            cls.worker = importlib.import_module(MODULE)

    def setUp(self):
        self.assertIsNotNone(self.worker, 'Durable remote WorkerSpool has not been implemented')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runner = self.root / 'runner.py'
        self.runner.write_text(RUNNER, encoding='utf-8')
        self.counter = self.root / 'counter.txt'
        self.runtime = 'test-runtime-' + uuid.uuid4().hex
        self.spool = self.worker.WorkerSpool(self.root / 'spool', self.runtime, 'fixed-nonce')

    def make(self, name='operation-1', *, task='task-1', **config):
        blobs = {}
        def put(raw):
            digest = hashlib.sha256(raw).hexdigest()
            blobs[digest] = b64encode(raw).decode()
            return {'digest': digest, 'size': len(raw)}
        source = put(b'# candidate fixture is never executed\n')
        oracle = put(b'# frozen oracle fixture\n')
        sb = {'schema_version': 1, 'entry': {'path': 'kernel.py', 'symbol': 'kernel'},
              'files': [{'path': 'kernel.py', 'artifact_ref': source, 'role': 'source'}]}
        ob = {'schema_version': 1, 'entry': {'path': 'oracle.py', 'symbol': 'oracle'},
              'files': [{'path': 'oracle.py', 'artifact_ref': oracle, 'role': 'oracle'}]}
        sbref, obref = put(canonical(sb)), put(canonical(ob))
        cfg = put(canonical({'counter': str(self.counter), **config}))
        runner_digest = hashlib.sha256(self.runner.read_bytes()).hexdigest()
        build = put(canonical({'schema_version': 1, 'source_bundle_digest': sbref['digest'],
              'oracle_bundle_digest': obref['digest'], 'runner_digest': runner_digest, 'oracle_digest': oracle['digest']}))
        identity = {'source_digest': source['digest'], 'config_digest': cfg['digest'], 'build_digest': build['digest']}
        protocol = {'runner_digest': runner_digest, 'oracle_bundle_ref': obref,
                    'oracle_digest': oracle['digest'], 'trusted_files': {'oracle.py': oracle['digest']}}
        job = {'id': 'job-' + name, 'task_id': task, 'run_id': 'run-' + name,
               'candidate_id': 'candidate-' + name, 'parent_id': 'parent', 'context_id': 'context',
               'contract_digest': '0' * 64, 'contract': {'context': {'environment': {'runtime_session_id': self.runtime}, 'protocol': protocol}},
               'variant': {'source_ref': source, 'source_bundle_ref': sbref, 'config_ref': cfg, 'build_ref': build,
                           'cost': {'executions': 1, 'tokens': 0, 'time_s': 1}}}
        request = {'schema_version': 1, 'backend_id': 'colab.v1', 'operation_id': name, 'stage': 'compile',
                   'job': job, 'candidate_identity': identity, 'parent_identity': identity, 'provenance': 'real'}
        request['request_digest'] = hashlib.sha256(canonical(request)).hexdigest()
        return request, {'source_bundle': sb, 'oracle_bundle': ob, 'blobs': blobs}

    def wait(self, operation, wanted='completed', seconds=12):
        stop = time.monotonic() + seconds
        while time.monotonic() < stop:
            result = self.spool.lookup(operation)
            if result['status'] == wanted:
                return result
            time.sleep(.03)
        self.fail(f'Expected {wanted}, got {result}')

    def test_missing_marker_is_unknown_and_bootstrap_cannot_reinitialize_lost_marker(self):
        self.assertEqual(self.spool.lookup('missing')['status'], 'unknown')
        self.spool.bootstrap()
        self.assertEqual(self.spool.lookup('missing')['status'], 'definitely_not_started')
        other = self.worker.WorkerSpool(self.root / 'spool', 'runtime-2', 'fixed-nonce')
        self.assertEqual(other.lookup('missing')['status'], 'unknown')
        with self.assertRaises(ValueError):
            other.bootstrap()
        (self.root / 'spool' / 'runtime.json').unlink()
        self.assertEqual(self.spool.lookup('missing')['status'], 'unknown')
        with self.assertRaises(ValueError):
            self.spool.bootstrap()

    def test_completed_replay_counts_one_execution_and_returns_hashed_artifacts(self):
        self.spool.bootstrap()
        request, payload = self.make()
        self.spool.submit(request, payload, self.runner)
        result = self.wait(request['operation_id'])
        for _ in range(3):
            self.assertEqual(self.spool.submit(request, payload, self.runner), result)
        self.assertEqual(self.counter.read_text().splitlines(), ['operation-1 start', 'operation-1 end'])
        self.assertEqual(result['receipt']['request_digest'], request['request_digest'])
        self.assertEqual(result['receipt']['outputs']['raw_artifact_refs'], [result['artifacts'][0]['ref']])
        artifact = result['artifacts'][0]
        self.assertEqual(hashlib.sha256(Path(artifact['path']).read_bytes()).hexdigest(), artifact['ref']['digest'])
        fresh = self.worker.WorkerSpool(self.root / 'spool', self.runtime, 'fixed-nonce')
        self.assertEqual(fresh.lookup('operation-1'), result)

    def test_corrupt_receipt_body_is_unknown(self):
        self.spool.bootstrap()
        request, payload = self.make()
        self.spool.submit(request, payload, self.runner)
        self.wait('operation-1')
        completion = next((self.root / 'spool' / 'operations').glob('*/completed.json'))
        record = json.loads(completion.read_bytes())
        record['receipt']['outputs']['fixture'] = False
        completion.write_bytes(canonical(record))
        self.assertEqual(self.spool.lookup('operation-1')['status'], 'unknown')
        self.assertEqual(self.spool.submit(request, payload, self.runner)['status'], 'unknown')
        self.assertEqual(len(self.counter.read_text().splitlines()), 2)

    def test_payload_trusted_oracle_change_rejected(self):
        self.spool.bootstrap()
        request, payload = self.make()
        changed = deepcopy(request)
        changed['job']['contract']['context']['protocol']['oracle_digest'] = '1' * 64
        changed['request_digest'] = hashlib.sha256(canonical({k:v for k,v in changed.items() if k != 'request_digest'})).hexdigest()
        with self.assertRaises(ValueError):
            self.spool.submit(changed, payload, self.runner)
        self.assertFalse(self.counter.exists())

    def test_conflicting_request_cannot_reuse_operation(self):
        self.spool.bootstrap()
        request, payload = self.make()
        self.spool.submit(request, payload, self.runner)
        self.wait(request['operation_id'])
        changed, changed_payload = self.make(task='other-task')
        with self.assertRaises(ValueError):
            self.spool.submit(changed, changed_payload, self.runner)
        self.assertEqual(len(self.counter.read_text().splitlines()), 2)

    def test_concurrent_identical_submit_runs_effect_once(self):
        self.spool.bootstrap()
        request, payload = self.make(delay=.2)
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(self.spool.submit, request, payload, self.runner) for _ in range(4)]
            for future in futures:
                self.assertIn(future.result()['status'], ('running', 'completed'))
        self.wait('operation-1')
        self.assertEqual(self.counter.read_text().splitlines(), ['operation-1 start', 'operation-1 end'])

    def test_bootstrap_rejects_prior_operations_without_any_marker(self):
        (self.root / 'spool' / 'operations' / 'existing-operation').mkdir(parents=True)
        with self.assertRaises(ValueError):
            self.spool.bootstrap()
        self.assertEqual(self.spool.lookup('operation-1')['status'], 'unknown')

    def test_same_runtime_cannot_use_second_spool_to_bypass_serialization(self):
        self.spool.bootstrap()
        second = self.worker.WorkerSpool(self.root / 'different-spool', self.runtime, 'other-nonce')
        with self.assertRaises(ValueError):
            second.bootstrap()
        self.assertEqual(second.lookup('unknown-operation')['status'], 'unknown')
        self.assertEqual(self.spool.lookup('unknown-operation')['status'], 'definitely_not_started')

    def test_global_registration_loss_does_not_create_fresh_execution_space(self):
        self.spool.bootstrap()
        (self.spool.coordinator / 'registration.json').unlink()
        self.assertEqual(self.spool.lookup('missing')['status'], 'unknown')
        with self.assertRaises(ValueError):
            self.spool.bootstrap()

    def test_fixed_mla_runner_entry_rejection_flows_through_real_worker(self):
        self.runner = Path(__file__).resolve().parents[1] / 'src/rsi_kernel_agent/workflow/mla_runner.py'
        self.spool.bootstrap()
        request, payload = self.make()
        raw = canonical({'schema_version': 1, 'kernel': 'mla_decode', 'case_overrides': {}})
        ref = {'digest': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
        payload['blobs'][ref['digest']] = b64encode(raw).decode()
        request['job']['variant']['config_ref'] = ref
        request['candidate_identity']['config_digest'] = ref['digest']
        request['job']['contract']['workload'] = {'spec': {'kernel': 'mla_decode', 'interface': 'contiguous',
            'cases': [{'id': 'tiny', 'config_name': 'mla_decode_vcs', 'outputs': [
                {'name': 'attention', 'shape': [1, 16, 128], 'dtype': 'bfloat16', 'comparison': 'allclose'},
                {'name': 'cache', 'shape': [1, 128, 256], 'dtype': 'bfloat16', 'comparison': 'exact'}]}]}}
        request['request_digest'] = hashlib.sha256(canonical({k:v for k,v in request.items() if k != 'request_digest'})).hexdigest()
        self.spool.submit(request, payload, self.runner)
        result = self.wait('operation-1')
        self.assertEqual(result['receipt']['status'], 'failed')
        self.assertEqual(result['receipt']['outputs']['error_type'], 'ValueError')
        self.assertIn('Candidate entry', result['receipt']['outputs']['message'])
        self.assertEqual(result['receipt']['artifact_refs'], [a['ref'] for a in result['artifacts']])
        self.assertFalse(self.counter.exists())

    def test_runner_and_payload_identity_rejected_before_execution(self):
        self.spool.bootstrap()
        request, payload = self.make()
        bad = deepcopy(payload)
        bad['blobs'][request['job']['variant']['source_ref']['digest']] = b64encode(b'wrong').decode()
        with self.assertRaises(ValueError):
            self.spool.submit(request, bad, self.runner)
        self.runner.write_text(RUNNER + '\n# changed', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.spool.submit(request, payload, self.runner)
        self.assertFalse(self.counter.exists())

    def test_child_does_not_execute_pythonpath_startup_code(self):
        self.spool.bootstrap()
        request, payload = self.make()
        startup = self.root / 'untrusted-startup'; startup.mkdir()
        touched = self.root / 'startup-executed'
        (startup / 'sitecustomize.py').write_text('from pathlib import Path\nPath(' + repr(str(touched)) + ').write_text("executed")\n', encoding='utf-8')
        previous = os.environ.get('PYTHONPATH')
        try:
            os.environ['PYTHONPATH'] = str(startup)
            self.spool.submit(request, payload, self.runner)
            self.wait('operation-1')
        finally:
            if previous is None:
                os.environ.pop('PYTHONPATH', None)
            else:
                os.environ['PYTHONPATH'] = previous
        self.assertFalse(touched.exists(), 'Remote child executed untrusted PYTHONPATH startup code')

    def test_source_escape_rejected_without_outside_write(self):
        self.spool.bootstrap()
        request, payload = self.make()
        payload['source_bundle']['files'][0]['path'] = '../escape.py'
        with self.assertRaises(ValueError):
            self.spool.submit(request, payload, self.runner)
        self.assertFalse((self.root / 'escape.py').exists())

    def test_all_stages_serialize_across_tasks(self):
        self.spool.bootstrap()
        first, p1 = self.make('first', delay=.3)
        second, p2 = self.make('second', task='different-task', delay=.3)
        self.spool.submit(first, p1, self.runner)
        self.spool.submit(second, p2, self.runner)
        self.wait('first')
        self.wait('second')
        lines = self.counter.read_text().splitlines()
        self.assertIn(lines, [['first start', 'first end', 'second start', 'second end'],
                              ['second start', 'second end', 'first start', 'first end']])

    def test_crash_after_external_effect_is_unknown_and_never_reexecuted(self):
        self.spool.bootstrap()
        request, payload = self.make(crash=True)
        self.spool.submit(request, payload, self.runner)
        self.wait('operation-1', 'unknown')
        for _ in range(2):
            self.assertEqual(self.spool.submit(request, payload, self.runner)['status'], 'unknown')
        other, other_payload = self.make('second', task='other-task')
        self.spool.submit(other, other_payload, self.runner)
        self.wait('second', 'unknown')
        self.assertEqual(self.counter.read_text().splitlines(), ['operation-1 start'])

    def test_completed_receipt_survives_client_process_death(self):
        self.spool.bootstrap()
        request, payload = self.make()
        payload_path, request_path = self.root / 'payload.json', self.root / 'request.json'
        payload_path.write_bytes(canonical(payload)); request_path.write_bytes(canonical(request))
        code = ('import json,os,sys;sys.path.insert(0,sys.argv[1]);'
                'from rsi_kernel_agent.workflow.colab_worker import run_command;'
                'run_command(json.loads(sys.argv[2]));os._exit(73)')
        command = {'action': 'submit', 'root': str(self.root / 'spool'), 'runtime_id': self.runtime, 'nonce': 'fixed-nonce',
                   'request': request, 'payload_path': str(payload_path), 'runner_path': str(self.runner)}
        child = subprocess.run([sys.executable, '-B', '-c', code, str(Path(__file__).resolve().parents[1] / 'src'), json.dumps(command)], timeout=10)
        self.assertEqual(child.returncode, 73)
        self.wait('operation-1')
        self.spool.submit(request, payload, self.runner)
        self.assertEqual(self.counter.read_text().splitlines(), ['operation-1 start', 'operation-1 end'])

    def test_corrupt_completed_artifact_is_unknown_not_reexecuted(self):
        self.spool.bootstrap()
        request, payload = self.make()
        self.spool.submit(request, payload, self.runner)
        result = self.wait('operation-1')
        Path(result['artifacts'][0]['path']).write_bytes(b'corrupt')
        self.assertEqual(self.spool.lookup('operation-1')['status'], 'unknown')
        self.assertEqual(self.spool.submit(request, payload, self.runner)['status'], 'unknown')
        self.assertEqual(len(self.counter.read_text().splitlines()), 2)

    def test_runner_exception_becomes_durable_failed_receipt(self):
        self.spool.bootstrap()
        request, payload = self.make(fail=True)
        self.spool.submit(request, payload, self.runner)
        result = self.wait('operation-1')
        self.assertEqual(result['receipt']['status'], 'failed')
        self.assertEqual(result['receipt']['outputs']['error_type'], 'ValueError')
        self.assertIn(b'partial CPU fixture trace', [Path(a['path']).read_bytes() for a in result['artifacts']])
        self.spool.submit(request, payload, self.runner)
        self.assertEqual(self.counter.read_text().splitlines(), ['operation-1 start'])

    def test_runner_stdout_stderr_are_returned_as_verified_artifacts(self):
        self.spool.bootstrap()
        request, payload = self.make()
        self.spool.submit(request, payload, self.runner)
        result = self.wait('operation-1')
        contents = [Path(item['path']).read_bytes() for item in result['artifacts']]
        self.assertIn(b'CPU fixture stdout\n', contents)
        self.assertIn(b'CPU fixture stderr\n', contents)

    def test_unobservable_device_cannot_become_valid_benchmark(self):
        observation = self.worker.observe_device_owners()
        if observation['status'] == 'clear_visible_processes':
            self.skipTest('This local safety test expects no real TPU available')
        self.spool.bootstrap()
        request, payload = self.make()
        request['stage'] = 'benchmark'
        request['request_digest'] = hashlib.sha256(canonical({k:v for k,v in request.items() if k != 'request_digest'})).hexdigest()
        self.spool.submit(request, payload, self.runner)
        result = self.wait('operation-1')
        self.assertEqual(result['receipt']['status'], 'invalid')
        self.assertEqual(result['receipt']['outputs']['error_type'], 'InterferenceUnknown')
        self.assertFalse(self.counter.exists())


if __name__ == '__main__':
    unittest.main()
