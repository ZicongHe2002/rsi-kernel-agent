"""Durable workflow state, optimistic concurrency and process lock boundaries."""
import copy
import importlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rsi_kernel_agent.contracts import Decision, Task
from rsi_kernel_agent.store import ConflictError, ExperimentStore


class WorkflowJournalTests(unittest.TestCase):
    def setUp(self):
        try:
            self.api = importlib.import_module('rsi_kernel_agent.workflow.journal')
        except ImportError:
            self.fail('Workflow journal implementation is absent')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = ExperimentStore(self.root/'store.db', self.root/'artifacts')
        self.store.append(Task('task', 'Synthetic task', 'v1'), 'task')
        self.journal = self.api.WorkflowJournal(self.store, 'task')

    def test_restart_replays_detached_history_and_rejects_stale_save(self):
        self.assertIsNone(self.journal.load())
        initial = {'task_id':'task', 'jobs':{}, 'spent':{'tokens':0}}
        first = self.journal.save(initial, 'Initialize')
        stale = copy.deepcopy(first)
        first['jobs']['one'] = {'state':'queued'}
        second = self.journal.save(first, 'Queue job')
        reopened = self.api.WorkflowJournal(ExperimentStore(self.root/'store.db', self.root/'artifacts'), 'task')
        self.assertEqual(reopened.load()['jobs'], {'one':{'state':'queued'}})
        second['jobs'].clear()
        self.assertTrue(reopened.load()['jobs'])
        stale['spent']['tokens'] = 100
        with self.assertRaises(ConflictError): reopened.save(stale, 'Stale writer')
        self.assertEqual(initial, {'task_id':'task','jobs':{},'spent':{'tokens':0}})

    def test_commit_response_loss_retries_original_state_without_another_event(self):
        initial = {'task_id':'task','jobs':{}}
        original = self.journal.save(initial, 'Initialize')
        retried = self.journal.save(initial, 'Initialize')
        self.assertEqual(retried, original)
        self.assertEqual(len(self.store.history('task')), 2)
        with self.assertRaises(ConflictError): self.journal.save(initial, 'Different operation')

    def test_invalid_journal_event_is_not_ignored(self):
        self.journal.save({'task_id':'task','jobs':{}}, 'Initialize')
        self.store.append(Decision('bad-journal', 'task','note','task','Malformed state',
            {'type':'workflow_state.v1','state':{'task_id':'task'}}), 'bad-journal')
        with self.assertRaises(ValueError): self.journal.load()

    def test_lock_is_exclusive_across_objects_and_releases_on_process_death(self):
        other = self.api.WorkflowJournal(self.store, 'task')
        with self.journal.lock():
            with self.assertRaises(ValueError):
                with other.lock(): pass
        with other.lock(): pass
        script = """import os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.workflow.journal import WorkflowJournal
s=ExperimentStore(sys.argv[2],sys.argv[3])
with WorkflowJournal(s,'task').lock():
 print('locked',flush=True)
 os._exit(17)
"""
        proc = subprocess.run([sys.executable,'-B','-c',script,str(Path(__file__).resolve().parents[1]/'src'),
            str(self.root/'store.db'),str(self.root/'artifacts')],capture_output=True,text=True,timeout=20)
        self.assertEqual(proc.returncode,17,proc.stderr)
        self.assertIn('locked',proc.stdout)
        with self.journal.lock(): pass


if __name__ == '__main__': unittest.main()
