"""Temporary fixtures for completed admission loss; no real Store or TPU."""
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import importlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / 'src'), str(HERE.parents[1] / 'tests')]
import test_runtime4_admission as admission_tests
from rsi_kernel_agent.contracts import Candidate, Decision, Run, canonical_json
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from rsi_kernel_agent.workflow.execution import stable_id
from real_round_aux import LIMITS


class GenerationTests(unittest.TestCase):
    def test_canonical_generations_and_previous(self):
        api = importlib.import_module('p35_runtime_generations')
        self.assertEqual(api.generation_number('runtime7', minimum=6), 7)
        self.assertEqual(api.previous_generation('runtime12'), 'runtime11')
        for value in ('runtime06', 'runtime0', '../runtime7', 'runtime7/child', 'runtime7\n', 7, None):
            with self.assertRaises(ValueError): api.generation_number(value, minimum=6)
        with self.assertRaises(ValueError): api.generation_number('runtime5', minimum=6)

    def test_runtime8_requires_all_intervening_completed_loss_records(self):
        api=importlib.import_module('admit_runtime4')
        with patch.object(api,'require_runtime4_archive',return_value={'preserved':True}), \
             patch('p35_archive_runtime4_prerequisite_loss.require_archived_runtime4_loss'), \
             patch('p35_archive_runtime5_round_loss.require_archived_runtime5_loss'), \
             patch('p35_archive_completed_prerequisite_loss.require_completed_prerequisite_loss',return_value={}) as loss:
            self.assertEqual(api.require_generation_archive(None,Path('.'),'runtime8'),{'preserved':True})
            self.assertEqual([c.args[-1] for c in loss.call_args_list],['runtime6','runtime7'])

    def test_runtime7_observation_uses_exact_previous_generation_and_new_nonce(self):
        f=admission_tests.Runtime4Tests(methodName='test_four_new_runs_preserve_sources_and_are_idempotent')
        f.setUp();self.addCleanup(f.doCleanups)
        manifests,env,report=f.observation_files()
        env['runtime_session_id']='runtime-seven';report['environment']=env
        for stem in ('environment-isolated','isolated-environment-setup','isolated-probe'):
            value=json.loads((manifests/(stem+'-runtime4.json')).read_text())
            if stem=='environment-isolated':value=env
            if stem=='isolated-probe':value['stdout']='RSI_PROBE:'+json.dumps(report)
            (manifests/(stem+'-runtime7.json')).write_text(json.dumps(value))
        (f.root/'observation-runtime7.json').write_text('{"runtime_id":"runtime-seven"}')
        (f.root/'allocation-intent.json').write_text('{"status":"allocated","runtime_id":"runtime-seven","session":"s7"}')
        (f.root/'colab-config-runtime6.json').write_text('{"runtime_id":"runtime-six","nonce":"six","session":"s6"}')
        observed,config=f.api.observed_inputs(f.root,manifests,generation='runtime7')
        self.assertEqual(observed['runtime_session_id'],'runtime-seven')
        self.assertNotEqual(config['nonce'],'six')
        self.assertTrue(config['remote_root'].endswith('runtime7'))


class CompletedLossTests(unittest.TestCase):
    def fixture(self):
        f = admission_tests.Runtime4Tests(methodName='test_four_new_runs_preserve_sources_and_are_idempotent')
        f.setUp(); self.addCleanup(f.doCleanups)
        api = importlib.import_module('p35_archive_completed_prerequisite_loss')
        # The unused RealProtocolFixture run is unrelated historical work.
        from rsi_kernel_agent.contracts import EvaluationContext
        old = f.store.get('context')
        f.store.append(EvaluationContext('context-six', old.task_id, old.workload_id,
            {**old.environment, 'runtime_session_id':'runtime-six'}, old.protocol, old.provenance), 'context-six')
        jobs, results = {}, {}
        for name, origin in f.origins.items():
            job = deepcopy(origin)
            job.update(context_id='context-six', run_id='six-'+name)
            job['contract']['context']['id']='context-six'
            job['contract']['context']['environment']['runtime_session_id']='runtime-six'
            job['variant']['device']='runtime-six'
            f.store.append(Run(job['run_id'],job['candidate_id'],'context-six'),job['run_id'])
            jobs[name]=job
            results[name]={'run_id':job['run_id'],'candidate_id':job['candidate_id'],
                'correctness':'failed' if name.startswith('oracle-negative') else 'passed',
                'timing_validity':'unknown' if name.startswith('oracle-negative') else 'valid',
                'confirmed':not name.startswith('oracle-negative'), 'receipt_refs':{}}
            identity=stable_id('workflow-result',job['run_id'])
            f.store.append(Decision(identity,'task','note',job['run_id'],'fixture completed gate',
                {'type':'workflow_result.v1','result':results[name],'job_id':job['id'],'cancelled':False}),identity)
            (f.root/(name+'-runtime6-job.json')).write_text(canonical_json(job),encoding='utf-8')
        journal=WorkflowJournal(f.store,'task')
        state=journal.save({'task_id':'task','revision':0,'cancelled':False,'jobs':{},
            'limits':LIMITS,'spent':{'executions':11,'tokens':0,'time_s':19800.0}},'fixture original budget')
        (f.root/'colab-config-runtime6.json').write_text('{"runtime_id":"runtime-six","nonce":"six"}')
        report={'schema_version':1,'generation':'runtime6','runtime_id':'runtime-six',
            'status':'runtime6_prerequisites_verified','completed_views':4,
            'counts':{'verified':4,'pending':0,'failed_verification':0},
            'aggregate_gate_verification':'all_four_original_gates_verified','jobs':[]}
        for name,job in jobs.items():
            report['jobs'].append({'name':name,'verification':'verified','id':job['id'],
                'run_id':job['run_id'],'context_id':job['context_id'],'runtime_id':'runtime-six',
                'job_file_sha256':sha256((f.root/(name+'-runtime6-job.json')).read_bytes()).hexdigest(),
                'result_digest':sha256(canonical_json(results[name]).encode()).hexdigest()})
        path=f.root/'verification.json';path.write_text(canonical_json(report),encoding='utf-8')
        observation={'schema_version':1,'runtime_id':'runtime-six','observed_at_utc':'2026-11-13T15:50:28Z',
            'control_plane_reachable':True,'assignments':[],'driver_processes_stopped':True,
            'reason':'Fixture complete prerequisites, no exploration intake before loss'}
        gates=patch.object(api,'require_gates',return_value=(jobs,results));gates.start();self.addCleanup(gates.stop)
        prior=patch.object(api,'require_previous_archives',return_value=state);prior.start();self.addCleanup(prior.stop)
        return api,f,jobs,state,path,observation

    def test_only_notes_and_snapshot_no_run_or_budget_mutation(self):
        api,f,jobs,state,path,obs=self.fixture()
        before={r.id:r for r in f.store.history('task')}
        out=api.archive_completed_prerequisite_loss(f.store,f.root,'runtime6',obs,path)
        after={r.id:r for r in f.store.history('task')}
        self.assertTrue(all(after[k]==v for k,v in before.items()))
        self.assertEqual({type(v) for k,v in after.items() if k not in before},{Decision})
        self.assertEqual(len(after)-len(before),2)
        self.assertEqual(WorkflowJournal(f.store,'task').load(),state)
        self.assertEqual(out['summary']['exploration_run_ids'],[])
        count=len(after)
        self.assertEqual(api.archive_completed_prerequisite_loss(f.store,f.root,'runtime6',obs,path),out)
        self.assertEqual(len(f.store.history('task')),count)
        with patch.object(f.store,'_write',side_effect=AssertionError('readonly guard wrote')):
            self.assertEqual(api.require_completed_prerequisite_loss(f.store,f.root,'runtime6'),out)

    def test_extra_run_or_candidate_request_refuses_without_write(self):
        for mutation in ('run','orphan_request','orphan_receipt','intake'):
            with self.subTest(mutation=mutation):
                api,f,jobs,state,path,obs=self.fixture()
                if mutation=='run':f.store.append(Run('new-exploration','candidate-baseline-repair','context-six'),'new-exploration')
                elif mutation=='orphan_request':
                    f.store.append(Decision('orphan','task','note','task','fixture',{'type':'colab_execution_request.v1',
                        'runtime_id':'runtime-six','request':{'job':{'run_id':'missing-exploration','context_id':'context-six'}}}),'orphan')
                elif mutation=='orphan_receipt':
                    ref=f.store.artifacts.put(canonical_json({'context_id':'context-six',
                        'run_id':'missing-exploration','operation_id':'orphan-op','stage':'generate'}).encode())
                    f.store.append(Decision('orphan-receipt','task','note','task','fixture',
                        {'type':'execution_receipt.v1','artifact_ref':asdict(ref),'operation_id':'orphan-op',
                         'request_digest':'unknown'}),'orphan-receipt')
                else:
                    f.store.append(Decision('intake','task','note','task','fixture',{'type':'workflow_intake.v1',
                        'result':{'items':[{'jobs':[{'context_id':'context-six','run_id':'missing','variant':{'device':'runtime-six'}}]}]}}),'intake')
                count=len(f.store.history('task'))
                with self.assertRaises(ValueError):api.archive_completed_prerequisite_loss(f.store,f.root,'runtime6',obs,path)
                self.assertEqual(len(f.store.history('task')),count)

    def test_incomplete_misbound_report_live_runtime_or_stale_state_refused(self):
        for mutation in ('pending','binding','live','stale'):
            with self.subTest(mutation=mutation):
                api,f,jobs,state,path,obs=self.fixture()
                if mutation in ('pending','binding'):
                    report=json.loads(path.read_text());report['jobs'][0]['verification' if mutation=='pending' else 'run_id']='wrong';path.write_text(canonical_json(report))
                elif mutation=='live':obs['assignments']=[{'runtime_id':'runtime-six','session_names':[],'accelerator':'TPU','variant':'v6e'}]
                else:
                    original=f.store._write
                    def crash(*a,**kw):original(*a,**kw);raise RuntimeError('after intent')
                    with patch.object(f.store,'_write',side_effect=crash):
                        with self.assertRaises(RuntimeError):api.archive_completed_prerequisite_loss(f.store,f.root,'runtime6',obs,path)
                    changed=WorkflowJournal(f.store,'task').load();changed['served']={'changed':1}
                    WorkflowJournal(f.store,'task').save(changed,'fixture concurrent change')
                count=len(f.store.history('task'))
                with self.assertRaises(ValueError):api.archive_completed_prerequisite_loss(f.store,f.root,'runtime6',obs,path)
                self.assertEqual(len(f.store.history('task')),count)

    def test_crash_boundaries_and_output_recovery(self):
        for boundary in (1,2,3,4):
            api,f,jobs,state,path,obs=self.fixture();original=f.store._write;calls=[0]
            def crash(*a,**kw):
                result=original(*a,**kw);calls[0]+=1
                if calls[0]==boundary:raise RuntimeError('fixture crash')
                return result
            with patch.object(f.store,'_write',side_effect=crash):
                try:api.archive_completed_prerequisite_loss(f.store,f.root,'runtime6',obs,path)
                except RuntimeError:pass
            out=api.archive_completed_prerequisite_loss(f.store,f.root,'runtime6',obs,path)
            self.assertEqual(api.require_completed_prerequisite_loss(f.store,f.root,'runtime6'),out)
        api,f,jobs,state,path,obs=self.fixture()
        with patch.object(api,'write_once',side_effect=RuntimeError('output crash')):
            with self.assertRaises(RuntimeError):api.archive_completed_prerequisite_loss(f.store,f.root,'runtime6',obs,path)
        api.archive_completed_prerequisite_loss(f.store,f.root,'runtime6',obs,path)

    def test_late_run_and_changed_report_invalidates_guard(self):
        api,f,jobs,state,path,obs=self.fixture()
        api.archive_completed_prerequisite_loss(f.store,f.root,'runtime6',obs,path)
        f.store.append(Run('late-run','candidate-baseline-repair','context-six'),'late-run')
        with self.assertRaises(ValueError):api.require_completed_prerequisite_loss(f.store,f.root,'runtime6')

    def test_guard_allows_later_runtime_active_job_without_refund(self):
        api,f,jobs,state,path,obs=self.fixture()
        output=api.archive_completed_prerequisite_loss(f.store,f.root,'runtime6',obs,path)
        current=WorkflowJournal(f.store,'task').load()
        later=deepcopy(f.job())
        later.update(id='later-runtime-job',round_id='later-round',state='active',stage='compile',reserved=True)
        later['variant']['cost']={'executions':1,'tokens':0,'time_s':1800.0}
        current['jobs'][later['id']]=later
        current['spent']={'executions':12,'tokens':0,'time_s':21600.0}
        WorkflowJournal(f.store,'task').save(current,'fixture later Runtime active job')
        self.assertEqual(api.require_completed_prerequisite_loss(f.store,f.root,'runtime6'),output)


if __name__=='__main__':unittest.main(verbosity=2)
