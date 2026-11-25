from copy import deepcopy
from dataclasses import replace
import tempfile
import unittest

from phase3_fixtures import RealProtocolFixture
from rsi_kernel_agent.contracts import Decision, RunEvent
from rsi_kernel_agent.workflow import execution
from rsi_kernel_agent.workflow.colab import ColabBackend
from rsi_kernel_agent.workflow.controller import Controller
from rsi_kernel_agent.workflow.execution import make_request, stable_id
from test_phase3_backend import RemoteFixture


class CandidateExecutionTests(RealProtocolFixture, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.prepare(self.temp.name)
        context = self.store.get('context')
        self.store.append(replace(context, id='continuous-context',
            protocol={**context.protocol, 'execution_mode': 'candidate.v1'}), 'continuous-context')

    def continuous_job(self):
        from rsi_kernel_agent.contracts import Run
        job = self.job()
        self.store.append(Run('continuous-run', 'candidate', 'continuous-context'), 'continuous-run')
        contract, digest = execution.execution_contract(self.store, 'continuous-context')
        job.update(run_id='continuous-run', context_id='continuous-context', contract=contract, contract_digest=digest)
        return job

    def test_candidate_request_binds_all_stages_and_run_identity(self):
        job = self.continuous_job()
        request = execution.make_candidate_request(self.store, job)
        self.assertEqual(request['type'], 'candidate_execution.v1')
        self.assertEqual(request['operation_id'], stable_id('candidate-operation', job['run_id']))
        self.assertEqual([r['stage'] for r in request['requests']],
                         ['generate', 'compile', 'correctness', 'benchmark', 'profile'])
        self.assertEqual(request['requests'], [make_request(self.store, job, stage)
                                              for stage in execution.stages_for('real')])
        self.assertEqual(execution.validate_candidate_request(self.store, request), request)
        for mutation in ('order', 'digest', 'run'):
            changed = deepcopy(request)
            if mutation == 'order':
                changed['requests'].reverse()
            elif mutation == 'digest':
                changed['request_digest'] = '0' * 64
            else:
                changed['requests'][1]['job']['run_id'] = 'other-run'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                execution.validate_candidate_request(self.store, changed)

    def test_candidate_identity_requires_run_scoped_process_metadata(self):
        from rsi_kernel_agent.workflow.real_protocol import validate_identity
        job = self.continuous_job()
        request = make_request(self.store, job, 'compile')
        output = {'environment': self.environment, 'identity': self.identity_output()}
        with self.assertRaises(ValueError):
            validate_identity(self.store, request, output)
        output['identity'].update(process_scope='candidate_run', process_run_id=job['run_id'], process_id=123)
        validate_identity(self.store, request, output)
        for changes in ({'process_run_id': 'other-run'}, {'process_scope': 'stage'}, {'process_id': 0}):
            changed = deepcopy(output)
            changed['identity'].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_identity(self.store, request, changed)

    def test_stage_receipts_cannot_mix_candidate_processes(self):
        job = self.continuous_job()
        request = execution.make_candidate_request(self.store, job)
        remote = RemoteFixture()
        remote.runtime_id = self.environment['runtime_session_id']
        backend = ColabBackend(self.store, remote)
        outputs = self.outputs(job)
        stages = []
        for stage in ('generate', 'compile', 'correctness'):
            output = deepcopy(outputs[stage])
            if stage != 'generate':
                output['identity'].update(process_scope='candidate_run', process_run_id=job['run_id'],
                                          process_id=123 if stage == 'compile' else 456)
            stage_request = make_request(self.store, job, stage)
            raw = {key: stage_request[key] for key in ('schema_version', 'backend_id', 'operation_id',
                   'request_digest', 'stage', 'candidate_identity', 'parent_identity', 'provenance')}
            raw.update({key: job[key] for key in ('contract_digest', 'run_id', 'candidate_id', 'parent_id', 'context_id')})
            raw.update(status='succeeded', outputs=output, cost=job['variant']['cost'] if stage == 'generate'
                       else {'executions': 0, 'tokens': 0, 'time_s': 0})
            raw['artifact_refs'] = output.get('raw_artifact_refs', [])
            artifacts = [{'ref': ref, 'path': '/unused/' + ref['digest']} for ref in raw['artifact_refs']]
            stages.append({'status': 'completed', 'receipt': raw, 'artifacts': artifacts})
        with self.assertRaisesRegex(ValueError, 'process'):
            backend._candidate_state(request, {'status': 'running', 'stages': stages, 'cancelled': False})

    def test_evaluator_rejects_cross_process_stages(self):
        from rsi_kernel_agent.workflow.evaluation import integrate_result
        job = self.continuous_job()
        self.store.transition(RunEvent(job['run_id'] + ':start', job['run_id'], 'running', 'active', 'Local fixture'),
                              None, 'eval-start')
        outputs = self.outputs(job)
        for stage, output in outputs.items():
            if stage != 'generate':
                output['identity'] = {**output['identity'], 'process_scope': 'candidate_run',
                    'process_run_id': job['run_id'], 'process_id': 123 if stage != 'profile' else 456}
        receipts = {stage: self.receipt(job, stage, output) for stage, output in outputs.items()}
        with self.assertRaisesRegex(ValueError, 'process'):
            integrate_result(self.store, job, receipts)
        self.assertIsNone(self.store.best('task', job['context_id']))

    def test_candidate_submission_requires_new_protocol_and_active_run(self):
        with self.assertRaises(ValueError):
            execution.make_candidate_request(self.store, self.job())
        job = self.continuous_job()
        request = execution.make_candidate_request(self.store, job)
        remote = RemoteFixture()
        remote.runtime_id = self.environment['runtime_session_id']
        backend = ColabBackend(self.store, remote)
        with self.assertRaises(ValueError):
            backend.submit_candidate(request)
        self.assertEqual(remote.calls, 0)

    def test_candidate_lost_response_reuses_intents_without_stage_fallback(self):
        job = self.continuous_job()
        request = execution.make_candidate_request(self.store, job)
        self.store.transition(RunEvent('continuous-start', job['run_id'], 'running', 'active', 'Local fixture'),
                              None, 'continuous-start')
        fixture = self
        class Remote(RemoteFixture):
            def lookup_candidate(self, operation_id):
                return deepcopy(getattr(self, 'candidate_state',
                    {'status': 'definitely_not_started', 'stages': [], 'cancelled': False}))
            def submit_candidate(self, candidate_request, payload):
                from rsi_kernel_agent.workflow.colab_bridge import BridgeUnavailable
                self.calls += 1
                records = fixture.store.history('task')
                intents = [r for r in records if isinstance(r, Decision)
                           and r.details.get('type') == 'colab_execution_request.v1']
                fixture.assertEqual(len(intents), 5)
                self.candidate_state = {'status': 'running', 'stages': [], 'cancelled': False}
                raise BridgeUnavailable('Acknowledgement lost')
        remote = Remote()
        remote.runtime_id = self.environment['runtime_session_id']
        backend = ColabBackend(self.store, remote)
        self.assertEqual(backend.submit_candidate(request)['status'], 'unknown')
        self.assertEqual(ColabBackend(self.store, remote).submit_candidate(request)['status'], 'running')
        self.assertEqual(remote.calls, 1)
        with self.assertRaises(ValueError):
            backend.submit(request['requests'][0])

    def controller(self, backend, *, budget=1):
        job = self.continuous_job()
        for key in ('candidate_id', 'run_id'):
            job.pop(key)
        intake = {'id': 'continuous-intake', 'task_id': 'task', 'items': [{'status': 'accepted', 'jobs': [job]}]}
        self.store.append(Decision(intake['id'], 'task', 'note', 'task', 'Local protocol fixture',
            {'type': 'workflow_intake.v1', 'request_digest': 'd' * 64, 'result': intake}), intake['id'])
        controller = Controller(self.store, 'task', backend, limits={'executions': budget, 'max_concurrency': 1})
        controller.accept_intake(intake)
        return controller

    def test_controller_reserves_before_candidate_submission_and_cancellation_waits(self):
        fixture = self
        class Backend:
            calls = 0
            cancellations = 0
            state = 'definitely_not_started'
            def lookup_candidate(self, op):
                return {'status': self.state, 'receipts': {}, 'cancelled': self.state == 'completed'}
            def submit_candidate(self, request):
                self.calls += 1
                job = request['requests'][0]['job']
                state = fixture.controller_instance.journal.load()
                fixture.assertTrue(state['jobs'][job['id']]['reserved'])
                fixture.assertEqual(state['spent']['executions'], 1)
                fixture.assertEqual(state['jobs'][job['id']]['candidate_operation'], request)
                self.state = 'running'
                return self.lookup_candidate(request['operation_id'])
            def cancel_candidate(self, op):
                self.cancellations += 1
                return self.lookup_candidate(op)
            def lookup(self, op):
                raise AssertionError('Continuous Run cannot fall back to a stage operation')
        backend = Backend()
        c = self.controller(backend)
        self.controller_instance = c
        state = c.step()
        self.assertEqual(backend.calls, 1)
        self.assertEqual(next(iter(state['jobs'].values()))['state'], 'active')
        unchanged = c.step()
        self.assertEqual(unchanged['revision'], state['revision'])
        self.assertEqual(backend.calls, 1)
        c.cancel('User cancelled')
        state = c.step()
        self.assertEqual(backend.cancellations, 1)
        self.assertEqual(next(iter(state['jobs'].values()))['state'], 'active')
        backend.state = 'completed'
        c.step()
        result = c.run_until_idle()
        self.assertEqual(next(iter(result['jobs'].values()))['result']['classification'], 'cancelled')
        self.assertEqual(result['spent']['executions'], 1)
        self.assertEqual(backend.calls, 1)

    def test_continuous_attempt_cannot_inherit_old_requests_via_repair_or_retry(self):
        fixture = self
        class Backend:
            result = {'status': 'definitely_not_started', 'receipts': {}, 'cancelled': False}
            def lookup_candidate(self, op):
                return self.result
            def submit_candidate(self, request):
                job = request['requests'][0]['job']
                outputs = fixture.outputs(job)
                receipts = {'generate': fixture.receipt(job, 'generate', outputs['generate']),
                    'compile': fixture.receipt(job, 'compile', {'error_type': 'CompileError', 'message': 'Fixture failure'}, 'failed')}
                self.result = {'status': 'completed', 'receipts': receipts, 'cancelled': False}
                return self.result
        c = self.controller(Backend(), budget=2)
        result = c.run_until_idle()
        failed = next(iter(result['jobs'].values()))
        response = c.repair_packet(failed['id'])
        variant = deepcopy(failed['variant'])
        variant['source_ref'] = self.put(b'def kernel(x): return x + 0\n')
        import json
        from rsi_kernel_agent.contracts import ArtifactRef
        bundle = json.loads(self.store.artifacts.read(ArtifactRef(**variant['source_bundle_ref'])))
        bundle['files'][0]['artifact_ref'] = variant['source_ref']
        variant['source_bundle_ref'] = self.put(bundle)
        build = json.loads(self.store.artifacts.read(ArtifactRef(**variant['build_ref'])))
        build['source_bundle_digest'] = variant['source_bundle_ref']['digest']
        variant['build_ref'] = self.put(build)
        response.update(variant=variant, reason='Reviewed change belongs to next design round')
        before = c.status()
        with self.assertRaisesRegex(ValueError, 'design round'):
            c.submit_repair(failed['id'], response, 'continuous-repair')
        state = c.journal.load()
        state['jobs'][failed['id']]['result']['classification'] = 'execution_failed'
        c.journal.save(state, 'Local fixture retry boundary')
        with self.assertRaisesRegex(ValueError, 'design round'):
            c.retry(failed['id'], 'continuous-retry')
        self.assertEqual(c.status()['spent'], before['spent'])
        self.assertEqual(len(c.status()['jobs']), 1)

    def test_continuous_backend_integrates_all_stage_receipts_through_fixed_evaluator(self):
        fixture = self
        class Remote(RemoteFixture):
            def lookup_candidate(self, op):
                return deepcopy(getattr(self, 'candidate_state',
                    {'status': 'definitely_not_started', 'stages': [], 'cancelled': False}))
            def submit_candidate(self, request, payload):
                self.calls += 1
                job = request['requests'][0]['job']
                outputs = fixture.outputs(job)
                stages = []
                for stage_request in request['requests']:
                    stage = stage_request['stage']
                    output = deepcopy(outputs[stage])
                    if stage != 'generate':
                        output['identity'].update(process_scope='candidate_run', process_run_id=job['run_id'], process_id=123)
                    body = {key: stage_request[key] for key in ('schema_version', 'backend_id', 'operation_id',
                        'request_digest', 'stage', 'candidate_identity', 'parent_identity', 'provenance')}
                    body.update({key: job[key] for key in ('contract_digest', 'run_id', 'candidate_id', 'parent_id', 'context_id')})
                    refs = [*output.get('raw_artifact_refs', []), *output.get('raw_xplane_refs', [])]
                    if output.get('report_ref'):
                        refs.append(output['report_ref'])
                    body.update(status='succeeded', outputs=output, artifact_refs=refs,
                        cost=job['variant']['cost'] if stage == 'generate' else {'executions': 0, 'tokens': 0, 'time_s': 0})
                    stages.append({'status': 'completed', 'receipt': body,
                        'artifacts': [{'ref': ref, 'path': '/unused/' + ref['digest']} for ref in refs]})
                self.candidate_state = {'status': 'completed', 'stages': stages, 'cancelled': False}
                return self.candidate_state
        remote = Remote()
        remote.runtime_id = self.environment['runtime_session_id']
        backend = ColabBackend(self.store, remote)
        c = self.controller(backend)
        result = c.run_until_idle()
        job = next(iter(result['jobs'].values()))
        self.assertEqual(job['state'], 'completed')
        self.assertTrue(job['result']['confirmed'])
        self.assertEqual(job['result']['latency_ms'], 3.0)
        self.assertEqual(set(job['result']['receipt_refs']), set(execution.stages_for('real')))
        self.assertEqual(result['spent']['executions'], 1)
        self.assertEqual(remote.calls, 1)
        remote.runtime_id, remote.nonce = 'lost-runtime', 'changed'
        self.assertEqual(backend.lookup_candidate(job['candidate_operation']['operation_id'])['status'], 'completed')
        self.assertEqual(remote.calls, 1)

    def test_local_candidate_completion_remains_readable_after_runtime_changes(self):
        from dataclasses import asdict
        from rsi_kernel_agent.contracts import canonical_json
        job = self.continuous_job()
        request = execution.make_candidate_request(self.store, job)
        remote = RemoteFixture()
        remote.runtime_id = self.environment['runtime_session_id']
        backend = ColabBackend(self.store, remote)
        rid = stable_id('candidate-request', request['operation_id'])
        self.store.append(Decision(rid, 'task', 'note', job['run_id'], 'Local completed candidate fixture',
            {'type': 'colab_candidate_request.v1', 'request': request, 'runtime_id': remote.runtime_id,
             'nonce': remote.nonce, 'remote_root': remote.root}), rid)
        state = {'status': 'completed', 'stages': [], 'cancelled': True}
        ref = asdict(self.store.artifacts.put(canonical_json(state).encode()))
        complete = stable_id('candidate-complete', request['operation_id'])
        self.store.append(Decision(complete, 'task', 'note', job['run_id'], 'Verified local terminal fixture',
            {'type': 'candidate_completion.v1', 'request_digest': request['request_digest'], 'state_ref': ref}), complete)
        remote.runtime_id = 'new-runtime'
        remote.nonce = 'new-nonce'
        self.assertEqual(backend.lookup_candidate(request['operation_id']),
                         {'status': 'completed', 'receipts': {}, 'cancelled': True})
        self.assertEqual(remote.calls, 0)

    def test_unknown_candidate_never_resubmits_when_remote_ledger_disappears(self):
        class Backend:
            state = 'definitely_not_started'
            calls = 0
            def lookup_candidate(self, op):
                return {'status': self.state, 'receipts': {}, 'cancelled': False}
            def submit_candidate(self, request):
                self.calls += 1
                self.state = 'unknown'
                return self.lookup_candidate(request['operation_id'])
        backend = Backend()
        c = self.controller(backend)
        result = c.step()
        self.assertEqual(next(iter(result['jobs'].values()))['state'], 'blocked')
        backend.state = 'definitely_not_started'
        result = c.step()
        self.assertEqual(backend.calls, 1)
        self.assertEqual(next(iter(result['jobs'].values()))['state'], 'blocked')
        self.assertEqual(result['spent']['executions'], 1)

    def test_run_until_idle_keeps_collecting_an_unchanged_running_candidate(self):
        class Backend:
            submitted = False
            polls = 0
            def lookup_candidate(self, op):
                if self.submitted:
                    self.polls += 1
                return {'status': 'running' if self.submitted else 'definitely_not_started',
                        'receipts': {}, 'cancelled': False}
            def submit_candidate(self, request):
                self.submitted = True
                return {'status': 'running', 'receipts': {}, 'cancelled': False}
        backend = Backend()
        c = self.controller(backend)
        state = c.run_until_idle(max_steps=5)
        self.assertEqual(backend.polls, 4)
        self.assertEqual(next(iter(state['jobs'].values()))['state'], 'active')

    def test_cancel_after_unavailable_preflight_without_submission_releases_slot(self):
        class Backend:
            state = 'unknown'
            def lookup_candidate(self, op):
                return {'status': self.state, 'receipts': {}, 'cancelled': False}
            def submit_candidate(self, request):
                raise AssertionError('No submission while cancelling')
        backend = Backend()
        c = self.controller(backend)
        result = c.step()
        self.assertEqual(next(iter(result['jobs'].values()))['state'], 'blocked')
        c.cancel('Cancel an attempt never submitted')
        backend.state = 'definitely_not_started'
        c.step()
        result = c.run_until_idle()
        self.assertEqual(next(iter(result['jobs'].values()))['result']['classification'], 'cancelled')
        self.assertEqual(result['spent']['executions'], 1)

    def test_zero_budget_never_calls_candidate_backend(self):
        class Backend:
            def lookup_candidate(self, op):
                raise AssertionError('No execution allowed')
        result = self.controller(Backend(), budget=0).run_until_idle()
        self.assertEqual(result['spent']['executions'], 0)
        self.assertEqual(next(iter(result['jobs'].values()))['state'], 'deferred')


if __name__ == '__main__':
    unittest.main()
