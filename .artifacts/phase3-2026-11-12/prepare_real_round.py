"""Prepare or restore the original Analyzer session after persisted real Gates.

This host helper has no Colab calls. It does not invent model responses.
"""
import argparse
import asyncio
from dataclasses import asdict
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.contracts import canonical_json
from rsi_kernel_agent.knowledge import method_note_id
from rsi_kernel_agent.knowledge_mcp import StdioKnowledge
from rsi_kernel_agent.workflow.rounds import prepare_round
from rsi_kernel_agent.workflow.controller import Controller
from rsi_kernel_agent.profiler.session import start_session
from rsi_kernel_agent.profiler.outputs import require
from real_round_aux import (LIMITS, completed_round, load_prepared, read_json, require_gates,
                            require_new_history, validate_session, write_once, runtime_binding)
from p35_round2_gate import require_confirmation


async def prepare(number, directory=None, *, recovery_generation=None):
    require(type(number) is int and number in (1, 2), 'Round must be 1 or 2')
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
        require(binding == {'generation': recovery_generation}, 'Recovery active binding differs')
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
        completed_round(store, task_id, 'real-runtime2-round:1')
    # Initial budget only; an existing journal must retain the identical limits.
    Controller(store, task_id, None, limits=LIMITS)
    target.mkdir(exist_ok=True)
    write_once(target / 'runtime-binding.json', binding)
    prepared = load_prepared(store, task_id, context_id, strategies, round_id)
    if prepared is None:
        require(not (target / 'session.json').exists(), 'Session exists without its durable prepared round')
        inputs_path = target / 'preparation-input.json'
        expected = {'task_id': task_id, 'context_id': context_id, 'strategies': strategies, 'round_id': round_id}
        async with StdioKnowledge(ROOT / 'knowledge-site') as knowledge:
            if inputs_path.exists():
                inputs = read_json(inputs_path)
                require(all(inputs.get(k) == v for k, v in expected.items()), 'Preparation identity changed')
            else:
                searches = {query: await knowledge.search(query) for query in ('MLA', 'DMA', 'online', 'pipeline')}
                refs = []
                for mid in ('hist-decode-dma2', 'hist-decode-prepare-padding', 'hist-fwd-state-lifetime',
                            'hist-profile-accounting', 'official-jax-synchronized-benchmark',
                            'official-tpu-profile-mode-version-gates'):
                    doc = await knowledge.fetch_document(method_note_id(mid))
                    refs.append({'method_id': mid, 'note_id': doc.id, 'revision': doc.revision})
                inputs = {**expected, 'knowledge': refs, 'searches': searches}
                write_once(inputs_path, inputs)
            prepared = await prepare_round(store, knowledge, task_id, context_id, strategies,
                                           round_id, inputs['knowledge'])
        write_once(target / 'knowledge-search.json', inputs['searches'])
    if number == 2:
        require_new_history(store, task_id, prepared, 'real-runtime2-round:1')
        if recovery_generation is None:
            require_confirmation(store, directory, task_id, context_id, strategies, prepared=prepared)
        else:
            require_recovery_context(store, directory, gates, prepared=prepared)
    write_once(target / 'prepared.json', {k: v for k, v in prepared.items() if k != 'manifest'})
    write_once(target / 'manifest-ref.json', asdict(prepared['manifest'].artifact_ref))
    if (target / 'session.json').exists():
        validate_session(target, prepared['manifest'])
    else:
        start_session(store, prepared['manifest'], target,
            model_label='current conversation model; no standalone API', configuration='review',
            max_turns=8, max_tool_calls=24)
    return {'directory': str(target), 'manifest': asdict(prepared['manifest'].artifact_ref),
            'cards': len(prepared['cards']), 'histories': {sid: {k: v for k, v in h.items() if k != 'records'}
                                                        for sid, h in prepared['histories'].items()}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('round', type=int, choices=(1, 2))
    args = parser.parse_args()
    print(canonical_json(asyncio.run(prepare(args.round))))
