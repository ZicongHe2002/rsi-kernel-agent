"""Freeze ABBA policy and publish verified observations into the original Store."""
from dataclasses import asdict
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.contracts import ArtifactRef, Decision, Strategy, canonical_json
from rsi_kernel_agent.store import StoreError
from rsi_kernel_agent.workflow.execution import stable_id
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from real_round_aux import read_json, write_once, completed_round
from prepare_confirmation_round import validate_selection, target_for
from p35_confirmation_statistics import analyze_abba
from verify_real_round import verify_job


def freeze_policy(store, directory, selection):
    """Idempotent pre-dispatch record; an already-started round cannot add one."""
    validate_selection(selection)
    policy = read_json(Path(directory) / 'p35-confirmation-policy.json')
    if policy != {'version': 'p35-abba-v1', 'min_paired_reduction': .02, 'max_within_role_spread': .05}:
        raise ValueError('Policy differs from predeclared p35-abba-v1')
    identity = stable_id('p35-confirmation-policy', selection['task_id'], selection['round_id'])
    journal = WorkflowJournal(store, selection['task_id'])
    with journal.lock():
        try:
            prior = store.get(identity)
        except StoreError:
            prior = None
        if prior is None:
            state = journal.load()
            intakes = [r for r in store.history(selection['task_id']) if type(r) is Decision
                and r.details.get('type') == 'workflow_intake.v1'
                and r.details.get('result', {}).get('round_id') == selection['round_id']]
            if intakes or state and any(j['round_id'] == selection['round_id'] for j in state['jobs'].values()):
                raise ValueError('Policy must be frozen before confirmation intake')
        ref = store.artifacts.put(canonical_json(policy).encode())
        note = Decision(identity, selection['task_id'], 'note', selection['task_id'],
            'Predeclared Run-level ABBA confirmation policy; no Store promotion change',
            {'type': 'p35_confirmation_policy.v1', 'selection': selection, 'policy_ref': asdict(ref)})
        if prior is not None and prior != note:
            raise ValueError('Saved policy or confirmation selection differs')
        store.append(note, note.id)
    return note


def require_frozen_policy(store, directory, selection):
    """Read-only check before dispatch; never recreate a lost freeze marker."""
    validate_selection(selection)
    path = Path(directory) / 'p35-confirmation-policy.json'
    if not path.is_file():
        raise ValueError('Predeclared confirmation policy file is required')
    policy = read_json(path)
    if policy != {'version': 'p35-abba-v1', 'min_paired_reduction': .02, 'max_within_role_spread': .05}:
        raise ValueError('Confirmation policy differs from predeclared p35-abba-v1')
    identity = stable_id('p35-confirmation-policy', selection['task_id'], selection['round_id'])
    try:
        note = store.get(identity)
    except StoreError as exc:
        raise ValueError('Frozen confirmation policy is required before dispatch') from exc
    if (type(note) is not Decision or note.kind != 'note'
            or not (note.task_id == note.target_id == selection['task_id'])
            or note.details.get('type') != 'p35_confirmation_policy.v1'
            or note.details.get('selection') != selection):
        raise ValueError('Frozen confirmation policy identity differs')
    ref = note.details.get('policy_ref')
    if not isinstance(ref, dict) or store.artifacts.read(ArtifactRef(**ref)) != canonical_json(policy).encode():
        raise ValueError('Frozen confirmation policy artifact differs')
    history = store.history(selection['task_id'])
    order = {record.id: index for index, record in enumerate(history)}
    intakes = [r for r in history if type(r) is Decision and r.details.get('type') == 'workflow_intake.v1'
               and r.details.get('result', {}).get('round_id') == selection['round_id']]
    if any(order[note.id] >= order[intake.id] for intake in intakes):
        raise ValueError('Confirmation policy was not frozen before intake')
    return note


def publish_confirmation(store, directory, selection, strategies):
    """Verify original completion/NPZ/receipts before publishing shared history."""
    from p35_round2_gate import receipt_row, require_confirmation, validate_origin
    directory = Path(directory)
    validate_selection(selection)
    target = target_for(directory, selection)
    if not (target / 'completed.json').is_file():
        raise ValueError('Confirmation completion is required before publication')
    if read_json(target / 'selection.json') != selection:
        raise ValueError('Prepared confirmation selection differs')
    completion = read_json(target / 'completed.json')
    policy = read_json(directory / 'p35-confirmation-policy.json')
    policy_note = require_frozen_policy(store, directory, selection)
    if len(strategies) != 2 or len(set(strategies)) != 2:
        raise ValueError('Expected both participating strategy associations')
    for sid in strategies:
        strategy = store.get(sid)
        if type(strategy) is not Strategy or strategy.task_id != selection['task_id']:
            raise ValueError('Confirmation association has another task or record type')
    journal = WorkflowJournal(store, selection['task_id'])
    with journal.lock():
        jobs = completed_round(store, selection['task_id'], selection['round_id'])
        if (len(jobs) != 4 or any(j['state'] != 'completed' for j in jobs)
                or completion['round_id'] != selection['round_id']
                or completion['order'] != [r['label'] for r in selection['sequence']]
                or completion['results'] != {j['id']: j['result'] for j in jobs}):
            raise ValueError('Confirmation completion differs from four original Controller jobs')
        rows, checks, cache = [], [], {}
        order = {record.id: index for index, record in enumerate(store.history(selection['task_id']))}
        for selected in selection['sequence']:
            matches = [j for j in jobs if j['variant']['label'] == selected['label']]
            if len(matches) != 1:
                raise ValueError('Confirmation labels do not uniquely identify jobs')
            job = matches[0]
            if (job['context_id'] != selection['context_id'] or job['strategy_id'] != selection['strategy_id']
                    or order[policy_note.id] >= order[job['run_id']]):
                raise ValueError('Confirmation identity or pre-dispatch policy order differs')
            role = 'control' if selected['role'] == 'baseline' else 'treatment'
            validate_origin(store, job, selection, role)
            row = receipt_row(store, job, role)
            if (row['profile_started_ns'] < row['benchmark_finished_ns']
                    or rows and row['benchmark_started_ns'] < rows[-1]['profile_finished_ns']):
                raise ValueError('Confirmation measurements are not in fixed chronological order')
            checked = verify_job(store, job, recompute_npz=True, comparison_cache=cache)
            if checked.get('verification') != 'verified':
                raise ValueError('Original result verification did not complete')
            rows.append(row)
            checks.append(checked)
        report = {'schema_version': 1, 'selection': selection, 'policy': policy,
            'statistics': analyze_abba(rows, policy), 'rows': rows,
            'independent_verifications': checks,
            'verification_limits': [
                'Raw NPZ attention/cache comparisons recomputed; opaque NaN cache bytes are preserved.',
                'Runner input-cache immutability and finite metadata checks are not independently re-executed on device.',
                'Saved parser statistics recomputed; raw XPlane protobuf is not independently decoded.',
                'Two independent Runs per role; no confidence interval, significance, or broader speedup claim.']}
        ref = asdict(store.artifacts.put(canonical_json(report).encode()))
        marker = Decision(stable_id('p35-confirmation-result', selection['task_id'], selection['round_id']),
            selection['task_id'], 'note', selection['task_id'], 'Verified ABBA observations; original promotion retained',
            {'type': 'p35_confirmation_result.v1', 'artifact_ref': ref,
             'policy_ref': policy_note.details['policy_ref'], 'context_id': selection['context_id'],
             'round_id': selection['round_id'], 'run_ids': [r['run_id'] for r in rows]})
        store.append(marker, marker.id)
        notes = {}
        for sid in strategies:
            note = Decision(stable_id('p35-confirmation-association', marker.id, sid), selection['task_id'],
                'note', sid, 'ABBA conclusion available to complete participating strategy history',
                {'type': 'p35_confirmation_association.v1', 'confirmation_id': marker.id,
                 'artifact_ref': ref, 'context_id': selection['context_id'], 'summary': report['statistics']})
            store.append(note, note.id)
            notes[sid] = note.id
        pointer = {'decision_id': marker.id, 'artifact_ref': ref, 'strategy_note_ids': notes,
                   'policy_decision_id': policy_note.id}
        write_once(directory / 'p35-confirmation-result.json', pointer)
        require_confirmation(store, directory, selection['task_id'], selection['context_id'], strategies)
    return pointer


if __name__ == '__main__':
    import argparse
    from rsi_kernel_agent.store import ExperimentStore
    from real_round_aux import require_gates
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('selection', type=Path)
    parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    store = ExperimentStore(args.directory / 'experiments.sqlite3', args.directory / 'artifacts')
    gates, _ = require_gates(store, args.directory)
    strategies = [gates[name]['strategy_id'] for name in ('baseline-repair', 'baseline-b')]
    print(canonical_json(publish_confirmation(store, args.directory, read_json(args.selection), strategies)))
