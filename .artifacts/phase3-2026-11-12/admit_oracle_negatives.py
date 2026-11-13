"""Locally admit two known-wrong candidates for P3.2 Oracle validation only.

No remote calls, execution, parent selection, Frontier or promotion. The host
must stop after correctness; an unexpected pass is a gate failure, not a reason
to benchmark these deliberately incorrect candidates.
"""
import ast
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.associations import MemoryWriter
from rsi_kernel_agent.contracts import (ArtifactRef, Candidate, CandidateIdentity, Decision, EvaluationContext,
    ParentCandidate, Plan, Run, Strategy, Workload, canonical_json)
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.workflow.execution import execution_contract, make_request, stable_id

WORK = Path(__file__).resolve().parent
MANIFEST = ROOT / 'knowledge-site/manifests/phase3-2026-11-12'


def raw_ast_check(parent_raw, candidate_raw):
    before, after = ast.parse(parent_raw), ast.parse(candidate_raw)
    if len(before.body) != len(after.body):
        raise ValueError('Negative Candidate changes top-level layout')
    changed = [(old, new) for old, new in zip(before.body, after.body)
               if ast.dump(old) != ast.dump(new)]
    if not (len(changed) == 1 and all(isinstance(n, ast.FunctionDef)
            and n.name == 'build_mla_decode_contiguous' for n in changed[0])):
        raise ValueError('Negative Candidate changes more than the contiguous wrapper')
    old, new = changed[0]
    original_native = next(n for n in old.body if isinstance(n, ast.FunctionDef) and n.name == 'native')
    candidate_native = next(n for n in new.body if isinstance(n, ast.FunctionDef) and n.name == 'native')
    if ast.dump(original_native) != ast.dump(candidate_native):
        raise ValueError('Negative Candidate changes the native kernel instead of its output wrapper')


def admit():
    store = ExperimentStore(WORK / 'experiments.sqlite3', WORK / 'artifacts')
    base = json.loads((WORK / 'baseline-repair-job.json').read_bytes())
    parent_candidate = store.get(base['candidate_id'])
    if type(parent_candidate) is not Candidate or parent_candidate.identity.source_digest != '2fc23aa9d0358ea18698f916cbc7ed5e02a4218dec2de2fc955661a8b513ebae':
        raise ValueError('Unexpected repaired baseline source identity')
    parent_raw = store.artifacts.read(ArtifactRef(**base['variant']['source_ref']))
    original_context = store.get(base['context_id'])
    original_workload = store.get(original_context.workload_id)
    validation_spec = deepcopy(original_workload.spec)
    selected_ids = ('decode-small', 'decode-boundary')
    validation_spec['cases'] = [case for case in validation_spec['cases'] if case['id'] in selected_ids]
    if tuple(case['id'] for case in validation_spec['cases']) != selected_ids:
        raise ValueError('Requested unchanged small and boundary cases are not available')
    workload = Workload(stable_id('oracle-negative-workload', base['task_id'], validation_spec), base['task_id'], validation_spec)
    context = EvaluationContext(stable_id('oracle-negative-context', workload.id, asdict(original_context)),
        base['task_id'], workload.id, deepcopy(original_context.environment), deepcopy(original_context.protocol), 'real')
    strategy = Strategy(stable_id('oracle-validation-strategy', base['task_id']), base['task_id'],
        'P3.2 independent Oracle negative controls; never optimization or benchmark')
    parent = ParentCandidate(stable_id('oracle-negative-parent', parent_candidate.id), base['task_id'],
        parent_candidate.identity, parent_candidate.id)
    for record in (workload, context, strategy, parent):
        store.append(record, record.id)
    contract, contract_digest = execution_contract(store, context.id)
    base_bundle = json.loads(store.artifacts.read(ArtifactRef(**base['variant']['source_bundle_ref'])))
    base_build = json.loads(store.artifacts.read(ArtifactRef(**base['variant']['build_ref'])))
    rows = []
    for kind in ('attention', 'cache'):
        directory = WORK / 'candidates' / ('oracle-negative-' + kind)
        response_raw = (directory / 'response.json').read_bytes()
        response = json.loads(response_raw)
        source_raw = (directory / 'mla_decode.py').read_bytes()
        diff_raw = (directory / 'change.diff').read_bytes()
        if not (response['source_sha256'] == sha256(source_raw).hexdigest()
                and response['source_size'] == len(source_raw)
                and response['parent_source_sha256'] == sha256(parent_raw).hexdigest()
                and response['mechanism']['output'] == kind
                and response['purpose'] == 'known-wrong real-oracle validation; not optimization'):
            raise ValueError('Negative Candidate differs from its explicit response: ' + kind)
        raw_ast_check(parent_raw, source_raw)
        source_ref = asdict(store.artifacts.put(source_raw))
        bundle = deepcopy(base_bundle)
        entries = [item for item in bundle['files'] if item['path'] == bundle['entry']['path']]
        if len(entries) != 1 or entries[0]['artifact_ref'] != base['variant']['source_ref']:
            raise ValueError('Baseline source bundle entry is inconsistent')
        entries[0]['artifact_ref'] = source_ref
        bundle_ref = asdict(store.artifacts.put(canonical_json(bundle).encode('utf-8')))
        build = {**base_build, 'source_bundle_digest': bundle_ref['digest']}
        build_ref = asdict(store.artifacts.put(canonical_json(build).encode('utf-8')))
        identity = CandidateIdentity(source_ref['digest'], base['variant']['config_ref']['digest'], build_ref['digest'])
        plan = Plan(stable_id('oracle-negative-plan', strategy.id, kind), strategy.id,
            'Known-wrong ' + kind + ' output mutation; require fixed Oracle rejection on unchanged small/boundary cases')
        store.append(plan, plan.id)
        binding = MemoryWriter(store).bind_plan(plan.id, parent.id, stable_id('oracle-negative-bind', plan.id))
        candidate = Candidate(stable_id('oracle-negative-candidate', plan.id, asdict(identity)), plan.id, parent.id, identity)
        run = Run(stable_id('oracle-negative-run', candidate.id, context.id), candidate.id, context.id)
        for record in (candidate, run):
            store.append(record, record.id)
        variant = deepcopy(base['variant'])
        variant.update(label='Known-wrong ' + kind + ' Oracle validation only', source_ref=source_ref,
                       source_bundle_ref=bundle_ref, build_ref=build_ref)
        job = {'id': stable_id('oracle-negative-job', run.id), 'task_id': base['task_id'],
            'round_id': stable_id('oracle-validation', base['task_id'], context.id), 'strategy_id': strategy.id,
            'plan_id': plan.id, 'parent_id': parent.id, 'context_id': context.id, 'candidate_id': candidate.id,
            'run_id': run.id, 'parent_evaluation_id': None, 'variant': variant, 'contract': contract,
            'contract_digest': contract_digest, 'stage': 'generate', 'state': 'queued', 'receipts': {},
            'attempt': 0, 'repair_depth': 0, 'result': None, 'reserved': False,
            'reason': 'P3.2 Oracle negative control; not an optimization Round',
            'validation_constraints': {'purpose': 'oracle_negative_control',
                'allowed_stages': ['generate', 'compile', 'correctness'], 'required_verdict': 'failed',
                'on_unexpected_pass': 'stop_and_fail_oracle_gate', 'benchmark_allowed': False,
                'scope': 'Original small and boundary cases only; no default-shape performance comparison',
                'enforcement': 'Host must stop at correctness; metadata does not change the shared execution protocol'}}
        # Rebuild the actual request to validate immutable bindings without submission.
        make_request(store, job, 'generate')
        response_ref = asdict(store.artifacts.put(response_raw))
        diff_ref = asdict(store.artifacts.put(diff_raw))
        note = Decision(stable_id('oracle-negative-source', candidate.id), base['task_id'], 'note', candidate.id,
            'Immutable known-wrong Oracle validation Candidate and explicit response',
            {'type': 'workflow_candidate.v1', 'source_ref': source_ref, 'source_bundle_ref': bundle_ref,
             'config_ref': variant['config_ref'], 'build_ref': build_ref, 'job_id': job['id'], 'provenance': 'real'})
        admission = Decision(stable_id('oracle-negative-admission', candidate.id), base['task_id'], 'note', run.id,
            'Admitted only for generate/compile/correctness; never benchmark or promote',
            {'type': 'oracle_negative_admission.v1', 'response_ref': response_ref, 'diff_ref': diff_ref,
             'parent_candidate_id': parent_candidate.id, 'parent_identity': asdict(parent_candidate.identity),
             'plan_binding_id': binding.id, 'original_context_id': original_context.id,
             'workload_case_ids': list(selected_ids), 'output_contracts_unchanged': True,
             'environment_unchanged': True, 'oracle_tolerance_and_protocol_unchanged': True,
             'validation_constraints': job['validation_constraints']})
        for record in (note, admission):
            store.append(record, record.id)
        state = store.run_state(run.id)
        if state != {'execution_status': 'not_run', 'scheduling_status': 'ready', 'event_id': None}:
            raise ValueError('Admission-only script found an already started negative Run')
        job_path = WORK / ('oracle-negative-' + kind + '-job.json')
        job_path.write_text(canonical_json(job), encoding='utf-8')
        rows.append({'kind': kind, 'job_file': str(job_path.relative_to(ROOT)), 'strategy_id': strategy.id,
            'plan_id': plan.id, 'plan_binding_id': binding.id, 'parent_id': parent.id,
            'parent_candidate_id': parent_candidate.id, 'candidate_id': candidate.id, 'run_id': run.id,
            'run_state': state, 'workload_id': workload.id, 'context_id': context.id,
            'source_ref': source_ref, 'source_bundle_ref': bundle_ref, 'build_ref': build_ref,
            'response_ref': response_ref, 'diff_ref': diff_ref, 'admission_id': admission.id})
    report = {'schema_version': 1, 'purpose': 'P3.2 known-wrong Oracle validation preparation',
        'task_id': base['task_id'], 'scope': list(selected_ids), 'candidates': rows,
        'remote_calls': 0, 'execution_calls': 0, 'consider_calls': 0, 'parent_selection_changes': 0,
        'oracle_validation_complete': False, 'is_optimization_round': False,
        'required_host_action': 'Advance only generate/compile/correctness; expect failed. If passed, stop and fail the Gate. Never benchmark.'}
    (MANIFEST / 'oracle-negative-admission.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'local_admission_only': True, 'remote_calls': 0,
                      'candidates': [{k: r[k] for k in ('kind', 'job_file', 'candidate_id', 'run_id', 'run_state')} for r in rows]}, ensure_ascii=False))


if __name__ == '__main__':
    admit()
