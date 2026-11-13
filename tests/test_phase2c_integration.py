"""End-to-end offline analysis through actual store, artifacts and KnowledgeClient."""
import copy
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from phase2c_fixtures import make_analysis_fixture
from test_analyzer import proposal
from rsi_kernel_agent.profiler.inputs import AnalysisRequest, capture_inputs, load_manifest
from rsi_kernel_agent.profiler.inputs import AnalysisInputManifest
from rsi_kernel_agent.contracts import canonical_json
from rsi_kernel_agent.contracts import Evidence, ProfileBundle
from rsi_kernel_agent.profiler.analyzer import Analyzer
from rsi_kernel_agent.profiler.outputs import publish_result
from rsi_kernel_agent.profiler.tools import AnalysisTools


class Phase2CIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_predicted_trace_cannot_be_reclassified_as_measured(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=make_analysis_fixture(tmp); store=f['store']
            store.append(Evidence('prediction','A-run','model_predicted','available','completed','supported',
                {'target':'hypothetical'},artifacts=store.get('A-trace').artifacts,source={'provenance':'synthetic'}),'prediction')
            store.append(ProfileBundle('predicted-profile','A-run',('prediction',),'available'),'predicted-profile')
            snapshot=store.create_snapshot('task','predicted-snapshot')
            f['request']['strategies'][0].update(snapshot_id=snapshot['id'],profile_ids=['predicted-profile'])
            manifest=await capture_inputs(store,f['knowledge'],AnalysisRequest.from_dict(f['request']))
            result=AnalysisTools(manifest.to_dict(),store.artifacts).call('profile',{'strategy_id':'A','profile_id':'predicted-profile'})
            self.assertEqual(result['availability'],'unavailable')
            store.append(Evidence('derived-prediction','A-run','derived_measurement','available','completed','supported',
                {'target':'hypothetical'},artifacts=store.get('A-trace').artifacts,input_ids=('prediction',),source={'provenance':'synthetic'}),'derived-prediction')
            store.append(ProfileBundle('derived-profile','A-run',('derived-prediction',),'available'),'derived-profile')
            snapshot=store.create_snapshot('task','derived-snapshot')
            f['request']['strategies'][0].update(snapshot_id=snapshot['id'],profile_ids=['derived-profile'])
            manifest=await capture_inputs(store,f['knowledge'],AnalysisRequest.from_dict(f['request']))
            result=AnalysisTools(manifest.to_dict(),store.artifacts).call('profile',{'strategy_id':'A','profile_id':'derived-profile'})
            self.assertEqual(result['availability'],'unavailable')

    async def test_unverified_manifest_body_cannot_override_real_artifact_during_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=make_analysis_fixture(tmp); store=f['store']
            manifest=await capture_inputs(store,f['knowledge'],AnalysisRequest.from_dict(f['request']))
            raw=manifest.to_dict(); raw['references']['evidence:A-timing']['value']['value']=999
            forged=AnalysisInputManifest(canonical_json(raw),manifest.artifact_ref)
            p=proposal(); p['observations'][0].update(refs=['evidence:A-timing'],predicate={'ref':'evidence:A-timing','path':['value'],'value':999})
            result=Analyzer(forged,artifacts=store.artifacts).run(lambda packet:{'final':p})
            with self.assertRaises(ValueError): publish_result(store,forged,result,'forged-body')

    async def test_missing_profile_cannot_claim_available_outer_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=make_analysis_fixture(tmp); store=f['store']
            store.artifacts.path_for(store.get('A-trace').artifacts[0]).unlink()
            manifest=await capture_inputs(store,f['knowledge'],AnalysisRequest.from_dict(f['request']))
            result=AnalysisTools(manifest.to_dict(),store.artifacts).call('profile',{'strategy_id':'A','profile_id':'A-profile'})
            self.assertEqual(result['availability'],'unavailable')

    async def test_pinned_profile_math_multiplan_publish_retry_and_best_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=make_analysis_fixture(tmp); store=f['store']
            manifest=await capture_inputs(store,f['knowledge'],AnalysisRequest.from_dict(f['request']))
            before=store.best('task','context')
            turns=[]
            def model(packet):
                turns.append(packet)
                if len(turns)==1:
                    return {'tool_calls':[
                        {'id':'A-profile','name':'profile','arguments':{'strategy_id':'A','profile_id':'A-profile'}},
                        {'id':'A-history','name':'history','arguments':{'strategy_id':'A'}},
                        {'id':'B-history','name':'history','arguments':{'strategy_id':'B'}},
                        {'id':'calc','name':'calculate','arguments':{'model':'tile','inputs':{'m':32,'n':32,'k':64,'tile_m':16,'tile_n':16,'tile_k':16,'dtype_bytes':4},'evidence_refs':['source:A'],'assumptions':['Synthetic matrix dimensions']}}]}
                p=proposal(); p['plans'][0]['calculation_ids']=['calc']
                return {'final':p}
            result=Analyzer(manifest,artifacts=store.artifacts).run(model)
            self.assertNotIn('error',turns[1]['tool_results'][0])
            normalized=turns[1]['tool_results'][0]['result']['reports'][0]
            self.assertEqual(normalized['binding']['candidate_id'],'A-candidate')
            self.assertEqual(normalized['capabilities']['dma']['availability'],'unavailable')
            self.assertTrue(turns[1]['tool_results'][2]['result']['complete'])
            decision=publish_result(store,manifest,result,'example')
            self.assertEqual(publish_result(store,manifest,result,'example'),decision)
            self.assertEqual(len(decision.details['bundles']),2)
            self.assertEqual(len(decision.details['plans']),4)
            self.assertEqual(store.best('task','context'),before)
            self.assertEqual(load_manifest(store,manifest.artifact_ref).to_dict(),manifest.to_dict())
            tampered=copy.deepcopy(result)
            tampered['analysis']['observations'][0]['category']='observed_fact'
            with self.assertRaises(ValueError): publish_result(store,manifest,tampered,'forged-observation')
            tampered=copy.deepcopy(result)
            tampered['calculations']['calc']['outputs']['tiled_total_bytes']=0
            with self.assertRaises(ValueError): publish_result(store,manifest,tampered,'forged-calculation')
            result['plan_set']['plans'][0]['parent_id']='B-parent'
            with self.assertRaises(ValueError): publish_result(store,manifest,result,'tampered')

    async def test_session_exchange_replays_offline_and_rejects_output_edit(self):
        self.assertIsNotNone(importlib.util.find_spec('rsi_kernel_agent.profiler.session'),
                             'Conversation-model exchange transport must exist')
        api=importlib.import_module('rsi_kernel_agent.profiler.session')
        with tempfile.TemporaryDirectory() as tmp:
            f=make_analysis_fixture(tmp); store=f['store']
            manifest=await capture_inputs(store,f['knowledge'],AnalysisRequest.from_dict(f['request']))
            directory=Path(tmp)/'exchange'
            api.start_session(store,manifest,directory,model_label='current conversation model')
            packet=json.loads((directory/'packet.json').read_text(encoding='utf-8'))
            self.assertEqual(packet['manifest_digest'],manifest.artifact_ref.digest)
            response=directory/'answer.json'
            response.write_text(json.dumps({'tool_calls':[{'id':'inspect','name':'source','arguments':{'strategy_id':'B'}}]}),encoding='utf-8')
            self.assertIsNone(api.submit_response(store,directory,response))
            response.write_text(json.dumps({'final':proposal()}),encoding='utf-8')
            result=api.submit_response(store,directory,response)
            self.assertEqual(len(result['plan_set']['plans']),4)
            self.assertTrue((directory/'result.json').is_file())
            with self.assertRaises(ValueError): api.submit_response(store,directory,response)


if __name__=='__main__': unittest.main()
