import copy
from dataclasses import asdict, replace
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from phase2c_fixtures import make_analysis_fixture
from test_analyzer import proposal
from rsi_kernel_agent.contracts import Decision, EvaluationContext, Strategy, canonical_json
from rsi_kernel_agent.profiler.analyzer import Analyzer
from rsi_kernel_agent.profiler.inputs import AnalysisRequest, capture_inputs, load_manifest
from rsi_kernel_agent.profiler.outputs import publish_result
from rsi_kernel_agent.workflow.design import begin_design_round, route_manifest, complete_design_round


class OfflinePublicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_publication_rejects_offline_input_without_writing_records(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = make_analysis_fixture(root)
            request = copy.deepcopy(fixture['request'])
            request.update(schema_version=2, strategies=request['strategies'][:1])
            request['environment'].update(analysis_phase='offline_preparation',
                preparation_id='preparation:test', design_batch_id='preparation:test', design_round_id='draft-test')
            manifest = await capture_inputs(fixture['store'], fixture['knowledge'], AnalysisRequest.from_dict(request))
            answer = route_answer('A')
            result = Analyzer(manifest).advance({'final': answer})
            before = fixture['store'].history('task')
            with self.assertRaisesRegex(ValueError, 'Offline|offline'):
                publish_result(fixture['store'], manifest, result, 'must-not-publish')
            self.assertEqual(fixture['store'].history('task'), before)


def route_answer(sid, *, no_plan=False):
    answer = proposal()
    for field in ('code_understanding', 'observations', 'hypotheses', 'plans'):
        answer[field] = [row for row in answer[field] if row['strategy_id'] == sid]
    if not answer['hypotheses']:
        answer['hypotheses'] = [{'id': 'hyp-' + sid, 'strategy_id': sid, 'text': 'Independent tile hypothesis',
                                'refs': ['source:' + sid], 'alternatives': ['Keep parent tile']}]
    if no_plan:
        answer['plans'] = []
        answer['hypotheses'] = []
        answer['outcome'] = {'status': 'needs_research', 'reason': 'Need exact target capability evidence',
                             'refs': ['source:' + sid]}
    return answer


class OfflinePreparationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNotNone(importlib.util.find_spec('rsi_kernel_agent.workflow.offline_preparation'),
                             'Offline preparation workflow is missing')
        from rsi_kernel_agent.workflow import offline_preparation
        self.api = offline_preparation
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.f = make_analysis_fixture(self.root)
        self.store = self.f['store']
        self.assessments = {sid: {'status': 'hypothesis', 'reason': 'Research before allocating hardware',
                                  'hypothesis': 'Independent tile shape', 'refs': []} for sid in ('A', 'B')}
        self.parents = {'A': 'A-parent', 'B': 'B-parent'}

    async def begin(self, key='prep-1', **updates):
        return await self.api.begin_preparation(self.store, self.f['knowledge'], 'task', 'context', key,
                                               self.assessments, parents=self.parents, **updates)

    def result(self, prep, sid, *, no_plan=True):
        manifest = self.api.preparation_manifest(self.store, prep.id, sid)
        analyzer = Analyzer(manifest, artifacts=self.store.artifacts, max_tool_calls=500)
        offset = 0
        while True:
            analyzer.advance({'tool_calls': [{'id': 'page-' + str(offset), 'name': 'context_page',
                'arguments': {'offset': offset, 'limit': 24000}}]})
            offset = analyzer.last_results[0]['result']['next_offset']
            if offset is None:
                break
        return manifest, analyzer.advance({'final': route_answer(sid, no_plan=no_plan)})

    async def test_preparation_has_no_executable_records_or_runtime_calls_and_replays(self):
        before = self.store.history('task')
        prep = await self.begin()
        self.assertEqual((await self.begin()).id, prep.id)
        self.assertEqual(prep.details['type'], 'offline_preparation.v1')
        self.assertEqual(set(prep.details['routes']), {'A', 'B'})
        first = self.api.preparation_manifest(self.store, prep.id, 'A')
        self.assertEqual(first.to_dict()['request']['environment']['analysis_phase'], 'offline_preparation')
        self.assertEqual(set(first.to_dict()['strategies']), {'A'})
        self.assertEqual(load_manifest(self.store, first.artifact_ref).artifact_ref, first.artifact_ref)
        added = self.store.history('task')[len(before):]
        self.assertTrue(all(type(row) is Decision for row in added))
        self.assertFalse(any(row.details.get('type') in ('design_round.v1', 'design_complete.v1', 'workflow_state.v1')
                             for row in added))
        with self.assertRaisesRegex(ValueError, 'previous|order'):
            self.api.preparation_manifest(self.store, prep.id, 'B')
        with self.assertRaises(ValueError):
            route_manifest(self.store, prep.id, 'A')
        with self.assertRaises(ValueError):
            complete_design_round(self.store, prep.id)
        self.parents['A'] = 'B-parent'
        with self.assertRaises(ValueError):
            await self.begin()

    async def test_no_plan_routes_close_without_creating_plans_and_keep_frozen_snapshot(self):
        prep = await self.begin()
        snapshots = []
        before = self.store.history('task')
        with self.assertRaises(ValueError):
            self.api.complete_preparation(self.store, prep.id)
        for sid in ('A', 'B'):
            manifest, result = self.result(prep, sid)
            snapshots.append(manifest.to_dict()['request']['strategies'][0]['snapshot_id'])
            closed = self.api.seal_preparation_route(self.store, prep.id, sid, result, {}, None)
            self.assertEqual(closed.details['outcome']['status'], 'needs_research')
            self.assertEqual(self.api.seal_preparation_route(self.store, prep.id, sid, result, {}, None), closed)
        complete = self.api.complete_preparation(self.store, prep.id)
        self.assertEqual(complete, self.api.complete_preparation(self.store, prep.id))
        self.assertEqual(snapshots[0], snapshots[1])
        self.assertTrue(all(type(row) is Decision for row in self.store.history('task')[len(before):]))
        self.assertEqual(complete.details['executable'], False)

    async def complete_empty(self):
        prep = await self.begin()
        for sid in ('A', 'B'):
            _, result = self.result(prep, sid)
            self.api.seal_preparation_route(self.store, prep.id, sid, result, {}, None)
        self.api.complete_preparation(self.store, prep.id)
        return prep

    async def test_formal_input_contains_only_own_draft_and_requires_explicit_review(self):
        prep = await self.complete_empty()
        original = self.store.get('context')
        context = replace(original, id='next-context', environment={**original.environment, 'runtime_session_id': 'next-runtime'})
        self.store.append(context, context.id)
        batch = await begin_design_round(self.store, self.f['knowledge'], 'task', context.id, 'next-round',
            self.assessments, parents=self.parents, preparation_id=prep.id)
        manifest = route_manifest(self.store, batch.id, 'A')
        data = manifest.to_dict()
        self.assertIn('draft:A', data['references'])
        self.assertNotIn('draft:B', data['references'])
        draft = data['references']['draft:A']['value']
        self.assertEqual(draft['analysis']['code_understanding'][0]['strategy_id'], 'A')
        self.assertEqual(draft['outcome']['status'], 'needs_research')
        self.assertEqual(draft['changes']['context']['before'], 'context')
        self.assertEqual(draft['changes']['context']['after'], 'next-context')
        self.assertEqual(load_manifest(self.store, manifest.artifact_ref).artifact_ref, manifest.artifact_ref)
        answer = route_answer('A', no_plan=True)
        with self.assertRaisesRegex(ValueError, 'draft|schema'):
            Analyzer(manifest).advance({'final': answer})
        answer['draft_review'] = {'preparation_id': prep.id, 'route_result_digest': draft['result_digest'],
                                  'plan_decisions': []}
        result = Analyzer(manifest).advance({'final': answer})
        before = self.store.history('task')
        with self.assertRaisesRegex(ValueError, 'coverage|read|page'):
            publish_result(self.store, manifest, result, 'unread-new-conditions')
        self.assertEqual(before, self.store.history('task'))

    async def test_binding_rejects_incomplete_preparation_parent_and_protocol_drift(self):
        prep = await self.begin()
        with self.assertRaises(ValueError):
            await begin_design_round(self.store, self.f['knowledge'], 'task', 'context', 'incomplete',
                self.assessments, parents=self.parents, preparation_id=prep.id)
        for sid in ('A', 'B'):
            _, result = self.result(prep, sid)
            self.api.seal_preparation_route(self.store, prep.id, sid, result, {}, None)
        self.api.complete_preparation(self.store, prep.id)
        with self.assertRaisesRegex(ValueError, 'parent|Parent'):
            await begin_design_round(self.store, self.f['knowledge'], 'task', 'context', 'bad-parent',
                self.assessments, parents={**self.parents, 'A': 'B-parent'}, preparation_id=prep.id)
        old = self.store.get('context')
        changed = replace(old, id='changed-protocol', protocol={**old.protocol, 'new_policy': True})
        self.store.append(changed, changed.id)
        with self.assertRaisesRegex(ValueError, 'protocol|Protocol'):
            await begin_design_round(self.store, self.f['knowledge'], 'task', changed.id, 'bad-protocol',
                self.assessments, parents=self.parents, preparation_id=prep.id)

    async def test_relabeling_preparation_phase_cannot_publish_offline_batch(self):
        prep = await self.begin()
        manifest = self.api.preparation_manifest(self.store, prep.id, 'A')
        request = manifest.to_dict()['request']
        request['environment'].pop('analysis_phase')
        changed = await capture_inputs(self.store, self.f['knowledge'], AnalysisRequest.from_dict(request))
        result = Analyzer(changed).advance({'final': route_answer('A')})
        before = self.store.history('task')
        with self.assertRaisesRegex(ValueError, 'Offline|offline|preparation'):
            publish_result(self.store, changed, result, 'phase-relabel')
        self.assertEqual(before, self.store.history('task'))

    async def test_missing_page_delivery_cannot_seal_route(self):
        prep = await self.begin()
        manifest = self.api.preparation_manifest(self.store, prep.id, 'A')
        result = Analyzer(manifest).advance({'final': route_answer('A', no_plan=True)})
        before = self.store.history('task')
        with self.assertRaisesRegex(ValueError, 'coverage|read|page'):
            self.api.seal_preparation_route(self.store, prep.id, 'A', result, {}, None)
        self.assertEqual(self.store.history('task'), before)


class OfflineCandidateBindingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import test_offline_checks
        from rsi_kernel_agent.workflow import offline_preparation
        self.api = offline_preparation
        self.fixture = test_offline_checks.OfflineCheckTests()
        await self.fixture.asyncSetUp()
        self.addCleanup(self.fixture.temp.cleanup)
        from test_cpu_candidates import configure_cpu_fixture, cpu_fixture_execution
        self.cpu_context = await configure_cpu_fixture(self.fixture)
        self.enterContext(cpu_fixture_execution())
        self.store = self.fixture.store
        from rsi_kernel_agent.contracts import Candidate, ParentCandidate, Plan
        parent_identity = self.store.get('A-parent').identity
        for record in (Plan('parent-plan', 'A', 'CPU fixture parent'),
                       Candidate('parent-candidate', 'parent-plan', 'A-parent', parent_identity),
                       ParentCandidate('measured-parent', 'task', parent_identity, 'parent-candidate')):
            self.store.append(record, record.id)
        self.fixture.answer['plans'][0]['parent_id'] = 'measured-parent'
        self.assessments = {'A': {'status': 'hypothesis', 'reason': 'Prepare local candidate',
                                  'hypothesis': 'Independent tile', 'refs': []}}
        self.prep = await self.api.begin_preparation(self.store, None, 'task', self.cpu_context, 'offline',
            self.assessments, parents={'A': 'measured-parent'})
        self.manifest = self.api.preparation_manifest(self.store, self.prep.id, 'A')
        self.result = self.read_and_answer(self.manifest, self.fixture.answer)
        from rsi_kernel_agent.workflow.offline_checks import check_drafts
        self.checks = check_drafts(self.store, self.manifest, self.result, self.fixture.drafts,
                                  cpu_python=sys.executable, output_dir=self.fixture.root / 'checks')
        self.closed = self.api.seal_preparation_route(self.store, self.prep.id, 'A', self.result,
                                                      self.fixture.drafts, self.checks)
        self.api.complete_preparation(self.store, self.prep.id)
        context = replace(self.store.get(self.cpu_context), id='formal-context', environment={
            **self.store.get(self.cpu_context).environment, 'runtime_session_id': 'new-runtime'})
        self.store.append(context, context.id)
        self.measure_parent(context.id)
        self.batch = await begin_design_round(self.store, None, 'task', context.id, 'formal', self.assessments,
            parents={'A': 'measured-parent'}, preparation_id=self.prep.id)

    def measure_parent(self, context_id):
        from rsi_kernel_agent.contracts import Run, RunEvent, Evidence, EvaluationBundle, ArtifactRef
        rid = 'parent-run:' + context_id
        self.store.append(Run(rid, 'parent-candidate', context_id), rid)
        event = RunEvent(rid + ':start', rid, 'running', 'active', 'CPU protocol fixture')
        self.store.transition(event, None, event.id)
        self.store.transition(RunEvent(rid + ':done', rid, 'succeeded', 'finished', 'CPU protocol fixture'),
                              event.id, rid + ':done')
        raw = self.store.artifacts.put(b'CPU protocol fixture timing, not TPU evidence')
        self.store.append(Evidence(rid + ':timing', rid, 'measured_timing', 'available', 'completed',
            'supported', 1.0, 'ms', (raw,), source={'provenance': 'real'}), rid + ':timing')
        self.store.append(EvaluationBundle(rid + ':evaluation', rid, 'passed', 'valid', 1.0, True,
            (rid + ':timing',)), rid + ':evaluation')

    def read_and_answer(self, manifest, answer):
        analyzer = Analyzer(manifest, artifacts=self.store.artifacts, max_turns=100, max_tool_calls=100)
        offset = 0
        while True:
            analyzer.advance({'tool_calls': [{'id': 'read-' + str(offset), 'name': 'context_page',
                'arguments': {'offset': offset, 'limit': 24000}}]})
            offset = analyzer.last_results[0]['result']['next_offset']
            if offset is None:
                break
        return analyzer.advance({'final': answer})

    def final_answer(self):
        answer = copy.deepcopy(self.fixture.answer)
        answer['draft_review'] = {'preparation_id': self.prep.id,
            'route_result_digest': self.closed.details['result_digest'],
            'plan_decisions': [{'draft_plan_id': 'A-P1', 'status': 'retained', 'final_plan_ids': ['A-P1'],
                                'reason': 'Same parent and contract; final TPU correctness is still required'}]}
        return answer

    async def test_all_variants_bind_only_after_fresh_review_and_complete_design(self):
        self.assertTrue(hasattr(self.api, 'bind_prepared_variants'), 'Prepared variant binding is missing')
        with self.assertRaises(ValueError):
            self.api.bind_prepared_variants(self.store, self.batch.id)
        manifest = route_manifest(self.store, self.batch.id, 'A')
        packet = Analyzer(manifest).packet()
        self.assertIn('draft_review', packet['output_schema'])
        answer = self.final_answer()
        result = self.read_and_answer(manifest, answer)
        marker = publish_result(self.store, manifest, result, 'fresh-confirmation')
        from rsi_kernel_agent.workflow.design import finish_route_design
        finish_route_design(self.store, self.batch.id, 'A', marker.id)
        complete_design_round(self.store, self.batch.id)
        variants = self.api.bind_prepared_variants(self.store, self.batch.id)
        self.assertEqual(variants, {marker.details['plans'][0]['record_id']: [{**self.fixture.variant, 'device': 'new-runtime'}]})
        self.assertEqual(self.api.bind_prepared_variants(self.store, self.batch.id), variants)
        from rsi_kernel_agent.workflow.intake import intake_publication
        intake = await intake_publication(self.store, marker.id, 'formal', variants)
        self.assertEqual([row['status'] for row in intake['items']], ['accepted'])
        self.assertEqual(intake['items'][0]['jobs'][0]['context_id'], 'formal-context')
        self.assertFalse(any(type(row) is Decision and row.details.get('type') == 'workflow_state.v1'
                             for row in self.store.history('task')))

    async def test_binary_dependency_stays_hashed_without_text_decode(self):
        from rsi_kernel_agent.contracts import ArtifactRef
        from rsi_kernel_agent.workflow.offline_checks import check_drafts
        prep = await self.api.begin_preparation(self.store, None, 'task', self.cpu_context, 'binary',
            self.assessments, parents={'A': 'measured-parent'})
        manifest = self.api.preparation_manifest(self.store, prep.id, 'A')
        result = self.read_and_answer(manifest, self.fixture.answer)
        drafts = copy.deepcopy(self.fixture.drafts)
        variant = drafts['A-P1'][0]
        binary = self.fixture.put(b'\xff\xfe\x00\x80')
        bundle = json.loads(self.store.artifacts.read(ArtifactRef(**variant['source_bundle_ref'])))
        bundle['files'].append({'path': 'coefficients.bin', 'role': 'dependency', 'artifact_ref': binary})
        variant['source_bundle_ref'] = self.fixture.put(bundle)
        build = json.loads(self.store.artifacts.read(ArtifactRef(**variant['build_ref'])))
        build['source_bundle_digest'] = variant['source_bundle_ref']['digest']
        variant['build_ref'] = self.fixture.put(build)
        checks = check_drafts(self.store, manifest, result, drafts, cpu_python=sys.executable, output_dir=self.fixture.root / 'binary-checks')
        self.assertTrue(checks['ready'])
        self.api.seal_preparation_route(self.store, prep.id, 'A', result, drafts, checks)
        self.api.complete_preparation(self.store, prep.id)
        batch = await begin_design_round(self.store, None, 'task', 'formal-context', 'binary-formal', self.assessments,
            parents={'A': 'measured-parent'}, preparation_id=prep.id)
        self.assertEqual(batch.details['routes']['A']['status'], 'pending')
        data = route_manifest(self.store, batch.id, 'A').to_dict()
        sources = data['references']['draft:A']['value']['sources']
        file = next(f for f in sources[variant['source_bundle_ref']['digest']]['files'] if f['path'] == 'coefficients.bin')
        self.assertEqual(file['artifact_ref'], binary)
        self.assertIsNone(file['content'])
        self.assertEqual(file['encoding'], 'binary')

    async def test_prepared_repair_cannot_reuse_failed_parent_cpu_checks(self):
        from rsi_kernel_agent.workflow.design import finish_route_design, ready_design_jobs
        from rsi_kernel_agent.workflow.intake import intake_publication
        from rsi_kernel_agent.workflow.controller import Controller
        from rsi_kernel_agent.workflow.colab import ColabBackend
        from rsi_kernel_agent.contracts import ArtifactRef
        from difflib import unified_diff
        manifest = route_manifest(self.store, self.batch.id, 'A')
        result = self.read_and_answer(manifest, self.final_answer())
        marker = publish_result(self.store, manifest, result, 'repairable-preparation')
        finish_route_design(self.store, self.batch.id, 'A', marker.id)
        complete_design_round(self.store, self.batch.id)
        variants = self.api.bind_prepared_variants(self.store, self.batch.id)
        intake = await intake_publication(self.store, marker.id, 'formal', variants)
        store = self.store
        class Remote:
            runtime_id, nonce, root = 'new-runtime', 'offline-fixture', '/fixture'
            def __init__(self):
                self.operations = {}
            def lookup(self, operation_id):
                return self.operations.get(operation_id, {'status': 'definitely_not_started', 'receipt': None})
            def submit(self, request, payload):
                job, stage = request['job'], request['stage']
                variant = job['variant']
                output = ({'workspace': '/fixture/candidate', 'source_digest': variant['source_ref']['digest'],
                    'config_digest': variant['config_ref']['digest'], 'build_digest': variant['build_ref']['digest'],
                    'source_bundle_digest': variant['source_bundle_ref']['digest']} if stage == 'generate'
                    else {'error_type': 'FixtureCompileError', 'message': 'Deliberate local protocol failure'})
                body = {k: request[k] for k in ('schema_version', 'backend_id', 'operation_id', 'request_digest',
                    'stage', 'candidate_identity', 'parent_identity', 'provenance')}
                body.update({k: job[k] for k in ('contract_digest', 'run_id', 'candidate_id', 'parent_id', 'context_id')})
                body.update(status='succeeded' if stage == 'generate' else 'failed', outputs=output, artifact_refs=[],
                    cost=variant['cost'] if stage == 'generate' else {'executions': 0, 'tokens': 0, 'time_s': 0})
                response = {'status': 'completed', 'receipt': body, 'artifacts': []}
                self.operations[request['operation_id']] = response
                return response
        controller = Controller(store, 'task', ColabBackend(store, Remote()),
                                limits={'executions': 2, 'max_concurrency': 1, 'max_repairs': 1})
        controller.accept_intake(intake)
        state = controller.run_until_idle()
        failed = next(j for j in state['jobs'].values() if j['state'] == 'failed')
        response = controller.repair_packet(failed['id'])
        variant = copy.deepcopy(failed['variant'])
        before = store.artifacts.read(ArtifactRef(**variant['source_ref'])).decode()
        after = before.replace('return args[0], args[2]', 'return (args[0]), args[2]')
        variant['source_ref'] = self.fixture.put(after.encode())
        bundle = json.loads(store.artifacts.read(ArtifactRef(**variant['source_bundle_ref'])))
        next(row for row in bundle['files'] if row['path'] == bundle['entry']['path'])['artifact_ref'] = variant['source_ref']
        variant['source_bundle_ref'] = self.fixture.put(bundle)
        build = json.loads(store.artifacts.read(ArtifactRef(**variant['build_ref'])))
        build['source_bundle_digest'] = variant['source_bundle_ref']['digest']
        variant['build_ref'] = self.fixture.put(build)
        diff = ''.join(unified_diff(before.splitlines(True), after.splitlines(True),
                                   fromfile='failed/source', tofile='repair/source')).encode()
        response.update(variant=variant, reason='Host-reviewed parenthesis-only syntax repair', repair_review={
            'classification': 'implementation_fix', 'preserves_plan_hypothesis': True,
            'plan_id': failed['plan_id'], 'failure_run_id': failed['run_id'], 'diff_ref': self.fixture.put(diff),
            'reason': 'Same source semantics and Plan hypothesis'})
        repair_id = controller.submit_repair(failed['id'], response, 'one-fix')
        with self.assertRaisesRegex(ValueError, 'new design round'):
            ready_design_jobs(store, controller.status())
        self.assertEqual(controller.status()['spent']['executions'], 1)

    async def test_later_cpu_failure_blocks_queued_candidate_before_budget_reservation(self):
        from rsi_kernel_agent.workflow.design import finish_route_design
        from rsi_kernel_agent.workflow.intake import intake_publication
        from rsi_kernel_agent.workflow.controller import Controller
        from rsi_kernel_agent.workflow import offline_checks
        import subprocess
        manifest = route_manifest(self.store, self.batch.id, 'A')
        result = self.read_and_answer(manifest, self.final_answer())
        marker = publish_result(self.store, manifest, result, 'queued-before-failure')
        finish_route_design(self.store, self.batch.id, 'A', marker.id)
        complete_design_round(self.store, self.batch.id)
        variants = self.api.bind_prepared_variants(self.store, self.batch.id)
        intake = await intake_publication(self.store, marker.id, 'formal', variants)
        controller = Controller(self.store, 'task', None, limits={'executions': 2, 'max_concurrency': 1})
        controller.accept_intake(intake)
        before = controller.status()
        with patch.object(offline_checks, '_probe', return_value=(
                {'python': 'fixture', 'executable': sys.executable, 'missing': []}, {'fixture_probe': True})), \
             patch.object(offline_checks.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, b'', b'failure')):
            failed = offline_checks.check_drafts(self.store, self.manifest, self.result, self.fixture.drafts,
                cpu_python=str(self.fixture.root / 'recheck-python.exe'), output_dir=self.fixture.root / 'queued-failure')
        self.assertFalse(failed['ready'])
        with self.assertRaisesRegex(ValueError, 'failure|failed'):
            controller.step()
        self.assertEqual(controller.status()['spent'], before['spent'])
        self.assertEqual(controller.status()['revision'], before['revision'])

    async def test_unreadable_prepared_artifact_cannot_turn_into_no_plan_formal_route(self):
        from rsi_kernel_agent.contracts import ArtifactRef
        self.store.artifacts.path_for(ArtifactRef(**self.fixture.source)).write_bytes(b'tampered source')
        with self.assertRaises(ValueError):
            await begin_design_round(self.store, None, 'task', 'formal-context', 'corrupt-draft', self.assessments,
                parents={'A': 'measured-parent'}, preparation_id=self.prep.id)

    async def test_formerly_unprepared_route_requires_its_own_measured_parent(self):
        from rsi_kernel_agent.workflow.offline_checks import check_drafts
        self.store.append(Strategy('B', 'task', 'Missing local parent'), 'new-B')
        assessments = {sid: dict(self.assessments['A']) for sid in ('A', 'B')}
        prep = await self.api.begin_preparation(self.store, None, 'task', self.cpu_context, 'unprepared-parent', assessments,
            parents={'A': 'measured-parent'})
        manifest = self.api.preparation_manifest(self.store, prep.id, 'A')
        result = self.read_and_answer(manifest, self.fixture.answer)
        checks = check_drafts(self.store, manifest, result, self.fixture.drafts, cpu_python=sys.executable, output_dir=self.fixture.root / 'unprepared-checks')
        self.api.seal_preparation_route(self.store, prep.id, 'A', result, self.fixture.drafts, checks)
        self.api.complete_preparation(self.store, prep.id)
        with self.assertRaisesRegex(ValueError, 'parent|Parent'):
            await begin_design_round(self.store, None, 'task', 'formal-context', 'unmeasured-B', assessments,
                parents={'A': 'measured-parent', 'B': 'A-parent'}, preparation_id=prep.id)

    async def test_direct_intake_rejects_wrong_device_without_budget_reservation(self):
        from rsi_kernel_agent.workflow.design import finish_route_design
        from rsi_kernel_agent.workflow.intake import intake_publication
        from rsi_kernel_agent.workflow.controller import Controller
        manifest = route_manifest(self.store, self.batch.id, 'A')
        marker = publish_result(self.store, manifest, self.read_and_answer(manifest, self.final_answer()), 'wrong-device-check')
        finish_route_design(self.store, self.batch.id, 'A', marker.id)
        complete_design_round(self.store, self.batch.id)
        controller = Controller(self.store, 'task', None, limits={'executions': 1, 'max_concurrency': 1})
        before = controller.status()['spent']
        result = await intake_publication(self.store, marker.id, 'formal',
            {'A-P1': [{**self.fixture.variant, 'device': 'historical-fixture'}]})
        self.assertEqual(result['items'][0]['status'], 'rejected')
        self.assertEqual(result['items'][0]['jobs'], [])
        self.assertEqual(controller.status()['spent'], before)

    async def test_formal_new_plan_on_unprepared_route_still_requires_local_checks(self):
        from rsi_kernel_agent.workflow.design import finish_route_design
        from rsi_kernel_agent.workflow.intake import intake_publication
        from rsi_kernel_agent.workflow.offline_checks import check_drafts
        self.store.append(Strategy('B', 'task', 'Independent future B'), 'new-B')
        assessments = {sid: dict(self.assessments['A']) for sid in ('A', 'B')}
        prep = await self.api.begin_preparation(self.store, None, 'task', self.cpu_context, 'new-plan-prep', assessments,
                                               parents={'A': 'measured-parent'})
        manifest = self.api.preparation_manifest(self.store, prep.id, 'A')
        result = self.read_and_answer(manifest, self.fixture.answer)
        checks = check_drafts(self.store, manifest, result, self.fixture.drafts, cpu_python=sys.executable, output_dir=self.fixture.root / 'new-plan-checks')
        a = self.api.seal_preparation_route(self.store, prep.id, 'A', result, self.fixture.drafts, checks)
        self.api.complete_preparation(self.store, prep.id)
        batch = await begin_design_round(self.store, None, 'task', 'formal-context', 'new-plan-formal', assessments,
            parents={'A': 'measured-parent', 'B': 'measured-parent'}, preparation_id=prep.id)
        for sid in ('A', 'B'):
            manifest = route_manifest(self.store, batch.id, sid)
            answer = self.final_answer() if sid == 'A' else route_answer('B')
            if sid == 'A':
                answer['draft_review'].update(preparation_id=prep.id, route_result_digest=a.details['result_digest'])
            else:
                answer['plans'] = answer['plans'][:1]
                answer['plans'][0].update(parent_id='measured-parent', variants=['one'])
            result = self.read_and_answer(manifest, answer)
            marker = publish_result(self.store, manifest, result, 'new-plan:' + sid)
            finish_route_design(self.store, batch.id, sid, marker.id)
        complete_design_round(self.store, batch.id)
        variants = {'B-P1': [{**self.fixture.variant, 'device': 'new-runtime'}]}
        rejected = await intake_publication(self.store, marker.id, 'new-plan-formal', variants)
        self.assertEqual(rejected['items'][0]['status'], 'rejected')
        check_drafts(self.store, manifest, result, {'B-P1': [self.fixture.variant]}, cpu_python=sys.executable, output_dir=self.fixture.root / 'new-B-checks')
        accepted = await intake_publication(self.store, marker.id, 'new-plan-formal', variants, retry_key='checked-new-B')
        self.assertEqual(accepted['items'][0]['status'], 'accepted')

    async def test_unprepared_no_plan_route_does_not_break_prepared_candidate_export(self):
        from rsi_kernel_agent.workflow.design import finish_route_design
        from rsi_kernel_agent.workflow.offline_checks import check_drafts
        self.store.append(Strategy('B', 'task', 'Needs parent research'), 'new-B')
        assessments = {sid: dict(self.assessments['A']) for sid in ('A', 'B')}
        prep = await self.api.begin_preparation(self.store, None, 'task', self.cpu_context, 'partial-prep',
            assessments, parents={'A': 'measured-parent'})
        self.assertEqual(prep.details['routes']['B']['status'], 'needs_research')
        manifest = self.api.preparation_manifest(self.store, prep.id, 'A')
        result = self.read_and_answer(manifest, self.fixture.answer)
        checks = check_drafts(self.store, manifest, result, self.fixture.drafts, cpu_python=sys.executable, output_dir=self.fixture.root / 'partial-checks')
        closed = self.api.seal_preparation_route(self.store, prep.id, 'A', result, self.fixture.drafts, checks)
        self.api.complete_preparation(self.store, prep.id)
        batch = await begin_design_round(self.store, None, 'task', 'formal-context', 'partial-formal', assessments,
            parents={'A': 'measured-parent', 'B': 'measured-parent'}, preparation_id=prep.id)
        for sid in ('A', 'B'):
            manifest = route_manifest(self.store, batch.id, sid)
            answer = self.final_answer() if sid == 'A' else route_answer('B', no_plan=True)
            if sid == 'A':
                answer['draft_review'].update(preparation_id=prep.id, route_result_digest=closed.details['result_digest'])
            marker = publish_result(self.store, manifest, self.read_and_answer(manifest, answer), 'partial-formal:' + sid)
            finish_route_design(self.store, batch.id, sid, marker.id)
        complete_design_round(self.store, batch.id)
        variants = self.api.bind_prepared_variants(self.store, batch.id)
        self.assertEqual(len(variants), 1)

    async def test_two_independent_routes_can_reuse_local_plan_id(self):
        from rsi_kernel_agent.workflow.design import finish_route_design
        from rsi_kernel_agent.workflow.offline_checks import check_drafts
        from rsi_kernel_agent.workflow.intake import intake_publication
        self.store.append(Strategy('B', 'task', 'Independent B'), 'new-B')
        assessments = {sid: dict(self.assessments['A']) for sid in ('A', 'B')}
        parents = {sid: 'measured-parent' for sid in ('A', 'B')}
        prep = await self.api.begin_preparation(self.store, None, 'task', self.cpu_context, 'two-local-ids', assessments, parents=parents)
        closed, answers = {}, {}
        for sid in ('A', 'B'):
            answer = route_answer(sid)
            answer['plans'] = answer['plans'][:1]
            answer['plans'][0].update(id='P1', parent_id='measured-parent', variants=['one'])
            answers[sid] = answer
            manifest = self.api.preparation_manifest(self.store, prep.id, sid)
            result = self.read_and_answer(manifest, answer)
            drafts = {'P1': [self.fixture.variant]}
            checks = check_drafts(self.store, manifest, result, drafts, cpu_python=sys.executable, output_dir=self.fixture.root / ('checks-' + sid))
            closed[sid] = self.api.seal_preparation_route(self.store, prep.id, sid, result, drafts, checks)
        self.api.complete_preparation(self.store, prep.id)
        batch = await begin_design_round(self.store, None, 'task', 'formal-context', 'two-local-formal',
                                         assessments, parents=parents, preparation_id=prep.id)
        publications = []
        for sid in ('A', 'B'):
            manifest = route_manifest(self.store, batch.id, sid)
            answer = answers[sid]
            answer['draft_review'] = {'preparation_id': prep.id, 'route_result_digest': closed[sid].details['result_digest'],
                'plan_decisions': [{'draft_plan_id': 'P1', 'status': 'retained', 'final_plan_ids': ['P1'],
                                    'reason': 'Same exact parent and independent candidate'}]}
            marker = publish_result(self.store, manifest, self.read_and_answer(manifest, answer), 'two-local:' + sid)
            finish_route_design(self.store, batch.id, sid, marker.id)
            publications.append(marker)
        complete_design_round(self.store, batch.id)
        variants = self.api.bind_prepared_variants(self.store, batch.id)
        self.assertEqual(set(variants), {pub.details['plans'][0]['record_id'] for pub in publications})
        self.assertEqual(len(variants), 2)
        for pub in publications:
            result = await intake_publication(self.store, pub.id, 'two-local-formal', variants)
            self.assertEqual(result['items'][0]['status'], 'accepted')

    async def test_finish_cli_replays_sealed_checks_without_running_cpu_again(self):
        from argparse import Namespace
        from rsi_kernel_agent.workflow.__main__ import dispatch
        from rsi_kernel_agent.profiler.session import _write
        directory = self.fixture.root / 'cli-replay'
        directory.mkdir()
        _write(directory / 'session.json', {'manifest': asdict(self.manifest.artifact_ref),
                                           'result_digest': self.closed.details['result_digest']})
        _write(directory / 'result.json', self.result)
        _write(directory / 'drafts.json', self.fixture.drafts)
        args = Namespace(command='preparation-finish', db=str(self.store.db_path), artifacts=str(self.store.artifacts.root),
            preparation=self.prep.id, strategy='A', session=str(directory), drafts=str(directory / 'drafts.json'),
            cpu_python=sys.executable)
        with patch('rsi_kernel_agent.workflow.offline_checks.check_drafts', side_effect=AssertionError('CPU rerun')):
            actual = await dispatch(args)
        self.assertEqual(actual['id'], self.closed.id)

    async def test_export_uses_published_record_ids_not_route_local_proposal_ids(self):
        from rsi_kernel_agent.workflow.design import finish_route_design
        manifest = route_manifest(self.store, self.batch.id, 'A')
        marker = publish_result(self.store, manifest, self.read_and_answer(manifest, self.final_answer()), 'record-key')
        finish_route_design(self.store, self.batch.id, 'A', marker.id)
        complete_design_round(self.store, self.batch.id)
        variants = self.api.bind_prepared_variants(self.store, self.batch.id)
        self.assertEqual(set(variants), {marker.details['plans'][0]['record_id']})

    async def test_historical_draft_replay_survives_later_failed_check_but_new_binding_does_not(self):
        from rsi_kernel_agent.workflow import offline_checks
        from unittest.mock import patch
        import subprocess
        formal = route_manifest(self.store, self.batch.id, 'A')
        with patch.object(offline_checks, '_probe', return_value=(
                {'python': 'fixture', 'executable': sys.executable, 'missing': []}, {'fixture_probe': True})), \
             patch.object(offline_checks.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, b'', b'failure')):
            failed = offline_checks.check_drafts(self.store, self.manifest, self.result, self.fixture.drafts,
                cpu_python=str(self.fixture.root / 'recheck-python.exe'), output_dir=self.fixture.root / 'failure')
        self.assertFalse(failed['ready'])
        self.assertEqual(load_manifest(self.store, formal.artifact_ref).artifact_ref, formal.artifact_ref)
        with self.assertRaisesRegex(ValueError, 'failure|failed'):
            await begin_design_round(self.store, None, 'task', 'formal-context', 'after-failure', self.assessments,
                parents={'A': 'measured-parent'}, preparation_id=self.prep.id)

    async def test_formal_binding_cannot_publish_without_exact_parent_measurement(self):
        context = replace(self.store.get(self.cpu_context), id='unmeasured-context',
                          environment={**self.store.get(self.cpu_context).environment, 'runtime_session_id': 'unmeasured-runtime'})
        self.store.append(context, context.id)
        with self.assertRaisesRegex(ValueError, 'parent|Parent'):
            await begin_design_round(self.store, None, 'task', context.id, 'unmeasured', self.assessments,
                parents={'A': 'measured-parent'}, preparation_id=self.prep.id)

    async def test_revised_implementation_requires_new_checks_before_intake(self):
        from rsi_kernel_agent.workflow.design import finish_route_design
        from rsi_kernel_agent.workflow.intake import intake_publication
        from rsi_kernel_agent.workflow.offline_checks import check_drafts
        manifest = route_manifest(self.store, self.batch.id, 'A')
        answer = self.final_answer()
        answer['plans'][0]['modification'] = 'Use a revised tile after reviewing target conditions'
        answer['draft_review']['plan_decisions'][0]['status'] = 'revised'
        result = self.read_and_answer(manifest, answer)
        marker = publish_result(self.store, manifest, result, 'revised-confirmation')
        finish_route_design(self.store, self.batch.id, 'A', marker.id)
        complete_design_round(self.store, self.batch.id)
        variants = {'A-P1': [{**self.fixture.variant, 'device': 'new-runtime'}]}
        rejected = await intake_publication(self.store, marker.id, 'formal', variants)
        self.assertEqual(rejected['items'][0]['status'], 'rejected')
        checks = check_drafts(self.store, manifest, result, self.fixture.drafts,
                              cpu_python=sys.executable, output_dir=self.fixture.root / 'new-checks')
        bound = self.api.bind_prepared_variants(self.store, self.batch.id,
            revisions={marker.id: {'drafts': self.fixture.drafts, 'checks': checks}})
        self.assertEqual(bound, {marker.details['plans'][0]['record_id']: variants['A-P1']})
        accepted = await intake_publication(self.store, marker.id, 'formal', variants, retry_key='checked')
        self.assertEqual(accepted['items'][0]['status'], 'accepted')
        self.assertEqual(accepted['items'][0]['jobs'][0]['variant']['source_ref'], self.fixture.source)

    async def test_direct_intake_cannot_replace_retained_draft_implementation(self):
        from rsi_kernel_agent.workflow.design import finish_route_design
        from rsi_kernel_agent.workflow.intake import intake_publication
        manifest = route_manifest(self.store, self.batch.id, 'A')
        result = self.read_and_answer(manifest, self.final_answer())
        marker = publish_result(self.store, manifest, result, 'checked-retained')
        finish_route_design(self.store, self.batch.id, 'A', marker.id)
        complete_design_round(self.store, self.batch.id)
        variant = copy.deepcopy(self.fixture.variant)
        source = self.fixture.put(b'def kernel(x): return x + 1\n')
        variant['source_ref'] = source
        from rsi_kernel_agent.contracts import ArtifactRef
        bundle = json.loads(self.store.artifacts.read(ArtifactRef(**variant['source_bundle_ref'])))
        bundle['files'][0]['artifact_ref'] = source
        variant['source_bundle_ref'] = self.fixture.put(bundle)
        build = json.loads(self.store.artifacts.read(ArtifactRef(**variant['build_ref'])))
        build['source_bundle_digest'] = variant['source_bundle_ref']['digest']
        variant['build_ref'] = self.fixture.put(build)
        variant['device'] = 'new-runtime'
        result = await intake_publication(self.store, marker.id, 'formal', {'A-P1': [variant]})
        self.assertEqual(result['items'][0]['status'], 'rejected')
        self.assertEqual(result['items'][0]['jobs'], [])

    async def test_execution_helper_requires_gates_before_binding_preparation(self):
        import importlib.util
        helper = Path(__file__).resolve().parents[1] / '.artifacts/next-stage-completion-2026-11-16/prepare_design.py'
        spec = importlib.util.spec_from_file_location('tested_prepare_design', helper)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        jobs = {name: {'name': name} for name in ('oracle-negative-attention', 'oracle-negative-cache',
                                                 'baseline-repair', 'baseline-b')}
        def read(path):
            return {'runtime_id': 'new-runtime'} if 'colab-config' in str(path) else next(
                job for name, job in jobs.items() if Path(path).name.startswith(name))
        with patch.object(sys, 'argv', ['prepare_design.py', '--generation', 'runtime99', '--preparation', self.prep.id]), \
             patch.object(module, 'read_json', side_effect=read), \
             patch.object(module, 'ExperimentStore', return_value=self.store), \
             patch.object(module, 'verified_gate_result', side_effect=ValueError('Gate is incomplete')), \
             patch.object(module, 'begin_design_round', side_effect=AssertionError('Must not bind before gate')):
            before = self.store.history('task')
            with self.assertRaisesRegex(ValueError, 'Gate is incomplete'):
                await module.main()
            self.assertEqual(self.store.history('task'), before)

    async def test_review_cannot_silently_drop_or_relabel_drafts(self):
        manifest = route_manifest(self.store, self.batch.id, 'A')
        answer = self.final_answer()
        answer['draft_review']['plan_decisions'] = []
        with self.assertRaises(ValueError):
            Analyzer(manifest).advance({'final': answer})
        answer = self.final_answer()
        answer['plans'][0]['variants'] = ['different']
        with self.assertRaisesRegex(ValueError, 'Retained'):
            Analyzer(manifest).advance({'final': answer})


if __name__ == '__main__':
    unittest.main()
