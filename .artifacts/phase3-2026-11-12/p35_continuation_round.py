"""Bounded continuation through the original round/session/intake/Controller.

No grant creation, model generation, Runtime allocation, repair or promotion.
The caller supplies a spec only after verified real feedback and an explicit
grant. Existing R1/R2/diagnostic helpers and their frozen limits are untouched.
"""
import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.contracts import Decision, EvaluationContext, canonical_json
from rsi_kernel_agent.profiler.outputs import publish_result, require
from rsi_kernel_agent.profiler.session import start_session, _write
from rsi_kernel_agent.workflow.controller import Controller, stable
from rsi_kernel_agent.workflow.intake import intake_publication, validate_variant
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from rsi_kernel_agent.workflow.rounds import prepare_round
from real_round_aux import (completed_session, load_prepared, read_json, require_gates,
                            runtime_config_path, validate_session, write_once)
from p35_run_diagnostic_round import driver_lock
from p35_b2_milestone import validate_p35_milestone, validate_b2_variants


def digest(value):
    return sha256(canonical_json(value).encode('utf-8')).hexdigest()


def same(left, right):
    return canonical_json(left) == canonical_json(right)


def target_for(directory, spec):
    return Path(directory) / ('continuation-' + sha256(spec['round_id'].encode()).hexdigest()[:20])


def validate_spec(store, directory, spec):
    fields = {'schema_version', 'task_id', 'round_id', 'context_id', 'runtime_generation',
              'runtime_id', 'strategies', 'grant_key', 'grant_digest', 'milestone', 'knowledge',
              'milestone_validation'}
    require(type(spec) is dict and set(spec) == fields and type(spec['schema_version']) is int
            and spec['schema_version'] == 1, 'Invalid continuation spec')
    for name in ('task_id', 'round_id', 'context_id', 'runtime_id', 'grant_key', 'grant_digest'):
        require(type(spec[name]) is str and spec[name].strip(), 'Missing continuation ' + name)
    require(type(spec['runtime_generation']) is str and re.fullmatch(r'runtime[2-9][0-9]*|runtime1[0-9]+',
            spec['runtime_generation']), 'Invalid continuation Runtime generation')
    require(type(spec['strategies']) is list and spec['strategies']
            and all(type(s) is str and s for s in spec['strategies'])
            and len(set(spec['strategies'])) == len(spec['strategies'])
            and type(spec['knowledge']) is list, 'Invalid continuation strategies or Knowledge pins')
    journal = WorkflowJournal(store, spec['task_id'])
    state = journal.load()  # Original Journal verifies the entire cumulative grant chain.
    require(state is not None and not state['cancelled'], 'Continuation requires an existing uncancelled Task')
    entries = [v for k, v in state['commands'].items() if k.startswith('budget:')]
    require(entries, 'Continuation requires an explicit budget grant')
    entry = max(entries, key=lambda value: value['sequence'])
    require(entry['key'] == spec['grant_key'] and entry['digest'] == spec['grant_digest']
            and entry['digest'] == digest(entry['grant']), 'Continuation grant is absent, changed or superseded')
    grant = entry['grant']
    require(grant['task_id'] == spec['task_id'] and grant['next_batch']['round_id'] == spec['round_id']
            and same(grant['milestone'], spec['milestone']), 'Continuation milestone or funded round differs')
    success = next(r for r in grant['milestone']['rounds']
                   if r['round_id'] == grant['milestone']['successful_round_id'])
    context = store.get(spec['context_id'])
    require(type(context) is EvaluationContext and context.provenance == 'real'
            and context.task_id == spec['task_id'] and success['context_id'] == context.id
            and context.environment['runtime_session_id'] == spec['runtime_id'],
            'Continuation must retain the verified formal-workload Context and Runtime')
    config = read_json(runtime_config_path(directory, {'generation': spec['runtime_generation']}))
    require(config['runtime_id'] == spec['runtime_id'], 'Pinned continuation Runtime configuration changed')
    gates, _ = require_gates(store, directory, {'generation': spec['runtime_generation']})
    parents = [gates[name] for name in ('baseline-repair', 'baseline-b')]
    require(all(j['task_id'] == spec['task_id'] and j['context_id'] == context.id for j in parents)
            and set(spec['strategies']) <= {j['strategy_id'] for j in parents},
            'Continuation strategies or Context lack original Runtime admission')
    validate_p35_milestone(store, directory, spec, grant, gates)
    anchor_id = journal.prefix + str(entry['applied_revision'])
    anchor = store.get(anchor_id)
    require(type(anchor) is Decision and anchor.details.get('type') == 'workflow_state.v1'
            and same(anchor.details['state']['commands']['budget:' + entry['key']], entry),
            'Continuation grant lacks its exact original Journal checkpoint')
    checkpoint = anchor.details['state']
    require(not any(j['round_id'] == spec['round_id'] for j in checkpoint['jobs'].values())
            and all(j['state'] in ('completed', 'failed', 'cancelled') for j in checkpoint['jobs'].values()),
            'Continuation grant checkpoint contains unfinished or previously admitted work')
    return state, checkpoint, entry, anchor_id


def check_scope(state, checkpoint, spec, expected=None):
    require(not state['cancelled'] and same(state['limits'], checkpoint['limits']),
            'Continuation task cancellation or cumulative limits changed')
    jobs = {key: job for key, job in state['jobs'].items() if job['round_id'] == spec['round_id']}
    prior = {key: job for key, job in state['jobs'].items() if job['round_id'] != spec['round_id']}
    require(same(prior, checkpoint['jobs']), 'Other task jobs changed outside the bounded batch')
    if expected is not None:
        require(not jobs or set(jobs) == set(expected), 'Continuation has extra or missing attempts')
        for key, job in jobs.items():
            require(all(same(job[name], value) for name, value in expected[key].items())
                    and job['context_id'] == spec['context_id']
                    and job['candidate_id'] == stable('wf-c', key)
                    and job['run_id'] == stable('wf-r', key + ':0')
                    and job['attempt'] == 0 and job['repair_depth'] == 0
                    and not job.get('retry_of') and not job.get('repair_of'),
                    'Continuation attempt differs from its original bounded intake')
    charged = {name: sum(job['variant']['cost'][name] for job in jobs.values() if job['reserved'])
               for name in ('executions', 'tokens', 'time_s')}
    require(same(state['spent'], {k: checkpoint['spent'][k] + charged[k] for k in charged}),
            'Continuation spending differs from original reservations')
    grant = checkpoint['commands']['budget:' + spec['grant_key']]['grant']
    require(all(charged[k] <= grant['next_batch']['cost'][k] for k in charged),
            'Continuation reservations exceed this grant batch')
    return jobs


def require_history(store, prepared, spec, anchor_id):
    metadata = prepared['snapshot']
    page = store.snapshot_page(metadata['id'], limit=metadata['record_count'])
    require(page['complete'] and page['snapshot'] == metadata, 'Incomplete continuation Snapshot')
    ids = {r.id for r in page['records']}
    require(anchor_id in ids, 'Continuation Snapshot omits its budget grant')
    source = spec['milestone']['snapshot']
    prior = store.snapshot_page(source['id'], limit=source['record_count'])
    require(prior['complete'] and prior['snapshot'] == source
            and {r.id for r in prior['records']} <= ids,
            'Continuation Snapshot omits prior feedback, diagnostic or archive records')
    require(all(h['complete'] for h in prepared['histories'].values()), 'Incomplete participating history')


async def prepare(store, directory, spec, knowledge):
    with driver_lock(store, spec['task_id']):
        state, checkpoint, entry, anchor_id = validate_spec(store, directory, spec)
        check_scope(state, checkpoint, spec)
        target = target_for(directory, spec)
        target.mkdir(exist_ok=True)
        write_once(target / 'spec.json', spec)
        write_once(target / 'checkpoint.json', {'journal_id': anchor_id, 'state_digest': digest(checkpoint)})
        for sid in spec['strategies']:
            identity = stable('continuation-input', spec['round_id'] + ':' + sid)
            note = Decision(identity, spec['task_id'], 'note', sid, 'Verified prior feedback and bounded continuation',
                {'type': 'workflow_continuation_inputs.v1', 'context_id': spec['context_id'],
                 'grant_journal_id': anchor_id, 'grant': entry, 'milestone': spec['milestone']})
            store.append(note, identity)
        prepared = load_prepared(store, spec['task_id'], spec['context_id'], spec['strategies'], spec['round_id'])
        if prepared is None:
            prepared = await prepare_round(store, knowledge, spec['task_id'], spec['context_id'],
                spec['strategies'], spec['round_id'], spec['knowledge'], parent_policy='branch_best')
        require(same(prepared['request']['knowledge'], spec['knowledge']), 'Pinned Knowledge changed')
        require_history(store, prepared, spec, anchor_id)
        write_once(target / 'prepared.json', {k: v for k, v in prepared.items() if k != 'manifest'})
        write_once(target / 'manifest-ref.json', asdict(prepared['manifest'].artifact_ref))
        if (target / 'session.json').exists():
            validate_session(target, prepared['manifest'])
        else:
            start_session(store, prepared['manifest'], target, model_label='current conversation model; no standalone API',
                          configuration='review', max_turns=8, max_tool_calls=24)
        return prepared


def validate_variants(store, result, variants, grant, spec):
    plans = result['plan_set']['plans']
    require(type(variants) is dict and set(variants) == {p['id'] for p in plans},
            'Variants must exactly cover the completed Analyzer PlanSet')
    all_variants = []
    for plan in plans:
        rows = variants[plan['id']]
        require(type(rows) is list and len(rows) == len(plan['variants']), 'Variant count differs from Plan')
        all_variants.extend(validate_variant(store, v, label, provenance='real')
                            for v, label in zip(rows, plan['variants']))
    total = {k: sum(v['cost'][k] for v in all_variants) for k in ('executions', 'tokens', 'time_s')}
    require(total == grant['next_batch']['cost'], 'Variants do not match this finite grant cost')
    validate_b2_variants(spec, plans, all_variants)


async def run(store, directory, spec, variants, knowledge, backend, *, steps=1, interval=0):
    require(type(steps) is int and steps > 0 and type(interval) is int and 0 <= interval <= 60,
            'Positive steps and 0..60 second interval required')
    with driver_lock(store, spec['task_id']):
        state, checkpoint, entry, anchor_id = validate_spec(store, directory, spec)
        check_scope(state, checkpoint, spec)
        target = target_for(directory, spec)
        require(same(read_json(target / 'spec.json'), spec), 'Frozen continuation spec changed')
        prepared = load_prepared(store, spec['task_id'], spec['context_id'], spec['strategies'], spec['round_id'])
        require(prepared is not None, 'Prepare the exact continuation round first')
        require_history(store, prepared, spec, anchor_id)
        result = completed_session(store, target, prepared['manifest'])
        validate_variants(store, result, variants, entry['grant'], spec)
        key = spec['round_id'] + ':' + prepared['manifest'].artifact_ref.digest
        inputs = {'spec_digest': digest(spec), 'manifest': asdict(prepared['manifest'].artifact_ref),
                  'result_digest': digest(result), 'variants': variants, 'publication_key': key}
        write_once(target / 'execution-input.json', inputs)
        marker = publish_result(store, prepared['manifest'], result, key)
        intake = await intake_publication(store, marker.id, spec['round_id'], variants, knowledge)
        write_once(target / 'intake.json', intake)
        require(intake['items'] and all(i['status'] == 'accepted' for i in intake['items']),
                'Continuation Intake has rejected or waiting Plans; no partial batch is dispatched')
        expected = {j['id']: j for item in intake['items'] for j in item['jobs']}
        controller = Controller(store, spec['task_id'], backend)
        check_scope(controller.journal.load(), checkpoint, spec, expected)
        controller.accept_intake(intake)
        for index in range(steps):
            state = controller.journal.load()
            jobs = check_scope(state, checkpoint, spec, expected)
            if jobs and all(j['state'] in ('completed', 'failed', 'cancelled') for j in jobs.values()):
                break
            controller.step()
            state = controller.status()
            jobs = check_scope(state, checkpoint, spec, expected)
            _write(target / 'controller-status.json', state)
            event = {'utc': datetime.now(timezone.utc).isoformat(), 'revision': state['revision'],
                     'round_id': spec['round_id'], 'states': {k: [j['state'], j['stage']] for k, j in jobs.items()}}
            with (target / 'progress.jsonl').open('a', encoding='utf-8') as out:
                out.write(canonical_json(event) + '\n')
            if any(j['state'] == 'blocked' for j in jobs.values()):
                break
            if not any(j['state'] in ('queued', 'active', 'evaluating') for j in jobs.values()):
                break
            if interval and index + 1 < steps:
                time.sleep(interval)
        state = controller.status()
        jobs = check_scope(state, checkpoint, spec, expected)
        if jobs and all(j['state'] in ('completed', 'failed', 'cancelled') for j in jobs.values()):
            snapshot = store.create_snapshot(spec['task_id'], spec['round_id'] + ':completed-snapshot')
            write_once(target / 'completed.json', {'spec_digest': digest(spec), 'round_id': spec['round_id'],
                'context_id': spec['context_id'], 'snapshot': snapshot, 'grant_key': entry['key'],
                'results': {k: j['result'] for k, j in jobs.items()}})
        return state


async def main(args):
    from rsi_kernel_agent.knowledge_mcp import StdioKnowledge
    from rsi_kernel_agent.store import ExperimentStore
    from rsi_kernel_agent.workflow.__main__ import colab_remote
    from rsi_kernel_agent.workflow.colab import ColabBackend
    directory = args.directory.resolve()
    require((directory / 'experiments.sqlite3').is_file(), 'Original experiment Store is required')
    spec = read_json(args.spec)
    store = ExperimentStore(directory / 'experiments.sqlite3', directory / 'artifacts')
    async with StdioKnowledge(ROOT / 'knowledge-site') as knowledge:
        if args.command == 'prepare':
            prepared = await prepare(store, directory, spec, knowledge)
            return {'directory': str(target_for(directory, spec)), 'snapshot': prepared['snapshot'],
                    'manifest': asdict(prepared['manifest'].artifact_ref)}
        validate_spec(store, directory, spec)  # Reject unfunded work before opening the remote interface.
        remote = colab_remote(runtime_config_path(directory, {'generation': spec['runtime_generation']}))
        state = await run(store, directory, spec, read_json(args.variants), knowledge,
                          ColabBackend(store, remote), steps=args.steps, interval=args.interval)
        return {'round_id': spec['round_id'], 'revision': state['revision'],
                'spent': state['spent'], 'limits': state['limits'],
                'jobs': {key: {'state': j['state'], 'stage': j['stage']}
                         for key, j in state['jobs'].items() if j['round_id'] == spec['round_id']}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'run'))
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--variants', type=Path)
    parser.add_argument('--steps', type=int, default=1)
    parser.add_argument('--interval', type=int, default=15)
    args = parser.parse_args()
    if args.command == 'run' and args.variants is None:
        parser.error('run requires --variants')
    print(canonical_json(asyncio.run(main(args))))
