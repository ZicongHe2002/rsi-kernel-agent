"""Explicit Runtime 4 round identity and historical-evidence gate; no mutations."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.profiler.outputs import require
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from real_round_aux import require_gates
from p35_round2_gate import require_confirmation
from p35_archive_unsubmitted_runtime_loss import require_archived_unsubmitted_runtime_loss

ROUND_ID = 'real-runtime4-recovery-round:2'


def recovery_identity(directory, generation):
    require(generation == 'runtime4', 'Only the explicitly reviewed Runtime 4 recovery is supported')
    return Path(directory) / 'real-round-2-runtime4', ROUND_ID


def require_recovery_context(store, directory, gates, *, prepared=None, allowed_round_id=ROUND_ID):
    archive = require_archived_unsubmitted_runtime_loss(store, directory)
    old, _ = require_gates(store, directory, {'generation': 'runtime3'})
    base, other = gates['baseline-repair'], gates['baseline-b']
    prior = old['baseline-repair']
    require(base['context_id'] == other['context_id'] != prior['context_id'],
            'Recovery requires a distinct, consistently admitted Context')
    environment = base['contract']['context']['environment']
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
                                  [base['strategy_id'], other['strategy_id']], prepared=prepared)
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
    return {'archive': archive, 'confirmation': loaded}
