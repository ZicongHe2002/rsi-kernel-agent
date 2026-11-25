"""Exploration identity regressions using temporary Stores and CPU protocol fixtures."""
from hashlib import sha256
import unittest

import test_workflow_exploration as fixtures
from rsi_kernel_agent.contracts import (ArtifactRef, CandidateIdentity, Decision, ParentCandidate,
                                        canonical_json)
from rsi_kernel_agent.profiler.analyzer import Analyzer
from rsi_kernel_agent.profiler.inputs import AnalysisRequest, capture_inputs
from rsi_kernel_agent.profiler.outputs import publish_result
from rsi_kernel_agent.workflow.execution import stable_id
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from rsi_kernel_agent.workflow.intake import intake_publication
from rsi_kernel_agent.workflow.real_protocol import load_bundle


class ExplorationIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.case = fixtures.BundleExplorationTests()
        await self.case.asyncSetUp()
        self.addCleanup(self.case.tmp.cleanup)
        self.f, self.store, self.api = self.case.f, self.case.store, self.case.api
        self.original_identity_output = self.f.identity_output
        self.f.identity_output = self.loaded_identity

    def loaded_identity(self):
        """The local producer reports the entry selected for this CPU fixture round."""
        value = self.original_identity_output()
        bundle = load_bundle(self.store, self.case.parent_bundle)
        value['entry'] = bundle['entry']
        value['module_path'] = value['workspace'] + '/' + bundle['entry']['path']
        return value

    def build(self, bundle_ref):
        return self.f.put({'schema_version': 1, 'source_bundle_digest': bundle_ref['digest'],
            'oracle_bundle_digest': self.f.oracle_bundle['digest'], 'runner_digest': self.f.runner_digest,
            'oracle_digest': self.f.oracle['digest']})

    def mapped_parent(self):
        source = self.f.put(b'from left import helper\ndef kernel(x): return helper(x)\n')
        bundle = {'schema_version': 1, 'entry': {'path': 'kernel.py', 'symbol': 'kernel'}, 'files': [
            {'path': 'kernel.py', 'artifact_ref': source, 'role': 'source'},
            {'path': 'left.py', 'artifact_ref': self.f.put(b'def helper(x): return x + 1\n'), 'role': 'dependency'},
            {'path': 'right.py', 'artifact_ref': self.f.put(b'def helper(x): return x * 2\n'), 'role': 'dependency'}]}
        self.mapped_parent_bundle = self.add_parent('mapped-parent', bundle)
        return bundle

    def add_parent(self, parent_id, bundle):
        source = next(row['artifact_ref'] for row in bundle['files'] if row['path'] == bundle['entry']['path'])
        bundle_ref = self.f.put(bundle)
        build = self.build(bundle_ref)
        identity = CandidateIdentity(source['digest'], self.f.config['digest'], build['digest'])
        self.store.append(ParentCandidate(parent_id, 'task', identity), parent_id)
        return bundle_ref

    async def prepare_round(self, round_id, bundle, *, parent_id='bundled-parent', config=None):
        snapshot = self.store.create_snapshot('task', round_id + ':input')
        source = next(row['artifact_ref'] for row in bundle['files'] if row['path'] == bundle['entry']['path'])
        request = {'schema_version': 1, 'request_id': round_id + ':analysis', 'task_id': 'task',
            'workload_id': 'workload', 'knowledge': [], 'environment': {'fixture': 'CPU only, no TPU'},
            'tool_versions': {'fixture': '1'}, 'strategies': [{'strategy_id': 'A',
            'parent_id': parent_id, 'context_id': 'context', 'snapshot_id': snapshot['id'], 'run_id': None,
            'source_artifact': source, 'config_artifact': self.f.config,
            'profile_ids': [], 'benchmark_ids': []}]}
        manifest = await capture_inputs(self.store, None, AnalysisRequest.from_dict(request))
        answer = fixtures.proposal()
        for field in ('code_understanding', 'observations', 'hypotheses', 'plans'):
            answer[field] = [item for item in answer[field] if item['strategy_id'] == 'A']
        self.case.parent_bundle = self.f.put(bundle)
        build = self.build(self.case.parent_bundle)
        variants = {}
        for plan in answer['plans']:
            plan.update(parent_id=parent_id, variants=[plan['id']])
            variants[plan['id']] = [{**self.f.variant, 'label': plan['id'], 'source_ref': source,
                'config_ref': config or self.f.config,
                'source_bundle_ref': self.case.parent_bundle, 'build_ref': build}]
        result = Analyzer(manifest, artifacts=self.store.artifacts).advance({'final': answer})
        publication = publish_result(self.store, manifest, result, round_id + ':publication')
        intake = await intake_publication(self.store, publication.id, round_id, variants)
        self.assertEqual({row['status'] for row in intake['items']}, {'accepted'})
        self.case.controller.accept_intake(intake)
        state = self.case.controller.run_until_idle()
        jobs = [job for job in state['jobs'].values() if job['round_id'] == round_id]
        self.assertTrue(all(job['state'] == 'completed' for job in jobs))
        return {'input_snapshot_id': snapshot['id'], 'hypothesis': 'Investigate implementation ' + round_id,
            'change_kind': 'mechanism', 'job_ids': [job['id'] for job in jobs],
            'next_step': 'Inspect the dependency result', 'key': round_id + ':feedback'}

    async def run_round(self, round_id, bundle, **kwargs):
        args = await self.prepare_round(round_id, bundle, **kwargs)
        event = self.api.record_round_feedback(self.store, 'task', round_id, 'A', **args)
        self.assertEqual(event['outcome'], 'validated_feedback')
        self.assertTrue(all(row['valid'] and row['producer_verified'] for row in event['feedback_refs']))
        return event

    async def seed_legacy_feedback(self, round_id, bundle, version):
        """Append the old wire format around real fixed-Evaluator feedback, without rewriting records."""
        args = await self.prepare_round(round_id, bundle, parent_id='mapped-parent')
        journal = WorkflowJournal(self.store, 'task').load()
        rows = [self.api._verified_feedback(self.store, journal['jobs'][job_id]) for job_id in args['job_ids']]
        def legacy_identity(manifest):
            return sha256(canonical_json({'source_digests': sorted({
                row['artifact_ref']['digest'] for row in manifest['files']}),
                'config_digest': self.f.config['digest']}).encode()).hexdigest()
        old_identity = legacy_identity(bundle)
        old_parent_identity = legacy_identity(load_bundle(self.store, self.mapped_parent_bundle))
        for row in rows:
            self.assertTrue(row['valid'])
            row['change_comparison'] = {'status': 'different', 'identity': old_identity,
                                        'parent_identity': old_parent_identity}
        request = {'task_id': 'task', 'round_id': round_id, 'strategy_id': 'A',
                   **{key: value for key, value in args.items() if key != 'key'}}
        request['job_ids'] = sorted(request['job_ids'])
        details = {'type': 'exploration_round.v1', **request,
            'request_digest': sha256(canonical_json(request).encode()).hexdigest(),
            'effective': True, 'provenance': 'real', 'outcome': 'validated_feedback',
            'performance_validated': True, 'feedback_refs': rows, 'change_identities': [old_identity],
            'journal_revision': journal['revision']}
        if version is not None:
            details['change_identity_version'] = version
        event_id = stable_id('exploration-effective', 'task', 'A', round_id)
        record = Decision(event_id, 'task', 'note', 'A', 'Effective route feedback retained', details)
        self.store.append(record, event_id)
        return record, args

    async def test_swapping_dependency_paths_counts_as_a_distinct_implementation(self):
        bundle = self.mapped_parent()
        bundle['files'][1]['artifact_ref'], bundle['files'][2]['artifact_ref'] = (
            bundle['files'][2]['artifact_ref'], bundle['files'][1]['artifact_ref'])
        event = await self.run_round('path-swap', bundle, parent_id='mapped-parent')
        self.assertTrue(event['effective'])
        self.assertEqual({row['change_comparison']['status'] for row in event['feedback_refs']}, {'different'})
        bundle['files'].reverse()
        repeated = await self.run_round('path-swap-repeat', bundle, parent_id='mapped-parent')
        self.assertFalse(repeated['effective'])

    async def test_changing_entry_symbol_counts_with_the_same_file_mapping(self):
        source = self.f.put(b'def kernel(x): return x\ndef alternate(x): return x + 1\n')
        bundle = {'schema_version': 1, 'entry': {'path': 'kernel.py', 'symbol': 'kernel'}, 'files': [
            {'path': 'kernel.py', 'artifact_ref': source, 'role': 'source'}]}
        self.add_parent('symbol-parent', bundle)
        bundle['entry']['symbol'] = 'alternate'
        event = await self.run_round('entry-symbol', bundle, parent_id='symbol-parent')
        self.assertTrue(event['effective'])

    async def test_changing_entry_path_counts_when_both_files_have_the_same_digest(self):
        bundle = {'schema_version': 1, 'entry': {'path': 'kernel.py', 'symbol': 'kernel'}, 'files': [
            {'path': path, 'artifact_ref': self.f.source, 'role': 'source'}
            for path in ('kernel.py', 'alternate.py')]}
        self.add_parent('entry-parent', bundle)
        bundle['entry']['path'] = 'alternate.py'
        event = await self.run_round('entry-path', bundle, parent_id='entry-parent')
        self.assertTrue(event['effective'])

    async def test_adding_a_second_path_for_existing_content_counts(self):
        bundle = load_bundle(self.store, self.case.parent_bundle)
        bundle['files'].append({'path': 'second_helper.py', 'artifact_ref': self.case.helper, 'role': 'dependency'})
        event = await self.run_round('new-path', bundle)
        self.assertTrue(event['effective'])

    async def test_file_order_and_role_metadata_do_not_create_exploration(self):
        bundle = load_bundle(self.store, self.case.parent_bundle)
        bundle['files'].reverse()
        bundle['files'][0]['role'] = 'source'
        event = await self.run_round('metadata-only', bundle)
        self.assertFalse(event['effective'])
        self.assertEqual(event['change_identities'], [])

    async def test_configuration_changes_still_count_only_once(self):
        bundle = load_bundle(self.store, self.case.parent_bundle)
        config = self.f.put({'case': 'tiny', 'shape': [2], 'tile': 2})
        first = await self.run_round('config-first', bundle, config=config)
        self.assertTrue(first['effective'])
        repeated = await self.run_round('config-repeat', bundle, config=config)
        self.assertFalse(repeated['effective'])

    async def test_v2_history_rejects_duplicate_but_allows_new_path_mapping_without_rewriting(self):
        bundle = self.mapped_parent()
        bundle['files'][1]['artifact_ref'] = self.f.put(b'def helper(x): return x + 3\n')
        legacy, args = await self.seed_legacy_feedback('legacy-v2', bundle, 2)
        bundle['files'].reverse()
        repeated = await self.run_round('legacy-repeat', bundle, parent_id='mapped-parent')
        self.assertFalse(repeated['effective'])
        bundle['files'][0]['artifact_ref'], bundle['files'][1]['artifact_ref'] = (
            bundle['files'][1]['artifact_ref'], bundle['files'][0]['artifact_ref'])
        changed = await self.run_round('legacy-new-mapping', bundle, parent_id='mapped-parent')
        self.assertTrue(changed['effective'])
        self.assertEqual(self.store.get(legacy.id), legacy)
        replay = self.api.record_round_feedback(self.store, 'task', 'legacy-v2', 'A', **args)
        self.assertEqual(replay, {'id': legacy.id, **legacy.details})
        self.assertEqual(self.api.exploration_status(self.store.history('task'), 'A')['completed_effective_rounds'], 2)

    async def test_unversioned_history_does_not_recount_a_previous_implementation(self):
        bundle = self.mapped_parent()
        bundle['files'][1]['artifact_ref'] = self.f.put(b'def helper(x): return x + 3\n')
        legacy, _ = await self.seed_legacy_feedback('legacy-v1', bundle, None)
        bundle['files'].reverse()
        repeated = await self.run_round('unversioned-repeat', bundle, parent_id='mapped-parent')
        self.assertFalse(repeated['effective'])
        self.assertEqual(self.store.get(legacy.id), legacy)

    async def feedback_after_unavailable_legacy_build(self, damage, *, research_question=False):
        bundle = self.mapped_parent()
        bundle['files'][1]['artifact_ref'] = self.f.put(b'def helper(x): return x + 3\n')
        legacy, _ = await self.seed_legacy_feedback('legacy-unavailable', bundle, 2)
        candidate = self.store.get(legacy.details['feedback_refs'][0]['candidate_id'])
        old_build = self.store.artifacts.path_for(ArtifactRef(candidate.identity.build_digest, 0))
        bundle['files'].reverse()
        args = await self.prepare_round('same-after-loss', bundle, parent_id='mapped-parent')
        if damage == 'missing':
            old_build.unlink()
        else:
            old_build.write_bytes(b'corrupt historical build')
        if research_question:
            args.update(change_kind='research_question', hypothesis='tile')
        event = self.api.record_round_feedback(self.store, 'task', 'same-after-loss', 'A', **args)
        self.assertEqual(event['outcome'], 'validated_feedback')
        self.assertEqual(self.store.get(legacy.id), legacy)
        return legacy, event

    async def test_missing_legacy_build_cannot_make_an_identical_implementation_novel(self):
        legacy, event = await self.feedback_after_unavailable_legacy_build('missing')
        self.assertFalse(event['effective'])
        self.assertEqual(event['change_identities'], [])
        self.assertEqual({row['change_comparison']['status'] for row in event['feedback_refs']}, {'unknown'})
        self.assertEqual({issue['round_id'] for issue in event['prior_identity_issues']}, {legacy.details['round_id']})
        self.assertTrue(all(issue['reason'] for issue in event['prior_identity_issues']))
        self.assertEqual(self.api.exploration_status(self.store.history('task'), 'A')['completed_effective_rounds'], 1)

    async def test_corrupt_legacy_build_cannot_make_an_identical_implementation_novel(self):
        _, event = await self.feedback_after_unavailable_legacy_build('corrupt')
        self.assertFalse(event['effective'])
        self.assertEqual(event['change_identities'], [])
        self.assertEqual({row['change_comparison']['status'] for row in event['feedback_refs']}, {'unknown'})
        self.assertTrue(all(issue['reason'] for issue in event['prior_identity_issues']))
        self.assertEqual(self.api.exploration_status(self.store.history('task'), 'A')['completed_effective_rounds'], 1)

    async def test_plan_bound_new_research_question_can_count_despite_unknown_legacy_identity(self):
        _, event = await self.feedback_after_unavailable_legacy_build('missing', research_question=True)
        self.assertTrue(event['effective'])
        self.assertEqual(self.api.exploration_status(self.store.history('task'), 'A')['completed_effective_rounds'], 2)


if __name__ == '__main__':
    unittest.main()
