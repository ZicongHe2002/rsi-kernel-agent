"""Freeze fresh isolated route inputs only after four real prerequisite gates."""
import argparse
import asyncio
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
HISTORY = ROOT / '.artifacts/phase3-2026-11-12'
sys.path[:0] = [str(ROOT / 'src'), str(HISTORY)]
from rsi_kernel_agent.contracts import canonical_json
from rsi_kernel_agent.knowledge_mcp import StdioKnowledge
from rsi_kernel_agent.associations import MemoryWriter
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.workflow.design import begin_design_round
from rsi_kernel_agent.workflow.exploration import set_exploration_state
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from real_round_aux import read_json, require, verified_gate_result, write_once


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generation', default='runtime8')
    parser.add_argument('--round', default='next-stage-runtime8-independent:1')
    parser.add_argument('--preparation', help='Completed offline preparation to review against this Runtime')
    args = parser.parse_args()
    directory = OUT / args.generation
    require(directory.resolve().parent == OUT.resolve(), 'Runtime directory must remain within this delivery')
    config = read_json(directory / ('colab-config-' + args.generation + '.json'))
    names = ('oracle-negative-attention', 'oracle-negative-cache', 'baseline-repair', 'baseline-b')
    jobs = {name: read_json(directory / (name + '-' + args.generation + '-job.json')) for name in names}
    require(shutil.disk_usage(ROOT).free > 4 * 1024**3, 'At least 4 GiB required for retained frozen inputs')
    store = ExperimentStore(HISTORY / 'experiments.sqlite3', HISTORY / 'artifacts')
    gates = {name: verified_gate_result(store, job, negative=name.startswith('oracle-negative-'))
             for name, job in jobs.items()}
    base = jobs['baseline-repair']
    context_id, task_id = base['context_id'], base['task_id']
    require(jobs['baseline-b']['context_id'] == context_id, 'A and B require a common measured Context')
    require(all(job['contract']['context']['environment']['runtime_session_id'] == config['runtime_id']
                for job in jobs.values()), 'Prerequisite Runtime does not match active configuration')
    journal = WorkflowJournal(store, task_id)
    before = journal.load()
    require(before is not None and not before['cancelled'] and all(
        job['state'] in ('completed', 'failed', 'cancelled') for job in before['jobs'].values()),
        'Prior exploration jobs require reconciliation')
    spec = deepcopy(read_json(ROOT / '.artifacts/next-stage-2026-11-16/live-design-spec.json'))
    spec.update(context_id=context_id, round_id=args.round)
    if args.preparation:
        from rsi_kernel_agent.workflow.offline_preparation import _preparation, read_route, _route
        from rsi_kernel_agent.profiler.inputs import load_manifest
        from rsi_kernel_agent.contracts import ArtifactRef
        prep = _preparation(store, args.preparation)
        require(prep.task_id == task_id, 'Offline preparation belongs to another Task')
        spec['assessments'] = deepcopy(prep.details['assessments'])
        spec['parents'], spec['knowledge_refs'] = {}, {}
        for sid in prep.details['order']:
            route = _route(store, prep, sid)
            if route is not None:
                payload = read_route(store, route.id)
                manifest = load_manifest(store, ArtifactRef(**payload['manifest'])).to_dict()
                spec['parents'][sid] = manifest['strategies'][sid]['parent_id']
                spec['knowledge_refs'][sid] = manifest['request']['knowledge']
        spec['preparation_id'] = args.preparation
    optimization_ids = {jobs['baseline-repair']['strategy_id'], jobs['baseline-b']['strategy_id'],
                        'mla-strategy:transposed-score-state-v1'}
    for sid, parent_id in spec['parents'].items():
        if sid not in optimization_ids:
            continue
        parent = store.get(parent_id)
        origin = base if sid != jobs['baseline-b']['strategy_id'] else jobs['baseline-b']
        require(parent.candidate_id == origin['candidate_id'], 'Exact historical selected parent changed')
        MemoryWriter(store).select_parent(sid, context_id, parent_id,
            'Explicit historical parent retained and remeasured in the authorized new Runtime',
            args.round + ':parent:' + sid)
    d_sid = 'mla-strategy:transposed-score-state-v1'
    state = set_exploration_state(store, d_sid, 'active',
        'Authorized v6e Runtime and unchanged prerequisite gates are ready; resume protected planning',
        [], args.round + ':resume-D')
    write_once(directory / 'design-spec.json', spec)
    async with StdioKnowledge(ROOT / 'knowledge-site') as knowledge:
        batch = await begin_design_round(store, knowledge, **spec)
    require(journal.load() == before, 'Design capture must not change execution spending or jobs')
    write_once(directory / 'design-batch.json', asdict(batch))
    write_once(directory / 'design-preparation.json', {
        'at_utc': datetime.now(timezone.utc).isoformat(), 'batch_id': batch.id,
        'context_id': context_id, 'snapshot': batch.details['snapshot'],
        'gate_results': gates, 'exploration_state': state,
        'journal_revision': before['revision'], 'spent': before['spent'],
        'new_optimization_runs': 0, 'execution_journal_unchanged': True,
        'actual_model_reading_complete': False, 'design_complete': False,
    })
    print(canonical_json({'batch_id': batch.id, 'context_id': context_id,
        'snapshot': batch.details['snapshot'], 'order': batch.details['order'],
        'routes': {sid: row['status'] for sid, row in batch.details['routes'].items()}}))


if __name__ == '__main__':
    asyncio.run(main())
