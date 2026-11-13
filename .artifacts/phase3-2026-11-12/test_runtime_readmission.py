"""CPU tests: a replacement Runtime cannot inherit old execution evidence."""
from copy import deepcopy
import json
import importlib
from pathlib import Path
import sys
import tempfile
import unittest
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1]/'src'), str(HERE.parents[1]/'tests')]
from phase3_fixtures import RealProtocolFixture

class ReadmissionTests(RealProtocolFixture, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.prepare(self.temp.name)
        try:
            self.api = importlib.import_module('runtime_readmission')
        except ImportError:
            self.fail('Replacement Runtime admission is missing')

    def test_remeasure_preserves_source_and_old_result_but_changes_operation(self):
        old, receipts = self.execute_fixture()
        old.update(state='completed', stage='evaluate', receipts=receipts,
                   result={'old': True}, operation={'old': True}, reserved=True)
        before = deepcopy(old)
        env = {**self.environment, 'runtime_session_id': 'new-fixture-session'}
        new = self.api.remeasure(self.store, old, env)
        self.assertEqual(old, before)
        self.assertEqual(new['candidate_id'], old['candidate_id'])
        self.assertEqual(new['variant']['source_ref'], old['variant']['source_ref'])
        self.assertNotEqual(new['run_id'], old['run_id'])
        self.assertNotEqual(new['context_id'], old['context_id'])
        self.assertEqual(new['receipts'], {})
        self.assertIsNone(new['result'])
        self.assertNotIn('operation', new)
        self.assertFalse(new['reserved'])
        self.assertEqual(self.store.run_state(new['run_id'])['execution_status'], 'not_run')
        count = len(self.store.history('task'))
        self.assertEqual(self.api.remeasure(self.store, old, env), new)
        self.assertEqual(len(self.store.history('task')), count)

    def test_same_runtime_rejected_without_writes(self):
        old = self.job()
        count = len(self.store.history('task'))
        with self.assertRaises(ValueError):
            self.api.remeasure(self.store, old, self.environment)
        self.assertEqual(len(self.store.history('task')), count)

    def test_wrong_generation_rejected_before_remote_work(self):
        path=self.root/'colab-config-runtime2.json'
        path.write_text('{"runtime_id":"old"}')
        with self.assertRaises(ValueError):
            self.api.pin_observation_runtime(self.root,'-runtime2',{'runtime_id':'new','status':'allocated'})
        self.assertFalse((self.root/'observation-runtime2.json').exists())
        self.api.pin_observation_runtime(self.root,'-runtime3',{'runtime_id':'new','status':'allocated'})
        with self.assertRaises(ValueError):
            self.api.pin_observation_runtime(self.root,'-runtime3',{'runtime_id':'third','status':'allocated'})

    def test_observation_updates_archive_original_bytes(self):
        path=self.root/'setup.json'
        original=b'{"status": "running"}\n'
        path.write_bytes(original)
        self.api.save_observation(path,{'status':'ready'})
        self.assertEqual(json.loads(path.read_bytes()),{'status':'ready'})
        archives=list(self.root.glob('setup.json.*.original'))
        self.assertEqual(len(archives),1)
        self.assertEqual(archives[0].read_bytes(),original)

    def test_wrong_candidate_binding_rejected_without_writes(self):
        old = self.job(); old['candidate_id'] = 'nonexistent'
        count = len(self.store.history('task'))
        with self.assertRaises(ValueError):
            self.api.remeasure(self.store, old, {**self.environment, 'runtime_session_id':'new'})
        self.assertEqual(len(self.store.history('task')), count)

if __name__ == '__main__': unittest.main(verbosity=2)
