"""Explicit retries recover transient Intake failures without replaying accepted work."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from phase2d_fixtures import make_workflow_fixture
from test_analyzer import proposal
from rsi_kernel_agent.contracts import ArtifactRef, Decision
from rsi_kernel_agent.profiler.analyzer import Analyzer
from rsi_kernel_agent.profiler.outputs import publish_result
from rsi_kernel_agent.workflow import design
from rsi_kernel_agent.workflow.controller import Controller
from rsi_kernel_agent.workflow.execution import SyntheticBackend
from rsi_kernel_agent.workflow.intake import intake_publication


class UnavailableKnowledge:
    async def fetch_document(self, *args, **kwargs):
        raise OSError('temporary Knowledge transport failure')


class IntakeRetryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fixture = await make_workflow_fixture(self.tmp.name, intake=False)
        self.store = self.fixture['store']

    async def publish_route(self):
        assessments = {sid: {'status': 'hypothesis' if sid == 'A' else 'deferred',
            'reason': 'Try a tile change' if sid == 'A' else 'No current hypothesis',
            'hypothesis': 'Change tile' if sid == 'A' else None, 'refs': []} for sid in 'ABCDE'}
        batch = await design.begin_design_round(self.store, self.fixture['knowledge'], 'task', 'context',
            'batch-1', assessments, parents={'A': 'A-parent'},
            knowledge_refs={'A': self.fixture['request']['knowledge']})
        manifest = design.route_manifest(self.store, batch.id, 'A')
        answer = proposal()
        for field in ('code_understanding', 'observations', 'hypotheses', 'plans'):
            answer[field] = [row for row in answer[field] if row['strategy_id'] == 'A']
        for plan in answer['plans']:
            plan['refs'].append('knowledge:' + self.fixture['request']['knowledge'][0]['method_id'])
            plan['variants'] = [v['label'] for v in self.fixture['variants'][plan['id']]]
        self.publication = publish_result(self.store, manifest,
            Analyzer(manifest).advance({'final': answer}), 'retry-fixture')
        design.finish_route_design(self.store, batch.id, 'A', self.publication.id)
        design.complete_design_round(self.store, batch.id)

    async def intake(self, *, retry_key=None, knowledge='default', variants=None):
        return await intake_publication(self.store, self.publication.id, 'batch-1',
            self.fixture['variants'] if variants is None else variants,
            self.fixture['knowledge'] if knowledge == 'default' else knowledge,
            **({} if retry_key is None else {'retry_key': retry_key}))

    def records(self):
        return [r for r in self.store.history('task') if type(r) is Decision
                and r.details.get('type') == 'workflow_intake.v1']

    async def test_explicit_retry_recovers_unchanged_waiting_input_and_preserves_history(self):
        # A cache-only retry would strand the unchanged, now-available publication.
        await self.publish_route()
        waiting = await self.intake(knowledge=UnavailableKnowledge())
        self.assertEqual([item['status'] for item in waiting['items']], ['waiting', 'waiting'])
        self.assertEqual(await self.intake(), waiting)
        recovered = await self.intake(retry_key='knowledge-restored')
        self.assertEqual([item['status'] for item in recovered['items']], ['accepted', 'accepted'])
        self.assertNotEqual(recovered['id'], waiting['id'])
        self.assertEqual(self.store.get(waiting['id']).details['result'], waiting)
        self.assertEqual(recovered['retry']['base_intake_id'], waiting['id'])
        before = len(self.store.history('task'))
        self.assertEqual(await self.intake(retry_key='knowledge-restored', knowledge=UnavailableKnowledge()), recovered)
        self.assertEqual(len(self.store.history('task')), before)
        self.assertEqual(await self.intake(), waiting)

    async def test_rejected_plan_recovers_without_duplicating_accepted_jobs_or_costs(self):
        # Recreating the accepted peer's jobs would double the queued work/cost.
        await self.publish_route()
        reference = ArtifactRef(**self.fixture['variants']['A-P1'][0]['source_ref'])
        original = self.store.artifacts.read(reference)
        path = self.store.artifacts.path_for(reference)
        path.write_bytes(b'corrupted candidate source')
        partial = await self.intake()
        self.assertEqual([item['status'] for item in partial['items']], ['rejected', 'accepted'])
        controller = Controller(self.store, 'task', SyntheticBackend(self.store, Path(self.tmp.name) / 'workspaces'),
                                limits={'executions': 1, 'max_concurrency': 1})
        controller.accept_intake(partial)
        controller.step()
        self.assertEqual(controller.status()['spent']['executions'], 0)
        path.write_bytes(original)
        recovered = await self.intake(retry_key='artifact-restored')
        self.assertEqual([item['status'] for item in recovered['items']], ['accepted', 'accepted'])
        self.assertEqual(recovered['items'][1], partial['items'][1])
        controller.accept_intake(recovered)
        state = controller.run_until_idle()
        self.assertEqual(len(state['jobs']), 3)
        self.assertEqual(state['spent']['executions'], 1)
        controller.accept_intake(await self.intake(retry_key='artifact-restored'))
        self.assertEqual(controller.run_until_idle()['spent']['executions'], 1)
        self.assertEqual(len(controller.status()['jobs']), 3)

    async def test_retry_key_cannot_be_reused_with_changed_variants(self):
        await self.publish_route()
        await self.intake(knowledge=UnavailableKnowledge())
        await self.intake(retry_key='transport-1', knowledge=UnavailableKnowledge())
        changed = copy.deepcopy(self.fixture['variants'])
        changed['A-P1'][0]['priority'] = 5
        before = len(self.store.history('task'))
        with self.assertRaises(ValueError):
            await self.intake(retry_key='transport-1', variants=changed)
        self.assertEqual(len(self.store.history('task')), before)

    async def test_retry_attempts_are_bounded_and_existing_keys_replay_after_limit(self):
        await self.publish_route()
        base = await self.intake(knowledge=UnavailableKnowledge())
        previous = base
        retries = []
        for number in range(1, 4):
            retry = await self.intake(retry_key='transport-' + str(number), knowledge=UnavailableKnowledge())
            self.assertEqual([item['status'] for item in retry['items']], ['waiting', 'waiting'])
            self.assertEqual(retry['retry']['previous_intake_id'], previous['id'])
            self.assertEqual(retry['retry']['attempt'], number)
            retries.append(retry)
            previous = retry
        before = len(self.store.history('task'))
        with self.assertRaisesRegex(ValueError, '[Rr]etry limit'):
            await self.intake(retry_key='transport-4')
        self.assertEqual(await self.intake(retry_key='transport-1'), retries[0])
        self.assertEqual(len(self.store.history('task')), before)

    async def test_retry_requires_prior_nonaccepted_input(self):
        await self.publish_route()
        with self.assertRaises(ValueError):
            await self.intake(retry_key='without-base')
        accepted = await self.intake()
        with self.assertRaises(ValueError):
            await self.intake(retry_key='nothing-to-retry')
        self.assertEqual([record.id for record in self.records()], [accepted['id']])

    async def test_retry_rejects_invalid_identifiers_without_creating_records(self):
        await self.publish_route()
        await self.intake(knowledge=UnavailableKnowledge())
        before = len(self.store.history('task'))
        for key in ('', 'contains spaces', 'x' * 129, True, {}):
            with self.subTest(key=key), self.assertRaises(ValueError):
                await self.intake(retry_key=key)
        self.assertEqual(len(self.store.history('task')), before)

    async def test_legacy_intake_replay_and_round_recovery_semantics_are_unchanged(self):
        publication = self.fixture['publication'].id
        variants = self.fixture['variants']
        waiting = await intake_publication(self.store, publication, 'legacy-1', variants, UnavailableKnowledge())
        self.assertEqual(await intake_publication(self.store, publication, 'legacy-1', variants,
                                                 self.fixture['knowledge']), waiting)
        with self.assertRaises(ValueError):
            await intake_publication(self.store, publication, 'legacy-1', variants,
                                     self.fixture['knowledge'], retry_key='unsupported')
        recovered = await intake_publication(self.store, publication, 'legacy-2', variants, self.fixture['knowledge'])
        self.assertEqual({item['status'] for item in recovered['items']}, {'accepted'})
        self.assertEqual(self.store.get(waiting['id']).details['result'], waiting)


if __name__ == '__main__':
    unittest.main()
