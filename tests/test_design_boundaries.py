import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from phase2d_fixtures import make_workflow_fixture
from test_analyzer import proposal
from rsi_kernel_agent.contracts import Decision
from rsi_kernel_agent.profiler.analyzer import Analyzer
from rsi_kernel_agent.profiler.outputs import publish_result
from rsi_kernel_agent.workflow.design import begin_design_round, complete_design_round, finish_route_design, route_manifest


class DesignBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_freezing_a_round_after_any_of_its_jobs_started_is_rejected(self):
        from rsi_kernel_agent.workflow.controller import Controller
        from rsi_kernel_agent.workflow.execution import SyntheticBackend
        with tempfile.TemporaryDirectory() as root:
            f = await make_workflow_fixture(root)
            controller = Controller(f['store'], 'task', SyntheticBackend(f['store'], Path(root) / 'workspaces'))
            controller.accept_intake(f['intake'])
            controller.step()
            assessments = {sid: {'status': 'deferred', 'reason': 'Do not retroactively gate execution',
                                  'hypothesis': None, 'refs': []} for sid in 'ABCDE'}
            with self.assertRaises(ValueError):
                await begin_design_round(f['store'], f['knowledge'], 'task', 'context', 'round:1', assessments)

    async def test_legacy_publication_cannot_bypass_open_design_under_a_different_round(self):
        from rsi_kernel_agent.workflow.intake import intake_publication
        with tempfile.TemporaryDirectory() as root:
            f = await make_workflow_fixture(root, intake=False)
            assessments = {sid: {'status': 'hypothesis' if sid == 'A' else 'deferred',
                                  'reason': 'Independent design still open', 'hypothesis': 'Tile' if sid == 'A' else None,
                                  'refs': []} for sid in 'ABCDE'}
            await begin_design_round(f['store'], f['knowledge'], 'task', 'context', 'new-batch', assessments,
                                     parents={'A': 'A-parent'})
            with self.assertRaises(ValueError):
                await intake_publication(f['store'], f['publication'].id, 'legacy-bypass', f['variants'], f['knowledge'])

    async def test_invalid_v2_variants_can_be_corrected_without_duplicate_execution(self):
        from rsi_kernel_agent.workflow.intake import intake_publication
        with tempfile.TemporaryDirectory() as root:
            f = await make_workflow_fixture(root, intake=False)
            assessments = {sid: {'status': 'hypothesis' if sid == 'A' else 'deferred',
                                  'reason': 'Fix implementation metadata only', 'hypothesis': 'Tile' if sid == 'A' else None,
                                  'refs': []} for sid in 'ABCDE'}
            batch = await begin_design_round(f['store'], f['knowledge'], 'task', 'context', 'retry-intake', assessments,
                                              parents={'A': 'A-parent'})
            manifest = route_manifest(f['store'], batch.id, 'A')
            answer = proposal()
            for field in ('code_understanding', 'observations', 'hypotheses', 'plans'):
                answer[field] = [item for item in answer[field] if item['strategy_id'] == 'A']
            for plan in answer['plans']:
                plan['variants'] = [v['label'] for v in f['variants'][plan['id']]]
            result = Analyzer(manifest).advance({'final': answer})
            publication = publish_result(f['store'], manifest, result, 'retry-intake')
            finish_route_design(f['store'], batch.id, 'A', publication.id)
            complete_design_round(f['store'], batch.id)
            rejected = await intake_publication(f['store'], publication.id, 'retry-intake', {}, f['knowledge'])
            self.assertEqual({row['status'] for row in rejected['items']}, {'rejected'})
            partial_specs = copy.deepcopy(f['variants'])
            del partial_specs['A-P1']
            partial = await intake_publication(f['store'], publication.id, 'retry-intake', partial_specs, f['knowledge'])
            self.assertEqual([row['status'] for row in partial['items']], ['rejected', 'accepted'])
            from rsi_kernel_agent.workflow.controller import Controller
            controller = Controller(f['store'], 'task', None)
            controller.accept_intake(partial)
            accepted = await intake_publication(f['store'], publication.id, 'retry-intake', f['variants'], f['knowledge'])
            self.assertEqual({row['status'] for row in accepted['items']}, {'accepted'})
            self.assertNotEqual(rejected['id'], accepted['id'])
            controller.accept_intake(accepted)
            self.assertEqual(len(controller.status()['jobs']), 3)
            self.assertEqual(controller.status()['spent']['executions'], 0)
            self.assertEqual(await intake_publication(f['store'], publication.id, 'retry-intake', f['variants'], f['knowledge']), accepted)
            self.assertEqual(f['store'].get(rejected['id']).details['result'], rejected)

    async def test_stale_publication_cannot_claim_another_batch(self):
        with tempfile.TemporaryDirectory() as root:
            f = await make_workflow_fixture(root, intake=False)
            assessments = {sid: {'status': 'hypothesis' if sid == 'A' else 'deferred',
                                  'reason': 'Check original input binding', 'hypothesis': 'Tile' if sid == 'A' else None,
                                  'refs': []} for sid in 'ABCDE'}
            batch = await begin_design_round(f['store'], f['knowledge'], 'task', 'context', 'bound', assessments,
                                              parents={'A': 'A-parent'})
            with self.assertRaises(ValueError):
                finish_route_design(f['store'], batch.id, 'A', f['publication'].id)
            with self.assertRaises(ValueError):
                complete_design_round(f['store'], batch.id)


if __name__ == '__main__':
    unittest.main()
