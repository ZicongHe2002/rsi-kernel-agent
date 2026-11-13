"""Behavioral tests for the model/tool boundary and conservative evidence review."""
import copy
import importlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def proposal():
    return {
        'code_understanding': [dict(strategy_id=s, stages=['load', 'multiply', 'store'],
            loops=['k reduction'], movement=['A/B loads'], dependencies=['reduction'],
            layout_memory=['row-major assumption'], synchronization=['unknown'],
            edge_cases=['tail tiles'], refs=['source:' + s]) for s in 'AB'],
        'observations': [dict(id='o1', strategy_id='A', category='observed_fact',
            text='DMA is the proven bottleneck', refs=['source:A'], predicate=None)],
        'hypotheses': [dict(id='h1', strategy_id='A', text='Transfer overlap may help',
            refs=['source:A'], alternatives=['compute may dominate'])],
        'plans': [dict(id=s+'-P'+str(i), strategy_id=s, parent_id=s+'-parent',
            mechanism=m, modification=change, refs=['source:'+s], observation_ids=[],
            calculation_ids=[], tradeoffs=['extra resources'], risks=['tail handling'],
            unknowns=['hardware effect'], variants=[change+' minimal', change+' guarded'],
            validation={'correctness':['reference including tails'],
                        'performance':['same context repeated timing']})
            for s in 'AB' for i,m,change in ((1,'tile','change tile only'), (2,'pipeline','double buffer only'))],
        'unknowns':['No hardware validation'],
    }


def manifest():
    return {
        'schema_version':1, 'request':{'request_id':'round','task_id':'task','workload_id':'workload',
                                      'tool_versions':{'python':'test'}},
        'strategies':{s:{'strategy_id':s,'parent_id':s+'-parent',
            'parent':{'identity':{'source_digest':'0'*64,'config_digest':'1'*64,'build_digest':'2'*64}},
            'source':'def kernel(a, b):\n    return a @ b\n', 'config':'{}',
            'context':{'id':'context','provenance':'synthetic'},'run':None,
            'history':{'complete':True,'records':[],'total':0},'profiles':[],'benchmarks':[]}
            for s in 'AB'},
        'knowledge':{},'references':{**{'source:'+s:{'kind':'source','strategy_id':s,'value':'def kernel(a,b): return a @ b', 'availability':'available'} for s in 'AB'},
                                    'evidence:A-time':{'kind':'Evidence','strategy_id':'A','availability':'available','value':{'evidence_kind':'measured_timing','value':10,'unit':'ms','claim_status':'supported','request_status':'completed'}}},
        'issues':[], 'consistency':{'combined_atomic_snapshot':False}}


class AnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('rsi_kernel_agent.profiler.analyzer'),
                             'Phase 2C Analyzer must be implemented')
        self.api = importlib.import_module('rsi_kernel_agent.profiler.analyzer')
        self.outputs = importlib.import_module('rsi_kernel_agent.profiler.outputs')

    def test_model_selects_math_and_inspects_actual_source_before_multiple_plans(self):
        calls=[]
        def reasoner(packet):
            calls.append(copy.deepcopy(packet))
            if len(calls)==1:
                return {'tool_calls':[{'id':'read','name':'source','arguments':{'strategy_id':'A'}},
                    {'id':'calc','name':'calculate','arguments':{'model':'pipeline',
                        'inputs':{'stages':4,'transfer_s':2,'compute_s':3},
                        'evidence_refs':['source:A'],'assumptions':['Synthetic stage costs']}}]}
            return {'final':proposal()}
        result=self.api.Analyzer(manifest()).run(reasoner)
        self.assertEqual(len(result['plan_set']['plans']),4)
        self.assertEqual(calls[1]['tool_results'][0]['result']['source'], manifest()['strategies']['A']['source'])
        self.assertEqual(calls[1]['tool_results'][1]['result']['outputs']['ideal_pipeline_s'],14)
        self.assertEqual(result['tool_calls'][1]['name'],'calculate')
        self.assertFalse(result['winner_selected'])

    def test_unproved_cause_downgraded_not_deleted(self):
        out=self.outputs.review_proposal(proposal(),manifest(),{})
        self.assertEqual(out['observations'][0]['category'],'hypothesis')
        self.assertEqual(len(out['plans']),4)
        self.assertTrue(out['review'])

    def test_cross_route_measurement_reference_and_wrong_parent_rejected(self):
        p=proposal(); p['plans'][2]['refs']=['evidence:A-time']
        with self.assertRaises(ValueError): self.outputs.review_proposal(p,manifest(),{})
        p=proposal(); p['plans'][0]['parent_id']='B-parent'
        with self.assertRaises(ValueError): self.outputs.review_proposal(p,manifest(),{})

    def test_duplicate_cosmetic_plans_and_missing_strategy_rejected(self):
        p=proposal(); p['plans'][1]['mechanism']='tile'; p['plans'][1]['modification']='change tile only'
        with self.assertRaises(ValueError): self.outputs.review_proposal(p,manifest(),{})
        p=proposal(); p['plans']=p['plans'][:2]
        with self.assertRaises(ValueError): self.outputs.review_proposal(p,manifest(),{})

    def test_fake_calculation_unknown_refs_and_confidence_rejected(self):
        p=proposal(); p['plans'][0]['calculation_ids']=['made-up']
        with self.assertRaises(ValueError): self.outputs.review_proposal(p,manifest(),{})
        p=proposal(); p['plans'][0]['refs']=['fake']
        with self.assertRaises(ValueError): self.outputs.review_proposal(p,manifest(),{})
        p=proposal(); p['observations'][0]['confidence']=99
        with self.assertRaises(ValueError): self.outputs.review_proposal(p,manifest(),{})

    def test_unavailable_metric_predicate_cannot_prove_zero(self):
        m=manifest(); m['references']['metric:A']={'kind':'Evidence','strategy_id':'A','availability':'unavailable','value':None}
        p=proposal(); p['observations'][0].update(refs=['metric:A'],predicate={'ref':'metric:A','path':[],'value':0})
        out=self.outputs.review_proposal(p,m,{})
        self.assertEqual(out['observations'][0]['category'],'hypothesis')

    def test_bounded_loop_and_tool_errors_are_reported(self):
        calls=[]
        def reasoner(packet):
            calls.append(packet)
            return {'tool_calls':[{'id':str(len(calls)),'name':'execute_shell','arguments':{}}]}
        with self.assertRaises(ValueError): self.api.Analyzer(manifest(),max_turns=2).run(reasoner)
        self.assertIn('error',calls[1]['tool_results'][0])

    def test_equal_predicate_renders_verified_value_without_promoting_causal_prose(self):
        p=proposal(); p['observations'][0].update(refs=['evidence:A-time'],
            predicate={'ref':'evidence:A-time','path':['value'],'value':10})
        out=self.outputs.review_proposal(p,manifest(),{})
        self.assertEqual(out['observations'][0]['category'],'observed_fact')
        self.assertNotIn('bottleneck',out['observations'][0]['text'])
        self.assertIn('DMA',out['observations'][0]['proposed_explanation'])
        self.assertEqual(out['observations'][0]['explanation_status'],'hypothesis')

    def test_captured_history_and_method_never_become_observed_facts(self):
        for kind,category in [('History','historical_claim'),('MethodRecord','source_claim')]:
            m=manifest(); m['references']['extra']={'kind':kind,'strategy_id':None,'availability':'available','value':{'claim':'possible'}}
            p=proposal(); p['observations'][0].update(refs=['extra'],predicate={'ref':'extra','path':['claim'],'value':'possible'})
            out=self.outputs.review_proposal(p,m,{})
            self.assertEqual(out['observations'][0]['category'],category)

    def test_refuted_or_failed_evidence_cannot_prove_observation(self):
        for patch in ({'claim_status':'refuted'},{'request_status':'failed'}):
            m=manifest(); m['references']['evidence:A-time']['value'].update(patch)
            p=proposal(); p['observations'][0].update(refs=['evidence:A-time'],
                predicate={'ref':'evidence:A-time','path':['value'],'value':10})
            out=self.outputs.review_proposal(p,m,{})
            self.assertEqual(out['observations'][0]['category'],'hypothesis')

    def test_rejected_response_does_not_poison_accepted_exchange(self):
        analyzer=self.api.Analyzer(manifest())
        with self.assertRaises(ValueError): analyzer.advance({'final':{}})
        result=analyzer.advance({'final':proposal()})
        self.assertEqual(len(result['exchanges']),1)


if __name__=='__main__': unittest.main()
