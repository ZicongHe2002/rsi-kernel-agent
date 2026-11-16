"""Explicit replacement round identity and historical-evidence gate; no mutations."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.profiler.outputs import require
from rsi_kernel_agent.memory import MemoryReader
from rsi_kernel_agent.workflow.execution import stable_id
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from real_round_aux import completed_round, require_gates
from p35_round2_gate import require_confirmation
from p35_archive_unsubmitted_runtime_loss import require_archived_unsubmitted_runtime_loss
from p35_runtime_generations import generation_number

ROUND_ID = 'real-runtime4-recovery-round:2'
RUNTIME6_PLANS = {'R2-A-length-row': 'length-row', 'R2-A-length-smem': 'length-smem'}


def recovery_identity(directory, generation):
    generation_number(generation, minimum=4)
    return Path(directory) / ('real-round-2-' + generation), 'real-' + generation + '-recovery-round:2'


def two_candidate_recovery(generation):
    return generation is not None and generation_number(generation, minimum=4) >= 6


def require_runtime4_loss(store, directory):
    from p35_archive_runtime4_prerequisite_loss import require_archived_runtime4_loss
    return require_archived_runtime4_loss(store, directory)


def require_runtime5_loss(store, directory):
    from p35_archive_runtime5_round_loss import require_archived_runtime5_loss
    return require_archived_runtime5_loss(store, directory)


def require_completed_prerequisite_loss(store, directory, generation):
    from p35_archive_completed_prerequisite_loss import require_completed_prerequisite_loss as checked
    return checked(store, directory, generation)


def recovery_strategies(gates, generation):
    recovery_identity(Path('.'), generation)
    roles = ('baseline-repair',) if two_candidate_recovery(generation) else ('baseline-repair', 'baseline-b')
    return [gates[role]['strategy_id'] for role in roles]


def require_recovery_proposal(generation, gates, prepared, result, variants):
    """Freeze the generation-6-onward two-A subbatch before publication/intake."""
    if not two_candidate_recovery(generation):
        return
    strategy = gates['baseline-repair']['strategy_id']
    selected = prepared['request']['strategies']
    require(len(selected) == 1 and selected[0]['strategy_id'] == strategy,
            'Recovery subbatch must select only admitted strategy A')
    plans = result['plan_set']['plans']
    require(len(plans) == 2 and {p['id'] for p in plans} == set(RUNTIME6_PLANS)
            and set(variants) == set(RUNTIME6_PLANS), 'Recovery subbatch requires exactly the two A Plans')
    for plan in plans:
        label = RUNTIME6_PLANS[plan['id']]
        rows = variants[plan['id']]
        require(plan['strategy_id'] == strategy and plan['parent_id'] == selected[0]['parent_id']
                and plan['variants'] == [label] and len(rows) == 1 and rows[0]['label'] == label
                and rows[0]['cost'] == {'executions': 1, 'tokens': 0, 'time_s': 1800},
                'Recovery proposal changes its A parent, variant scope, or reserved cost')


def require_runtime6_history(store, prepared, loaded, task_id, strategy_id):
    """A participates; all original A/B feedback stays in the full new Snapshot."""
    view = MemoryReader(store).open_snapshot(prepared['snapshot']['id'])
    require(view.snapshot == prepared['snapshot'], 'Recovery snapshot metadata differs')
    histories = view.histories([strategy_id])
    require(prepared['histories'] == histories and histories[strategy_id]['complete'],
            'Recovery subbatch must read the complete selected A history')
    page = store.snapshot_page(view.snapshot['id'], limit=view.snapshot['record_count'])
    records = {record.id: record for record in page['records']}
    required = [loaded['result'], loaded['policy_note'], *loaded['association_notes'].values()]
    require(all(records.get(note.id) == note for note in required)
            and all(row['run_id'] in records for row in loaded['report']['rows']),
            'Recovery snapshot omits historical ABBA evidence')
    owned = {row['payload']['id'] for row in histories[strategy_id]['records']}
    require(loaded['association_notes'][strategy_id].id in owned,
            'A history omits the historical ABBA association')
    jobs = completed_round(store, task_id, 'real-runtime2-round:1')
    require(all(job['run_id'] in records and stable_id('workflow-result', job['run_id']) in records
                for job in jobs), 'Recovery snapshot omits completed first-round feedback')


def require_recovery_context(store, directory, gates, *, prepared=None, allowed_round_id=None,
                             generation='runtime4'):
    _, round_id = recovery_identity(directory, generation)
    allowed_round_id = round_id if allowed_round_id is None else allowed_round_id
    archive = require_archived_unsubmitted_runtime_loss(store, directory)
    number = generation_number(generation, minimum=4)
    replacement = require_runtime4_loss(store, directory) if number >= 5 else None
    runtime5_loss = require_runtime5_loss(store, directory) if number >= 6 else None
    completed_losses = [{'generation': 'runtime' + str(previous),
                         'archive': require_completed_prerequisite_loss(store, directory, 'runtime' + str(previous))}
                        for previous in range(6, number)]
    old, _ = require_gates(store, directory, {'generation': 'runtime3'})
    base, other = gates['baseline-repair'], gates['baseline-b']
    prior = old['baseline-repair']
    require(base['context_id'] == other['context_id'] != prior['context_id'],
            'Recovery requires a distinct, consistently admitted Context')
    environment = base['contract']['context']['environment']
    require(all(base['context_id'] not in row['archive']['summary']['context_ids']
                and environment['runtime_session_id'] != row['archive']['summary']['runtime_id']
                for row in completed_losses), 'Recovery cannot reuse a lost Context or Runtime')
    require(environment == other['contract']['context']['environment']
            and environment['runtime_session_id'] != prior['contract']['context']['environment']['runtime_session_id'],
            'Recovery requires a newly observed Runtime')
    for name in ('baseline-repair', 'baseline-b'):
        current, origin = gates[name], old[name]
        require(current['task_id'] == origin['task_id'] == base['task_id']
                and current['strategy_id'] == origin['strategy_id']
                and current['candidate_id'] == origin['candidate_id'], 'Recovery admission origin differs')
        require(current['contract']['context']['protocol'] == origin['contract']['context']['protocol'],
                'Recovery admission changed the frozen protocol')
        require(all(current['variant'][key] == origin['variant'][key]
                    for key in ('source_ref', 'config_ref', 'build_ref', 'source_bundle_ref')),
                'Recovery admission changed source/config/build identity')
    # ABBA remains evidence about Runtime 3. The new Context obtains its own
    # parent evaluations from require_gates; old times never become its controls.
    loaded = require_confirmation(store, directory, base['task_id'], prior['context_id'],
                                  [base['strategy_id'], other['strategy_id']],
                                  prepared=None if number >= 6 else prepared)
    if number >= 6 and prepared is not None:
        require_runtime6_history(store, prepared, loaded, base['task_id'], base['strategy_id'])
    state = WorkflowJournal(store, base['task_id']).load()
    require(state is not None and not state['cancelled'], 'Recovery task is missing or cancelled')
    require(all(job['round_id'] == allowed_round_id or job['state'] in ('completed', 'failed', 'cancelled')
                for job in state['jobs'].values()), 'Another round has unfinished work')
    if prepared is not None:
        snapshot = prepared['snapshot']
        page = store.snapshot_page(snapshot['id'], limit=snapshot['record_count'])
        ids = {record.id for record in page['records']}
        require(archive['decision_id'] in ids and archive['intent_id'] in ids,
                'Recovery snapshot omits the unsubmitted Runtime loss archive')
        if replacement is not None:
            require(replacement['decision_id'] in ids and replacement['intent_id'] in ids,
                    'Recovery snapshot omits the Runtime 4 unknown prerequisite loss archive')
        if runtime5_loss is not None:
            require(runtime5_loss['decision_id'] in ids and runtime5_loss['intent_id'] in ids,
                    'Recovery snapshot omits the Runtime 5 unsubmitted recovery loss archive')
        require(all(row['archive']['decision_id'] in ids and row['archive']['intent_id'] in ids
                    for row in completed_losses), 'Recovery snapshot omits a completed prerequisite loss archive')
    result = {'archive': archive, 'confirmation': loaded}
    if replacement is not None:
        result['replacement_archive'] = replacement
    if runtime5_loss is not None:
        result['runtime5_archive'] = runtime5_loss
    if completed_losses:
        result['completed_prerequisite_losses'] = completed_losses
    return result
