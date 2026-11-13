"""Real Store/backend Controller lifecycle and durable recovery acceptance."""
import asyncio
import copy
import importlib
import json
from pathlib import Path
import tempfile
import unittest

from phase2c_fixtures import make_analysis_fixture
from test_analyzer import proposal
from rsi_kernel_agent.contracts import Candidate, Decision, ParentCandidate, Strategy, canonical_json, record_to_dict
from rsi_kernel_agent.profiler.analyzer import Analyzer
from rsi_kernel_agent.profiler.inputs import AnalysisRequest, capture_inputs
from rsi_kernel_agent.profiler.outputs import publish_result


async def fixture(root, scenarios=None, latencies=None):
    data = make_analysis_fixture(root)
    store = data['store']
    for sid in 'CDE':
        store.append(Strategy(sid, 'task', sid), 'extra:' + sid)
    store.consider('A-benchmark', 'initial:A')
    store.consider('B-benchmark', 'initial:B')
    p = proposal()
    p['plans'][1]['variants'] = p['plans'][1]['variants'][:1]
    p['plans'][2]['variants'] = p['plans'][2]['variants'][:1]
    variants = {}
    scenarios = scenarios or ['success'] * 6
    n = 0
    for plan in p['plans']:
        variants[plan['id']] = []
        for label in plan['variants']:
            variant = dict(label=label,
                source_ref=record_to_dict(store.artifacts.put(f'# synthetic {n}\n'.encode())),
                config_ref=record_to_dict(store.artifacts.put(canonical_json({'tile': n + 1}).encode())),
                build_ref=record_to_dict(store.artifacts.put(b'synthetic-recipe')),
                scenario=scenarios[n], latency_ms=latencies[n] if latencies else 8 if plan['strategy_id']=='A' else 11,
                priority=0, device='synthetic-device', cost={'tokens':10,'time_s':1,'executions':1})
            variants[plan['id']].append(variant)
            n += 1
    manifest = await capture_inputs(store, data['knowledge'], AnalysisRequest.from_dict(data['request']))
    result = Analyzer(manifest, artifacts=store.artifacts).advance({'final':p})
    publication = publish_result(store, manifest, result, 'workflow:analysis')
    from rsi_kernel_agent.workflow.intake import intake_publication
    intake = await intake_publication(store, publication.id, 'round1', variants, knowledge=data['knowledge'])
    return {**data, 'intake':intake,'variants':variants,'publication':publication,'manifest':manifest}


class WorkflowControllerTests(unittest.TestCase):
    def setUp(self):
        try:
            self.api = importlib.import_module('rsi_kernel_agent.workflow.controller')
        except ImportError:
            self.fail('Phase 2D Controller has not been implemented')
        from rsi_kernel_agent.workflow.execution import SyntheticBackend
        self.backend_type = SyntheticBackend
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def make(self, scenarios=None, limits=None, latencies=None):
        self.data = asyncio.run(fixture(self.tmp.name, scenarios, latencies))
        self.store = self.data['store']
        self.backend = self.backend_type(self.store, Path(self.tmp.name)/'workspaces')
        self.controller = self.api.Controller(self.store, 'task', self.backend, limits=limits)
        self.controller.accept_intake(self.data['intake'])
        return self.controller

    def test_variable_variants_both_routes_finish_and_nonwinning_branch_survives(self):
        c = self.make()
        result = c.run_until_idle()
        self.assertEqual(len(result['jobs']), 6)
        self.assertEqual({j['state'] for j in result['jobs'].values()}, {'completed'})
        self.assertEqual(self.store.best('task','context').latency_ms,8)
        self.assertEqual(self.store.best('task','context','B').latency_ms,11)
        self.assertTrue(any(self.store.get(x).plan_id in
            [j['plan_id'] for j in result['jobs'].values() if j['strategy_id']=='B']
            for x in self.store.frontier('task','context')))
        self.assertEqual(result['spent']['executions'],6)
        c.accept_intake(self.data['intake'])
        again = c.run_until_idle()
        self.assertEqual(again['spent'],result['spent'])
        self.assertEqual(len(again['jobs']),6)

    def test_mixed_failures_remain_distinct(self):
        c = self.make(['compile_failure','correctness_failure','timeout','invalid_measurement','success','generation_failure'])
        state = c.run_until_idle()
        classes = {j['result']['classification'] for j in state['jobs'].values()}
        self.assertTrue({'compile_failed','generation_failed','incorrect','incomplete','invalid_measurement'} <= classes, classes)
        self.assertEqual(len([j for j in state['jobs'].values() if j['state']=='completed']),1)
        self.assertEqual(len([r for r in self.store.history('task') if isinstance(r,Candidate)]),8)

    def test_repair_new_source_exact_failed_parent_original_retained_and_limit(self):
        c = self.make(['compile_failure'] + ['success']*5)
        state = c.run_until_idle()
        failed = next(j for j in state['jobs'].values() if j['state']=='failed')
        response = c.repair_packet(failed['id'])
        variant = copy.deepcopy(failed['variant'])
        variant.update(scenario='success', source_ref=record_to_dict(self.store.artifacts.put(b'# synthetic repair\n')))
        response.update(variant=variant, reason='Recorded synthetic compile repair')
        repaired = c.submit_repair(failed['id'], response, 'repair1')
        c.submit_repair(failed['id'], response, 'repair1')
        state = c.run_until_idle()
        child = self.store.get(state['jobs'][repaired]['candidate_id'])
        self.assertEqual(child.change_kind,'repair')
        self.assertEqual(self.store.get(child.parent_id).candidate_id,failed['candidate_id'])
        self.assertEqual(state['jobs'][failed['id']]['state'],'failed')
        self.assertEqual(state['jobs'][repaired]['state'],'completed')
        with self.assertRaises(ValueError): c.submit_repair(failed['id'],response,'repair2')

    def test_budget_pause_defer_cancel_and_resume_do_not_start_work(self):
        c = self.make(limits={'executions':1})
        c.pause('A','User pause')
        state = c.run_until_idle()
        self.assertEqual(state['spent']['executions'],1)
        self.assertEqual(sum(j['state']=='completed' for j in state['jobs'].values()),1)
        self.assertTrue(all(j['state']!='completed' for j in state['jobs'].values() if j['strategy_id']=='A'))
        c.cancel('User cancellation')
        state = c.run_until_idle()
        self.assertFalse(any(j['state']=='active' for j in state['jobs'].values()))

    def test_rebuild_after_each_step_matches_uninterrupted_and_no_duplicate_receipts(self):
        c = self.make()
        for _ in range(80):
            state = c.step()
            c = self.api.Controller(self.store,'task',self.backend_type(self.store,Path(self.tmp.name)/'workspaces'))
            if not any(j['state'] in ('queued','active','evaluating') for j in state['jobs'].values()): break
        self.assertEqual(sum(j['state']=='completed' for j in c.status()['jobs'].values()),6)
        self.assertEqual(c.status()['spent']['executions'],6)
        self.assertEqual(self.store.best('task','context').latency_ms,8)

    def test_cancel_during_pending_execution_reconciles_without_later_stages(self):
        c=self.make()
        c.step()
        c.cancel('Stop after current durable operation')
        state=c.run_until_idle()
        self.assertFalse(any(j['state'] in ('active','evaluating','queued') for j in state['jobs'].values()))
        self.assertTrue(any(j['result'] and j['result']['classification']=='cancelled' for j in state['jobs'].values()))

    def test_pending_backend_continues_polling_and_same_device_never_overlaps(self):
        c=self.make()
        self.backend=self.backend_type(self.store,Path(self.tmp.name)/'workspaces',polls_before_completion=2)
        c=self.api.Controller(self.store,'task',self.backend)
        state=c.run_until_idle()
        self.assertEqual(sum(j['state']=='completed' for j in state['jobs'].values()),6)

    def test_retry_retains_candidate_creates_new_run_charges_once_and_stops_at_limit(self):
        c=self.make(['timeout']+['success']*5)
        state=c.run_until_idle()
        failed=next(j for j in state['jobs'].values() if j['state']=='failed')
        retry=c.retry(failed['id'],'retry1')
        self.assertEqual(c.retry(failed['id'],'retry1'),retry)
        state=c.run_until_idle()
        self.assertEqual(state['jobs'][retry]['candidate_id'],failed['candidate_id'])
        self.assertNotEqual(state['jobs'][retry]['run_id'],failed['run_id'])
        self.assertEqual(state['spent']['executions'],7)
        with self.assertRaises(ValueError):c.retry(retry,'retry2')

    def test_resume_deferred_preserves_contract_and_runs_once(self):
        c=self.make(['deferred']+['success']*5)
        state=c.run_until_idle()
        job=next(j for j in state['jobs'].values() if j['state']=='deferred')
        self.assertEqual(self.store.run_state(job['run_id'])['scheduling_status'],'deferred')
        c.resume_job(job['id'],'Synthetic capacity now available')
        state=c.run_until_idle()
        self.assertEqual(state['jobs'][job['id']]['state'],'completed')
        self.assertEqual(state['spent']['executions'],6)

    def test_unknown_outcome_blocks_and_cancel_never_submits_definitely_unstarted_work(self):
        c=self.make(limits={'max_concurrency':1})
        original=self.backend
        class Ambiguous:
            unknown=True
            def lookup(self, op):
                return {'status':'unknown','receipt':None} if self.unknown else original.lookup(op)
            def submit(self, request):return original.submit(request)
            def poll(self, op):return original.poll(op)
        backend=Ambiguous()
        c=self.api.Controller(self.store,'task',backend)
        state=c.step()
        self.assertEqual(sum(j['state']=='blocked' for j in state['jobs'].values()),1)
        self.assertFalse(any(isinstance(r,Decision) and r.details.get('type')=='execution_request.v1'
                             for r in self.store.history('task')))
        c.cancel('Cancel while reconciling')
        backend.unknown=False
        state=c.run_until_idle()
        self.assertFalse(any(isinstance(r,Decision) and r.details.get('type')=='execution_request.v1'
                             for r in self.store.history('task')))
        self.assertFalse(any(j['state'] in ('blocked','active','evaluating') for j in state['jobs'].values()))

    def test_zero_budget_is_immediately_visible_as_deferred_in_original_run_state(self):
        c=self.make(limits={'executions':0})
        state=c.run_until_idle()
        self.assertEqual(state['remaining_budget']['executions'],0)
        self.assertTrue(all(j['state']=='deferred' for j in state['jobs'].values()))
        self.assertTrue(all(self.store.run_state(j['run_id'])['scheduling_status']=='deferred'
                            for j in state['jobs'].values()))

    def test_unknown_submission_reconciles_running_operation_and_finishes_same_attempt(self):
        c=self.make(limits={'max_concurrency':1,'executions':1})
        real=self.backend_type(self.store,Path(self.tmp.name)/'workspaces',polls_before_completion=2)
        class LostAcknowledgement:
            first=True
            def lookup(self,op):return real.lookup(op)
            def poll(self,op):return real.poll(op)
            def submit(self,request):
                out=real.submit(request)
                if self.first:
                    self.first=False
                    return {'status':'unknown','receipt':None}
                return out
        c=self.api.Controller(self.store,'task',LostAcknowledgement())
        c.step()
        state=c.run_until_idle()
        self.assertEqual(sum(j['state']=='completed' for j in state['jobs'].values()),1)
        self.assertEqual(state['spent']['executions'],1)

    def test_out_of_order_completion_preserves_newer_attempt_and_best(self):
        from rsi_kernel_agent.memory import MemoryReader
        c=self.make(limits={'max_concurrency':6},latencies=[9,8,7,6,5,2])
        ids=[j['id'] for item in self.data['intake']['items'] for j in item['jobs']]
        slow_id=ids[0]
        backend_type=self.backend_type
        class DelayedOlderCandidate(backend_type):
            def submit(self,request):
                self.polls_before_completion=8 if request['job']['id']==slow_id and request['stage']=='generate' else 0
                return super().submit(request)
        c=self.api.Controller(self.store,'task',DelayedOlderCandidate(self.store,Path(self.tmp.name)/'workspaces'))
        state=c.run_until_idle()
        older,newer=state['jobs'][ids[0]],state['jobs'][ids[2]]
        records=[r.id for r in self.store.history('task')]
        self.assertGreater(records.index(older['run_id']+':finish'),records.index(newer['run_id']+':finish'))
        self.assertEqual(self.store.best('task','context','A').latency_ms,7)
        self.assertEqual(self.store.best('task','context').latency_ms,2)
        snap=self.store.create_snapshot('task','caseG:complete')
        cards=MemoryReader(self.store).open_snapshot(snap['id']).status_cards('context')
        self.assertEqual(next(card for card in cards if card['strategy_id']=='A')['latest_attempt']['run_id'],newer['run_id'])
        count=len(self.store.history('task'))
        self.assertEqual(c.run_until_idle()['spent']['executions'],6)
        self.assertEqual(len(self.store.history('task')),count)

    def test_arbitrary_note_cannot_impersonate_validated_intake_marker(self):
        data=asyncio.run(fixture(self.tmp.name))
        store=data['store']
        forged=copy.deepcopy(data['intake']);forged['id']='forged-intake'
        store.append(Decision('forged-intake','task','note','task','Unrelated note',{'result':forged}),'forged-intake')
        c=self.api.Controller(store,'task',self.backend_type(store,Path(self.tmp.name)/'workspaces'))
        before=len(store.history('task'))
        with self.assertRaises(ValueError):c.accept_intake(forged)
        self.assertEqual(c.status()['jobs'],{})
        self.assertEqual(len(store.history('task')),before)


if __name__=='__main__': unittest.main()
