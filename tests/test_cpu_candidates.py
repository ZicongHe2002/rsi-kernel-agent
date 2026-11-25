import copy
from contextlib import contextmanager
from dataclasses import asdict, replace
from hashlib import sha256
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
from rsi_kernel_agent.contracts import ArtifactRef, Decision, canonical_json
from rsi_kernel_agent.profiler.analyzer import Analyzer
from rsi_kernel_agent.profiler.inputs import AnalysisRequest, capture_inputs


_RUNTIME = '''
import json, math, struct, sys, types
class Array:
    def __init__(self, values, shape, dtype='bfloat16'):
        self.values = list(values)
        self.shape = tuple(shape)
        self.dtype = dtype
    def copy(self):
        return Array(self.values, self.shape, self.dtype)
    def tobytes(self, order='C'):
        return b''.join(struct.pack('<d', x) for x in self.values)
    def astype(self, dtype):
        return self
    def ravel(self):
        return self
    def tolist(self):
        return list(self.values)
class Finite:
    def __init__(self, array): self.array = array
    def all(self): return all(math.isfinite(x) for x in self.array.values)
np = types.ModuleType('numpy')
np.asarray = lambda x: x
np.isfinite = lambda x: Finite(x)
np.float64 = float
np.__version__ = 'cpu-protocol-fixture'
sys.modules['numpy'] = np
jax = types.ModuleType('jax')
jax.devices = lambda: [types.SimpleNamespace(platform='cpu')]
jax.default_backend = lambda: 'cpu'
jax.device_put = lambda arrays: tuple(x.copy() for x in arrays)
jax.block_until_ready = lambda value: value
jax.process_count = lambda: 1
jax.__version__ = 'cpu-protocol-fixture'
sys.modules['jax'] = jax
'''


def _fixture_runner():
    raw = (ROOT / 'src/rsi_kernel_agent/workflow/mla_runner.py').read_text(encoding='utf-8')
    raw = raw.split("if __name__ == '__main__':")[0]
    return raw + _RUNTIME + '''
def _load_arrays(path):
    return tuple(Array(row['values'], row['shape'], row['dtype']) for row in json.loads(Path(path).read_text()))
def _save_arrays(path, arrays):
    Path(path).write_text(json.dumps([{'values': row.values, 'shape': row.shape, 'dtype': row.dtype} for row in arrays]))
    return []
if __name__ == '__main__':
    p = json.loads(Path(sys.argv[2]).read_text())
    inputs = [Array([1.0, 2.0], [1, 1, 2]), Array([0.0], [1]), Array([4.0, float('nan')], [1, 2]), Array([1.0], [1], 'int32')]
    _save_arrays(p['inputs_path'], inputs)
    _save_arrays(p['outputs_path'], (inputs[0], inputs[2]))
    loaded = verify_loaded_bundle(p['workspace'], p['bundle'])
    _write_json(p['metadata_path'], {'oracle_identity': {'source_bundle_digest': p['bundle_digest'], 'entry': p['bundle']['entry'], 'loaded_files': loaded}})
'''


_CANDIDATE = '''
def build_mla_decode_contiguous(*args):
    return args[0], args[2]
'''
_CONFIG = '''
from types import SimpleNamespace
def load_kernel_config(name, overrides):
    return SimpleNamespace(params=overrides.get('params', {}), tiling=overrides.get('tiling', {}),
        dtype='bfloat16', get_tolerance=lambda dtype: {'atol': .01, 'rtol': .01})
'''
_KERNELS = '''
from .mla_decode import build_mla_decode_contiguous
def get_kernel(name, cfg):
    assert cfg.params['interpret_mode'] is True
    assert cfg.tiling['block_size'] == 256
    assert cfg.params['vmem_limit_bytes'] == 4096
    return build_mla_decode_contiguous
'''


async def configure_cpu_fixture(fixture, source=_CANDIDATE, *, real_numpy=False):
    fixture.source = fixture.put(source.encode())
    files = [('pallas_kernels/__init__.py', b''), ('pallas_kernels/kernels/__init__.py', _KERNELS.encode()),
             ('pallas_kernels/kernels/config_loader.py', _CONFIG.encode()),
             ('pallas_kernels/kernels/mla_decode.py', source.encode()), ('helper.py', b'VALUE = 1\n')]
    fixture.bundle = fixture.put({'schema_version': 1, 'entry': {'path': 'pallas_kernels/kernels/mla_decode.py',
        'symbol': 'build_mla_decode_contiguous'}, 'files': [{'path': path, 'artifact_ref': fixture.put(raw),
        'role': 'source' if path.endswith('mla_decode.py') else 'dependency'} for path, raw in files]})
    runner_code = _fixture_runner()
    if real_numpy:
        runner_code = (ROOT / 'src/rsi_kernel_agent/workflow/mla_runner.py').read_text(encoding='utf-8').split("if __name__ == '__main__':")[0]
        runner_code += '''
if __name__ == '__main__':
    import jax.numpy as jnp
    p = json.loads(Path(sys.argv[2]).read_text())
    inputs = (jnp.asarray([[[1., 2.]]], dtype=jnp.bfloat16), jnp.zeros((1,), dtype=jnp.bfloat16),
              jnp.asarray([[4., float('nan')]], dtype=jnp.bfloat16), jnp.asarray([1], dtype=jnp.int32))
    _save_arrays(p['inputs_path'], inputs)
    _save_arrays(p['outputs_path'], (inputs[0], inputs[2]))
    loaded = verify_loaded_bundle(p['workspace'], p['bundle'])
    _write_json(p['metadata_path'], {'oracle_identity': {'source_bundle_digest': p['bundle_digest'], 'entry': p['bundle']['entry'], 'loaded_files': loaded}})
'''
    fixture.runner = fixture.put(runner_code.encode())
    fixture.workload['cases'][0]['overrides'] = {'params': {'interface': 'contiguous', 'vmem_limit_bytes': 2048},
                                                'tiling': {'block_size': 128}}
    fixture.workload['cases'][0]['outputs'][1]['shape'] = [1, 2]
    workload = replace(fixture.store.get('workload'), id='cpu-fixture-workload', spec=fixture.workload)
    fixture.store.append(workload, workload.id)
    fixture.config = fixture.put({'schema_version': 1, 'kernel': 'mla_decode',
        'case_overrides': {'tiny': {'tiling': {'block_size': 256}, 'params': {'vmem_limit_bytes': 4096}}}})
    fixture.protocol['runner_digest'] = fixture.runner['digest']
    context = replace(fixture.store.get('context'), id='cpu-fixture-context', workload_id=workload.id,
                      protocol=fixture.protocol)
    fixture.store.append(context, context.id)
    fixture.build = fixture.put({'schema_version': 1, 'source_bundle_digest': fixture.bundle['digest'],
        'oracle_bundle_digest': fixture.oracle_bundle['digest'], 'runner_digest': fixture.runner['digest'],
        'oracle_digest': fixture.oracle['digest']})
    fixture.variant.update(source_ref=fixture.source, source_bundle_ref=fixture.bundle,
                           config_ref=fixture.config, build_ref=fixture.build)
    request = copy.deepcopy(fixture.manifest.to_dict()['request'])
    request.update(request_id='cpu-fixture-manifest', workload_id=workload.id)
    request['strategies'][0].update(context_id=context.id,
        snapshot_id=fixture.store.create_snapshot('task', 'cpu-check-fixture')['id'])
    fixture.manifest = await capture_inputs(fixture.store, None, AnalysisRequest.from_dict(request))
    fixture.result = Analyzer(fixture.manifest, artifacts=fixture.store.artifacts).advance({'final': fixture.answer})
    return context.id


@contextmanager
def cpu_fixture_execution():
    from rsi_kernel_agent.workflow import offline_checks
    with patch.object(offline_checks, '_probe', return_value=(
            {'python': sys.version.split()[0], 'executable': sys.executable, 'missing': []},
            {'fixture': 'CPU protocol fixture, not a real MLA numerical result'})):
        yield


class CpuCandidateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from test_offline_checks import OfflineCheckTests
        self.f = OfflineCheckTests()
        await self.f.asyncSetUp()
        self.addCleanup(self.f.temp.cleanup)
        await configure_cpu_fixture(self.f)

    def check(self):
        with cpu_fixture_execution():
            return self.f.check(cpu_python=sys.executable)

    async def test_real_candidate_process_merges_config_and_passes_complete_contract(self):
        report = self.check()
        self.assertTrue(report['ready'])
        self.assertEqual(report['schema_version'], 2)
        self.assertEqual(report['candidate_correctness'], 'passed')
        self.assertFalse(report['hardware_executed'])
        self.assertEqual(self.f.statuses(report), {'static': 'passed', 'oracle_reference': 'passed',
                                                  'candidate_numerics': 'passed'})
        self.assertEqual(self.f.api().validate_checks(self.f.store, self.f.manifest, self.f.result, self.f.drafts, report), report)
        self.f.api().require_current_checks(self.f.store, report)
        with patch('subprocess.run', side_effect=AssertionError('Cached report reran CPU')):
            self.assertEqual(self.f.check(cpu_python=sys.executable), report)

    async def test_wrong_attention_complete_cache_and_mutated_input_are_failures(self):
        sources = {
            'attention': 'def build_mla_decode_contiguous(*xs):\n    output = xs[0].copy()\n    output.values[0] += 1\n    return output, xs[2]\n',
            'cache': 'def build_mla_decode_contiguous(*xs):\n    cache = xs[2].copy()\n    cache.values[0] += 1\n    return xs[0], cache\n',
            'input': 'def build_mla_decode_contiguous(*xs):\n    cache = xs[2].copy()\n    xs[2].values[0] += 1\n    return xs[0], cache\n',
        }
        for label, source in sources.items():
            with self.subTest(failure=label):
                variant = self.f.variant
                variant['source_ref'] = self.f.put(source.encode())
                bundle = json.loads(self.f.store.artifacts.read(ArtifactRef(**variant['source_bundle_ref'])))
                next(row for row in bundle['files'] if row['path'].endswith('mla_decode.py'))['artifact_ref'] = variant['source_ref']
                variant['source_bundle_ref'] = self.f.put(bundle)
                build = json.loads(self.f.store.artifacts.read(ArtifactRef(**variant['build_ref'])))
                build['source_bundle_digest'] = variant['source_bundle_ref']['digest']
                variant['build_ref'] = self.f.put(build)
                report = self.check()
                self.assertFalse(report['ready'])
                self.assertEqual(self.f.statuses(report)['candidate_numerics'], 'failed')
                self.assertEqual(report['candidate_correctness'], 'failed')
                with self.assertRaises(ValueError):
                    self.f.api().require_current_checks(self.f.store, report)

    async def test_unavailable_candidate_cannot_replace_prior_failure(self):
        self.f.variant['source_ref'] = self.f.put(b'def build_mla_decode_contiguous(*xs):\n    raise RuntimeError("bad candidate")\n')
        bundle = json.loads(self.f.store.artifacts.read(ArtifactRef(**self.f.variant['source_bundle_ref'])))
        next(row for row in bundle['files'] if row['path'].endswith('mla_decode.py'))['artifact_ref'] = self.f.variant['source_ref']
        self.f.variant['source_bundle_ref'] = self.f.put(bundle)
        build = json.loads(self.f.store.artifacts.read(ArtifactRef(**self.f.variant['build_ref'])))
        build['source_bundle_digest'] = self.f.variant['source_bundle_ref']['digest']
        self.f.variant['build_ref'] = self.f.put(build)
        report = self.check()
        self.assertEqual(self.f.statuses(report)['candidate_numerics'], 'failed')
        unchecked = self.f.check()
        self.assertFalse(unchecked['ready'])
        with self.assertRaises(ValueError):
            self.f.api().require_current_checks(self.f.store, unchecked)

    async def test_legacy_report_remains_readable_but_cannot_authorize_candidates(self):
        report = self.f.check()
        legacy = {key: copy.deepcopy(value) for key, value in report.items() if key not in ('report_ref', 'decision_id')}
        legacy.update(schema_version=1, ready=True, candidate_correctness='unknown')
        ref = self.f.put(legacy)
        rid = 'offline-checks:' + ref['digest']
        self.f.store.append(Decision(rid, 'task', 'note', 'A', 'Historical checks',
            {'type': 'offline_checks.v1', 'subject_digest': legacy['subject_digest'], 'report_ref': ref}), rid)
        historical = {**legacy, 'report_ref': ref, 'decision_id': rid}
        self.assertEqual(self.f.api().validate_checks(self.f.store, self.f.manifest, self.f.result, self.f.drafts,
                                                    historical), historical)
        with self.assertRaises(ValueError):
            self.f.api().require_current_checks(self.f.store, historical)

    async def test_tampered_cpu_result_cannot_be_replayed(self):
        report = self.check()
        row = report['checks'][2]
        log = json.loads(self.f.store.artifacts.read(ArtifactRef(**row['log_ref'])))
        ref = log['candidates'][0]['result_ref']
        self.f.store.artifacts.path_for(ArtifactRef(**ref)).write_bytes(b'{}')
        with self.assertRaises(ValueError):
            self.f.api().validate_checks(self.f.store, self.f.manifest, self.f.result, self.f.drafts, report)

    async def test_unsupported_cpu_candidate_is_not_ready(self):
        variant = self.f.variant
        variant['source_ref'] = self.f.put(b'def build_mla_decode_contiguous(*xs):\n    raise NotImplementedError("CPU backend unsupported")\n')
        bundle = json.loads(self.f.store.artifacts.read(ArtifactRef(**variant['source_bundle_ref'])))
        next(row for row in bundle['files'] if row['path'].endswith('mla_decode.py'))['artifact_ref'] = variant['source_ref']
        variant['source_bundle_ref'] = self.f.put(bundle)
        build = json.loads(self.f.store.artifacts.read(ArtifactRef(**variant['build_ref'])))
        build['source_bundle_digest'] = variant['source_bundle_ref']['digest']
        variant['build_ref'] = self.f.put(build)
        report = self.check()
        self.assertFalse(report['ready'])
        self.assertEqual(self.f.statuses(report)['candidate_numerics'], 'unavailable')
        self.assertEqual(report['candidate_correctness'], 'unknown')

    async def test_revalidation_rejects_oracle_case_coverage_removed_with_valid_hashes(self):
        report = self.check()
        changed = copy.deepcopy(report)
        oracle = json.loads(self.f.store.artifacts.read(ArtifactRef(**changed['checks'][1]['log_ref'])))
        oracle['cases'] = []
        changed['checks'][1]['log_ref'] = self.f.put(oracle)
        raw = {key: value for key, value in changed.items() if key not in ('report_ref', 'decision_id')}
        ref = self.f.put(raw)
        rid = 'offline-checks:' + ref['digest']
        self.f.store.append(Decision(rid, 'task', 'note', 'A', 'Altered bound report',
            {'type': 'offline_checks.v1', 'subject_digest': raw['subject_digest'], 'report_ref': ref}), rid)
        changed.update(report_ref=ref, decision_id=rid)
        with self.assertRaises(ValueError):
            self.f.api().validate_checks(self.f.store, self.f.manifest, self.f.result, self.f.drafts, changed)

    async def test_new_candidate_failure_prevents_reusing_an_older_success(self):
        passed = self.check()
        from rsi_kernel_agent.workflow import offline_checks
        failure = copy.deepcopy(passed)
        failure['ready'] = False
        failure['candidate_correctness'] = 'failed'
        log = json.loads(self.f.store.artifacts.read(ArtifactRef(**failure['checks'][2]['log_ref'])))
        result = log['candidates'][0]['result']
        log['candidates'][0]['result'] = {'schema_version': 1, 'type': 'cpu_candidate_result.v1',
            'subject': result['subject'], 'status': 'failed', 'error_type': 'CpuFailure', 'reason': 'Later failure'}
        failure['checks'][2] = offline_checks._row(self.f.store, 'candidate_numerics', 'failed',
            'Later CPU verification failed', failure['subject_digest'], log,
            versions=failure['checks'][2]['tool_versions'])
        raw = {key: value for key, value in failure.items() if key not in ('report_ref', 'decision_id')}
        ref = self.f.put(raw)
        rid = 'offline-checks:' + ref['digest']
        self.f.store.append(Decision(rid, 'task', 'note', 'A', 'Later failure',
            {'type': 'offline_checks.v1', 'subject_digest': raw['subject_digest'], 'report_ref': ref}), rid)
        with self.assertRaises(ValueError):
            self.f.api().require_current_checks(self.f.store, passed)
        with patch.object(offline_checks, '_oracle', side_effect=AssertionError('Fresh CPU check requested')):
            with self.assertRaisesRegex(AssertionError, 'Fresh CPU'):
                self.f.check(cpu_python=sys.executable)
        self.f.drafts['A-P1'][0]['priority'] = 1
        with patch.object(offline_checks, '_oracle', side_effect=AssertionError('Fresh CPU check requested')):
            with self.assertRaisesRegex(AssertionError, 'Fresh CPU'):
                self.f.check(cpu_python=sys.executable)

    async def test_failure_under_another_scheduling_subject_triggers_fresh_cpu_check(self):
        passed = self.check()
        from rsi_kernel_agent.workflow import offline_checks
        failed = copy.deepcopy(passed)
        failed['drafts']['A-P1'][0]['priority'] = 1
        failed['subject']['drafts_digest'] = offline_checks._digest(failed['drafts'])
        failed['subject_digest'] = offline_checks._digest(failed['subject'])
        failed['ready'], failed['candidate_correctness'] = False, 'failed'
        log = json.loads(self.f.store.artifacts.read(ArtifactRef(**failed['checks'][2]['log_ref'])))
        log['candidates'][0]['result']['status'] = 'failed'
        failed['checks'][2] = offline_checks._row(self.f.store, 'candidate_numerics', 'failed',
            'Later verification failed', failed['subject_digest'], log,
            versions=failed['checks'][2]['tool_versions'])
        raw = {key: value for key, value in failed.items() if key not in ('report_ref', 'decision_id')}
        ref = self.f.put(raw)
        rid = 'offline-checks:' + ref['digest']
        self.f.store.append(Decision(rid, 'task', 'note', 'A', 'Changed scheduling metadata',
            {'type': 'offline_checks.v1', 'subject_digest': raw['subject_digest'], 'report_ref': ref}), rid)
        with patch.object(offline_checks, '_oracle', side_effect=AssertionError('Fresh CPU check requested')):
            with self.assertRaisesRegex(AssertionError, 'Fresh CPU'):
                self.f.check(cpu_python=sys.executable)

    async def test_invalid_static_draft_history_does_not_break_later_valid_cpu_checks(self):
        report = self.check()
        self.assertFalse(self.f.check({'A-P1': [{}]})['ready'])
        self.f.api().require_current_checks(self.f.store, report)

    async def test_changed_cpu_interpreter_cannot_reuse_a_passing_report(self):
        self.check()
        report = self.f.check(cpu_python=str(self.f.root / 'other-python.exe'))
        self.assertFalse(report['ready'])
        self.assertEqual(self.f.statuses(report)['oracle_reference'], 'unavailable')

    async def test_returned_report_is_detached_from_mutable_drafts(self):
        report = self.check()
        before = canonical_json(report)
        self.f.drafts['A-P1'].append(copy.deepcopy(self.f.variant))
        self.f.drafts['A-P1'][0]['priority'] = 99
        self.assertEqual(canonical_json(report), before)

    async def test_failed_sibling_does_not_invalidate_passing_candidate(self):
        passed = self.check()
        log = json.loads(self.f.store.artifacts.read(ArtifactRef(**passed['checks'][2]['log_ref'])))
        original_subject = log['candidates'][0]['result']['subject']
        original_status = log['candidates'][0]['result']['status']
        log['candidates'].append({'result': {'subject': {**original_subject,
            'source_ref': {'digest': 'f' * 64, 'size': 1}}, 'status': 'failed'}})
        from rsi_kernel_agent.workflow import offline_checks
        newer = copy.deepcopy(passed)
        newer['ready'], newer['candidate_correctness'] = False, 'failed'
        newer['checks'][2] = offline_checks._row(self.f.store, 'candidate_numerics', 'failed',
            'Different candidate failed in the same batch', newer['subject_digest'], log,
            versions=newer['checks'][2]['tool_versions'])
        raw = {key: value for key, value in newer.items() if key not in ('report_ref', 'decision_id')}
        ref = self.f.put(raw)
        rid = 'offline-checks:' + ref['digest']
        self.f.store.append(Decision(rid, 'task', 'note', 'A', 'Later mixed local results',
            {'type': 'offline_checks.v1', 'subject_digest': raw['subject_digest'], 'report_ref': ref}), rid)
        self.assertEqual(original_status, 'passed')
        self.f.api().require_current_checks(self.f.store, passed)

    async def test_plan_order_is_stable_after_canonical_report_reload(self):
        first = self.f.answer['plans'][0]
        first['id'] = 'Z-plan'
        second = copy.deepcopy(first)
        second['id'] = 'A-plan'
        second['mechanism'] = 'Independent layout reuse'
        second['modification'] = 'Preserve the kernel contract using a separate layout hypothesis'
        self.f.answer['plans'] = [first, second]
        self.f.drafts = {'Z-plan': [self.f.variant], 'A-plan': [copy.deepcopy(self.f.variant)]}
        self.f.result = Analyzer(self.f.manifest, artifacts=self.f.store.artifacts).advance({'final': self.f.answer})
        report = self.check()
        self.assertTrue(report['ready'])
        restored = json.loads(canonical_json(self.f.drafts))
        restored_report = json.loads(canonical_json(report))
        self.assertEqual(self.f.api().validate_checks(self.f.store, self.f.manifest, self.f.result,
                                                     restored, restored_report), restored_report)

    async def test_no_plan_can_close_without_candidate_cpu_execution(self):
        answer = copy.deepcopy(self.f.answer)
        answer['plans'] = []
        answer['outcome'] = {'status': 'needs_research', 'reason': 'No new hypothesis', 'refs': ['source:A']}
        self.f.result = Analyzer(self.f.manifest).advance({'final': answer})
        report = self.f.check({})
        self.assertTrue(report['ready'])
        self.f.api().require_current_checks(self.f.store, report)

    async def test_each_variant_uses_a_fresh_process_and_preserves_own_identity(self):
        self.f.answer['plans'][0]['variants'] = ['one', 'two']
        self.f.result = Analyzer(self.f.manifest, artifacts=self.f.store.artifacts).advance({'final': self.f.answer})
        second = copy.deepcopy(self.f.variant)
        second['label'] = 'two'
        config = json.loads(self.f.store.artifacts.read(ArtifactRef(**second['config_ref'])))
        config['case_overrides']['tiny']['params']['vmem_limit_bytes'] = 4096
        second['source_ref'] = self.f.put((_CANDIDATE + '\nOTHER = 1\n').encode())
        bundle = json.loads(self.f.store.artifacts.read(ArtifactRef(**second['source_bundle_ref'])))
        next(row for row in bundle['files'] if row['path'].endswith('mla_decode.py'))['artifact_ref'] = second['source_ref']
        second['source_bundle_ref'] = self.f.put(bundle)
        build = json.loads(self.f.store.artifacts.read(ArtifactRef(**second['build_ref'])))
        build['source_bundle_digest'] = second['source_bundle_ref']['digest']
        second['build_ref'] = self.f.put(build)
        self.f.drafts['A-P1'].append(second)
        report = self.check()
        self.assertTrue(report['ready'])
        row = report['checks'][2]
        result = json.loads(self.f.store.artifacts.read(ArtifactRef(**row['log_ref'])))
        self.assertEqual(len(result['candidates']), 2)
        self.assertEqual(len({entry['result']['process_id'] for entry in result['candidates']}), 2)
        self.assertEqual([entry['result']['subject']['source_bundle_ref'] for entry in result['candidates']],
                         [self.f.variant['source_bundle_ref'], second['source_bundle_ref']])


if __name__ == '__main__':
    unittest.main()
