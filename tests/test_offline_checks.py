import copy
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.contracts import (ArtifactRef, CandidateIdentity, Decision, EvaluationContext,
    ParentCandidate, Strategy, Task, Workload, canonical_json)
from rsi_kernel_agent.profiler.analyzer import Analyzer
from rsi_kernel_agent.profiler.inputs import AnalysisRequest, capture_inputs
from rsi_kernel_agent.store import ExperimentStore
from test_analyzer import proposal


class OfflineCheckTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = ExperimentStore(self.root / 'store.sqlite', self.root / 'artifacts')
        self.source = self.put(b'def kernel(x):\n    return x\n')
        self.oracle = self.put(b'def reference(x):\n    return x\n')
        self.helper = self.put(b'VALUE = 1\n')
        self.bundle = self.put({'schema_version': 1, 'entry': {'path': 'kernel.py', 'symbol': 'kernel'},
            'files': [{'path': 'kernel.py', 'artifact_ref': self.source, 'role': 'source'},
                      {'path': 'helper.py', 'artifact_ref': self.helper, 'role': 'dependency'}]})
        self.oracle_bundle = self.put({'schema_version': 1, 'entry': {'path': 'oracle.py', 'symbol': 'reference'},
            'files': [{'path': 'oracle.py', 'artifact_ref': self.oracle, 'role': 'oracle'}]})
        self.runner = self.put((ROOT / 'src/rsi_kernel_agent/workflow/mla_runner.py').read_bytes())
        self.config = self.put({'schema_version': 1, 'kernel': 'mla_decode', 'case_overrides': {}})
        self.build = self.put({'schema_version': 1, 'source_bundle_digest': self.bundle['digest'],
            'oracle_bundle_digest': self.oracle_bundle['digest'], 'runner_digest': self.runner['digest'],
            'oracle_digest': self.oracle['digest']})
        self.workload = {'kernel': 'mla_decode', 'interface': 'contiguous', 'cases': [
            {'id': 'tiny', 'config_name': 'mla_decode', 'overrides': {'params': {'interface': 'contiguous'}},
             'outputs': [{'name': 'attention', 'shape': [1, 1, 2], 'dtype': 'bfloat16', 'comparison': 'allclose'},
                         {'name': 'cache', 'shape': [1, 2, 128], 'dtype': 'bfloat16', 'comparison': 'exact'}]}]}
        self.protocol = {'backend_id': 'colab.v1', 'runner_digest': self.runner['digest'],
            'oracle_digest': self.oracle['digest'], 'oracle_bundle_ref': self.oracle_bundle,
            'trusted_files': {'helper.py': self.helper['digest']}, 'correctness': {'atol': .01, 'rtol': .01},
            'benchmark': {'version': 'fixture', 'timer': 'fixture', 'warmup': 1, 'measurements': 2,
                          'statistic': 'median', 'unit': 'ms', 'scope': 'fixture', 'outlier_rule': 'none'},
            'profile': {'required': True, 'parser_digest': self.helper['digest'], 'trace_mode': 'fixture'}}
        for row in (Task('task', 'Local checks only', 'fixed-v1'), Workload('workload', 'task', self.workload),
                    Strategy('A', 'task', 'A'), EvaluationContext('context', 'task', 'workload',
                        {'runtime_session_id': 'historical-fixture', 'tpu_model': 'fixture', 'device_count': 1},
                        self.protocol, 'real'),
                    ParentCandidate('A-parent', 'task', CandidateIdentity(self.source['digest'], self.config['digest'], self.build['digest']))):
            self.store.append(row, row.id)
        snapshot = self.store.create_snapshot('task', 'snapshot')
        request = AnalysisRequest.from_dict({'schema_version': 2, 'request_id': 'checks-input',
            'task_id': 'task', 'workload_id': 'workload', 'strategies': [{'strategy_id': 'A',
                'parent_id': 'A-parent', 'context_id': 'context', 'snapshot_id': snapshot['id'], 'run_id': None,
                'source_artifact': self.source, 'config_artifact': self.config, 'profile_ids': [], 'benchmark_ids': []}],
            'knowledge': [], 'environment': {'design_batch_id': 'batch', 'design_round_id': 'round'},
            'tool_versions': {'fixture': '1'}})
        self.manifest = await capture_inputs(self.store, None, request)
        answer = proposal()
        for name in ('code_understanding', 'observations', 'hypotheses', 'plans'):
            answer[name] = [row for row in answer[name] if row['strategy_id'] == 'A']
        answer['plans'] = answer['plans'][:1]
        answer['plans'][0]['variants'] = ['one']
        self.answer = answer
        self.result = Analyzer(self.manifest, artifacts=self.store.artifacts).advance({'final': answer})
        self.variant = {'label': 'one', 'source_ref': self.source, 'config_ref': self.config,
            'build_ref': self.build, 'source_bundle_ref': self.bundle, 'priority': 0,
            'cost': {'executions': 1, 'tokens': 0, 'time_s': 1800}}
        self.drafts = {'A-P1': [self.variant]}

    def put(self, value):
        return asdict(self.store.artifacts.put(value if isinstance(value, bytes) else canonical_json(value).encode()))

    def api(self):
        self.assertIsNotNone(importlib.util.find_spec('rsi_kernel_agent.workflow.offline_checks'),
                             'Offline candidate checks must be implemented')
        from rsi_kernel_agent.workflow import offline_checks
        return offline_checks

    def check(self, drafts=None, **options):
        return self.api().check_drafts(self.store, self.manifest, self.result,
            self.drafts if drafts is None else drafts, output_dir=self.root / 'checks', **options)

    def statuses(self, report):
        return {row['kind']: row['status'] for row in report['checks']}

    async def test_static_checks_never_execute_candidate_or_claim_correctness(self):
        self.source = self.put(b'raise RuntimeError("candidate executed")\ndef kernel(x): return x\n')
        bundle = copy.deepcopy(self.store.artifacts.read(ArtifactRef(**self.bundle)))
        value = json.loads(bundle)
        value['files'][0]['artifact_ref'] = self.source
        self.variant['source_ref'] = self.source
        self.variant['source_bundle_ref'] = self.put(value)
        build = json.loads(self.store.artifacts.read(ArtifactRef(**self.build)))
        build['source_bundle_digest'] = self.variant['source_bundle_ref']['digest']
        self.variant['build_ref'] = self.put(build)
        before = [r.id for r in self.store.history('task')]
        report = self.check()
        self.assertFalse(report['ready'])
        self.assertEqual(self.statuses(report)['static'], 'passed')
        self.assertEqual(self.statuses(report)['oracle_reference'], 'unavailable')
        self.assertEqual(self.statuses(report)['candidate_numerics'], 'unavailable')
        additions = [r for r in self.store.history('task') if r.id not in before]
        self.assertTrue(additions)
        self.assertTrue(all(type(r) is Decision and r.target_id == 'A' and r.details['type'] == 'offline_checks.v1' for r in additions))
        self.assertEqual(self.api().validate_checks(self.store, self.manifest, self.result, self.drafts, report), report)

    async def test_no_plan_requires_no_drafts_and_does_not_need_cpu(self):
        answer = copy.deepcopy(self.answer)
        answer['plans'] = []
        answer['outcome'] = {'status': 'needs_research', 'reason': 'No supported implementation', 'refs': ['source:A']}
        self.result = Analyzer(self.manifest).advance({'final': answer})
        report = self.check({})
        self.assertTrue(report['ready'])
        self.assertFalse(self.check(self.drafts)['ready'])

    async def test_missing_extra_duplicate_variant_and_device_are_failed_reports(self):
        invalid = [{}, {**self.drafts, 'unknown': [self.variant]}, {'A-P1': []},
                   {'A-P1': [self.variant, copy.deepcopy(self.variant)]},
                   {'A-P1': [{**self.variant, 'device': 'not-an-offline-field'}]},
                   {'A-P1': [{**self.variant, 'label': 'another-label'}]}]
        for drafts in invalid:
            with self.subTest(drafts=drafts):
                report = self.check(drafts)
                self.assertFalse(report['ready'])
                self.assertEqual(self.statuses(report)['static'], 'failed')
                self.assertTrue(self.store.get(report['decision_id']))

    async def test_syntax_error_or_trusted_dependency_change_blocks_ready(self):
        for source, helper in [(b'def broken(\n', self.helper), (b'def kernel(x): return x\n', self.put(b'VALUE = 2\n'))]:
            with self.subTest(source=source):
                variant = copy.deepcopy(self.variant)
                variant['source_ref'] = self.put(source)
                bundle = json.loads(self.store.artifacts.read(ArtifactRef(**self.bundle)))
                bundle['files'][0]['artifact_ref'] = variant['source_ref']
                bundle['files'][1]['artifact_ref'] = helper
                variant['source_bundle_ref'] = self.put(bundle)
                build = json.loads(self.store.artifacts.read(ArtifactRef(**self.build)))
                build['source_bundle_digest'] = variant['source_bundle_ref']['digest']
                variant['build_ref'] = self.put(build)
                self.assertFalse(self.check({'A-P1': [variant]})['ready'])

    async def test_config_cannot_change_semantics_or_frozen_oracle(self):
        for field, payload in [('config_ref', {'schema_version': 1, 'kernel': 'mla_decode',
                'case_overrides': {'tiny': {'params': {'interpret_mode': True}}}}),
                ('build_ref', {'schema_version': 1, 'source_bundle_digest': self.bundle['digest'],
                 'oracle_bundle_digest': self.oracle_bundle['digest'], 'runner_digest': self.runner['digest'],
                 'oracle_digest': '0' * 64})]:
            variant = {**self.variant, field: self.put(payload)}
            self.assertFalse(self.check({'A-P1': [variant]})['ready'])

    async def test_check_report_must_be_registered_unchanged_and_bound(self):
        report = self.check()
        for field, value in [('ready', True), ('subject_digest', '0' * 64), ('decision_id', 'not-registered')]:
            tampered = copy.deepcopy(report)
            tampered[field] = value
            with self.assertRaises(ValueError):
                self.api().validate_checks(self.store, self.manifest, self.result, self.drafts, tampered)
        with self.assertRaises(ValueError):
            self.api().validate_checks(self.store, self.manifest, self.result, {}, report)
        raw = self.store.artifacts.path_for(ArtifactRef(**report['report_ref']))
        raw.write_bytes(b'{}')
        with self.assertRaises(ValueError):
            self.api().validate_checks(self.store, self.manifest, self.result, self.drafts, report)

    async def test_revalidation_rejects_later_tampered_candidate_artifact(self):
        report = self.check()
        self.store.artifacts.path_for(ArtifactRef(**self.helper)).write_bytes(b'VALUE = 9\n')
        with self.assertRaises(ValueError):
            self.api().validate_checks(self.store, self.manifest, self.result, self.drafts, report)

    async def test_existing_python_missing_dependencies_is_unavailable_not_passed(self):
        report = self.check(cpu_python=sys.executable)
        self.assertFalse(report['ready'])
        self.assertEqual(self.statuses(report)['oracle_reference'], 'unavailable')
        row = next(r for r in report['checks'] if r['kind'] == 'oracle_reference')
        self.assertIn('missing', row['reason'].lower())
        self.assertTrue(self.store.artifacts.read(ArtifactRef(**row['log_ref'])))

    async def test_cpu_oracle_failure_cannot_be_replaced_by_unavailable_for_same_drafts(self):
        from rsi_kernel_agent.workflow import offline_checks
        with patch.object(offline_checks, '_probe', return_value=(
                {'python': 'fixture', 'executable': sys.executable, 'missing': []}, {'fixture_probe': True})), \
             patch.object(offline_checks.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, b'', b'fixture failure')):
            failed = self.check(cpu_python=sys.executable)
        self.assertFalse(failed['ready'])
        unchecked = self.check()
        self.assertFalse(unchecked['ready'], 'Omitting the CPU check must not erase its previous failure')
        self.assertEqual(self.statuses(unchecked)['oracle_reference'], 'failed')
        priority_only = copy.deepcopy(self.drafts)
        priority_only['A-P1'][0]['priority'] = 7
        self.assertFalse(self.check(priority_only)['ready'], 'Queue metadata cannot clear a frozen Oracle failure')

    async def test_cpu_timeout_is_failed_with_retained_log(self):
        from rsi_kernel_agent.workflow import offline_checks
        with patch.object(offline_checks, '_probe', return_value=(
                {'python': 'fixture', 'executable': sys.executable, 'missing': []}, {'fixture_probe': True})), \
             patch.object(offline_checks.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixture', 120)):
            report = self.check(cpu_python=sys.executable)
        self.assertFalse(report['ready'])
        self.assertEqual(self.statuses(report)['oracle_reference'], 'failed')
        self.assertEqual(self.api().validate_checks(self.store, self.manifest, self.result, self.drafts, report), report)

    async def test_real_subprocess_frozen_oracle_fixture_records_outputs_not_candidate_correctness(self):
        from rsi_kernel_agent.workflow import offline_checks
        script = b'''import hashlib,json,sys\nfrom pathlib import Path\np=json.loads(Path(sys.argv[2]).read_text())\nb=p["bundle"]\nidentity={"source_bundle_digest":p["bundle_digest"],"entry":b["entry"],"loaded_files":[{"path":f["path"],"digest":f["artifact_ref"]["digest"]} for f in sorted(b["files"],key=lambda f:f["path"])]}\nPath(p["inputs_path"]).write_bytes(b"fixture-input-not-MLA")\nPath(p["outputs_path"]).write_bytes(b"fixture-reference-not-MLA")\nPath(p["metadata_path"]).write_text(json.dumps({"oracle_identity":identity}))\n'''
        runner = self.put(script)
        from dataclasses import replace
        context = replace(self.store.get('context'), id='fixture-cpu', protocol={**self.protocol, 'runner_digest': runner['digest']})
        self.store.append(context, context.id)
        request = self.manifest.to_dict()['request']
        request['request_id'] = 'cpu-fixture-input'
        request['strategies'][0]['context_id'] = context.id
        request['strategies'][0]['snapshot_id'] = self.store.create_snapshot('task', 'cpu-fixture-snapshot')['id']
        self.manifest = await capture_inputs(self.store, None, AnalysisRequest.from_dict(request))
        self.result = Analyzer(self.manifest, artifacts=self.store.artifacts).advance({'final': self.answer})
        build = json.loads(self.store.artifacts.read(ArtifactRef(**self.build)))
        build['runner_digest'] = runner['digest']
        self.variant['build_ref'] = self.put(build)
        calls = []
        real_run = subprocess.run
        def record_run(*args, **kwargs):
            calls.append(args[0])
            return real_run(*args, **kwargs)
        with patch.object(offline_checks, '_probe', return_value=(
                {'python': sys.version.split()[0], 'executable': sys.executable, 'missing': []}, {'fixture_probe': True})), \
             patch.object(offline_checks.subprocess, 'run', side_effect=record_run):
            report = self.check(cpu_python=sys.executable)
            repeated = self.check(cpu_python=sys.executable)
        self.assertEqual(len(calls), 2, 'An unavailable candidate check cannot be cached as ready')
        self.assertFalse(repeated['ready'])
        self.assertFalse(report['ready'])
        self.assertEqual(self.statuses(report)['oracle_reference'], 'passed')
        self.assertEqual(self.statuses(report)['candidate_numerics'], 'unavailable')
        self.assertEqual(report['candidate_correctness'], 'unknown')
        self.assertFalse(report['hardware_executed'])
        self.assertEqual(self.api().validate_checks(self.store, self.manifest, self.result, self.drafts, report), report)

    async def test_oracle_loaded_file_order_does_not_change_identity(self):
        from rsi_kernel_agent.workflow import offline_checks
        extra = self.put(b'HELPER = 1\n')
        self.oracle_bundle = self.put({'schema_version': 1, 'entry': {'path': 'oracle.py', 'symbol': 'reference'},
            'files': [{'path': 'oracle.py', 'artifact_ref': self.oracle, 'role': 'oracle'},
                      {'path': 'a-helper.py', 'artifact_ref': extra, 'role': 'dependency'}]})
        self.protocol['oracle_bundle_ref'] = self.oracle_bundle
        build = json.loads(self.store.artifacts.read(ArtifactRef(**self.build)))
        build['oracle_bundle_digest'] = self.oracle_bundle['digest']
        self.build = self.put(build)
        self.variant['build_ref'] = self.build
        await self.test_real_subprocess_frozen_oracle_fixture_records_outputs_not_candidate_correctness()

    async def test_cpu_scratch_is_removed_after_archiving_outcome(self):
        report = self.check(cpu_python=sys.executable)
        self.assertEqual(self.statuses(report)['oracle_reference'], 'unavailable')
        self.assertEqual(list((self.root / 'checks').glob('oracle-*')), [])
        row = next(r for r in report['checks'] if r['kind'] == 'oracle_reference')
        self.assertTrue(self.store.artifacts.read(ArtifactRef(**row['log_ref'])))

    async def test_missing_python_executable_is_explicitly_unavailable(self):
        report = self.check(cpu_python=str(self.root / 'missing-python.exe'))
        self.assertFalse(report['ready'])
        self.assertEqual(self.statuses(report)['oracle_reference'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
