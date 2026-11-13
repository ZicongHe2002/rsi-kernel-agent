"""DRAFT host driver: publish a completed session and advance original Controller.

Usage after explicit conversation responses and manually saved variant specs:
  python -B run_real_round.py 1 --variants real-round-1/variants.json --steps 1

Re-run with the same arguments to recover. No bootstrap, runtime allocation,
model generation, repair generation, direct promotion or independent state machine.
"""
import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.contracts import canonical_json
from rsi_kernel_agent.knowledge_mcp import StdioKnowledge
from rsi_kernel_agent.profiler.outputs import publish_result, require
from rsi_kernel_agent.profiler.session import _write
from rsi_kernel_agent.workflow.intake import intake_publication
from rsi_kernel_agent.workflow.controller import Controller
from rsi_kernel_agent.workflow.colab import ColabBackend
from rsi_kernel_agent.workflow.__main__ import colab_remote
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from real_round_aux import (LIMITS, completed_session, load_prepared, read_json, require_gates,
                            require_new_history, write_once, runtime_binding, runtime_config_path)
from p35_round2_gate import require_confirmation


async def run(number, variants_path, *, steps=1, interval=15, directory=None, recovery_generation=None):
    require(type(number) is int and number in (1, 2), 'Round must be 1 or 2')
    require(type(steps) is int and steps > 0 and type(interval) is int and 1 <= interval <= 60,
            'Need positive step limit and 1..60 second poll interval')
    directory = Path(directory) if directory is not None else Path(__file__).resolve().parent
    require((directory / 'experiments.sqlite3').is_file(), 'Existing Phase 3 Store is required')
    store = ExperimentStore(directory / 'experiments.sqlite3', directory / 'artifacts')
    target = directory / ('real-round-' + str(number))
    if recovery_generation is not None:
        from p35_recovery_round import recovery_identity, require_recovery_context
        require(number == 2, 'Only round 2 has a Runtime recovery path')
        target, recovery_round_id = recovery_identity(directory, recovery_generation)
    binding = runtime_binding(directory, target)
    if recovery_generation is not None:
        require(binding == {'generation': recovery_generation}, 'Recovery Runtime binding differs')
    gates, _ = require_gates(store, directory, binding)
    base, other = gates['baseline-repair'], gates['baseline-b']
    task_id, context_id = base['task_id'], base['context_id']
    strategies = [base['strategy_id'], other['strategy_id']]
    round_id = 'real-runtime2-round:' + str(number)
    if recovery_generation is not None:
        round_id = recovery_round_id
    if number == 2:
        if recovery_generation is None:
            require_confirmation(store, directory, task_id, context_id, strategies)
        else:
            require_recovery_context(store, directory, gates)
    prepared = load_prepared(store, task_id, context_id, strategies, round_id)
    require(prepared is not None, 'Prepare this exact round before publication')
    if number == 2:
        require_new_history(store, task_id, prepared, 'real-runtime2-round:1')
        if recovery_generation is None:
            require_confirmation(store, directory, task_id, context_id, strategies, prepared=prepared)
        else:
            require_recovery_context(store, directory, gates, prepared=prepared)
    manifest = prepared['manifest']
    result = completed_session(store, target, manifest)
    variants = read_json(variants_path)
    require(type(variants) is dict and set(variants) == {p['id'] for p in result['plan_set']['plans']},
            'Variants must exactly cover the completed Analyzer PlanSet')
    # A changed file on restart cannot silently admit a new experiment.
    publication_key = round_id + ':' + manifest.artifact_ref.digest
    inputs = {'round_id': round_id, 'manifest': asdict(manifest.artifact_ref),
              'result_digest': sha256(canonical_json(result).encode()).hexdigest(),
              'variants': variants, 'publication_key': publication_key}
    write_once(target / 'execution-input.json', inputs)
    state = WorkflowJournal(store, task_id).load()
    require(state is not None and not state['cancelled'], 'Prepared Controller journal is missing or cancelled')
    require(all(j['round_id'] == round_id or j['state'] in ('completed', 'failed', 'cancelled')
                for j in state['jobs'].values()), 'Another round has unfinished work')
    marker = publish_result(store, manifest, result, publication_key)
    async with StdioKnowledge(ROOT / 'knowledge-site') as knowledge:
        intake = await intake_publication(store, marker.id, round_id, variants, knowledge)
    write_once(target / 'intake.json', intake)
    remote = colab_remote(runtime_config_path(directory, binding))
    controller = Controller(store, task_id, ColabBackend(store, remote), limits=LIMITS)
    controller.accept_intake(intake)
    for index in range(steps):
        # Existing Controller owns intents, reservations, submit/lookup/poll and
        # integration. Unknown stays blocked and retains the original operation.
        controller.step()
        state = controller.status()
        _write(target / 'controller-status.json', state)
        jobs = [job for job in state['jobs'].values() if job['round_id'] == round_id]
        event = {'utc': datetime.now(timezone.utc).isoformat(), 'round_id': round_id,
                 'revision': state['revision'], 'jobs': [{k: job[k] for k in
                    ('id', 'candidate_id', 'run_id', 'state', 'stage', 'reason')} for job in jobs]}
        with (target / 'progress.jsonl').open('a', encoding='utf-8') as log:
            log.write(canonical_json(event) + '\n'); log.flush(); os.fsync(log.fileno())
        print(canonical_json(event), flush=True)
        if not jobs or all(j['state'] in ('completed', 'failed', 'cancelled') for j in jobs):
            if jobs:
                snapshot = store.create_snapshot(task_id, round_id + ':completed-snapshot')
                write_once(target / 'completed.json', {'round_id': round_id, 'snapshot': snapshot,
                    'publication_id': marker.id, 'intake_id': intake['id'],
                    'results': {j['id']: j['result'] for j in jobs}})
            break
        if any(j['state'] == 'blocked' for j in jobs):
            break
        if not any(j['state'] in ('queued', 'active', 'evaluating') for j in jobs):
            break
        if index + 1 < steps:
            time.sleep(interval)
    return {'round_id': round_id, 'publication_id': marker.id, 'intake': intake,
            'state_file': str(target / 'controller-status.json'), 'workflow_state': state['workflow_state']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('round', type=int, choices=(1, 2))
    parser.add_argument('--variants', type=Path, required=True)
    parser.add_argument('--steps', type=int, default=1)
    parser.add_argument('--interval', type=int, default=15)
    args = parser.parse_args()
    print(canonical_json(asyncio.run(run(args.round, args.variants, steps=args.steps, interval=args.interval))))
