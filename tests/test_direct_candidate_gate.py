from dataclasses import replace
from pathlib import Path
import sys
import unittest

from test_cpu_candidates import configure_cpu_fixture, cpu_fixture_execution
import test_offline_checks
from rsi_kernel_agent.profiler.analyzer import Analyzer
from rsi_kernel_agent.profiler.outputs import publish_result
from rsi_kernel_agent.workflow.design import begin_design_round, route_manifest, finish_route_design, complete_design_round
from rsi_kernel_agent.workflow.intake import intake_publication
from rsi_kernel_agent.workflow.offline_checks import check_drafts


class DirectCandidateGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = test_offline_checks.OfflineCheckTests()
        await self.fixture.asyncSetUp()
        self.addCleanup(self.fixture.temp.cleanup)
        original = await configure_cpu_fixture(self.fixture)
        self.enterContext(cpu_fixture_execution())
        self.store = self.fixture.store
        context = self.store.get(original)
        self.context = replace(context, id='continuous-formal',
            protocol={**context.protocol, 'execution_mode': 'candidate.v1'})
        self.store.append(self.context, self.context.id)
        assessments = {'A': {'status': 'hypothesis', 'reason': 'New candidate', 'hypothesis': 'Preserve outputs', 'refs': []}}
        batch = await begin_design_round(self.store, None, 'task', self.context.id, 'direct-round',
                                        assessments, parents={'A': 'A-parent'})
        self.manifest = route_manifest(self.store, batch.id, 'A')
        self.result = Analyzer(self.manifest).advance({'final': self.fixture.answer})
        self.publication = publish_result(self.store, self.manifest, self.result, 'direct-publication')
        finish_route_design(self.store, batch.id, 'A', self.publication.id)
        complete_design_round(self.store, batch.id)
        self.variants = {'A-P1': [{**self.fixture.variant, 'device': self.context.environment['runtime_session_id']}]}

    async def test_schema_v1_continuous_job_rechecks_cpu_before_reserving_budget(self):
        import copy
        from rsi_kernel_agent.profiler.inputs import AnalysisRequest, capture_inputs
        from rsi_kernel_agent.workflow.controller import Controller
        from unittest.mock import patch
        fixture = test_offline_checks.OfflineCheckTests()
        await fixture.asyncSetUp()
        self.addCleanup(fixture.temp.cleanup)
        context_id = await configure_cpu_fixture(fixture)
        store = fixture.store
        context = store.get(context_id)
        context = replace(context, id='legacy-continuous', protocol={**context.protocol, 'execution_mode': 'candidate.v1'})
        store.append(context, context.id)
        request = fixture.manifest.to_dict()['request']
        request['schema_version'], request['request_id'] = 1, 'legacy-continuous'
        request['strategies'][0].update(context_id=context.id, snapshot_id=store.create_snapshot('task', 'v1')['id'])
        manifest = await capture_inputs(store, None, AnalysisRequest.from_dict(request))
        answer = copy.deepcopy(fixture.answer)
        second = copy.deepcopy(answer['plans'][0])
        second.update(id='A-P2', mechanism='Separate layout', modification='A separate layout proposal')
        answer['plans'].append(second)
        drafts = {'A-P1': [fixture.variant], 'A-P2': [fixture.variant]}
        result = Analyzer(manifest).advance({'final': answer})
        publication = publish_result(store, manifest, result, 'legacy-continuous')
        checks = check_drafts(store, manifest, result, drafts,
                              cpu_python=sys.executable, output_dir=fixture.root / 'legacy-cpu')
        self.assertTrue(checks['ready'])
        variants = {key: [{**fixture.variant, 'device': context.environment['runtime_session_id']}] for key in drafts}
        intake = await intake_publication(store, publication.id, 'legacy-round', variants)
        self.assertTrue(all(item['status'] == 'accepted' for item in intake['items']))
        controller = Controller(store, 'task', None, limits={'executions': 1})
        controller.accept_intake(intake)
        from rsi_kernel_agent.workflow import offline_checks
        with patch.object(offline_checks, 'require_current_checks', side_effect=ValueError('Newer CPU failure')):
            with self.assertRaisesRegex(ValueError, 'Newer CPU failure'):
                controller.step()
        self.assertEqual(controller.status()['spent']['executions'], 0)

    async def test_direct_continuous_intake_requires_cpu_candidate_evidence(self):
        rejected = await intake_publication(self.store, self.publication.id, 'direct-round', self.variants)
        self.assertEqual(rejected['items'][0]['status'], 'rejected')
        self.assertEqual(rejected['items'][0]['jobs'], [])
        checks = check_drafts(self.store, self.manifest, self.result, self.fixture.drafts,
                              cpu_python=sys.executable, output_dir=self.fixture.root / 'direct-cpu')
        self.assertTrue(checks['ready'])
        accepted = await intake_publication(self.store, self.publication.id, 'direct-round', self.variants,
                                            retry_key='checked')
        self.assertEqual(accepted['items'][0]['status'], 'accepted')
        self.assertEqual(len(accepted['items'][0]['jobs']), 1)


if __name__ == '__main__':
    unittest.main()
