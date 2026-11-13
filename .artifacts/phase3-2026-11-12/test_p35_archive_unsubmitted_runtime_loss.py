"""Temporary real-protocol Store fixtures only; no TPU or production Store."""
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / 'src'), str(HERE.parents[1] / 'tests')]
from phase3_fixtures import RealProtocolFixture
from rsi_kernel_agent.contracts import Candidate, Decision, Plan, Run, RunEvent, Strategy, canonical_json
from rsi_kernel_agent.workflow.controller import Controller, stable
from rsi_kernel_agent.workflow.execution import make_request, stable_id
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from real_round_aux import LIMITS


class ArchiveTests(unittest.TestCase):
    def fixture(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        directory = Path(temp.name)
        f = RealProtocolFixture()
        f.prepare(directory)
        store = f.store
        store.append(Strategy('B', 'task', 'B fixture'), 'B')
        target = directory / 'real-round-2'
        target.mkdir()
        checkpoint_dir = directory / 'confirmation-f686274a57bb7ebb'
        checkpoint_dir.mkdir()
        controller = Controller(store, 'task', None, limits=LIMITS)
        state = controller.journal.load()
        prior = {}
        for index in range(9):
            prior['prior:' + str(index)] = {'id': 'prior:' + str(index), 'state': 'completed',
                'round_id': 'prior-round', 'result': {'preserved': index}}
        state.update(jobs=deepcopy(prior), spent={'executions': 9, 'tokens': 0, 'time_s': 16200.0})
        prior_state = controller.journal.save(state, 'CPU prior checkpoint')
        items, variants, jobs = [], {}, {}
        mapping = {'R2-A-length-row': 'length-row', 'R2-A-length-smem': 'length-smem',
                   'R2-B-query': 'query-in-attention', 'R2-B-b1-fusion': 'b1-fused-prepare'}
        for pid, label in mapping.items():
            sid = 'A' if '-A-' in pid else 'B'
            store.append(Plan(pid, sid, 'CPU runtime loss fixture'), pid)
            job = deepcopy(f.job())
            job.update(id='fixture-' + label, round_id='real-runtime2-round:2', strategy_id=sid, plan_id=pid)
            job['variant'].update(label=label, cost={'executions': 1, 'tokens': 0, 'time_s': 1800.0})
            variants[pid] = [deepcopy(job['variant'])]
            for key in ('candidate_id', 'run_id'):
                job.pop(key)
            items.append({'status': 'accepted', 'jobs': [job]})
        intake = {'id': 'fixture-intake', 'task_id': 'task', 'round_id': 'real-runtime2-round:2', 'items': items}
        store.append(Decision(intake['id'], 'task', 'note', 'task', 'CPU validated intake',
            {'type': 'workflow_intake.v1', 'result': intake}), intake['id'])
        state = controller.journal.load()
        for item in items:
            job = deepcopy(item['jobs'][0])
            job.update(candidate_id=stable('wf-c', job['id']), run_id=stable('wf-r', job['id'] + ':0'),
                state='queued', stage='generate', receipts={}, attempt=0, repair_depth=0,
                result=None, reason='Accepted Plan variant', reserved=False)
            controller._ensure_records(job)
            state['jobs'][job['id']] = job
        state['intakes'][intake['id']] = sha256(canonical_json(intake).encode()).hexdigest()
        state = controller.journal.save(state, 'CPU accepted validated intake')
        active = next(j for j in state['jobs'].values() if j.get('variant', {}).get('label') == 'length-row')
        active.update(state='blocked', stage='generate', reserved=True,
            reason='Backend outcome unknown; explicit reconciliation required', start_predecessor=None)
        active['operation'] = make_request(store, active, 'generate')
        store.transition(RunEvent(active['run_id'] + ':start', active['run_id'], 'running', 'active',
                                  'CPU journal reservation'), None, active['run_id'] + ':start')
        state['spent'] = {'executions': 10, 'tokens': 0, 'time_s': 18000.0}
        state['served'] = {'A': 1}
        state = controller.journal.save(state, 'CPU unknown lookup before durable submit')
        expected = {'task_id': 'task', 'round_id': 'real-runtime2-round:2', 'context_id': 'context',
            'runtime_id': f.environment['runtime_session_id'], 'initial_revision': state['revision'],
            'strategy_ids': {'A': 'A', 'B': 'B'},
            'job_ids': {j['variant']['label']: j['id'] for j in state['jobs'].values() if j['round_id'] == 'real-runtime2-round:2'}}
        for path, value in ((target / 'intake.json', intake), (target / 'variants.json', variants),
            (target / 'execution-input.json', {'round_id': expected['round_id'], 'variants': variants}),
            (target / 'runtime-binding.json', {'generation': 'runtime3'}),
            (target / 'controller-status.json', state),
            (directory / 'colab-config-runtime3.json', {'runtime_id': expected['runtime_id']}),
            (checkpoint_dir / 'controller-status.json', prior_state)):
            path.write_text(canonical_json(value), encoding='utf-8')
        observation = {'schema_version': 1, 'runtime_id': expected['runtime_id'],
            'observed_at_utc': '2026-11-13T10:09:00+00:00', 'control_plane_reachable': True,
            'assignments': [], 'driver_processes_stopped': True,
            'reason': 'CPU fixture: allocation absent; this is not hardware evidence'}
        return directory, store, expected, observation, state, active['id']

    def api(self):
        return importlib.import_module('p35_archive_unsubmitted_runtime_loss')

    def execute(self, data):
        directory, store, expected, observation, _, _ = data
        with patch.object(self.api(), 'EXPECTED', expected):
            return self.api().archive_unsubmitted_runtime_loss(store, directory, observation)

    def assert_outcome(self, data, output):
        directory, store, expected, _, old, active_id = data
        state = WorkflowJournal(store, 'task').load()
        self.assertEqual(state['spent'], old['spent'])
        self.assertFalse(state['cancelled'])
        self.assertEqual(state['limits'], old['limits'])
        self.assertEqual(len(state['jobs']), 13)
        for jid, before in old['jobs'].items():
            after = state['jobs'][jid]
            if before['round_id'] != expected['round_id']:
                self.assertEqual(before, after)
            elif jid == active_id:
                self.assertEqual(after['state'], 'cancelled')
                self.assertEqual(after['result']['classification'], 'cancelled')
                self.assertEqual(after['operation'], before['operation'])
                self.assertEqual(after['context_id'], before['context_id'])
                self.assertEqual(store.run_state(after['run_id'])['execution_status'], 'incomplete')
                self.assertTrue(after['reserved'])
                self.assertEqual(after['result']['receipt_refs'], {})
                self.assertEqual(after['result']['correctness'], 'unknown')
                self.assertIsNone(after['result']['latency_ms'])
            else:
                self.assertEqual(after['state'], 'cancelled')
                self.assertIsNone(after['result'])
                self.assertFalse(after['reserved'])
                self.assertNotIn('operation', after)
                self.assertEqual(store.run_state(after['run_id']),
                    {'execution_status': 'not_run', 'scheduling_status': 'ready', 'event_id': None})
        with patch.object(self.api(), 'EXPECTED', expected):
            self.assertEqual(self.api().require_archived_unsubmitted_runtime_loss(store, directory), output)
        self.assertEqual(output['summary']['spent_after'], old['spent'])
        self.assertFalse(output['summary']['round_execution_completed'])
        self.assertEqual(output['summary']['not_started_run_ids'], sorted(
            j['run_id'] for jid, j in state['jobs'].items() if j['round_id'] == expected['round_id'] and jid != active_id))
        snap = store.snapshot_page(output['snapshot']['id'], limit=1000)
        self.assertEqual(snap['snapshot'], output['snapshot'])
        self.assertIn(output['decision_id'], {r.id for r in snap['records']})
        self.assertIsNone(store.best('task', 'context'))

    def test_archive_retains_budget_unknown_evidence_and_unstarted_runs(self):
        data = self.fixture()
        output = self.execute(data)
        self.assert_outcome(data, output)

    def test_replay_is_idempotent_without_new_snapshot_or_history(self):
        data = self.fixture()
        first = self.execute(data)
        count = len(data[1].history('task'))
        second = self.execute(data)
        self.assertEqual(first, second)
        self.assertEqual(len(data[1].history('task')), count)

    def test_durable_submit_or_receipt_or_orphan_binding_prevents_any_archive_write(self):
        for kind in ('colab_execution_request.v1', 'execution_receipt.v1', 'orphan-receipt'):
            with self.subTest(kind=kind):
                data = self.fixture()
                _, store, _, _, state, active_id = data
                job = state['jobs'][active_id]
                details = ({'type': kind, 'request': job['operation']} if kind == 'colab_execution_request.v1'
                    else {'type': 'execution_receipt.v1', 'operation_id': job['operation']['operation_id']})
                target = 'task' if kind == 'orphan-receipt' else job['run_id']
                store.append(Decision('unexpected-ledger', 'task', 'note', target, 'CPU refusal fixture', details), 'unexpected-ledger')
                count = len(store.history('task'))
                with self.assertRaisesRegex(ValueError, 'durable|receipt|submitted'):
                    self.execute(data)
                self.assertEqual(len(store.history('task')), count)

    def test_queued_operation_or_run_start_refused(self):
        for mutation in ('operation', 'run_start'):
            with self.subTest(mutation=mutation):
                data = self.fixture()
                _, store, _, _, _, active_id = data
                journal = WorkflowJournal(store, 'task')
                state = journal.load()
                job = next(j for jid, j in state['jobs'].items() if j['round_id'] == 'real-runtime2-round:2' and jid != active_id)
                if mutation == 'operation':
                    job['operation'] = make_request(store, job, 'generate')
                    updated = journal.save(state, 'CPU unexpected operation')
                    data[2]['initial_revision'] = updated['revision']
                else:
                    store.transition(RunEvent(job['run_id'] + ':start', job['run_id'], 'running', 'active', 'CPU unexpected start'), None, job['run_id'] + ':start')
                count = len(store.history('task'))
                with self.assertRaises(ValueError):
                    self.execute(data)
                self.assertEqual(len(store.history('task')), count)

    def test_changed_budget_context_identity_or_global_cancel_refused_without_writes(self):
        for mutation in ('budget', 'context', 'job', 'cancel', 'limits', 'prior'):
            with self.subTest(mutation=mutation):
                data = self.fixture()
                _, store, _, _, _, active_id = data
                journal = WorkflowJournal(store, 'task')
                state = journal.load()
                if mutation == 'budget': state['spent']['executions'] = 9
                elif mutation == 'context': state['jobs'][active_id]['context_id'] = 'other-context'
                elif mutation == 'job': state['jobs'][active_id]['variant']['priority'] += 1
                elif mutation == 'cancel': state['cancelled'] = True
                elif mutation == 'limits': state['limits']['executions'] = 17
                else: state['jobs']['prior:0']['result'] = {'changed': True}
                updated = journal.save(state, 'CPU guard rejection')
                data[2]['initial_revision'] = updated['revision']
                count = len(store.history('task'))
                with self.assertRaises(ValueError): self.execute(data)
                self.assertEqual(len(store.history('task')), count)

    def test_changed_loss_observation_and_live_assignment_refused(self):
        data = self.fixture()
        data[3]['assignments'] = [{'runtime_id': data[2]['runtime_id'], 'session_names': ['old'],
                                  'accelerator': 'TPU', 'variant': 'v6e'}]
        count = len(data[1].history('task'))
        with self.assertRaises(ValueError): self.execute(data)
        self.assertEqual(len(data[1].history('task')), count)
        data[3]['assignments'] = []
        self.execute(data)
        data[3]['reason'] = 'Changed after immutable intent'
        count = len(data[1].history('task'))
        with self.assertRaises(ValueError): self.execute(data)
        self.assertEqual(len(data[1].history('task')), count)

    def test_replacement_runtime_present_is_accepted_when_old_runtime_absent(self):
        data = self.fixture()
        data[3]['assignments'] = [{'runtime_id': 'replacement-fixture-runtime',
            'session_names': ['replacement-session'], 'accelerator': 'TPU', 'variant': 'v6e'}]
        output = self.execute(data)
        self.assert_outcome(data, output)

    def test_malformed_or_unsanitized_discovery_rows_are_refused(self):
        valid = {'runtime_id': 'replacement-fixture-runtime', 'session_names': [],
                 'accelerator': 'TPU', 'variant': 'v6e'}
        assignments = ({}, [None], [{**valid, 'runtime_id': ''}], [{**valid, 'session_names': 'session'}],
            [{**valid, 'session_names': [1]}], [{**valid, 'extra': 'not sanitized'}],
            [{**valid, 'accelerator': None}], [{**valid, 'variant': ''}])
        for rows in assignments:
            with self.subTest(rows=rows):
                data = self.fixture()
                data[3]['assignments'] = rows
                count = len(data[1].history('task'))
                with self.assertRaises(ValueError): self.execute(data)
                self.assertEqual(len(data[1].history('task')), count)

    def test_readonly_guard_allows_new_jobs_but_refuses_refund_or_old_job_mutation(self):
        data = self.fixture()
        output = self.execute(data)
        journal = WorkflowJournal(data[1], 'task')
        state = journal.load()
        state['jobs']['later-runtime-job'] = {'id': 'later-runtime-job', 'round_id': 'new-runtime-round',
            'state': 'queued', 'reserved': False, 'variant': {'cost': deepcopy(self.api().COST)}}
        journal.save(state, 'CPU later Runtime admission')
        with patch.object(self.api(), 'EXPECTED', data[2]), \
             patch.object(data[1], '_write', side_effect=AssertionError('Read-only gate attempted a write')):
            self.assertEqual(self.api().require_archived_unsubmitted_runtime_loss(data[1], data[0]), output)
        state = journal.load()
        state['spent']['executions'] = 9
        journal.save(state, 'CPU forbidden refund')
        with patch.object(self.api(), 'EXPECTED', data[2]):
            with self.assertRaisesRegex(ValueError, 'refunded|spending'):
                self.api().require_archived_unsubmitted_runtime_loss(data[1], data[0])

    def test_every_store_effect_recovers_before_and_after_crash(self):
        baseline = self.fixture()
        effects = []
        original = baseline[1]._write
        def count(operation, value, key, action):
            effects.append((operation, key))
            return original(operation, value, key, action)
        with patch.object(baseline[1], '_write', side_effect=count):
            self.execute(baseline)
        self.assertGreaterEqual(len(effects), 10)
        for target in range(len(effects)):
            for after in (False, True):
                with self.subTest(effect=effects[target][0], index=target, after=after):
                    data = self.fixture()
                    original = data[1]._write
                    seen = 0
                    def fail(operation, value, key, action):
                        nonlocal seen
                        current = seen
                        seen += 1
                        if current == target and not after:
                            raise RuntimeError('injected durable effect crash')
                        result = original(operation, value, key, action)
                        if current == target and after:
                            raise RuntimeError('injected durable effect crash')
                        return result
                    with patch.object(data[1], '_write', side_effect=fail):
                        with self.assertRaisesRegex(RuntimeError, 'injected'):
                            self.execute(data)
                    output = self.execute(data)
                    self.assert_outcome(data, output)
                    records = len(data[1].history('task'))
                    self.assertEqual(self.execute(data), output)
                    self.assertEqual(len(data[1].history('task')), records)

    def test_output_file_crashes_recover_and_do_not_duplicate_snapshot(self):
        for after in (False, True):
            with self.subTest(after=after):
                data = self.fixture()
                original = self.api().write_once
                def fail(path, value):
                    if after: original(path, value)
                    raise RuntimeError('output crash')
                with patch.object(self.api(), 'write_once', side_effect=fail):
                    with self.assertRaisesRegex(RuntimeError, 'output crash'): self.execute(data)
                output = self.execute(data)
                self.assert_outcome(data, output)

    def test_new_request_after_intent_prevents_recovery(self):
        data = self.fixture()
        store = data[1]
        original = store._write
        def fail(operation, value, key, action):
            result = original(operation, value, key, action)
            if operation == 'append' and value.get('payload', {}).get('details', {}).get('type') == 'p35_unsubmitted_runtime_loss_intent.v1':
                raise RuntimeError('after intent')
            return result
        with patch.object(store, '_write', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'after intent'): self.execute(data)
        job = data[4]['jobs'][data[5]]
        store.append(Decision('late-submit', 'task', 'note', job['run_id'], 'CPU unsafe late submit',
            {'type': 'colab_execution_request.v1', 'request': job['operation']}), 'late-submit')
        count = len(store.history('task'))
        with self.assertRaisesRegex(ValueError, 'durable|submitted'): self.execute(data)
        self.assertEqual(len(store.history('task')), count)


if __name__ == '__main__':
    unittest.main(verbosity=2)
