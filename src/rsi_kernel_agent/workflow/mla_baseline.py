"""Prepare an unverified MLA baseline using the original Store and source tree.

Preparation is not execution or validation. A real context requires an observed
Runtime fingerprint; no historical result is promoted by this module.
"""
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

from ..contracts import (Task, Workload, EvaluationContext, Strategy, Plan, ParentCandidate,
                         Candidate, CandidateIdentity, Run, Decision, canonical_json)
from ..associations import MemoryWriter
from .execution import execution_contract, make_request, stable_id
from .mla_runner import validate_workload

SOURCE_ROOT = Path(r'C:\Users\fengluo\Desktop\training\PallasKernels')
ENTRY = {'path': 'pallas_kernels/kernels/mla_decode.py', 'symbol': 'build_mla_decode_contiguous'}
ORACLE_ENTRY = {'path': ENTRY['path'], 'symbol': 'mla_decode_paged_reference'}
TOOLS = ('benchmarks/profiler.py', 'benchmarks/runner.py', 'benchmarks/metrics.py',
         'tools/get_tpu_duration_info.py', 'tools/tpu_duration_runtime.py', 'tools/tpu_duration_profile.py')


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _json_ref(store, value):
    return asdict(store.artifacts.put(canonical_json(value).encode('utf-8')))


def _safe(path):
    path = Path(path).absolute()
    _require(not any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction())
                     for p in (path, *path.parents)), 'Source paths cannot contain links')
    return path.resolve()


def audit_source_tree(source_root=SOURCE_ROOT):
    """Index only explicit package source/config trees and audited tools."""
    root = _safe(source_root)
    required = (ENTRY['path'], 'jax_ops/mla_decode.py', 'pallas_kernels/configs/mla_decode.yaml',
                'pallas_kernels/configs/mla_decode_vcs.yaml', *TOOLS)
    _require(all((root / name).is_file() for name in required), 'Source root is not the audited PallasKernels layout')
    paths = set(required)
    for package in ('pallas_kernels', 'jax_ops'):
        for path in (root / package).rglob('*'):
            relative = path.relative_to(root)
            if any(part.startswith('.') or part == '__pycache__' for part in relative.parts):
                continue
            _safe(path)
            if path.is_file() and (path.suffix == '.py' or
                                  package == 'pallas_kernels' and relative.parts[1:2] == ('configs',) and path.suffix == '.yaml'):
                paths.add(relative.as_posix())
    rows = []
    for name in sorted(paths):
        data = _safe(root / name).read_bytes()
        rows.append({'path': name, 'size': len(data), 'digest': sha256(data).hexdigest()})
    return {'schema_version': 1, 'classification': 'Source audit', 'root': str(root),
            'entry': dict(ENTRY), 'files': rows}


def prepare_sources(store, source_root=SOURCE_ROOT):
    """Snapshot source bytes into the existing ArtifactStore, without any Run."""
    audit = audit_source_tree(source_root)
    root = Path(audit['root'])
    files = []
    for row in audit['files']:
        data = (root / row['path']).read_bytes()
        _require(sha256(data).hexdigest() == row['digest'] and len(data) == row['size'], 'Source changed during snapshot')
        files.append({'path': row['path'], 'artifact_ref': asdict(store.artifacts.put(data)),
                      'role': 'source' if row['path'] == ENTRY['path'] else 'dependency'})
    source = {'schema_version': 1, 'entry': dict(ENTRY), 'files': files}
    oracle = {'schema_version': 1, 'entry': dict(ORACLE_ENTRY),
              'files': [{**row, 'role': 'oracle' if row['path'] == ENTRY['path'] else 'dependency'} for row in files]}
    entry_ref = next(row['artifact_ref'] for row in files if row['path'] == ENTRY['path'])
    return {'source_bundle_ref': _json_ref(store, source), 'oracle_bundle_ref': _json_ref(store, oracle),
            'source_ref': entry_ref, 'oracle_digest': entry_ref['digest'], 'audit_ref': _json_ref(store, audit),
            # The source entry is the only editable Python file. Its independent
            # oracle copy is immutable in the separate Oracle bundle.
            'trusted_files': {row['path']: row['artifact_ref']['digest'] for row in files if row['path'] != ENTRY['path']}}


def workload_matrix():
    def case(name, batch, capacity, latent, block, *, boundary=False):
        result = {'id': name, 'config_name': 'mla_decode',
                  'overrides': {'shape': {'batch': batch, 'num_q_heads': 16, 'seq_len': capacity,
                                          'lkv_dim': latent, 'r_dim': 64},
                                'tiling': {'block_size': block},
                                'params': {'interface': 'contiguous', 'interpret_mode': False}},
                  'outputs': [{'name': 'attention', 'shape': [batch, 16, latent], 'dtype': 'bfloat16', 'comparison': 'allclose'},
                              {'name': 'cache', 'shape': [batch, capacity, latent + 128], 'dtype': 'bfloat16', 'comparison': 'exact'}]}
        if boundary:
            result.update(lengths=[0, 1, 129], opaque_tail_nan=True)
        return result
    return [case('decode-small', 1, 1024, 512, 1024),
            case('decode-boundary', 3, 256, 128, 128, boundary=True),
            case('decode-default', 8, 16384, 512, 1024)]


def _observed_v6e(environment):
    _require(type(environment) is dict, 'A real observed runtime environment is required')
    for name in ('runtime_session_id', 'python', 'jax', 'jaxlib', 'libtpu', 'tpu_model'):
        _require(type(environment.get(name)) is str and bool(environment[name]), 'Missing observed runtime field: ' + name)
    _require(environment['tpu_model'].lower() in ('tpu v6e', 'tpu v6 lite'), 'Observed device is not the target TPU v6e')
    _require(type(environment.get('device_count')) is int and environment['device_count'] > 0
             and environment.get('process_count') == 1, 'Invalid observed device/process count')
    devices = environment.get('devices')
    _require(type(devices) is list and len(devices) == environment['device_count']
             and all(d.get('platform') == 'tpu' and d.get('device_kind') == environment['tpu_model'] for d in devices),
             'Observed devices do not agree with hardware identity')


def create_baseline(store, environment, *, source_root=SOURCE_ROOT, task_id='mla-decode-v1',
                    benchmark_case_id='decode-small', cases=None, key='baseline:1'):
    """Create a ready, unverified baseline job; the backend/evaluator run it later."""
    _observed_v6e(environment)
    spec = validate_workload({'kernel': 'mla_decode', 'interface': 'contiguous',
                              'cases': workload_matrix() if cases is None else deepcopy(cases)})
    _require(benchmark_case_id in {case['id'] for case in spec['cases']}, 'Unknown benchmark case')
    sources = prepare_sources(store, source_root)
    runner_raw = Path(__file__).with_name('mla_runner.py').read_bytes()
    runner_digest = store.artifacts.put(runner_raw).digest
    benchmark = {'version': 'mla-contiguous-host-sync-v1', 'timer': 'original-benchmarks.profiler.measure',
                 'warmup': 5, 'measurements': 20, 'statistic': 'median', 'unit': 'ms',
                 'scope': 'public-contiguous-prepare-attention-full-cache-output-host-synchronized', 'outlier_rule': 'none'}
    protocol = {'backend_id': 'colab.v1', 'runner_digest': runner_digest,
                'oracle_digest': sources['oracle_digest'], 'oracle_bundle_ref': sources['oracle_bundle_ref'],
                'trusted_files': sources['trusted_files'], 'correctness': {'atol': .01, 'rtol': .01},
                'benchmark': benchmark, 'benchmark_case_id': benchmark_case_id,
                'profile': {'required': True, 'parser_digest': sources['trusted_files']['tools/tpu_duration_profile.py'],
                            'trace_mode': 'default_profile_options', 'profile_calls': 20, 'profile_warmup': 5}}
    task = Task(task_id, 'MLA Decode contiguous baseline and optimization', 'mla-contiguous-full-cache-bf16-v1')
    workload = Workload(stable_id('mla-workload', task_id, spec), task_id, spec)
    context = EvaluationContext(stable_id('mla-context', task_id, workload.id, environment, protocol),
                                task_id, workload.id, deepcopy(environment), protocol, 'real')
    strategy = Strategy(stable_id('mla-strategy', task_id, 'baseline'), task_id, 'Unmodified baseline validation')
    plan = Plan(stable_id('mla-plan', strategy.id, context.id), strategy.id,
                'Validate unchanged authoritative MLA source before optimization; no Analyzer optimization claim')
    config_ref = _json_ref(store, {'schema_version': 1, 'kernel': 'mla_decode', 'case_overrides': {}})
    build_ref = _json_ref(store, {'schema_version': 1, 'source_bundle_digest': sources['source_bundle_ref']['digest'],
                                  'oracle_bundle_digest': sources['oracle_bundle_ref']['digest'],
                                  'runner_digest': runner_digest, 'oracle_digest': sources['oracle_digest']})
    identity = CandidateIdentity(sources['source_ref']['digest'], config_ref['digest'], build_ref['digest'])
    parent = ParentCandidate(stable_id('mla-import', task_id, asdict(identity)), task_id, identity)
    candidate = Candidate(stable_id('mla-candidate', plan.id, asdict(identity)), plan.id, parent.id, identity)
    run = Run(stable_id('mla-run', candidate.id, context.id, key), candidate.id, context.id)
    for record in (task, workload, context, strategy, plan, parent, candidate, run):
        store.append(record, record.id)
    MemoryWriter(store).bind_plan(plan.id, parent.id, stable_id('mla-bind', plan.id))
    contract, contract_digest = execution_contract(store, context.id)
    variant = {'label': 'Unmodified authoritative baseline', 'source_ref': sources['source_ref'],
               'source_bundle_ref': sources['source_bundle_ref'], 'config_ref': config_ref, 'build_ref': build_ref,
               'priority': 0, 'device': environment['runtime_session_id'],
               'cost': {'executions': 1, 'tokens': 0, 'time_s': 1800.0}}
    job = {'id': stable_id('mla-job', run.id), 'task_id': task_id, 'round_id': stable_id('mla-baseline-round', task_id, context.id),
           'strategy_id': strategy.id, 'plan_id': plan.id, 'parent_id': parent.id, 'context_id': context.id,
           'candidate_id': candidate.id, 'run_id': run.id, 'parent_evaluation_id': None,
           'variant': variant, 'contract': contract, 'contract_digest': contract_digest,
           'state': 'queued', 'stage': 'generate', 'receipts': {}, 'attempt': 0, 'repair_depth': 0,
           'result': None, 'reason': 'Unverified baseline preparation', 'reserved': False}
    make_request(store, job, 'generate')
    details = {'type': 'mla_baseline_preparation.v1', 'job': job, 'audit_ref': sources['audit_ref'],
               'classification': 'Source audit', 'hardware_executed': False}
    note = Decision(stable_id('mla-preparation', run.id), task_id, 'note', run.id,
                    'Prepared original-source baseline; no execution or performance claim', details)
    store.append(note, note.id)
    source_note = Decision(stable_id('mla-source', candidate.id, run.id), task_id, 'note', candidate.id,
                          'Immutable baseline source artifacts', {'type': 'workflow_candidate.v1',
                          **{name: variant[name] for name in ('source_ref', 'source_bundle_ref', 'config_ref', 'build_ref')},
                          'job_id': job['id'], 'provenance': 'real'})
    store.append(source_note, source_note.id)
    return job
