"""Import already completed real probes; never contacts or executes a Runtime.

No remote ledger existed for these direct bridge calls. Local append-only import
IDs are not remote operation IDs and cannot establish remote retry guarantees.
"""
import ast
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.contracts import (Candidate, CandidateIdentity, Decision, EvaluationContext,
    Evidence, ParentCandidate, Plan, Run, RunEvent, Strategy, Task, Workload, canonical_json)
from rsi_kernel_agent.store import ExperimentStore

MANIFEST = ROOT / 'knowledge-site/manifests/phase3-2026-11-12'
WORK = Path(__file__).resolve().parent
TASK_ID = 'p3-connectivity-20261112'


def default_remote_source(path, runtime_id, expected_digest):
    """Extract one string literal without importing or executing the probe."""
    tree = ast.parse(path.read_bytes())
    node = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'source' for t in n.targets))
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'replace' and isinstance(node.func.value, ast.Constant)
            and isinstance(node.func.value.value, str) and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == 'RUNTIME_ID'):
        raise ValueError('Unexpected archived default probe source form')
    raw = node.func.value.value.replace('RUNTIME_ID', repr(runtime_id)).encode('utf-8')
    if sha256(raw).hexdigest() != expected_digest:
        raise ValueError('Default remote probe source no longer matches its recorded digest')
    return raw


def archive():
    store = ExperimentStore(WORK / 'experiments.sqlite3', WORK / 'artifacts')
    contract = {'schema_version': 1, 'purpose': 'real_colab_connectivity_only',
        'checks': ['real TPU device observed', 'all JAX outputs synchronized', 'known numeric result matches'],
        'benchmark': False, 'mla_correctness': False, 'promotion': False,
        'remote_operation_ledger': False, 'import_mode': 'retrospective_completed_probe'}
    contract_raw = canonical_json(contract).encode('utf-8')
    contract_ref = store.artifacts.put(contract_raw)
    task = Task(TASK_ID, 'Phase 3 real Colab synchronized connectivity probes', contract_ref.digest)
    store.append(task, TASK_ID + ':task')
    rows = []
    for name, result_name, env_name, script_name, expected, spec in (
        ('default', 'connectivity-probe.json', 'environment.json', 'probe_runtime.py',
         [[14.0, 38.0, 62.0, 86.0], [38.0, 126.0, 214.0, 302.0],
          [62.0, 214.0, 366.0, 518.0], [86.0, 302.0, 518.0, 734.0]],
         {'operation': 'x @ transpose(x)', 'input': 'arange(16).reshape(4,4)', 'dtype': 'float32'}),
        ('isolated', 'isolated-probe.json', 'environment-isolated.json', 'probe_isolated.py', 140.0,
         {'operation': 'sum(x*x)', 'input': 'arange(8)', 'dtype': 'float32', 'parser_import_check': True}),
    ):
        result_raw = (MANIFEST / result_name).read_bytes()
        result = json.loads(result_raw)
        reports = [json.loads(line[len('RSI_PROBE:'):]) for line in result['stdout'].splitlines()
                   if line.startswith('RSI_PROBE:')]
        if result['returncode'] != 0 or len(reports) != 1:
            raise ValueError('Probe did not return one successful real result: ' + name)
        report = reports[0]
        environment_raw = (MANIFEST / env_name).read_bytes()
        environment = json.loads(environment_raw)
        if report['environment'] != environment or report['synchronized'] is not True or report['result'] != expected:
            raise ValueError('Probe evidence/result/environment mismatch: ' + name)
        if not environment['devices'] or any(d['platform'] != 'tpu' for d in environment['devices']):
            raise ValueError('Probe does not establish execution on a TPU')
        if name == 'isolated' and report.get('xplane_parser_import') is not True:
            raise ValueError('Isolated XPlane protobuf import did not succeed')
        script_path = WORK / script_name
        recipe_raw = script_path.read_bytes()
        recipe_ref = store.artifacts.put(recipe_raw)
        if name == 'default':
            source_raw = default_remote_source(script_path, environment['runtime_session_id'], result['source_digest'])
            source_scope = 'exact_remote_probe_source_matches_previously_recorded_source_digest'
            source_limitation = 'Digest binds submitted script; no worker ledger or loaded-module attestation was captured.'
        else:
            source_raw = recipe_raw
            source_scope = 'local_probe_orchestration_recipe'
            source_limitation = ('The isolated result did not record the remote source digest. The recipe embedded '
                'the runner bytes available at execution time; the exact prior payload cannot be recovered from this result. '
                'Candidate identity here names the saved local recipe, not a verified remote loaded module.')
        source_ref = store.artifacts.put(source_raw)
        config_ref = store.artifacts.put(canonical_json({'schema_version': 1, **spec, 'expected': expected}).encode('utf-8'))
        build = {'schema_version': 1, 'source_scope': source_scope, 'recipe_ref': asdict(recipe_ref),
            'runtime_session_id': environment['runtime_session_id'], 'environment': environment,
            'remote_loaded_source_attestation': 'unavailable', 'limitations': source_limitation}
        build_ref = store.artifacts.put(canonical_json(build).encode('utf-8'))
        identity = CandidateIdentity(source_ref.digest, config_ref.digest, build_ref.digest)
        prefix = TASK_ID + ':' + name
        ids = {key: prefix + ':' + key for key in ('workload', 'context', 'strategy', 'plan', 'parent', 'candidate', 'run')}
        records = [Workload(ids['workload'], TASK_ID, {'probe': name, **spec, 'expected': expected}),
            EvaluationContext(ids['context'], TASK_ID, ids['workload'], environment,
                {**contract, 'source_scope': source_scope, 'receipt_mode': 'direct_synchronous_bridge_reply'}, 'real'),
            Strategy(ids['strategy'], TASK_ID, name + ' runtime connectivity'),
            Plan(ids['plan'], ids['strategy'], 'Archive the already completed synchronized ' + name + ' TPU probe'),
            ParentCandidate(ids['parent'], TASK_ID, identity),
            Candidate(ids['candidate'], ids['plan'], ids['parent'], identity),
            Run(ids['run'], ids['candidate'], ids['context'])]
        for record in records:
            store.append(record, record.id)
        start = RunEvent(ids['run'] + ':start', ids['run'], 'running', 'active',
            'Retrospective import of a direct probe; this event was not persisted before remote execution')
        store.transition(start, None, start.id)
        refs = {'source': source_ref, 'recipe': recipe_ref, 'configuration': config_ref,
                'build': build_ref, 'raw_result': store.artifacts.put(result_raw),
                'raw_environment': store.artifacts.put(environment_raw), 'fixed_contract': contract_ref}
        if name == 'isolated':
            refs['isolated_setup'] = store.artifacts.put((MANIFEST / 'isolated-environment-setup.json').read_bytes())
            refs['setup_recipe'] = store.artifacts.put((WORK / 'setup_runtime_environment.py').read_bytes())
        source = {'provenance': 'real', 'producer': 'existing_colab_bridge_direct_probe',
            'runtime_session_id': environment['runtime_session_id'], 'source_scope': source_scope,
            'remote_operation_id': None, 'remote_receipt_ledger': False,
            'import_mode': 'retrospective_completed_probe', 'limitations': source_limitation}
        source_evidence = Evidence(prefix + ':source-evidence', ids['run'], 'code_fact', 'available', 'completed',
            'supported', {'identity': asdict(identity), 'source_scope': source_scope}, None,
            tuple(refs[key] for key in ('source', 'configuration', 'build', 'fixed_contract')), (), source)
        store.append(source_evidence, source_evidence.id)
        result_evidence = Evidence(prefix + ':result-evidence', ids['run'], 'measured_counter', 'available', 'completed',
            'supported', {'synchronized': True, 'result': report['result'], 'expected': expected,
                'matched': True, 'returncode': 0, 'environment': environment,
                'xplane_parser_import': report.get('xplane_parser_import'),
                'claim': 'Real TPU synchronized connectivity computation only; no MLA or timing claim'},
            None, tuple(refs.values()), (source_evidence.id,), source)
        store.append(result_evidence, result_evidence.id)
        finish = RunEvent(ids['run'] + ':finish', ids['run'], 'succeeded', 'finished',
            'Archived acknowledged synchronous TPU connectivity result; no remote recovery guarantee asserted')
        store.transition(finish, start.id, finish.id)
        details = {'type': 'connectivity_probe_import.v1', 'probe': name,
            'source_scope': source_scope, 'remote_operation_id': None, 'remote_ledger_available': False,
            'evidence_ids': [source_evidence.id, result_evidence.id],
            'artifacts': {key: asdict(value) for key, value in refs.items()},
            'claims': ['real_colab_connectivity', 'real_tpu_execution', 'known_probe_result_matches'],
            'not_claimed': ['durable_remote_recovery', 'mla_correctness', 'benchmark', 'profile_capture', 'performance_improvement'],
            'limitations': source_limitation}
        marker = Decision(prefix + ':import', TASK_ID, 'note', ids['run'], 'Imported existing real connectivity evidence', details)
        store.append(marker, marker.id)
        if store.best(TASK_ID, ids['context']) is not None:
            raise ValueError('Connectivity Task must not have a Best')
        rows.append({'name': name, **ids, 'import_decision': marker.id, **details,
            'origin_files': [str((MANIFEST / result_name).relative_to(ROOT)),
                             str((MANIFEST / env_name).relative_to(ROOT)), str(script_path.relative_to(ROOT))]})
    snapshot = store.create_snapshot(TASK_ID, TASK_ID + ':snapshot:v1')
    output = {'schema_version': 1, 'task_id': TASK_ID,
        'database': str(store.db_path.relative_to(ROOT)), 'artifact_root': str((WORK / 'artifacts').relative_to(ROOT)),
        'record_count': len(store.history(TASK_ID)), 'snapshot': snapshot, 'probes': rows,
        'archive_only': True, 'remote_execution_performed_by_this_script': False,
        'evaluation_bundles_created': 0, 'promotion_calls': 0}
    target = MANIFEST / 'probe-evidence.json'
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'task_id': TASK_ID, 'record_count': output['record_count'],
        'probe_runs': len(rows), 'snapshot_id': snapshot['id'], 'manifest': str(target),
        'remote_execution_performed': False, 'promotion_calls': 0}, ensure_ascii=False))


if __name__ == '__main__':
    archive()
