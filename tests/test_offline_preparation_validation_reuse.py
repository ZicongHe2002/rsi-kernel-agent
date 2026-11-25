"""Bound route replay work without trusting previously checked mutable files."""
import copy
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import test_offline_checks as check_fixtures
from rsi_kernel_agent.contracts import ArtifactRef, Decision, canonical_json
from rsi_kernel_agent.profiler import analyzer, inputs, outputs
from rsi_kernel_agent.workflow import offline_checks, offline_preparation
from rsi_kernel_agent.workflow.intake import _id


class OfflineRouteValidationReuseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = check_fixtures.OfflineCheckTests()
        # The nested fixture registers cleanup on this running TestCase.
        self.fixture.addCleanup = self.addCleanup
        await self.fixture.asyncSetUp()
        from test_cpu_candidates import configure_cpu_fixture, cpu_fixture_execution
        context_id = await configure_cpu_fixture(self.fixture)
        self.enterContext(cpu_fixture_execution())
        self.store = self.fixture.store
        assessments = {'A': {'status': 'hypothesis', 'reason': 'Prepare a local candidate',
                             'hypothesis': 'Preserve values with a changed expression', 'refs': []}}
        self.prep = await offline_preparation.begin_preparation(
            self.store, None, 'task', context_id, 'validation-reuse', assessments,
            parents={'A': 'A-parent'})
        self.manifest = offline_preparation.preparation_manifest(self.store, self.prep.id, 'A')
        reader = analyzer.Analyzer(self.manifest, artifacts=self.store.artifacts,
                                   max_turns=100, max_tool_calls=100)
        offset = 0
        while offset is not None:
            reader.advance({'tool_calls': [{'id': 'read-' + str(offset), 'name': 'context_page',
                'arguments': {'offset': offset, 'limit': 24000}}]})
            offset = reader.last_results[0]['result']['next_offset']
        self.result = reader.advance({'final': self.fixture.answer})

        # A candidate-only blob exercises checks beyond the parent's manifest.
        original_source = self.store.artifacts.read(ArtifactRef(**self.fixture.source))
        self.candidate_source = self.fixture.put(original_source + b'\nCHECKED_VARIANT = 1\n')
        variant = copy.deepcopy(self.fixture.variant)
        variant['source_ref'] = self.candidate_source
        bundle = json.loads(self.store.artifacts.read(ArtifactRef(**variant['source_bundle_ref'])))
        next(row for row in bundle['files'] if row['path'] == bundle['entry']['path'])['artifact_ref'] = self.candidate_source
        variant['source_bundle_ref'] = self.fixture.put(bundle)
        build = json.loads(self.store.artifacts.read(ArtifactRef(**variant['build_ref'])))
        build['source_bundle_digest'] = variant['source_bundle_ref']['digest']
        variant['build_ref'] = self.fixture.put(build)
        self.drafts = {'A-P1': [variant]}
        self.checks = offline_checks.check_drafts(
            self.store, self.manifest, self.result, self.drafts,
            cpu_python=sys.executable, output_dir=self.fixture.root / 'reuse-checks')
        self.assertTrue(self.checks['ready'])

    def seal(self):
        return offline_preparation.seal_preparation_route(
            self.store, self.prep.id, 'A', self.result, self.drafts, self.checks)

    def register_unchecked_route(self, *, result=None, checks=None):
        """Construct an adversarial producer record, with correct outer hashes."""
        result = self.result if result is None else result
        checks = self.checks if checks is None else checks
        outcome = {'status': 'planned', 'reason': 'All offline proposals retained'}
        payload = {'schema_version': 1, 'preparation_id': self.prep.id, 'strategy_id': 'A',
                   'manifest': asdict(self.manifest.artifact_ref), 'result': result,
                   'drafts': self.drafts, 'checks': checks,
                   'coverage': offline_preparation.require_delivery(self.manifest.to_dict(), self.result),
                   'outcome': outcome, 'executable': False}
        ref = asdict(self.store.artifacts.put(canonical_json(payload).encode('utf-8')))
        rid = _id('offline-route', self.prep.id, 'A')
        details = {'type': 'offline_route.v1', 'preparation_id': self.prep.id,
                   'route_ref': ref, 'outcome': outcome}
        for name in ('result', 'drafts', 'checks'):
            details[name + '_digest'] = sha256(canonical_json(payload[name]).encode('utf-8')).hexdigest()
        marker = Decision(rid, 'task', 'note', 'A', 'Adversarial test producer', details)
        self.store.append(marker, marker.id)
        return marker

    async def test_route_reconstruction_does_not_repeat_manifest_validation_or_analysis(self):
        closed = self.seal()
        before = self.store.history('task')
        # Spies preserve real loading and replay; setup is outside the budget.
        with patch.object(inputs, 'load_manifest', wraps=inputs.load_manifest) as loading, \
             patch.object(offline_preparation, 'load_manifest', loading), \
             patch.object(analyzer, 'Analyzer', wraps=analyzer.Analyzer) as replaying:
            payload = offline_preparation.read_route(self.store, closed.id)
        self.assertEqual(payload['result']['plan_set']['plans'][0]['id'], 'A-P1')
        self.assertEqual(payload['drafts']['A-P1'][0]['source_ref'], self.candidate_source)
        self.assertFalse(payload['executable'])
        self.assertEqual(self.store.history('task'), before)
        self.assertEqual({'manifest_loads': loading.call_count, 'analysis_replays': replaying.call_count},
                         {'manifest_loads': 1, 'analysis_replays': 1})

    async def test_route_replay_rejects_altered_result_with_valid_outer_hashes(self):
        changed = copy.deepcopy(self.result)
        changed['plan_set']['plans'][0]['modification'] = 'Unreviewed producer edit'
        closed = self.register_unchecked_route(result=changed)
        with self.assertRaisesRegex(ValueError, 'deterministic exchange replay|PlanSet'):
            offline_preparation.read_route(self.store, closed.id)

    async def test_route_checks_reject_altered_report_with_valid_outer_hashes(self):
        changed = copy.deepcopy(self.checks)
        changed['checks'][0]['reason'] = 'Unrecorded producer claim'
        closed = self.register_unchecked_route(checks=changed)
        with self.assertRaisesRegex(ValueError, 'archived bytes'):
            offline_preparation.read_route(self.store, closed.id)

    async def test_each_route_read_rechecks_current_manifest_and_check_artifacts(self):
        closed = self.seal()
        targets = {'manifest': asdict(self.manifest.artifact_ref),
                   'candidate_source': self.candidate_source,
                   'check_report': self.checks['report_ref'],
                   'check_log': self.checks['checks'][0]['log_ref']}
        for name, ref in targets.items():
            with self.subTest(artifact=name):
                self.assertEqual(offline_preparation.read_route(self.store, closed.id)['outcome']['status'], 'planned')
                path = self.store.artifacts.path_for(ArtifactRef(**ref))
                original = path.read_bytes()
                try:
                    path.write_bytes(b'corrupted after successful validation')
                    with self.assertRaisesRegex(ValueError, 'integrity'):
                        offline_preparation.read_route(self.store, closed.id)
                finally:
                    path.write_bytes(original)

    async def test_public_validators_recheck_bytes_of_a_previously_loaded_manifest(self):
        path = self.store.artifacts.path_for(self.manifest.artifact_ref)
        original = path.read_bytes()
        try:
            path.write_bytes(b'corrupted after manifest was loaded')
            with self.subTest(entry='validate_result'), self.assertRaisesRegex(ValueError, 'integrity'):
                outputs.validate_result(self.store, self.manifest, self.result)
            with self.subTest(entry='validate_checks'), self.assertRaisesRegex(ValueError, 'integrity'):
                offline_checks.validate_checks(self.store, self.manifest, self.result, self.drafts, self.checks)
        finally:
            path.write_bytes(original)


if __name__ == '__main__':
    unittest.main()
