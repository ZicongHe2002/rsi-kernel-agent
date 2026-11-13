"""Prepare detached zero-length single-tile diagnostic records, without execution.

The returned records and run intents require a later genuine Analyzer response,
original Intake, and the existing task Controller. No Plan, Candidate, Run,
receipt, reservation, Store write, or hardware result is fabricated here.
"""
from copy import deepcopy
from dataclasses import asdict
import ast
from hashlib import sha256
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.contracts import EvaluationContext, Workload, validate_record
from rsi_kernel_agent.workflow.execution import stable_id
from rsi_kernel_agent.workflow.mla_baseline import workload_matrix
from rsi_kernel_agent.workflow.mla_runner import validate_candidate_config, validate_workload

_ROLES = ('baseline', 'single_tile')
_DIGESTS = {
    'baseline': '2fc23aa9d0358ea18698f916cbc7ed5e02a4218dec2de2fc955661a8b513ebae',
    'single_tile': '325e0e9098633d625bcac832f9cb043935c7ce205df64a13b53fc84e22133e23',
}
_CASES = ('decode-small', 'decode-boundary')
_COST = {'executions': 1, 'tokens': 0, 'time_s': 1800.0}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _record(cls, payload):
    _require(type(payload) is dict, cls.__name__ + ' must be a detached JSON object')
    try:
        record = cls(**deepcopy(payload))
        validate_record(record)
    except (TypeError, ValueError) as exc:
        raise ValueError('Invalid ' + cls.__name__) from exc
    return record


def _specialization_predicate(source):
    """Read the actual fixed candidate AST without importing or running JAX."""
    tree = ast.parse(source)
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == 'build_mla_decode_contiguous')
    expected = ast.dump(ast.parse('capacity == block_size', mode='eval').body)
    matches = [node.test for node in ast.walk(function)
               if isinstance(node, ast.If) and ast.dump(node.test) == expected]
    _require(len(matches) == 1, 'Expected one actual capacity == block_size specialization')
    return matches[0]


def validate_diagnostic(spec, configs):
    """Reject altered zero-length, shape, opaque-cache, or tile coverage contracts."""
    validate_workload(spec)
    _require(type(configs) is dict and set(configs) == set(_ROLES), 'Both diagnostic role configs are required')
    expected = workload_matrix()[:2]
    expected[0].update(lengths=[0], opaque_tail_nan=True)
    expected[1]['overrides']['tiling']['block_size'] = 256
    _require(spec == {'kernel': 'mla_decode', 'interface': 'contiguous', 'cases': expected},
             'Diagnostic must preserve the fixed small/boundary shapes, exact lengths, and opaque NaN tails')
    for config in configs.values():
        validate_candidate_config(config, spec)
        _require(set(config['case_overrides']) == set(_CASES), 'Both diagnostic cases need explicit tiling')
        for case in spec['cases']:
            effective = deepcopy(case['overrides'])
            for name, values in config['case_overrides'][case['id']].items():
                effective.setdefault(name, {}).update(values)
            _require(effective['shape']['seq_len'] == effective['tiling']['block_size'],
                     'Diagnostic configuration does not enter the single-tile branch')
    _require(configs['baseline'] == configs['single_tile'], 'Diagnostic roles must use identical resource configurations')
    return deepcopy(spec)


def prepare_diagnostic(workload_payload, context_payload, configs, source_bytes):
    """Build new Workload/Context payloads and two unadmitted run intents.

    Inputs are detached records from the same existing task and observed runtime,
    both original candidate configurations, and the archived baseline-repair-1
    and round1-single-tile bytes. This is a preparation artifact only: callers
    must establish current Runtime admission and later package configs through
    the original artifact and Analyzer/Intake mechanisms.
    """
    workload = _record(Workload, workload_payload)
    context = _record(EvaluationContext, context_payload)
    _require(context.task_id == workload.task_id and context.workload_id == workload.id,
             'Original Context and Workload are not bound to the same task')
    _require(context.provenance == 'real' and context.protocol.get('backend_id') == 'colab.v1',
             'Diagnostic requires the original real backend contract')
    _require(context.protocol.get('correctness') == {'atol': .01, 'rtol': .01}
             and context.protocol.get('benchmark_case_id') == 'decode-small',
             'Frozen tolerance or benchmark case differs')
    _require(type(context.environment.get('runtime_session_id')) is str
             and bool(context.environment['runtime_session_id']), 'Observed Runtime identity is absent')
    validate_workload(workload.spec)
    _require(workload.spec == {'kernel': 'mla_decode', 'interface': 'contiguous', 'cases': workload_matrix()},
             'Input Workload differs from the existing frozen three-case matrix')
    _require(type(configs) is dict and set(configs) == set(_ROLES), 'Need exactly baseline and single_tile configs')
    _require(type(source_bytes) is dict and set(source_bytes) == set(_ROLES), 'Need both archived source identities')
    for role in _ROLES:
        validate_candidate_config(configs[role], workload.spec)
        _require(type(source_bytes[role]) is bytes and sha256(source_bytes[role]).hexdigest() == _DIGESTS[role],
                 'Source differs from archived ' + role + ' candidate')
    predicate = _specialization_predicate(source_bytes['single_tile'])
    expression = compile(ast.Expression(predicate), '<archived-single-tile-predicate>', 'eval')
    cases = deepcopy(workload.spec['cases'][:2])
    cases[0].update(lengths=[0], opaque_tail_nan=True)
    cases[1]['overrides']['tiling']['block_size'] = 256
    spec = {'kernel': 'mla_decode', 'interface': 'contiguous', 'cases': cases}
    transformed = {}
    for role in _ROLES:
        config = deepcopy(configs[role])
        config['case_overrides'] = {case['id']: deepcopy(config['case_overrides'].get(case['id'], {}))
                                    for case in cases}
        for case in cases:
            config['case_overrides'][case['id']].setdefault('tiling', {})['block_size'] = case['overrides']['shape']['seq_len']
        transformed[role] = config
    validate_diagnostic(spec, transformed)
    new_workload = Workload(stable_id('p35-zero-workload', workload.task_id, spec), workload.task_id, spec)
    new_context = EvaluationContext(
        stable_id('p35-zero-context', context.task_id, new_workload.id, context.environment, context.protocol),
        context.task_id, new_workload.id, deepcopy(context.environment), deepcopy(context.protocol), 'real')
    validate_record(new_workload)
    validate_record(new_context)
    _require(new_workload.id != workload.id and new_context.id != context.id,
             'Diagnostic must create independent Workload and Context identities')
    branch_audit = {}
    for case in cases:
        capacity = case['overrides']['shape']['seq_len']
        block_size = transformed['single_tile']['case_overrides'][case['id']]['tiling']['block_size']
        predicate_result = eval(expression, {'__builtins__': {}}, {'capacity': capacity, 'block_size': block_size})
        _require(predicate_result is True, 'Actual archived specialization predicate is not selected')
        branch_audit[case['id']] = {
            'capacity': capacity, 'block_size': block_size, 'tile_count': capacity // block_size,
            'predicate_result': predicate_result, 'lengths': deepcopy(case['lengths']),
            'verification': 'static predicate evaluation; kernel not executed',
        }
    return {
        'schema_version': 1,
        'status': 'prepared_only_requires_analyzer_intake',
        'hardware_executed': False,
        'original_workload_id': workload.id,
        'original_context_id': context.id,
        'workload': asdict(new_workload),
        'context': asdict(new_context),
        'configs': transformed,
        'source_digests': dict(_DIGESTS),
        'branch_audit': {'single_tile': branch_audit},
        'output_contract': {
            'tested_outputs': ['attention', 'cache'],
            'attention_comparison': 'original oracle allclose with atol=rtol=0.01',
            'cache_comparison': 'opaque BF16 bytes including NaN tails; exact cache input preservation',
            'lse_tested': False,
            'lse_status': 'not_exposed_by_frozen_interface',
        },
        'run_intents': [{'role': role, 'requires_distinct_run': True, 'cost': deepcopy(_COST)} for role in _ROLES],
        'budget': {
            'authority': 'existing_task_controller',
            'cost_total': {'executions': 2, 'tokens': 0, 'time_s': 3600.0},
            'charged_by_preparation': False,
        },
        'pending': [
            'Admit the current observed Runtime and verify existing source/config/build/oracle artifacts',
            'Publish genuine Analyzer explanation and distinct diagnostic Plans through the original session',
            'Use original Intake to create distinct baseline and single_tile Runs in this new Context',
            'Charge two exploratory executions through the existing task Controller before execution',
            'Archive real correctness, benchmark, Profile, raw inputs and receipt closure before reporting results',
        ],
    }
