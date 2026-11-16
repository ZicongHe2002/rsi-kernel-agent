"""CPU original-Controller fixtures for resuming an admitted R6 subbatch."""
import ast
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import importlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / 'src'), str(ROOT / 'tests')]
from phase3_fixtures import RealProtocolFixture
from rsi_kernel_agent.contracts import Decision, ParentCandidate, Plan, canonical_json
from rsi_kernel_agent.workflow.controller import Controller
from rsi_kernel_agent.workflow.evaluation import integrate_result
from real_round_aux import LIMITS


class ResumeRoundTests(RealProtocolFixture, unittest.TestCase):
    generation = 'runtime6'

    def setUp(self):
        tree = ast.parse((HERE / 'resume_active_round.py').read_text('utf-8'))
        self.assertTrue(any(isinstance(node, ast.FunctionDef) and node.name == 'resume' for node in tree.body),
                        'Resume must expose an import-safe API before fixture import')
        self.api = importlib.import_module('resume_active_round')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.prepare(self.directory)
        destination = self.directory / 'experiments.sqlite3'
        self.store.db_path.rename(destination)
        self.store.db_path = destination
        self.rid = 'real-' + self.generation + '-recovery-round:2'
        self.target = self.directory / ('real-round-2-' + self.generation)
        self.target.mkdir()
        baseline, receipts = self.execute_fixture()
        result = integrate_result(self.store, baseline, receipts)
        self.store.append(ParentCandidate('selected-A', 'task', self.identity, baseline['candidate_id']), 'selected-A')
        self.save(self.directory / ('baseline-repair-' + self.generation + '-job.json'), baseline)
        self.save(self.target / 'runtime-binding.json', {'generation': self.generation})
        self.save(self.directory / ('colab-config-' + self.generation + '.json'), {'runtime_id': self.environment['runtime_session_id']})
        manifest = self.put(b'CPU immutable manifest fixture')
        result_ref = self.put(b'CPU immutable Analyzer result fixture')
        mapping, items, variants = [], [], {}
        for proposal, label in (('R2-A-length-row', 'length-row'), ('R2-A-length-smem', 'length-smem')):
            plan_id = 'published-' + label
            self.store.append(Plan(plan_id, 'A', 'CPU fixture proposal'), plan_id)
            variant = {**deepcopy(self.variant), 'label': label,
                       'cost': {'executions': 1, 'tokens': 0, 'time_s': 1800}}
            raw = {k: v for k, v in baseline.items() if k not in ('candidate_id', 'run_id')}
            raw.update(id='job-' + label, round_id=self.rid, plan_id=plan_id,
                       parent_id='selected-A', parent_evaluation_id=result['evaluation_id'], variant=variant)
            items.append({'proposal_id': proposal, 'plan_id': plan_id, 'strategy_id': 'A',
                          'status': 'accepted', 'jobs': [raw]})
            mapping.append({'proposal_id': proposal, 'record_id': plan_id})
            variants[proposal] = [variant]
        self.publication = Decision('publication', 'task', 'note', 'task', 'CPU publication fixture',
            {'type': 'analysis_result.v1', 'manifest': manifest, 'result': result_ref, 'plans': mapping, 'bundles': []})
        self.store.append(self.publication, self.publication.id)
        self.intake = {'id': 'intake', 'task_id': 'task', 'round_id': self.rid,
                       'publication_id': self.publication.id, 'items': items}
        self.store.append(Decision('intake', 'task', 'note', 'task', 'CPU intake fixture',
            {'type': 'workflow_intake.v1', 'request_digest': 'f' * 64, 'result': self.intake}), 'intake')
        self.save(self.target / 'intake.json', self.intake)
        self.save(self.target / 'manifest-ref.json', manifest)
        self.save(self.target / 'execution-input.json', {'round_id': self.rid, 'manifest': manifest,
            'result_digest': result_ref['digest'], 'variants': variants,
            'publication_key': self.rid + ':' + manifest['digest']})
        owner = self
        class Backend:
            unknown = True
            submissions = []
            lookups = []
            results = {}
            def lookup(self, op):
                self.lookups.append(op)
                if self.unknown and op in self.results:
                    return {'status': 'unknown'}
                return self.results.get(op, {'status': 'definitely_not_started'})
            def submit(self, request):
                self.submissions.append(request['operation_id'])
                owner.variant = request['job']['variant']
                receipt = owner.receipt(request['job'], request['stage'], owner.outputs(request['job'])[request['stage']])
                self.results[request['operation_id']] = {'status': 'completed', 'receipt': receipt}
                return {'status': 'unknown'} if self.unknown else self.results[request['operation_id']]
            def poll(self, op):
                return self.lookup(op)
        self.backend = Backend()
        self.controller = Controller(self.store, 'task', self.backend, limits=LIMITS)
        state = self.controller.journal.load()
        state['spent'] = {'executions': 11, 'tokens': 0, 'time_s': 19800}
        self.controller.journal.save(state, 'CPU preserved lost-runtime budget checkpoint')
        self.controller.accept_intake(self.intake)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.remote = self.stack.enter_context(patch.object(self.api, 'colab_remote', return_value=None))
        self.stack.enter_context(patch.object(self.api, 'ColabBackend', return_value=self.backend))
        self.stack.enter_context(patch.object(self.api.time, 'sleep'))

    def save(self, path, value):
        path.write_text(canonical_json(value), encoding='utf-8')

    def call(self, **kwargs):
        with redirect_stdout(io.StringIO()):
            return self.api.resume(2, generation=self.generation, directory=self.directory, steps=kwargs.pop('steps', 1), **kwargs)

    def test_unknown_resume_looks_up_same_operation_without_extra_charge(self):
        self.controller.step()
        before = self.controller.status()
        blocked = next(job for job in before['jobs'].values() if job['state'] == 'blocked')
        op = blocked['operation']['operation_id']
        self.call(steps=2)
        after = self.controller.status()
        self.assertEqual(after['spent'], before['spent'])
        self.assertEqual(after['jobs'][blocked['id']]['operation']['operation_id'], op)
        self.assertEqual(self.backend.submissions, [op])
        self.backend.unknown = False
        self.call()
        self.assertEqual(self.controller.status()['spent'], before['spent'])
        self.assertEqual(self.backend.submissions, [op])
        self.assertIn(op, self.backend.lookups)

    def test_wrong_binding_round_or_extra_attempt_fails_before_remote(self):
        before = len(self.store.history('task'))
        self.save(self.target / 'runtime-binding.json', {'generation': 'runtime5'})
        with self.assertRaisesRegex(ValueError, 'binding'):
            self.call()
        self.save(self.target / 'runtime-binding.json', {'generation': self.generation})
        changed = deepcopy(self.intake)
        changed['round_id'] = 'real-runtime5-recovery-round:2'
        self.save(self.target / 'intake.json', changed)
        with self.assertRaises(ValueError):
            self.call()
        self.save(self.target / 'intake.json', self.intake)
        self.remote.assert_not_called()
        self.assertEqual(len(self.store.history('task')), before)
        state = self.controller.journal.load()
        extra = deepcopy(next(iter(state['jobs'].values())))
        extra['id'] = 'extra-repair'
        state['jobs'][extra['id']] = extra
        self.controller.journal.save(state, 'CPU extra same-round attempt')
        with self.assertRaisesRegex(ValueError, 'exact|two'):
            self.call()
        self.remote.assert_not_called()

    def test_terminal_snapshot_and_completed_file_use_original_identity_idempotently(self):
        self.backend.unknown = False
        self.call(steps=14)
        state = self.controller.status()
        self.assertTrue(all(job['state'] == 'completed' for job in state['jobs'].values()))
        self.assertEqual(state['spent']['executions'], 13)
        path = self.target / 'completed.json'
        complete = json.loads(path.read_bytes())
        self.assertEqual(set(complete), {'round_id', 'snapshot', 'publication_id', 'intake_id', 'results'})
        self.assertEqual(complete['snapshot'], self.store.create_snapshot('task', self.rid + ':completed-snapshot'))
        self.assertEqual(complete['results'], {jid: job['result'] for jid, job in state['jobs'].items()})
        before = (path.read_bytes(), len(self.store.history('task')), len(self.backend.submissions))
        self.remote.reset_mock()
        self.call(steps=4)
        self.remote.assert_not_called()
        self.assertEqual(before, (path.read_bytes(), len(self.store.history('task')), len(self.backend.submissions)))

    def test_missing_admission_or_changed_limits_never_initializes_or_dispatches(self):
        state = self.controller.journal.load()
        changed = deepcopy(state)
        changed['limits']['executions'] = 17
        with self.assertRaisesRegex(ValueError, 'budget'):
            self.api.admitted_jobs(self.store, self.target, self.intake, changed, self.rid,
                                   generation=self.generation, runtime_id=self.environment['runtime_session_id'],
                                   baseline=self.job())
        state['intakes'] = {}
        self.controller.journal.save(state, 'CPU missing admitted pointer')
        before = len(self.store.history('task'))
        with self.assertRaisesRegex(ValueError, 'admitted'):
            self.call()
        self.assertEqual(before, len(self.store.history('task')))
        self.remote.assert_not_called()

    def test_driver_lock_rejects_concurrent_resume(self):
        from rsi_kernel_agent.store import ConflictError
        with self.api.driver_lock(self.store, 'task'):
            with self.assertRaises(ConflictError):
                self.call()
        self.remote.assert_not_called()

    def test_terminal_completion_requires_original_result_marker(self):
        self.backend.unknown = False
        self.controller.run_until_idle()
        state = self.controller.journal.load()
        state['jobs']['job-length-row']['result']['classification'] = 'tampered'
        self.controller.journal.save(state, 'CPU corrupted result pointer')
        before = len(self.store.history('task'))
        with self.assertRaisesRegex(ValueError, 'result marker'):
            self.call()
        self.assertFalse((self.target / 'completed.json').exists())
        self.assertEqual(before, len(self.store.history('task')))
        self.remote.assert_not_called()


class Runtime7ResumeTests(ResumeRoundTests):
    generation = 'runtime7'


if __name__ == '__main__':
    unittest.main()
