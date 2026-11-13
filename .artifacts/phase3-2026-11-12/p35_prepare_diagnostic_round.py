"""Prepare two explicit single-tile diagnostic Plans through the original Analyzer."""
import argparse
import asyncio
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.contracts import (ArtifactRef, Candidate, EvaluationContext,
    ParentCandidate, Plan, Strategy, Workload, canonical_json)
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.knowledge_mcp import StdioKnowledge
from rsi_kernel_agent.profiler.outputs import require
from rsi_kernel_agent.profiler.session import start_session
from rsi_kernel_agent.workflow.execution import stable_id
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from rsi_kernel_agent.workflow.rounds import prepare_round
from rsi_kernel_agent.workflow.scheduling import remaining_budget, validate_limits
from real_round_aux import (LIMITS, completed_round, load_prepared, read_json,
    require_gates, validate_session, verified_gate_result, write_once)
from p35_round2_gate import require_confirmation
from p35_single_tile_diagnostic import prepare_diagnostic
from verify_real_round import ReadOnlyStore
import p35_recovery_round as recovery

PLAN_ROLES = {'baseline': 'diagnostic-baseline', 'single_tile': 'diagnostic-single-tile'}
COST = {'executions': 1, 'tokens': 0, 'time_s': 1800.0}
PREVIOUS_ROUNDS = ('real-runtime2-round:1', 'real-runtime2-round:2')


def validate_selection(selection):
    require(type(selection) is dict, 'Invalid diagnostic selection fields')
    version = selection.get('schema_version')
    fields = {'schema_version', 'task_id', 'context_id', 'strategy_id', 'round_id', 'knowledge'}
    require(type(version) is int and version in (1, 2)
            and set(selection) == (fields if version == 1 else fields | {'runtime_generation'}),
            'Invalid diagnostic selection fields')
    require(all(type(selection[key]) is str and bool(selection[key].strip())
                    for key in ('task_id', 'context_id', 'strategy_id', 'round_id')),
            'Invalid diagnostic selection identity')
    require(version == 1 or selection['runtime_generation'] == 'runtime4',
            'Diagnostic v2 supports only the explicitly admitted Runtime 4')
    require(bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', selection['round_id']))
            and selection['round_id'] not in (*PREVIOUS_ROUNDS, recovery.ROUND_ID),
            'Invalid independent diagnostic round')
    require(type(selection['knowledge']) is list, 'Diagnostic knowledge must be a list')
    return selection


def runtime_binding(selection):
    return {'generation': 'runtime3' if selection['schema_version'] == 1 else selection['runtime_generation']}


def prior_rounds(selection):
    return PREVIOUS_ROUNDS if selection['schema_version'] == 1 else (PREVIOUS_ROUNDS[0], recovery.ROUND_ID)


def scope_path(directory, selection):
    name = ('p35-diagnostic-selection.json' if selection['schema_version'] == 1
            else 'p35-diagnostic-selection-runtime4.json')
    return Path(directory) / name


def target_for(directory, selection):
    return Path(directory) / ('single-tile-diagnostic-' +
                              sha256(canonical_json(selection).encode()).hexdigest()[:16])


def prerequisites(store, directory, selection):
    validate_selection(selection)
    gates, _ = require_gates(store, directory, runtime_binding(selection))
    baseline, other = gates['baseline-repair'], gates['baseline-b']
    require(baseline['task_id'] == selection['task_id']
            and baseline['context_id'] == selection['context_id']
            and baseline['strategy_id'] == selection['strategy_id'], 'Diagnostic must use the selected admitted Runtime strategy A')
    if selection['schema_version'] == 1:
        require_confirmation(store, directory, selection['task_id'], selection['context_id'],
                             [baseline['strategy_id'], other['strategy_id']])
    else:
        recovery.require_recovery_context(store, directory, gates, allowed_round_id=selection['round_id'])
    prior = {round_id: completed_round(store, selection['task_id'], round_id)
             for round_id in prior_rounds(selection)}
    state = WorkflowJournal(store, selection['task_id']).load()
    require(state is not None and state['limits'] == validate_limits(LIMITS) and not state['cancelled'],
            'Original diagnostic budget is missing, changed, or cancelled')
    require(all(job['round_id'] == selection['round_id']
                or job['state'] in ('completed', 'failed', 'cancelled') for job in state['jobs'].values()),
            'Other work is unfinished before diagnostic preparation')
    existing = [job for job in state['jobs'].values() if job['round_id'] == selection['round_id']]
    available = remaining_budget(state)
    if not existing:
        require(available['executions'] >= 2 and available['time_s'] >= 3600,
                'Insufficient original diagnostic budget')
    singles = [job for job in prior[PREVIOUS_ROUNDS[0]]
               if job['strategy_id'] == selection['strategy_id'] and job['variant']['label'] == 'single-tile']
    require(len(singles) == 1 and singles[0]['state'] == 'completed',
            'Diagnostic needs the unique completed original A single-tile attempt')
    origins = {'baseline': baseline, 'single_tile': singles[0]}
    for role, job in origins.items():
        require(job['task_id'] == selection['task_id'] and job['strategy_id'] == selection['strategy_id'],
                'Diagnostic origin belongs to another task or strategy')
        candidate = store.get(job['candidate_id'])
        require(type(candidate) is Candidate, 'Diagnostic origin is not a Candidate')
        plan = store.get(candidate.plan_id)
        require(type(plan) is Plan and plan.strategy_id == selection['strategy_id'],
                'Diagnostic origin Candidate belongs to another strategy')
        owner = store.get(plan.strategy_id)
        require(type(owner) is Strategy and owner.task_id == selection['task_id'],
                'Diagnostic origin strategy belongs to another task')
        result = verified_gate_result(store, job)
        if role == 'single_tile':
            require(job['result'] == result, 'Original single-tile result differs from completion marker')
    return state, origins


def require_prepared_history(store, directory, selection, prepared):
    records = {row['payload']['id'] for row in prepared['histories'][selection['strategy_id']]['records']}
    for round_id in prior_rounds(selection):
        jobs = completed_round(store, selection['task_id'], round_id)
        require(all(job['run_id'] in records and stable_id('workflow-result', job['run_id']) in records
                    for job in jobs if job['strategy_id'] == selection['strategy_id']),
                'Diagnostic history omits a completed prior A attempt')
    if selection['schema_version'] == 2:
        gates, _ = require_gates(store, directory, runtime_binding(selection))
        # The diagnostic captures only A's history. Revalidate the shared old
        # ABBA at its own Context, then check its notes and Runs in the complete
        # diagnostic snapshot without relabelling it as Runtime4 evidence.
        loaded = recovery.require_recovery_context(store, directory, gates,
                                                   allowed_round_id=selection['round_id'])
        snapshot = prepared['snapshot']
        page = store.snapshot_page(snapshot['id'], limit=snapshot['record_count'])
        require(page['snapshot'] == snapshot, 'Diagnostic snapshot metadata differs')
        frozen = {record.id: record for record in page['records']}
        archive, confirmation = loaded['archive'], loaded['confirmation']
        required = [confirmation['result'], confirmation['policy_note'], *confirmation['association_notes'].values()]
        require(archive['decision_id'] in frozen and archive['intent_id'] in frozen
                and all(frozen.get(note.id) == note for note in required)
                and all(row['run_id'] in frozen for row in confirmation['report']['rows'])
                and confirmation['association_notes'][selection['strategy_id']].id in records,
                'Runtime4 diagnostic snapshot omits archived loss or historical ABBA evidence')


def build_packet(store, selection, origins, *, persist_config=True):
    """Validate both archived roles before packaging their shared diagnostic config."""
    require(type(persist_config) is bool, 'Invalid diagnostic configuration persistence option')
    require(set(origins) == set(PLAN_ROLES), 'Diagnostic needs both exact source roles')
    baseline = origins['baseline']
    require(baseline['task_id'] == selection['task_id'] and baseline['context_id'] == selection['context_id'],
            'Diagnostic source Context differs from selection')
    configs, sources = {}, {}
    for role, job in origins.items():
        configs[role] = json.loads(store.artifacts.read(ArtifactRef(**job['variant']['config_ref'])))
        sources[role] = store.artifacts.read(ArtifactRef(**job['variant']['source_ref']))
    packet = prepare_diagnostic(deepcopy(baseline['contract']['workload']),
                                deepcopy(baseline['contract']['context']), configs, sources)
    variants = {}
    for role, priority in (('baseline', 20), ('single_tile', 10)):
        raw = canonical_json(packet['configs'][role]).encode('utf-8')
        ref = asdict(store.artifacts.put(raw)) if persist_config else {'digest': sha256(raw).hexdigest(), 'size': len(raw)}
        variant = deepcopy(origins[role]['variant'])
        variant.update(label=PLAN_ROLES[role], priority=priority, config_ref=ref,
                       device=packet['context']['environment']['runtime_session_id'], cost=deepcopy(COST))
        variants[PLAN_ROLES[role]] = [variant]
    return packet, variants


async def prepare(selection_path, *, directory=None):
    directory = Path(directory) if directory is not None else Path(__file__).resolve().parent
    selection = validate_selection(read_json(selection_path))
    target = target_for(directory, selection)
    selection_scope = scope_path(directory, selection)
    require((directory / 'experiments.sqlite3').is_file(), 'Existing Phase 3 Store is required')
    if selection_scope.exists():
        require(read_json(selection_scope) == selection, 'Diagnostic scope is already frozen to another selection')
    store = ReadOnlyStore(directory / 'experiments.sqlite3', directory / 'artifacts')
    _, origins = prerequisites(store, directory, selection)
    packet, variants = build_packet(store, selection, origins, persist_config=False)
    context_id = packet['context']['id']
    prepared = load_prepared(store, selection['task_id'], context_id,
                             [selection['strategy_id']], selection['round_id'])
    frozen = {'selection.json': selection, 'diagnostic-packet.json': packet,
              'variants.json': variants, 'runtime-binding.json': runtime_binding(selection)}
    for name, value in frozen.items():
        if (target / name).exists():
            require(read_json(target / name) == value, 'Frozen diagnostic input differs: ' + name)
    write_once(selection_scope, selection)
    target.mkdir(exist_ok=True)
    for name, value in frozen.items():
        write_once(target / name, value)
    store = ExperimentStore(directory / 'experiments.sqlite3', directory / 'artifacts')
    saved_packet, saved_variants = build_packet(store, selection, origins)
    require(saved_packet == packet and saved_variants == variants, 'Diagnostic config packaging changed')
    for record in (Workload(**packet['workload']), EvaluationContext(**packet['context'])):
        store.append(record, record.id)
    candidate = store.get(origins['single_tile']['candidate_id'])
    parent = ParentCandidate(stable_id('p35-diagnostic-parent', selection), selection['task_id'],
                             candidate.identity, candidate.id)
    store.append(parent, parent.id)
    if prepared is None:
        async with StdioKnowledge(ROOT / 'knowledge-site') as knowledge:
            prepared = await prepare_round(store, knowledge, selection['task_id'], context_id,
                [selection['strategy_id']], selection['round_id'], selection['knowledge'],
                parents={selection['strategy_id']: parent.id}, parent_policy='explicit')
    require_prepared_history(store, directory, selection, prepared)
    write_once(target / 'manifest-ref.json', asdict(prepared['manifest'].artifact_ref))
    write_once(target / 'prepared.json', {key: value for key, value in prepared.items() if key != 'manifest'})
    if (target / 'session.json').exists():
        validate_session(target, prepared['manifest'])
    else:
        start_session(store, prepared['manifest'], target, configuration='review',
                      model_label='current conversation model; single-tile diagnostic', max_turns=6, max_tool_calls=16)
    return {'directory': str(target), 'manifest': asdict(prepared['manifest'].artifact_ref),
            'context_id': context_id, 'workload_id': packet['workload']['id']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('selection', type=Path)
    print(canonical_json(asyncio.run(prepare(parser.parse_args().selection))))
