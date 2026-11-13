"""Objective fixed-case rubric and replay checks, never model self-ratings."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from test_analyzer import manifest, proposal


class AnalyzerEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('rsi_kernel_agent.profiler.evaluation'),
                             'Fixed Analyzer evaluator is not implemented')
        from rsi_kernel_agent.profiler import evaluation
        self.api = evaluation
        self.cases = self.api.load_cases()

    def answer(self, case):
        gold = case['expected']
        return dict(verdict=gold['verdict'], category=gold['categories'][0], refs=['case:'+case['id']],
                    reason='The cited scenario separates the supplied observation from the proposition.',
                    controls=gold['controls'], numeric_result=gold['numeric_result'])

    def result(self, m=None):
        from rsi_kernel_agent.profiler.analyzer import Analyzer
        return Analyzer(m or manifest()).run(lambda packet: {'final':proposal()})

    def test_prompt_safe_cases_hide_gold_and_preserve_independent_synthetic_facts(self):
        safe = self.api.load_cases(include_expected=False)
        self.assertEqual(len(safe), 10)
        self.assertEqual(len({c['id'] for c in safe}), 10)
        self.assertTrue(all('expected' not in c and c['provenance']=='synthetic' for c in safe))
        self.assertIsNone(next(c for c in safe if c['id']=='missing-not-zero')['facts']['wait_us'])
        m = self.api.prepare_evaluation_manifest(manifest(), safe)
        self.assertEqual(m['strategies'], manifest()['strategies'])
        self.assertEqual(m['references']['case:profile-mismatch']['strategy_id'], 'B')
        m['references']['case:missing-not-zero']['value']['wait_us'] = 0
        self.assertIsNone(next(c for c in safe if c['id']=='missing-not-zero')['facts']['wait_us'])

    def test_all_ten_independent_rubrics_accept_expected_judgments_and_reject_opposites(self):
        m = self.api.prepare_evaluation_manifest(manifest(), self.cases)
        result = self.result(m)
        for case in self.cases:
            with self.subTest(case=case['id']):
                answer = self.answer(case)
                good = self.api.evaluate_result(case, result, m, case_answer=answer)
                self.assertTrue(good['case_passed'])
                wrong = copy.deepcopy(answer)
                wrong['verdict'] = 'supported'
                bad = self.api.evaluate_result(case, result, m, case_answer=wrong)
                self.assertFalse(bad['case_passed'])
                self.assertIn('verdict', bad['case_failures'])

    def test_numeric_rubric_rejects_wrong_units_and_nested_duration_sum(self):
        case = next(c for c in self.cases if c['id']=='nested-overlap')
        m = self.api.prepare_evaluation_manifest(manifest(), self.cases)
        answer = self.answer(case)
        answer['numeric_result'] = {'value':17,'unit':'us'}
        self.assertIn('numeric_result',self.api.evaluate_result(case,self.result(m),m,case_answer=answer)['case_failures'])
        answer['numeric_result'] = {'value':12,'unit':'ms'}
        self.assertIn('numeric_result',self.api.evaluate_result(case,self.result(m),m,case_answer=answer)['case_failures'])

    def test_reference_binding_and_missing_uncertainty_are_measured_independently(self):
        case = self.cases[0]
        m = self.api.prepare_evaluation_manifest(manifest(), self.cases)
        result = self.result(m)
        result['plan_set']['plans'][2]['parent_id'] = 'A-parent'
        result['plan_set']['plans'][0]['refs'] = ['missing-ref']
        answer = self.answer(case)
        answer['refs'] = ['case:profile-mismatch']
        answer['category'] = 'observed_fact'
        score = self.api.evaluate_result(case,result,m,case_answer=answer)
        self.assertLess(score['reference_validity']['value'],1)
        self.assertEqual(score['binding']['value'],0.75)
        self.assertEqual(score['uncertainty']['value'],0)

    def test_unsupported_facts_are_not_excused_by_existent_citation_or_self_rating(self):
        from rsi_kernel_agent.profiler.analyzer import Analyzer
        m = self.api.prepare_evaluation_manifest(manifest(), self.cases)
        result = Analyzer(m,configuration='base').run(lambda packet:{'final':proposal()})
        result['self_rating'] = 100
        score = self.api.evaluate_result(self.cases[0],result,m,case_answer=self.answer(self.cases[0]))
        self.assertEqual(score['unsupported_claim_rate']['value'],1)
        self.assertIn('prose_semantics_unscored',score['limitations'])
        result = self.result(m)
        score = self.api.evaluate_result(self.cases[0],result,m,case_answer=self.answer(self.cases[0]))
        self.assertEqual(score['unsupported_claim_rate']['denominator'],0)
        self.assertIsNone(score['unsupported_claim_rate']['value'])

    def test_captured_history_and_method_kinds_preserve_claim_categories(self):
        from rsi_kernel_agent.profiler.analyzer import Analyzer
        m = self.api.prepare_evaluation_manifest(manifest(),self.cases)
        for kind, category in (('History','historical_claim'),('MethodRecord','source_claim')):
            with self.subTest(kind=kind):
                m['references']['typed:A'] = dict(kind=kind, strategy_id='A', availability='available', value={'count':2})
                p = proposal()
                p['observations'][0].update(category=category,refs=['typed:A'],
                    predicate={'ref':'typed:A','path':['count'],'value':2})
                result = Analyzer(m).run(lambda packet:{'final':p})
                self.assertEqual(result['analysis']['observations'][0]['category'],category)
                score = self.api.evaluate_result(self.cases[0],result,m,case_answer=self.answer(self.cases[0]))
                self.assertEqual(score['unsupported_claim_rate']['value'],0)

    def test_duplicate_plans_and_missing_route_reduce_diversity(self):
        case = next(c for c in self.cases if c['id']=='multi-strategy-plans')
        m = self.api.prepare_evaluation_manifest(manifest(),self.cases)
        result = self.result(m)
        result['plan_set']['plans'][1] = copy.deepcopy(result['plan_set']['plans'][0])
        result['plan_set']['plans'] = result['plan_set']['plans'][:2]
        score = self.api.evaluate_result(case,result,m,case_answer=self.answer(case))
        self.assertGreater(score['duplicate_rate']['value'],0)
        self.assertLess(score['diversity']['value'],1)
        self.assertIn('minimum_plans_per_strategy',score['case_failures'])

    def test_tool_replay_detects_changed_numbers_and_no_tools_is_not_perfect_replay(self):
        from rsi_kernel_agent.profiler.analyzer import Analyzer
        m = self.api.prepare_evaluation_manifest(manifest(),self.cases)
        analyzer = Analyzer(m)
        analyzer.advance({'tool_calls':[{'id':'pipeline','name':'calculate','arguments':{
            'model':'pipeline','inputs':{'stages':4,'transfer_s':3,'compute_s':2},
            'evidence_refs':['case:prediction-not-measurement'],'assumptions':['hypothetical stage costs']}}]})
        result = analyzer.advance({'final':proposal()})
        score = self.api.evaluate_result(self.cases[4],result,m,case_answer=self.answer(self.cases[4]))
        self.assertEqual(score['tool_reproducibility']['value'],1)
        result['tool_calls'][0]['result']['outputs']['ideal_pipeline_s'] = 13
        score = self.api.evaluate_result(self.cases[4],result,m,case_answer=self.answer(self.cases[4]))
        self.assertEqual(score['tool_reproducibility']['value'],0)
        score = self.api.evaluate_result(self.cases[4],self.result(m),m,case_answer=self.answer(self.cases[4]))
        self.assertIsNone(score['tool_reproducibility']['value'])
        self.assertEqual(score['tool_reproducibility']['denominator'],0)

    def test_changed_or_invented_calculation_registry_is_not_reproducible(self):
        from rsi_kernel_agent.profiler.analyzer import Analyzer
        m = self.api.prepare_evaluation_manifest(manifest(),self.cases)
        analyzer = Analyzer(m)
        analyzer.advance({'tool_calls':[{'id':'p','name':'calculate','arguments':{
            'model':'pipeline','inputs':{'stages':4,'transfer_s':3,'compute_s':2},
            'evidence_refs':['case:prediction-not-measurement'],'assumptions':['hypothetical stage costs']}}]})
        result = analyzer.advance({'final':proposal()})
        result['calculations']['p']['outputs']['ideal_pipeline_s'] = 7
        score = self.api.evaluate_result(self.cases[4],result,m,case_answer=self.answer(self.cases[4]))
        self.assertEqual(score['tool_reproducibility']['value'],0)
        result['tool_calls'] = []
        score = self.api.evaluate_result(self.cases[4],result,m,case_answer=self.answer(self.cases[4]))
        self.assertEqual(score['tool_reproducibility']['value'],0)
        self.assertEqual(score['tool_reproducibility']['denominator'],1)

    def test_suite_keeps_case_results_and_malformed_answer_fails_explicitly(self):
        m = self.api.prepare_evaluation_manifest(manifest(),self.cases)
        answers = {c['id']:self.answer(c) for c in self.cases}
        summary = self.api.evaluate_suite(self.cases,self.result(m),m,answers)
        self.assertEqual(summary['passed_cases'],10)
        self.assertEqual(summary['total_cases'],10)
        self.assertEqual(summary['provenance'],'synthetic')
        answers['dma-count'] = {'self_rating':1}
        summary = self.api.evaluate_suite(self.cases,self.result(m),m,answers)
        self.assertEqual(summary['passed_cases'],9)
        self.assertIn('answer_schema',summary['cases'][0]['case_failures'])

    def test_answer_rubric_does_not_mask_invalid_analyzer_output(self):
        m=self.api.prepare_evaluation_manifest(manifest(),self.cases)
        result=self.result(m)
        for plan in result['plan_set']['plans']:
            plan['parent_id']='wrong'; plan['refs']=['nonexistent']
        summary=self.api.evaluate_suite(self.cases,result,m,{c['id']:self.answer(c) for c in self.cases})
        self.assertFalse(summary.get('overall_accepted',True))
        self.assertIn('binding',summary['acceptance_failures'])


if __name__=='__main__':
    unittest.main()
