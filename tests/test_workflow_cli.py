"""Unified executable entrypoint persists tasks and exposes recoverable status."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class WorkflowCLITests(unittest.TestCase):
    def test_create_and_status_work_in_fresh_interpreters_and_retry_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            spec={'task':{'id':'cli-task','name':'Synthetic CLI task','contract_revision':'v1'},
                  'workload':{'id':'cli-work','task_id':'cli-task','spec':{'synthetic':True}},
                  'context':{'id':'cli-context','task_id':'cli-task','workload_id':'cli-work',
                    'environment':{'device':'synthetic'},'protocol':{'timer':'synthetic'},'provenance':'synthetic'},
                  'strategies':[{'id':x,'task_id':'cli-task','name':x} for x in 'ABCDE']}
            path=root/'task.json';path.write_text(json.dumps(spec),encoding='utf-8')
            env={**os.environ,'PYTHONPATH':str(ROOT/'src'),'PYTHONUTF8':'1'}
            command=[sys.executable,'-B','-m','rsi_kernel_agent.workflow','--db',str(root/'db.sqlite'),
                     '--artifacts',str(root/'artifacts')]
            for _ in range(2):
                done=subprocess.run(command+['create','--spec',str(path)],env=env,capture_output=True,text=True)
                self.assertEqual(done.returncode,0,done.stderr)
                self.assertEqual(json.loads(done.stdout)['task_id'],'cli-task')
            done=subprocess.run(command+['status','--task','cli-task'],env=env,capture_output=True,text=True)
            self.assertEqual(done.returncode,0,done.stderr)
            self.assertEqual(json.loads(done.stdout)['jobs'],{})
            done=subprocess.run(command+['cards','--task','cli-task','--context','cli-context'],env=env,capture_output=True,text=True)
            self.assertEqual(done.returncode,0,done.stderr)
            self.assertEqual(len(json.loads(done.stdout)['cards']),5)


if __name__=='__main__':unittest.main()
