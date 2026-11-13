"""Crash real child processes at durable workflow boundaries, then recover."""
import asyncio
import copy
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from rsi_kernel_agent.contracts import Candidate, Decision, record_to_dict
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.workflow.controller import Controller
from rsi_kernel_agent.workflow.execution import SyntheticBackend
from test_workflow_controller import fixture


class CountedBackend(SyntheticBackend):
    """Test producer records actual synthetic benchmark invocations outside DB."""
    def _complete(self, request):
        if request['stage']=='benchmark' and self.lookup(request['operation_id'])['status']!='completed':
            with (self.workspace_root/'benchmark-calls.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(request['operation_id'])+'\n')
                stream.flush()
                os.fsync(stream.fileno())
        return super()._complete(request)


def child(root, mode):
    class CrashStore(ExperimentStore):
        def append(self, record, key):
            result=super().append(record,key)
            hit=False
            if mode=='candidate':
                hit=isinstance(record,Candidate) and record.id.startswith('wf-c:')
            if isinstance(record,Decision):
                kind=record.details.get('type')
                if mode=='pause' and record.kind=='paused':hit=True
                if mode=='compile' and kind=='execution_request.v1':
                    hit=record.details['request']['stage']=='compile'
                elif mode=='receipt' and kind=='execution_receipt.v1':
                    from rsi_kernel_agent.contracts import ArtifactRef
                    raw=json.loads(self.artifacts.read(ArtifactRef(**record.details['artifact_ref'])))
                    hit=raw['stage']=='benchmark'
                elif mode=='repair' and kind=='workflow_state.v1':
                    hit=any(j.get('repair_of') for j in record.details['state']['jobs'].values())
                elif mode=='result' and kind=='workflow_result.v1':
                    hit=True
            if hit: os._exit(73)
            return result

        def transition(self,event,expected_event_id,key):
            result=super().transition(event,expected_event_id,key)
            if mode=='terminal' and event.execution_status=='succeeded': os._exit(73)
            return result

        def consider(self,evaluation_id,key):
            result=super().consider(evaluation_id,key)
            if mode=='promotion': os._exit(73)
            return result

    root=Path(root)
    store=CrashStore(root/'experiments.sqlite3',root/'artifacts')
    controller=Controller(store,'task',CountedBackend(store,root/'workspaces',polls_before_completion=2))
    if mode=='candidate':
        controller.accept_intake(json.loads((root/'intake.json').read_text(encoding='utf-8')))
    elif mode=='repair':
        response=json.loads((root/'repair.json').read_text(encoding='utf-8'))
        controller.submit_repair(response['job_id'],response,'crash-repair')
    elif mode=='pause':
        controller.pause('A','Pause before process crash')
    else:
        controller.run_until_idle()
    raise AssertionError('Expected durable crash boundary was not reached')


class WorkflowRecoveryTests(unittest.TestCase):
    def setup_case(self, scenarios=None, limits=None):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root=Path(temp.name)
        data=asyncio.run(fixture(root,scenarios))
        store=data['store']
        controller=Controller(store,'task',CountedBackend(store,root/'workspaces',polls_before_completion=2),
            limits=limits or {'executions':1,'max_concurrency':1})
        (root/'intake.json').write_text(json.dumps(data['intake']),encoding='utf-8')
        return root,data,controller

    def crash(self,root,mode):
        process=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'--crash-child',str(root),mode],
            capture_output=True,text=True,timeout=90)
        self.assertEqual(process.returncode,73,process.stdout+'\n'+process.stderr)

    def recover(self,root):
        store=ExperimentStore(root/'experiments.sqlite3',root/'artifacts')
        controller=Controller(store,'task',CountedBackend(store,root/'workspaces',polls_before_completion=2))
        return store,controller,controller.run_until_idle()

    def assert_one_execution(self,root,store,controller,state):
        completed=[j for j in state['jobs'].values() if j['state']=='completed']
        self.assertEqual(len(completed),1)
        self.assertEqual(state['spent']['executions'],1)
        self.assertEqual(sum(j['state']=='deferred' for j in state['jobs'].values()),5)
        benchmark_calls=[json.loads(line) for line in (root/'workspaces'/'benchmark-calls.jsonl').read_text().splitlines()]
        self.assertEqual(len(benchmark_calls),1)
        requests=[r for r in store.history('task') if isinstance(r,Decision)
                  and r.details.get('type')=='execution_request.v1']
        self.assertEqual(Counter(r.details['request']['stage'] for r in requests),
                         {'generate':1,'compile':1,'correctness':1,'benchmark':1})
        count=len(store.history('task'))
        controller.run_until_idle()
        self.assertEqual(len(store.history('task')),count)
        self.assertEqual(controller.status()['spent']['executions'],1)

    def test_candidate_committed_before_acknowledgment_is_completed_after_process_restart(self):
        root,data,controller=self.setup_case()
        self.crash(root,'candidate')
        store,controller,state=self.recover(root)
        self.assert_one_execution(root,store,controller,state)
        self.assertEqual(len([r for r in store.history('task') if isinstance(r,Candidate)]),8)

    def test_compile_in_progress_survives_process_exit_without_new_operation(self):
        root,data,controller=self.setup_case()
        controller.accept_intake(data['intake'])
        self.crash(root,'compile')
        store,controller,state=self.recover(root)
        self.assert_one_execution(root,store,controller,state)

    def test_completed_benchmark_before_controller_writeback_executes_only_once(self):
        root,data,controller=self.setup_case()
        controller.accept_intake(data['intake'])
        self.crash(root,'receipt')
        old=controller.status()
        self.assertTrue(any(j.get('operation',{}).get('stage')=='benchmark' for j in old['jobs'].values()))
        self.assertFalse(any(j['result'] for j in old['jobs'].values()))
        store,controller,state=self.recover(root)
        self.assert_one_execution(root,store,controller,state)

    def test_terminal_transition_response_loss_replays_same_event(self):
        root,data,controller=self.setup_case()
        controller.accept_intake(data['intake'])
        self.crash(root,'terminal')
        store,controller,state=self.recover(root)
        self.assert_one_execution(root,store,controller,state)

    def test_promotion_response_loss_does_not_duplicate_evaluation_or_worsen_best(self):
        root,data,controller=self.setup_case()
        controller.accept_intake(data['intake'])
        self.crash(root,'promotion')
        before=data['store'].best('task','context').latency_ms
        store,controller,state=self.recover(root)
        self.assert_one_execution(root,store,controller,state)
        self.assertEqual(store.best('task','context').latency_ms,before)

    def test_cancellation_after_result_commit_recovers_original_completion_marker(self):
        root,data,controller=self.setup_case()
        controller.accept_intake(data['intake'])
        self.crash(root,'result')
        controller.cancel('Cancel remaining work after observed process exit')
        store,reopened,state=self.recover(root)
        self.assertEqual(sum(j['state']=='completed' for j in state['jobs'].values()),1)
        self.assertEqual(sum(j['state']=='cancelled' for j in state['jobs'].values()),5)
        self.assertEqual(state['spent']['executions'],1)
        self.assertEqual(len((root/'workspaces'/'benchmark-calls.jsonl').read_text().splitlines()),1)

    def test_repair_journal_committed_before_records_recreates_exact_lineage(self):
        root,data,controller=self.setup_case(['compile_failure']*6,{'executions':7,'max_concurrency':2})
        controller.accept_intake(data['intake'])
        initial=controller.run_until_idle()
        failed=next(j for j in initial['jobs'].values() if j['state']=='failed')
        response=controller.repair_packet(failed['id'])
        variant=copy.deepcopy(failed['variant'])
        variant.update(scenario='success',source_ref=record_to_dict(data['store'].artifacts.put(b'# repaired synthetic source\n')))
        response.update(variant=variant,reason='Recorded repair for compile failure')
        (root/'repair.json').write_text(json.dumps(response),encoding='utf-8')
        self.crash(root,'repair')
        store,controller,state=self.recover(root)
        repair=[j for j in state['jobs'].values() if j.get('repair_of')]
        self.assertEqual(len(repair),1)
        self.assertEqual(repair[0]['state'],'completed')
        parent=store.get(store.get(repair[0]['candidate_id']).parent_id)
        self.assertEqual(parent.candidate_id,failed['candidate_id'])
        self.assertEqual(state['jobs'][failed['id']]['state'],'failed')
        self.assertEqual(state['spent']['executions'],7)

    def test_pending_device_reservation_survives_unknown_outcome_and_strategy_pause(self):
        root,data,controller=self.setup_case(limits={'executions':6,'max_concurrency':6})
        controller.accept_intake(data['intake'])
        store=data['store']

        class InterruptedLookup(CountedBackend):
            unknown=set()
            def lookup(self,operation_id):
                if operation_id in self.unknown: return {'status':'unknown','receipt':None}
                return super().lookup(operation_id)

        backend=InterruptedLookup(store,root/'workspaces',polls_before_completion=2)
        controller=Controller(store,'task',backend)
        pending=None
        for _ in range(30):
            state=controller.step()
            pending=next((j for j in state['jobs'].values() if j.get('operation',{}).get('stage')=='benchmark'),None)
            if pending:break
        self.assertIsNotNone(pending)
        operation=pending['operation']['operation_id']
        backend.unknown.add(operation)
        state=controller.step()
        self.assertEqual(state['jobs'][pending['id']]['state'],'blocked')
        controller.pause(pending['strategy_id'],'Pause during an unknown pending benchmark')
        for _ in range(3):controller.step()
        requests=[r for r in store.history('task') if isinstance(r,Decision)
                  and r.details.get('type')=='execution_request.v1'
                  and r.details['request']['stage']=='benchmark']
        self.assertEqual(len(requests),1,'Blocked pending benchmark must retain exclusive device ownership')
        backend.unknown.clear()
        # A transport that recovers to running must resume polling its original
        # operation even while the strategy is paused for new admission.
        state=controller.run_until_idle()
        self.assertEqual(sum(j['state']=='completed' for j in state['jobs'].values()),6)
        self.assertEqual(state['spent']['executions'],6)
        active=set()
        for record in store.history('task'):
            if not isinstance(record,Decision):continue
            if record.details.get('type')=='execution_request.v1' and record.details['request']['stage']=='benchmark':
                self.assertFalse(active,'Same-device benchmark submissions overlap')
                active.add(record.details['request']['operation_id'])
            elif record.details.get('type')=='execution_receipt.v1':
                active.discard(record.details['operation_id'])
        self.assertFalse(active)

    def test_pause_decision_committed_before_acknowledgment_blocks_new_work_after_restart(self):
        root,data,controller=self.setup_case()
        controller.accept_intake(data['intake'])
        self.crash(root,'pause')
        store,controller,state=self.recover(root)
        self.assertIn('A',state['paused'])
        self.assertFalse(any(j['state']=='completed' for j in state['jobs'].values() if j['strategy_id']=='A'))
        self.assertEqual(sum(j['state']=='completed' for j in state['jobs'].values() if j['strategy_id']=='B'),1)
        from rsi_kernel_agent.memory import MemoryReader
        snapshot=store.create_snapshot('task','recovery-pause:snapshot')
        cards=MemoryReader(store).open_snapshot(snapshot['id']).status_cards('context')
        self.assertTrue(next(card for card in cards if card['strategy_id']=='A')['paused'])


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--crash-child': child(sys.argv[2],sys.argv[3])
    else: unittest.main()
