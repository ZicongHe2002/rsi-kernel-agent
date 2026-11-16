"""Bounded B2 milestone guards; local metadata fixtures are not TPU evidence."""
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import importlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE),str(HERE.parents[1]/'src'),str(HERE.parents[1]/'tests')]
import test_verify_p35_final_memory as memory_tests


class B2MilestoneTests(unittest.TestCase):
    def fixture(self):
        f=memory_tests.FinalMemoryTests()
        f.generation='runtime7';f.prior_count=11;f.recovery_strategies=('A','A')
        f.setUp();self.addCleanup(f.doCleanups);f.fixture()
        api=importlib.import_module('p35_b2_milestone')
        state=deepcopy(f.state)
        for i,label in enumerate(('length-row','length-smem')):
            state['jobs']['job:recovery'+str(i)]['variant']['label']=label
        for i,label in enumerate(('diagnostic-baseline','diagnostic-single-tile')):
            state['jobs']['job:diagnostic'+str(i)]['variant']['label']=label
        for j in state['jobs'].values():
            if j['round_id']=='old':continue
            j['result']['receipt_refs']={stage:{'digest':str(index)*64,'size':1} for index,stage in enumerate(
                ('generate','compile','correctness','benchmark','profile'))}
        milestone={'snapshot':f.diag['snapshot'],'successful_round_id':f.rec_id,'rounds':[
            {'round_id':f.rec_id,'context_id':'c5','run_ids':[j['run_id'] for j in state['jobs'].values() if j['round_id']==f.rec_id]},
            {'round_id':f.selection['round_id'],'context_id':'diag','run_ids':[j['run_id'] for j in state['jobs'].values() if j['round_id']==f.selection['round_id']]}]}
        spec={'task_id':'task','context_id':'c5','runtime_id':'r5','runtime_generation':'runtime7',
              'strategies':['B'],'round_id':'runtime7-b2:1','milestone':milestone}
        grant={'milestone':milestone,'old_limits':state['limits'],
            'new_limits':{**state['limits'],'executions':18,'time_s':32400},'spent':state['spent'],
            'next_batch':{'round_id':spec['round_id'],'purpose':'Finish the remaining B2 routes',
                          'cost':{'executions':2,'tokens':0,'time_s':3600}}}
        gates={'baseline-repair':{'strategy_id':'A'},'baseline-b':{'strategy_id':'B'}}
        return api,f,spec,grant,state,gates

    def test_exact_A2_and_two_diagnostics_preserve_one_reserved_execution(self):
        api,f,spec,grant,state,gates=self.fixture()
        recovery,diagnostics=api.validate_batch_evidence(spec,grant,state,gates,f.contexts)
        self.assertEqual(len(recovery),2);self.assertEqual(len(diagnostics),2)
        self.assertEqual(grant['new_limits']['executions']-grant['spent']['executions']-2,1)

    def test_old_success_missing_profile_bad_diagnostic_or_missing_candidate_refused(self):
        for mutation in ('old_success','no_profile','diagnostic_failed','missing_A','wrong_B','grant_one'):
            with self.subTest(mutation=mutation):
                api,f,spec,grant,state,gates=self.fixture()
                if mutation=='old_success':grant['milestone']['successful_round_id']='historical'
                elif mutation=='no_profile':
                    for j in state['jobs'].values():
                        if j['round_id']==f.rec_id:j['result']['receipt_refs'].pop('profile')
                elif mutation=='diagnostic_failed':state['jobs']['job:diagnostic1']['result']['correctness']='failed'
                elif mutation=='missing_A':state['jobs'].pop('job:recovery1')
                elif mutation=='wrong_B':spec['strategies']=['A']
                else:grant['next_batch']['cost']['executions']=1
                with self.assertRaises(ValueError):api.validate_batch_evidence(spec,grant,state,gates,f.contexts)

    def test_only_two_B_routes_with_exact_per_run_cost_can_be_dispatched(self):
        api=importlib.import_module('p35_b2_milestone')
        spec={'strategies':['B']}
        plans=[{'strategy_id':'B','variants':[label]} for label in ('query-in-attention','b1-fused-prepare')]
        variants=[{'label':p['variants'][0],'cost':{'executions':1,'tokens':0,'time_s':1800}} for p in plans]
        api.validate_b2_variants(spec,plans,variants)
        for mutation in ('other_label','third','wrong_strategy','uneven_cost'):
            p,v=deepcopy(plans),deepcopy(variants)
            if mutation=='other_label':p[0]['variants']=['other'];v[0]['label']='other'
            elif mutation=='third':p.append(deepcopy(p[0]));v.append(deepcopy(v[0]))
            elif mutation=='wrong_strategy':p[1]['strategy_id']='A'
            else:v[0]['cost']['time_s']=1799;v[1]['cost']['time_s']=1801
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):api.validate_b2_variants(spec,p,v)

    def test_frozen_snapshot_store_never_exposes_later_live_journal(self):
        api,f,spec,grant,state,gates=self.fixture()
        frozen=api.MilestoneStore(f.store,f.diag['snapshot'])
        before=frozen.history('task')
        from rsi_kernel_agent.contracts import Decision
        f.store.append(Decision('later','task','note','task','fixture later live note'),'later')
        self.assertEqual(frozen.history('task'),before)
        with self.assertRaises(Exception):frozen.get('later')
        self.assertFalse(hasattr(frozen,'append'))

    def report_fixture(self):
        api,f,spec,grant,state,gates=self.fixture()
        # This layer tests report/receipt rebinding. Exact A2/diagnostic semantics
        # are exercised above; this historical metadata fixture has compile only.
        report=f.call()
        report_path=f.write(f.root/'final-memory.json',report)
        spec['milestone_validation']={'final_memory_report':{
            'path':str(report_path),'sha256':sha256(report_path.read_bytes()).hexdigest()}}
        grant.update(task_id='task',expected_revision=f.state['revision'],expected_state_digest=api.digest(f.state))
        quote='同时如果第二轮ok的话，你就一直自己迭代'
        grant['authorization']={'source':'user request fixture','quote':quote,
            'artifact_ref':asdict(f.store.artifacts.put(quote.encode('utf-8')))}
        return api,f,spec,grant,gates,report_path

    def test_final_memory_and_source_report_are_rebound_before_snapshot_archive_guards(self):
        api,f,spec,grant,gates,path=self.report_fixture()
        def archive(store,directory,generation):
            self.assertIsInstance(store,api.MilestoneStore)
            self.assertEqual(store.metadata,grant['milestone']['snapshot'])
            self.assertEqual(api.WorkflowJournal(store,'task').load()['limits']['executions'],16)
        with patch.object(api,'validate_batch_evidence',return_value=({},{})), patch.object(
                api,'require_generation_archive',side_effect=archive) as guard:
            result=api.validate_p35_milestone(f.store,f.root,spec,grant,gates)
        self.assertEqual(result['status'],'verified')
        guard.assert_called_once()

    def test_changed_final_report_or_execution_verification_source_is_rejected(self):
        for mutation in ('report_bytes','source_bytes','report_claim','missing_source','bad_authorization'):
            with self.subTest(mutation=mutation):
                api,f,spec,grant,gates,path=self.report_fixture()
                if mutation=='report_bytes':path.write_bytes(path.read_bytes()+b' ')
                elif mutation=='source_bytes':f.reports.write_bytes(f.reports.read_bytes()+b' ')
                elif mutation=='missing_source':f.reports.unlink()
                elif mutation=='bad_authorization':grant['authorization']['quote']='continue something else'
                else:
                    body=json.loads(path.read_text('utf-8'));body['verified_runs'][0]['correctness']='failed'
                    f.write(path,body)
                    spec['milestone_validation']['final_memory_report']['sha256']=sha256(path.read_bytes()).hexdigest()
                with patch.object(api,'validate_batch_evidence',return_value=({},{})),patch.object(
                        api,'require_generation_archive') as guard,self.assertRaises((ValueError,FileNotFoundError)):
                    api.validate_p35_milestone(f.store,f.root,spec,grant,gates)
                guard.assert_not_called()


if __name__=='__main__':unittest.main(verbosity=2)
