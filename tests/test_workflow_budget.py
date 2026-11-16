"""Explicit cumulative budget grants use real protocol fixtures, never hardware."""
import copy
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from phase3_fixtures import RealProtocolFixture
from rsi_kernel_agent.contracts import ArtifactRef, Decision, Run, RunEvent, canonical_json
from rsi_kernel_agent.workflow.controller import Controller
from rsi_kernel_agent.workflow.evaluation import integrate_result
from rsi_kernel_agent.workflow.execution import stable_id
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from rsi_kernel_agent.workflow.scheduling import choose_jobs


def digest(value):
    return sha256(canonical_json(value).encode()).hexdigest()


class WorkflowBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fx = RealProtocolFixture()
        self.fx.prepare(self.root)
        self.store = self.fx.store
        self.c = Controller(self.store, 'task', None, limits={
            'executions': 1, 'time_s': 1, 'max_concurrency': 1})

    def completed(self, mode='success'):
        job, receipts = self.fx.execute_fixture()
        if mode == 'cancelled':
            receipts = {'generate': receipts['generate']}
        result = integrate_result(self.store, job, receipts, cancelled=mode == 'cancelled')
        job.update(state='completed' if mode == 'success' else 'cancelled', stage='evaluate',
                   receipts=receipts, result=result, reserved=True, attempt=0, repair_depth=0,
                   reason='Completed CPU fixture for the real protocol')
        state = self.c.journal.load()
        state['jobs'] = {job['id']: job}
        state['spent'] = {'executions': 1, 'tokens': 0, 'time_s': 1}
        state['served'] = {'A': 1}
        self.c.journal.save(state, 'CPU fixture completion')
        return job

    def grant(self, *, snapshot=None):
        state = self.c.journal.load()
        snapshot = snapshot or self.store.create_snapshot('task', 'milestone:' + str(state['revision']))
        return {'schema_version': 1, 'task_id': 'task',
            'expected_revision': state['revision'], 'expected_state_digest': digest(state),
            'old_limits': copy.deepcopy(state['limits']),
            'new_limits': {**state['limits'], 'executions': state['limits']['executions'] + 1,
                           'time_s': state['limits']['time_s'] + 1},
            'spent': copy.deepcopy(state['spent']),
            'next_batch': {'round_id': 'round:2', 'purpose': 'Continue after verified fixture feedback',
                           'cost': {'executions': 1, 'tokens': 0, 'time_s': 1}},
            'authorization': {'source': 'conversation:test-user-message',
                'quote': 'Continue iterating after the round succeeds.',
                'artifact_ref': self.fx.put(b'User: Continue iterating after the round succeeds.')},
            'milestone': {'snapshot': snapshot, 'rounds': [
                {'round_id': 'round:1', 'context_id': 'context', 'run_ids': ['run']}],
                'successful_round_id': 'round:1'}}

    def extend(self, grant, key='batch2'):
        self.assertTrue(callable(getattr(self.c, 'extend_budget', None)),
                        'Controller must expose an explicit budget grant command')
        return self.c.extend_budget(grant, key)

    def test_grant_changes_only_limits_and_audit_command_without_submission(self):
        self.completed()
        grant = self.grant()
        before = self.c.journal.load()
        history = len(self.store.history('task'))
        result = self.extend(grant)
        after = self.c.journal.load()
        self.assertEqual(after['limits']['executions'], 2)
        self.assertEqual(after['limits']['time_s'], 2)
        self.assertEqual(after['spent'], {'executions': 1, 'tokens': 0, 'time_s': 1})
        self.assertEqual(after['jobs'], before['jobs'])
        self.assertEqual(after['served'], before['served'])
        self.assertEqual(after['intakes'], before['intakes'])
        self.assertEqual(after['paused'], before['paused'])
        self.assertEqual(after['commands']['budget:batch2'], result)
        self.assertEqual(len(self.store.history('task')), history + 1)
        self.assertEqual(self.c.status()['remaining_budget']['executions'], 1)
        self.assertEqual(Controller(self.store, 'task', None).status()['limits'], after['limits'])
        with self.assertRaises(ValueError):
            Controller(self.store, 'task', None, limits=before['limits'])

    def test_replay_after_progress_and_response_loss_does_not_double_grant(self):
        self.completed()
        grant = self.grant()
        original = self.c.journal.save
        def lost(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('lost committed response')
        self.c.journal.save = lost
        try:
            with self.assertRaisesRegex(RuntimeError, 'lost committed response'):
                self.extend(grant)
        finally:
            self.c.journal.save = original
        self.c.pause('A', 'User pause after grant')
        before = self.c.journal.load()
        result = self.extend(grant)
        self.assertEqual(result, before['commands']['budget:batch2'])
        self.assertEqual(self.c.journal.load(), before)
        changed = copy.deepcopy(grant)
        changed['next_batch']['purpose'] = 'Different work'
        with self.assertRaises(ValueError): self.extend(changed)

    def test_failure_before_save_leaves_original_budget_and_retry_adds_once(self):
        self.completed()
        grant = self.grant()
        before = self.c.journal.load()
        original = self.c.journal.save
        def crash(*args, **kwargs): raise RuntimeError('before commit')
        self.c.journal.save = crash
        try:
            with self.assertRaisesRegex(RuntimeError, 'before commit'): self.extend(grant)
        finally:
            self.c.journal.save = original
        self.assertEqual(self.c.journal.load(), before)
        self.extend(grant)
        self.assertEqual(self.c.status()['limits']['executions'], 2)

    def test_deferred_attempt_requires_explicit_resume_and_uses_only_new_allowance(self):
        job = self.completed()
        state = self.c.journal.load()
        for suffix in ('next', 'extra'):
            state['jobs'][suffix] = {**copy.deepcopy(job), 'id': suffix, 'round_id': 'round:2',
                'run_id': suffix, 'candidate_id': suffix, 'state': 'deferred', 'reserved': False,
                'stage': 'generate', 'result': None, 'receipts': {}}
        self.c.journal.save(state, 'Unstarted future proposals')
        self.extend(self.grant())
        state = self.c.journal.load()
        self.assertEqual(choose_jobs(state), [])
        self.assertEqual(state['spent']['executions'], 1)
        self.c.resume_job('next', 'Explicit next attempt')
        self.c.resume_job('extra', 'Explicit extra attempt')
        state = self.c.journal.load()
        self.assertEqual(len(choose_jobs(state)), 1)
        self.assertEqual(state['spent']['executions'], 2)
        active = next(j for j in state['jobs'].values() if j['state'] == 'active')
        active['state'] = 'completed'
        self.assertEqual(choose_jobs(state), [])
        self.assertEqual(sum(j['state'] == 'deferred' for j in state['jobs'].values()), 1)

    def test_direct_limit_edit_is_rejected_by_journal_and_controller(self):
        self.completed()
        state = self.c.journal.load()
        state['limits']['executions'] = 99
        with self.assertRaises(ValueError): self.c.journal.save(state, 'Unrecorded top-up')
        # Even a caller bypassing Journal.save cannot make Controller read it.
        revision = state['revision'] + 1
        state['revision'] = revision
        record = Decision(self.c.journal.prefix + str(revision), 'task', 'note', 'task', 'Forged state',
            {'type': 'workflow_state.v1', 'revision': revision,
             'previous_id': self.c.journal.prefix + str(revision - 1), 'state': state,
             'state_digest': digest(state)})
        self.store.append(record, record.id)
        with self.assertRaises(ValueError): self.c.status()
        with self.assertRaises(ValueError): self.c.step()
        with self.assertRaises(ValueError): Controller(self.store, 'task', None)

    def test_invalid_limit_changes_and_costs_leave_state_unchanged(self):
        self.completed()
        good = self.grant()
        changes = [('executions', True), ('executions', -1), ('executions', 1),
                   ('time_s', float('nan')), ('time_s', 0), ('max_retries', 2),
                   ('tokens', 100001), ('max_concurrency', 2), ('weights', {'A': 2})]
        before = self.c.journal.load()
        for name, value in changes:
            with self.subTest(name=name, value=value):
                grant = copy.deepcopy(good)
                grant['new_limits'][name] = value
                with self.assertRaises(ValueError): self.extend(grant)
                self.assertEqual(self.c.journal.load(), before)
        grant = copy.deepcopy(good)
        grant['next_batch']['cost']['executions'] = 2
        with self.assertRaises(ValueError): self.extend(grant)

    def test_bool_aliases_in_direct_journal_limits_are_rejected(self):
        self.completed()
        state = self.c.journal.load()
        state['limits']['executions'] = True
        with self.assertRaises(ValueError): self.c.journal.save(state, 'Boolean limit alias')
        self.assertEqual(self.c.status()['limits']['executions'], 1)

    def test_stale_revision_authorization_and_snapshot_substitution_are_rejected(self):
        self.completed()
        good = self.grant()
        changes = [('expected_revision', 0), ('expected_state_digest', '0' * 64),
                   ('task_id', 'other'), ('spent', {'executions': 0, 'tokens': 0, 'time_s': 0})]
        for field, value in changes:
            grant = copy.deepcopy(good)
            grant[field] = value
            with self.assertRaises(ValueError): self.extend(grant)
        grant = copy.deepcopy(good)
        grant['authorization']['quote'] = 'Not in the archived source'
        with self.assertRaises(ValueError): self.extend(grant)
        grant = copy.deepcopy(good)
        grant['milestone']['snapshot']['high_watermark'] += 1
        with self.assertRaises(ValueError): self.extend(grant)
        self.c.pause('A', 'Concurrent progress')
        with self.assertRaises(ValueError): self.extend(good)

    def test_cancelled_only_round_is_not_a_successful_milestone(self):
        self.completed('cancelled')
        with self.assertRaises(ValueError): self.extend(self.grant())

    def test_old_snapshot_missing_completion_is_rejected(self):
        old = self.store.create_snapshot('task', 'before-feedback')
        self.completed()
        with self.assertRaises(ValueError): self.extend(self.grant(snapshot=old))

    def test_active_or_cancelled_task_is_rejected_without_budget_effect(self):
        job = self.completed()
        state = self.c.journal.load()
        state['jobs']['pending'] = {**copy.deepcopy(job), 'id': 'pending', 'state': 'blocked',
                                   'round_id': 'future', 'operation': {'operation_id': 'unknown'}}
        self.c.journal.save(state, 'Unknown operation')
        with self.assertRaises(ValueError): self.extend(self.grant())
        self.c.cancel('Cancelled by user')
        with self.assertRaises(ValueError): self.extend(self.grant())

    def test_milestone_run_set_context_and_real_result_are_bound(self):
        self.completed()
        good = self.grant()
        for field, value in [('run_ids', ['other-run']), ('context_id', 'other-context'),
                             ('round_id', 'prerequisite')]:
            grant = copy.deepcopy(good)
            grant['milestone']['rounds'][0][field] = value
            with self.assertRaises(ValueError): self.extend(grant)
        state = self.c.journal.load()
        state['jobs']['job']['result']['confirmed'] = False
        self.c.journal.save(state, 'Result substitution fixture')
        with self.assertRaises(ValueError): self.extend(self.grant())

    def test_receipt_corruption_prevents_grant(self):
        job = self.completed()
        ref = job['result']['receipt_refs']['compile']
        path = self.store.artifacts.path_for(ArtifactRef(**ref))
        path.write_bytes(b'corrupt')
        with self.assertRaises((ValueError, OSError)): self.extend(self.grant())

    def test_new_key_cannot_reuse_completed_round_to_add_budget_again(self):
        self.completed()
        self.extend(self.grant())
        another = self.grant()
        another['next_batch']['round_id'] = 'round:3'
        with self.assertRaises(ValueError): self.extend(another, 'another-key')
        self.assertEqual(self.c.status()['limits']['executions'], 2)

    def test_audit_entry_cannot_be_removed_or_grant_change_other_state(self):
        self.completed()
        self.extend(self.grant())
        state = self.c.journal.load()
        state['commands'].pop('budget:batch2')
        with self.assertRaises(ValueError): self.c.journal.save(state, 'Erase grant')
        state = self.c.journal.load()
        state['commands']['budget:batch2']['grant']['spent']['executions'] = 0
        with self.assertRaises(ValueError): self.c.journal.save(state, 'Change grant')

    def test_old_grant_numeric_type_alias_cannot_rewrite_audit_history(self):
        self.completed()
        original = self.extend(self.grant())
        before = self.c.journal.load()
        for alias in (True, 1.0):
            state = copy.deepcopy(before)
            state['commands']['budget:batch2']['sequence'] = alias
            with self.subTest(alias=alias):
                with self.assertRaises(ValueError): self.c.journal.save(state, 'Audit numeric alias')
                self.assertEqual(self.c.journal.load(), before)
        self.assertEqual(self.extend(original['grant']), original)

    def test_second_grant_uses_new_real_feedback_and_preserves_first_grant(self):
        self.completed()
        first = self.extend(self.grant())
        job = self.fx.job()
        job.update(id='job2', run_id='run2', round_id='round:2')
        self.store.append(Run('run2', 'candidate', 'context'), 'run2')
        self.store.transition(RunEvent('run2:start', 'run2', 'running', 'active', 'CPU fixture'),
                              None, 'run2:start')
        receipts = {stage: self.fx.receipt(job, stage, output)
                    for stage, output in self.fx.outputs(job).items()}
        result = integrate_result(self.store, job, receipts)
        job.update(state='completed', stage='evaluate', receipts=receipts, result=result,
                   reserved=True, attempt=0, repair_depth=0, reason='Next actual fixture feedback')
        state = self.c.journal.load()
        state['jobs']['job2'] = job
        state['spent'].update(executions=2, time_s=2)
        state['served']['A'] = 2
        self.c.journal.save(state, 'Second batch fixture completion')
        grant = self.grant()
        grant['next_batch']['round_id'] = 'round:3'
        grant['milestone']['rounds'] = [{'round_id': 'round:2', 'context_id': 'context', 'run_ids': ['run2']}]
        grant['milestone']['successful_round_id'] = 'round:2'
        second = self.extend(grant, 'batch3')
        state = self.c.journal.load()
        self.assertEqual(state['limits']['executions'], 3)
        self.assertEqual(state['spent']['executions'], 2)
        self.assertEqual(state['commands']['budget:batch2'], first)
        self.assertEqual(second['previous_key'], 'batch2')
        self.assertEqual(second['sequence'], 2)

    def test_direct_grant_cannot_change_spending_or_controls_in_same_revision(self):
        self.completed()
        grant = self.grant()
        # Build an otherwise valid transition through the public command, then
        # inject an unrelated change immediately before the real Journal save.
        original = self.c.journal.save
        def refund(state, *args, **kwargs):
            state['spent']['executions'] = 0
            return original(state, *args, **kwargs)
        self.c.journal.save = refund
        before = self.c.journal.load()
        try:
            with self.assertRaises(ValueError): self.extend(grant)
        finally:
            self.c.journal.save = original
        self.assertEqual(self.c.journal.load(), before)

    def test_new_grant_requires_named_batch_to_follow_previous_granted_batch(self):
        self.completed()
        self.extend(self.grant())
        # A different historical round cannot fund another unrelated batch.
        state = self.c.journal.load()
        grant = self.grant()
        grant['milestone']['rounds'][0]['round_id'] = 'unrelated'
        grant['milestone']['successful_round_id'] = 'unrelated'
        grant['next_batch']['round_id'] = 'round:4'
        with self.assertRaises(ValueError): self.extend(grant, 'skip-batch')
        self.assertEqual(self.c.journal.load(), state)

    def test_second_grant_cannot_skip_its_preceding_declared_batch(self):
        self.completed()
        self.extend(self.grant())
        job = self.fx.job()
        job.update(id='other-job', run_id='other-run', round_id='unrelated')
        self.store.append(Run('other-run', 'candidate', 'context'), 'other-run')
        self.store.transition(RunEvent('other-run:start', 'other-run', 'running', 'active', 'CPU fixture'),
                              None, 'other-run:start')
        receipts = {stage: self.fx.receipt(job, stage, output)
                    for stage, output in self.fx.outputs(job).items()}
        result = integrate_result(self.store, job, receipts)
        job.update(state='completed', stage='evaluate', receipts=receipts, result=result,
                   reserved=True, attempt=0, repair_depth=0, reason='Unrelated successful fixture')
        state = self.c.journal.load()
        state['jobs'][job['id']] = job
        state['spent'].update(executions=2, time_s=2)
        self.c.journal.save(state, 'Another complete round, not the declared next batch')
        state = self.c.journal.load()
        grant = self.grant()
        grant['milestone']['rounds'] = [{'round_id': 'unrelated', 'context_id': 'context',
                                         'run_ids': ['other-run']}]
        grant['milestone']['successful_round_id'] = 'unrelated'
        grant['next_batch']['round_id'] = 'round:3'
        with self.assertRaises(ValueError): self.extend(grant, 'skip-round2')
        self.assertEqual(self.c.journal.load(), state)

    def test_missing_grant_fields_raise_validation_error_without_mutation(self):
        self.completed()
        grant = self.grant()
        grant.pop('new_limits')
        before = self.c.journal.load()
        with self.assertRaises(ValueError): self.extend(grant)
        self.assertEqual(self.c.journal.load(), before)

    def test_cli_applies_exact_grant_and_replays_without_runtime_config(self):
        self.completed()
        grant = self.grant()
        path = self.root / 'grant.json'
        path.write_text(json.dumps(grant), encoding='utf-8')
        repo = Path(__file__).resolve().parents[1]
        env = {**os.environ, 'PYTHONPATH': str(repo / 'src'), 'PYTHONUTF8': '1'}
        command = [sys.executable, '-B', '-m', 'rsi_kernel_agent.workflow',
            '--db', str(self.store.db_path), '--artifacts', str(self.root / 'artifacts'),
            'extend-budget', '--task', 'task', '--grant', str(path), '--key', 'batch2']
        first = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        second = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(first.stdout), json.loads(second.stdout))
        self.assertEqual(self.c.status()['limits']['executions'], 2)


if __name__ == '__main__': unittest.main()
