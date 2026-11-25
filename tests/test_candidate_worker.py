import hashlib
import json
import os
from pathlib import Path
import sys
import time
import unittest
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import threading
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import test_phase3_worker as stage_fixture
from rsi_kernel_agent.workflow import colab_worker


RUNNER = '''import json, os, sys, time
from pathlib import Path
class CandidateRun:
    def __init__(self, requests, workspace, oracle_workspace, output_root, **kwargs):
        self.config=json.loads(kwargs['config_path'].read_text())
        self.counter=Path(self.config['counter'])
        self.root=output_root
        self.run_id=requests[0]['job']['run_id']
        self.sequence=[]
        sys.modules['__main__'].observe_device_owners=lambda: {'status':'clear_visible_processes'}
    def run_stage(self, request, output_dir, *, lease):
        stage=request['stage']
        self.sequence.append(stage)
        row={'stage':stage,'pid':os.getpid(),'run_id':self.run_id,'sequence':list(self.sequence)}
        with self.counter.open('a') as stream:
            stream.write(json.dumps(row)+'\\n'); stream.flush(); os.fsync(stream.fileno())
        if self.config.get('crash_stage')==stage: os._exit(73)
        if self.config.get('barrier_stage')==stage:
            barrier=Path(self.config['barrier'])
            while not barrier.exists(): time.sleep(.01)
        time.sleep(self.config.get('delay',0))
        if self.config.get('fail_stage')==stage:
            if stage=='correctness': return {'correctness':'failed'}
            if stage=='compile':
                (self.root/'shared').mkdir(exist_ok=True)
                (self.root/'shared'/'partial.bin').write_bytes(b'partial compile evidence')
            raise ValueError('deliberate stage failure')
        shared=self.root/'shared.bin'
        if stage=='compile': shared.write_bytes(b'shared input fixture')
        return {'fixture':row,'correctness':'passed','raw_artifact_paths':[str(shared)],'lease':lease}
'''


def digest(value):
    return hashlib.sha256(stage_fixture.canonical(value)).hexdigest()


class CandidateWorkerTests(unittest.TestCase):
    make_stage = stage_fixture.WorkerTests.make

    def setUp(self):
        self.worker = colab_worker
        stage_fixture.WorkerTests.setUp(self)
        self.runner.write_text(RUNNER, encoding='utf-8')
        self.spool.bootstrap()
        self.running = []
        self.addCleanup(self.finish_children)

    def finish_children(self):
        for barrier in self.root.glob('barrier-*'):
            barrier.write_text('release', encoding='utf-8')
        for identity in self.running:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                state = self.spool.lookup_candidate(identity)
                if state['status'] in ('completed', 'unknown'):
                    break
                time.sleep(.02)

    def candidate(self, name='one', **config):
        stage, payload = self.make_stage(name, **config)
        requests = []
        run_id = stage['job']['run_id']
        for kind in ('generate', 'compile', 'correctness', 'benchmark', 'profile'):
            row = deepcopy(stage)
            row.update(stage=kind, operation_id='operation:' + digest([run_id, kind]))
            row.pop('request_digest')
            row['request_digest'] = digest(row)
            requests.append(row)
        body = {'schema_version': 1, 'type': 'candidate_execution.v1',
                'operation_id': 'candidate-operation:' + digest([run_id]), 'requests': requests}
        return {**body, 'request_digest': digest(body)}, payload

    def submit(self, request, payload):
        self.running.append(request['operation_id'])
        return self.spool.submit_candidate(request, payload, self.runner)

    def wait(self, request, status='completed', seconds=15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = self.spool.lookup_candidate(request['operation_id'])
            if value['status'] == status:
                return value
            time.sleep(.02)
        self.fail(f'Expected {status}, got {value}')

    def wait_stage(self, kind, seconds=10):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            rows = self.rows()
            if any(row['stage'] == kind for row in rows):
                return
            time.sleep(.02)
        self.fail('Stage did not start: ' + kind)

    def rows(self):
        return [json.loads(line) for line in self.counter.read_text().splitlines()] if self.counter.exists() else []

    def test_complete_run_reuses_one_process_and_returns_original_stage_receipts(self):
        request, payload = self.candidate()
        self.submit(request, payload)
        result = self.wait(request)
        self.assertEqual(set(result), {'status', 'stages', 'cancelled'})
        self.assertFalse(result['cancelled'])
        receipts = [stage['receipt'] for stage in result['stages']]
        self.assertEqual([r['stage'] for r in receipts], ['generate', 'compile', 'correctness', 'benchmark', 'profile'])
        self.assertEqual([r['operation_id'] for r in receipts], [r['operation_id'] for r in request['requests']])
        self.assertEqual(len({r['pid'] for r in self.rows()}), 1)
        self.assertEqual(self.rows()[-1]['sequence'], ['compile', 'correctness', 'benchmark', 'profile'])
        self.assertEqual(sum(r['cost']['executions'] for r in receipts), 1)
        for _ in range(2):
            self.assertEqual(self.submit(request, payload), result)
        self.assertEqual(len(self.rows()), 4)
        rebuilt = colab_worker.WorkerSpool(self.spool.root, self.runtime, 'fixed-nonce')
        self.assertEqual(rebuilt.lookup_candidate(request['operation_id']), result)

    def test_candidate_validation_rejects_changed_source_order_and_mixed_run(self):
        request, payload = self.candidate()
        changes = []
        changed = deepcopy(request)
        changed['requests'][1]['candidate_identity']['source_digest'] = '0' * 64
        changes.append(changed)
        changed = deepcopy(request)
        changed['requests'][1], changed['requests'][2] = changed['requests'][2], changed['requests'][1]
        changes.append(changed)
        changed = deepcopy(request)
        changed['requests'][2]['job']['run_id'] = 'another-run'
        changes.append(changed)
        for changed in changes:
            for stage in changed['requests']:
                stage['request_digest'] = digest({k: v for k, v in stage.items() if k != 'request_digest'})
            changed['request_digest'] = digest({k: v for k, v in changed.items() if k != 'request_digest'})
            with self.subTest(request=changed['request_digest']), self.assertRaises(ValueError):
                self.spool.submit_candidate(changed, payload, self.runner)
        self.assertEqual(self.rows(), [])

    def test_failure_short_circuits_without_fabricating_later_receipts(self):
        request, payload = self.candidate(fail_stage='correctness')
        self.submit(request, payload)
        result = self.wait(request)
        self.assertEqual([s['receipt']['stage'] for s in result['stages']], ['generate', 'compile', 'correctness'])
        self.assertEqual(result['stages'][-1]['receipt']['status'], 'failed')
        self.assertEqual([r['stage'] for r in self.rows()], ['compile', 'correctness'])
        later = request['requests'][3]
        with self.assertRaisesRegex(ValueError, 'candidate'):
            self.spool.submit(later, payload, self.runner)
        self.assertNotEqual(self.spool.lookup(later['operation_id'])['status'], 'definitely_not_started')

    def test_compile_failure_retains_shared_oracle_and_input_artifacts(self):
        request, payload = self.candidate(fail_stage='compile')
        self.submit(request, payload)
        result = self.wait(request)
        self.assertEqual(result['stages'][-1]['receipt']['status'], 'failed')
        self.assertIn(hashlib.sha256(b'partial compile evidence').hexdigest(),
                      {item['ref']['digest'] for item in result['stages'][-1]['artifacts']})

    def test_cancel_waits_for_active_stage_to_exit_and_is_idempotent(self):
        barrier = self.root / 'barrier-cancel'
        request, payload = self.candidate(barrier_stage='compile', barrier=str(barrier))
        self.submit(request, payload)
        self.wait_stage('compile')
        for _ in range(2):
            value = self.spool.cancel_candidate(request['operation_id'])
            self.assertEqual(value['status'], 'running')
            self.assertFalse(value['cancelled'])
        barrier.write_text('release', encoding='utf-8')
        result = self.wait(request)
        self.assertTrue(result['cancelled'])
        self.assertEqual([s['receipt']['stage'] for s in result['stages']], ['generate', 'compile'])
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.spool.cancel_candidate(request['operation_id']), result)

    def test_running_response_contains_only_the_completed_prefix(self):
        barrier = self.root / 'barrier-profile'
        request, payload = self.candidate(barrier_stage='profile', barrier=str(barrier))
        self.submit(request, payload)
        self.wait_stage('profile')
        result = self.spool.lookup_candidate(request['operation_id'])
        self.assertEqual(result['status'], 'running')
        self.assertEqual([s['receipt']['stage'] for s in result['stages']], ['generate', 'compile', 'correctness', 'benchmark'])
        barrier.write_text('release', encoding='utf-8')
        self.wait(request)

    def test_worker_crash_quarantines_runtime_and_never_restarts_run(self):
        request, payload = self.candidate(crash_stage='correctness')
        self.submit(request, payload)
        self.wait_stage('correctness')
        result = self.wait(request, 'unknown')
        self.assertEqual(result['stages'], [])
        self.assertFalse(result['cancelled'])
        for _ in range(2):
            self.assertEqual(self.submit(request, payload), result)
        other, other_payload = self.candidate('after-crash')
        self.submit(other, other_payload)
        self.wait(other, 'unknown')
        self.assertEqual(len(self.rows()), 2)

    def test_partial_submission_cannot_be_spawned_again(self):
        request, payload = self.candidate()
        with patch.object(colab_worker.subprocess, 'Popen', side_effect=OSError('launch interrupted')):
            with self.assertRaises(OSError):
                self.submit(request, payload)
        self.assertEqual(self.spool.lookup_candidate(request['operation_id'])['status'], 'unknown')
        self.assertEqual(self.submit(request, payload)['status'], 'unknown')
        other, other_payload = self.candidate('after-partial')
        self.submit(other, other_payload)
        self.wait(other, 'unknown')
        self.assertEqual(self.rows(), [])

    def test_distinct_runs_use_distinct_processes(self):
        for name in ('one', 'two'):
            request, payload = self.candidate(name)
            self.submit(request, payload)
            self.wait(request)
        rows = self.rows()
        self.assertEqual(len({row['pid'] for row in rows}), 2)
        self.assertEqual([row['sequence'] for row in rows if row['stage'] == 'compile'], [['compile'], ['compile']])

    def test_two_submitted_candidates_serialize_without_quarantining_waiters(self):
        first, payload = self.candidate('first', delay=.05)
        second, other = self.candidate('second', delay=.05)
        self.submit(first, payload)
        self.submit(second, other)
        self.wait(first)
        self.wait(second)
        self.assertEqual([row['run_id'] for row in self.rows()], ['run-first'] * 4 + ['run-second'] * 4)

    def check_publication_boundary(self, boundary, *, interrupted=False, legacy=False):
        published, release, observed, returned = (threading.Event() for _ in range(4))
        barrier_thread = []
        publish, lock = colab_worker._publish, colab_worker._lock
        first, payload = self.candidate('first')
        second, other = self.candidate('second')
        pending = first if legacy else second
        pending_path = self.spool._candidate(pending['operation_id'])
        current = None if legacy else self.spool._candidate(first['operation_id'])

        def paused_publish(path, data):
            if path == pending_path / boundary:
                published.set()
                if not release.wait(10):
                    raise TimeoutError('Publisher was not released')
                if interrupted:
                    raise InterruptedError('Publication interrupted')
            return publish(path, data)

        @contextmanager
        def observed_lock(path, *, blocking=False):
            if (barrier_thread == [threading.get_ident()]
                    and path == self.spool.root / 'submission.lock'):
                # Observe actual contention, without replacing the blocking lock.
                with lock(path) as acquired:
                    if not acquired:
                        observed.set()
            with lock(path, blocking=blocking) as acquired:
                yield acquired

        def barrier():
            barrier_thread.append(threading.get_ident())
            try:
                return self.spool._candidate_barrier(current)
            finally:
                returned.set()
                observed.set()

        # Real children hold candidate locks but cannot execute until this
        # runtime lock is released. Only publication is paused by the spy.
        with lock(self.spool.coordinator / 'runtime.lock', blocking=True):
            if not legacy:
                self.submit(first, payload)
            with ThreadPoolExecutor(max_workers=2) as pool, \
                    patch.object(colab_worker, '_publish', side_effect=paused_publish), \
                    patch.object(colab_worker, '_lock', side_effect=observed_lock):
                publishing = pool.submit(self.submit, pending, payload if legacy else other)
                try:
                    self.assertTrue(published.wait(10), 'Publisher did not reach the boundary')
                    self.assertFalse((pending_path / boundary).exists())
                    checking = pool.submit(barrier)
                    self.assertTrue(observed.wait(10), 'Barrier neither waited nor returned')
                    self.assertFalse(returned.is_set(), 'Barrier consumed live incomplete publication')
                    release.set()
                    if interrupted:
                        with self.assertRaises(InterruptedError):
                            publishing.result(timeout=10)
                    else:
                        self.assertEqual(publishing.result(timeout=10)['status'], 'running')
                    self.assertEqual(checking.result(timeout=10), interrupted)
                finally:
                    release.set()
        if interrupted:
            self.assertEqual(self.spool.lookup_candidate(pending['operation_id'])['status'], 'unknown')
            self.wait(first, 'unknown')
            self.assertTrue((current / 'blocked.json').exists())
            self.assertEqual(self.rows(), [])
        else:
            self.wait(first)
            if legacy:
                self.assertEqual([row['run_id'] for row in self.rows()], ['run-first'] * 4)
            else:
                self.wait(second)
                self.assertIn([row['run_id'] for row in self.rows()],
                              [['run-first'] * 4 + ['run-second'] * 4,
                               ['run-second'] * 4 + ['run-first'] * 4])

    def test_barrier_waits_for_live_request_publication(self):
        self.check_publication_boundary('request.json')

    def test_barrier_waits_for_live_dispatch_publication(self):
        self.check_publication_boundary('dispatch.json')

    def test_barrier_quarantines_interrupted_request_publication(self):
        self.check_publication_boundary('request.json', interrupted=True)

    def test_barrier_quarantines_interrupted_dispatch_publication(self):
        self.check_publication_boundary('dispatch.json', interrupted=True)

    def test_legacy_barrier_waits_for_live_request_publication(self):
        self.check_publication_boundary('request.json', legacy=True)

    def test_legacy_barrier_waits_for_live_dispatch_publication(self):
        self.check_publication_boundary('dispatch.json', legacy=True)

    def test_barrier_rejects_corrupt_or_inconsistent_published_metadata(self):
        first, _ = self.candidate('first')
        second, _ = self.candidate('second')
        current = self.spool._candidate(first['operation_id'])
        pending = self.spool._candidate(second['operation_id'])
        for sequence, request in enumerate((first, second), 1):
            path = self.spool._candidate(request['operation_id'])
            colab_worker._publish(path / 'request.json', stage_fixture.canonical(request))
            colab_worker._publish(path / 'dispatch.json', stage_fixture.canonical(
                {'request_digest': request['request_digest'], 'sequence': sequence}))
        self.assertFalse(self.spool._candidate_barrier(current))
        corruptions = [('request.json', b'{'), ('dispatch.json', b'{'),
                       ('dispatch.json', stage_fixture.canonical(
                           {'request_digest': '0' * 64, 'sequence': 2})),
                       ('dispatch.json', stage_fixture.canonical(
                           {'request_digest': second['request_digest'], 'sequence': '2'})),
                       ('request.json', stage_fixture.canonical({**second, 'request_digest': '0' * 64}))]
        for filename, raw in corruptions:
            with self.subTest(filename=filename, raw=raw[:100]):
                path = pending / filename
                original = path.read_bytes()
                try:
                    path.write_bytes(raw)
                    self.assertTrue(self.spool._candidate_barrier(current))
                    self.assertTrue(self.spool._candidate_barrier())
                finally:
                    path.write_bytes(original)

    def test_barrier_releases_publication_lock_before_process_exit_wait(self):
        request, payload = self.candidate()
        self.submit(request, payload)
        self.wait(request)
        waiting, exited = threading.Event(), threading.Event()

        def process_exited(identity):
            waiting.set()
            return exited.is_set()

        with ThreadPoolExecutor(max_workers=1) as pool, \
                patch.object(colab_worker, '_process_exited', side_effect=process_exited):
            checking = pool.submit(self.spool._candidate_barrier)
            try:
                self.assertTrue(waiting.wait(10), 'Barrier did not check terminal process')
                with colab_worker._lock(self.spool.root / 'submission.lock') as acquired:
                    self.assertTrue(acquired, 'Process-exit wait held the publication lock')
                    exited.set()
                    self.assertFalse(checking.result(timeout=10))
            finally:
                exited.set()

    def test_completed_candidate_teardown_does_not_quarantine_next_run(self):
        self.runner.write_text("import atexit\natexit.register(lambda: time.sleep(1.3))\n" + RUNNER, encoding='utf-8')
        first, payload = self.candidate('first')
        self.submit(first, payload)
        self.wait_stage('profile')
        second, other = self.candidate('second')
        self.submit(second, other)
        self.wait(first)
        result = self.wait(second)
        self.assertEqual(result['status'], 'completed')

    def test_terminal_marker_does_not_report_complete_while_same_process_is_alive(self):
        request, payload = self.candidate()
        self.submit(request, payload)
        completed = self.wait(request)
        candidate_dir = next((self.spool.root / 'candidates').iterdir())
        started = json.loads((candidate_dir / 'started.json').read_bytes())
        with patch.object(colab_worker, '_process_identity', return_value=('alive', started['process'])):
            result = self.spool.lookup_candidate(request['operation_id'])
            self.assertEqual(result['status'], 'running')
            self.assertFalse(result['cancelled'])
        with patch.object(colab_worker, '_process_identity', return_value=('alive',
                {**started['process'], 'created': str(int(started['process']['created']) + 1)})):
            self.assertEqual(self.spool.lookup_candidate(request['operation_id']), completed)

    def test_corrupt_terminal_or_stage_owner_is_unknown(self):
        request, payload = self.candidate()
        self.submit(request, payload)
        self.wait(request)
        candidate_dir = next((self.spool.root / 'candidates').iterdir())
        terminal = candidate_dir / 'terminal.json'
        original = terminal.read_bytes()
        data = json.loads(original)
        data['completed_operations'].pop()
        terminal.write_bytes(stage_fixture.canonical(data))
        self.assertEqual(self.spool.lookup_candidate(request['operation_id']),
                         {'status': 'unknown', 'stages': [], 'cancelled': False})
        terminal.write_bytes(original)
        owner = self.spool._operation(request['requests'][1]['operation_id']) / 'candidate.json'
        data = json.loads(owner.read_bytes())
        data['request_digest'] = '0' * 64
        owner.write_bytes(stage_fixture.canonical(data))
        self.assertEqual(self.spool.lookup_candidate(request['operation_id'])['status'], 'unknown')

    def test_unknown_candidate_cancel_does_not_create_execution_state(self):
        operation = 'candidate-operation:' + 'f' * 64
        self.assertEqual(self.spool.lookup_candidate(operation),
                         {'status': 'definitely_not_started', 'stages': [], 'cancelled': False})
        result = self.spool.cancel_candidate(operation)
        self.assertEqual(result['status'], 'definitely_not_started')
        self.assertFalse((self.spool.root / 'candidates').exists())


if __name__ == '__main__':
    unittest.main()
