"""Regression for recovery CLI mutating a task without an admitted journal."""
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE),str(HERE.parents[1]/'src')]
from rsi_kernel_agent.contracts import Task
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.workflow.journal import WorkflowJournal

class GuardTest(unittest.TestCase):
    def test_missing_journal_does_not_initialize_budget_or_call_remote(self):
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp)/'.artifacts'/'phase3';d.mkdir(parents=True)
            target=d/'real-round-1';target.mkdir()
            (target/'intake.json').write_text('{"task_id":"task","id":"not-admitted"}',encoding='utf-8')
            store=ExperimentStore(d/'experiments.sqlite3',d/'artifacts')
            store.append(Task('task','Test','fixed'),'create-task')
            script=(HERE/'resume_active_round.py').read_text('utf-8')
            if os.environ.get('RSI_TEST_OLD_RESUME')=='1':
                script='\n'.join(line for line in script.splitlines() if not line.startswith(('require((d/', 'saved=WorkflowJournal', 'require(saved')))
            path=d/'resume_active_round.py';path.write_text(script,encoding='utf-8')
            before=len(store.history('task'))
            with patch.object(sys,'argv',[str(path),'1']),patch('rsi_kernel_agent.workflow.__main__.colab_remote',return_value=None) as remote:
                with self.assertRaises(ValueError): runpy.run_path(str(path),run_name='__main__')
                self.assertIsNone(WorkflowJournal(store,'task').load())
                self.assertEqual(len(store.history('task')),before)
                remote.assert_not_called()

if __name__=='__main__':unittest.main(verbosity=2)
