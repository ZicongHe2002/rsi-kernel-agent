"""Admission respects actual shared budgets, configured fairness and pauses."""
import copy
import importlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))


def job(identifier, strategy, priority=0, cost=None):
    return {'id':identifier,'strategy_id':strategy,'state':'queued','stage':'generate','reserved':False,
        'variant':{'priority':priority,'cost':cost or {'executions':1,'tokens':10,'time_s':1}}}


class WorkflowSchedulingTests(unittest.TestCase):
    def setUp(self):
        try: self.api=importlib.import_module('rsi_kernel_agent.workflow.scheduling')
        except ImportError: self.fail('Workflow scheduler implementation is absent')

    def state(self, jobs, limits=None):
        return {'task_id':'task','jobs':{j['id']:j for j in jobs},'limits':self.api.validate_limits(limits or {}),
            'spent':{'executions':0,'tokens':0,'time_s':0},'served':{},'paused':[],'cancelled':False}

    def test_admission_priority_fairness_and_pending_slots(self):
        state=self.state([job('a-low','A'),job('a-high','A',9),job('b','B')])
        self.assertEqual(self.api.choose_jobs(state),['a-high','b'])
        self.assertEqual(state['spent'],{'executions':2,'tokens':20,'time_s':2})
        self.assertEqual(self.api.choose_jobs(state),[])
        state['jobs']['a-high']['state']='blocked'
        state['jobs']['b']['state']='evaluating'
        self.assertEqual(self.api.choose_jobs(state),[])
        state['jobs']['b']['state']='completed'
        self.assertEqual(self.api.choose_jobs(state),['a-low'])

    def test_weighted_service_has_no_fixed_strategy_ratio(self):
        state=self.state([job('a'+str(i),'A') for i in range(8)]+[job('b'+str(i),'B') for i in range(8)],
            {'max_concurrency':1,'weights':{'A':2,'B':1}})
        selected=[]
        for _ in range(6):
            identifier=self.api.choose_jobs(state)[0]
            selected.append(state['jobs'][identifier]['strategy_id'])
            state['jobs'][identifier]['state']='completed'
        self.assertEqual(selected,['A','B','A','A','B','A'])

    def test_exhausted_budget_defers_without_partial_charge_and_can_fit_other_job(self):
        state=self.state([job('expensive','A',9,{'executions':2,'tokens':10,'time_s':1}),job('cheap','B')],
            {'executions':1})
        self.assertEqual(self.api.choose_jobs(state),['cheap'])
        self.assertEqual(state['jobs']['expensive']['state'],'deferred')
        self.assertIn('executions',state['jobs']['expensive']['reason'])
        self.assertEqual(state['spent'],{'executions':1,'tokens':10,'time_s':1})
        self.assertEqual(self.api.remaining_budget(state)['executions'],0)
        self.assertTrue(self.api.reserve(state,state['jobs']['cheap']))
        self.assertEqual(state['spent']['executions'],1)

    def test_pause_and_cancellation_do_not_charge_or_launch(self):
        state=self.state([job('a','A'),job('b','B')])
        state['paused']=['A']
        self.assertEqual(self.api.choose_jobs(state),['b'])
        self.assertEqual(state['jobs']['a']['state'],'queued')
        state['cancelled']=True
        state['jobs']['b']['state']='completed'
        state['paused']=[]
        self.assertEqual(self.api.choose_jobs(state),[])
        self.assertEqual(state['spent']['executions'],1)

    def test_invalid_limits_costs_and_spending_cannot_bypass_budget(self):
        for invalid in ({'max_concurrency':0},{'executions':True},{'tokens':-1},{'time_s':float('nan')},
                        {'weights':{'A':0}},{'unexpected':1}):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError): self.api.validate_limits(invalid)
        state=self.state([job('a','A')])
        state['jobs']['a']['variant']['cost']['executions']=-1
        original=copy.deepcopy(state)
        with self.assertRaises(ValueError): self.api.choose_jobs(state)
        self.assertEqual(state,original)
        state=self.state([job('a','A')]);state['spent']['tokens']=-10
        with self.assertRaises(ValueError): self.api.choose_jobs(state)
        state=self.state([job('a','A')]);state['jobs']['a']['variant']['cost']['executions']=0
        with self.assertRaises(ValueError): self.api.choose_jobs(state)


if __name__ == '__main__': unittest.main()
