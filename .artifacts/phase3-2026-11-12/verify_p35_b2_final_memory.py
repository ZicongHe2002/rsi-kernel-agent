"""Read-only final Snapshot reconciliation for the original Runtime 7 B2 batch.

Reuses saved original NPZ-verifier conclusions and rebinds receipts. This does
not rerun hardware, regenerate analysis, promote results, or grant more budget.
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.contracts import ArtifactRef, Candidate, Decision, ParentCandidate, canonical_json
from rsi_kernel_agent.memory import MemoryReader
from rsi_kernel_agent.workflow.execution import stable_id
from rsi_kernel_agent.workflow.intake import _id
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from rsi_kernel_agent.workflow.scheduling import remaining_budget, validate_limits
from p35_b2_milestone import B_LABELS, COST, BATCH_COST, MilestoneStore
from p35_continuation_round import check_scope, digest, same, target_for
from real_round_aux import LIMITS, read_json
from verify_p35_final_memory import TransactionStore, bind_verification, verify_projection_bindings
from verify_real_round import require

ROUND = 'real-runtime7-b2-continuation-round:2'
GRANT_KEY = 'p35-runtime7-b2-after-a2-diagnostic-v1'
SPEC_FIELDS = {'schema_version', 'task_id', 'round_id', 'context_id', 'runtime_generation',
    'runtime_id', 'strategies', 'grant_key', 'grant_digest', 'milestone', 'knowledge', 'milestone_validation'}


def read_report(path, expected_sha=None):
    raw = Path(path).read_bytes()
    actual = sha256(raw).hexdigest()
    require(expected_sha is None or actual == expected_sha, 'Pinned verification report bytes changed')
    return json.loads(raw), {'path': str(path), 'sha256': actual}


def note(frozen, identity, task, kind):
    record = frozen.get(identity)
    require(type(record) is Decision and record.kind == 'note' and record.task_id == task
            and record.target_id == task and record.details.get('type') == kind,
            'Original ' + kind + ' marker differs')
    return record


def admitted_b2(frozen, target, spec, state, checkpoint, completion, expected_parent_id):
    inputs, intake = read_json(target / 'execution-input.json'), read_json(target / 'intake.json')
    manifest = read_json(target / 'manifest-ref.json')
    session = read_json(target / 'session.json')
    key = spec['round_id'] + ':' + manifest['digest']
    publication_id = 'analysis:' + sha256(key.encode()).hexdigest()[:24] + ':complete'
    publication = note(frozen, publication_id, spec['task_id'], 'analysis_result.v1')
    require(set(inputs) == {'spec_digest', 'manifest', 'result_digest', 'variants', 'publication_key'}
            and inputs['spec_digest'] == digest(spec) and inputs['manifest'] == manifest
            and inputs['publication_key'] == key and publication.details['manifest'] == manifest
            and publication.details['result']['digest'] == inputs['result_digest'] == session['result_digest']
            and session['manifest'] == manifest, 'Frozen B2 Analyzer inputs differ')
    round_note = note(frozen, _id('wf-round', spec['task_id'], spec['round_id']), spec['task_id'], 'workflow_round.v1')
    require(round_note.details['round_id'] == spec['round_id'] and round_note.details['context_id'] == spec['context_id']
            and round_note.details['strategies'] == spec['strategies'] and round_note.details['manifest'] == manifest
            and round_note.details['request_digest'] == digest({'task_id': spec['task_id'],
                'context_id': spec['context_id'], 'strategies': spec['strategies'], 'round_id': spec['round_id'],
                'knowledge': spec['knowledge'], 'parents': None, 'parent_policy': 'branch_best'}),
            'Original B2 prepared round differs')
    require(intake['id'] == _id('wf-intake', spec['round_id'], publication_id)
            and intake['task_id'] == spec['task_id'] and intake['round_id'] == spec['round_id']
            and intake['publication_id'] == publication_id, 'Frozen B2 intake identity differs')
    marker = note(frozen, intake['id'], spec['task_id'], 'workflow_intake.v1')
    fingerprint = sha256(json.dumps({'publication_id': publication_id, 'round_id': spec['round_id'],
        'variants': inputs['variants']}, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    require(marker.details == {'type': 'workflow_intake.v1', 'request_digest': fingerprint, 'result': intake}
            and state['intakes'].get(intake['id']) == digest(intake), 'B2 original intake request/acceptance differs')
    require(len(intake['items']) == 2 and all(item['status'] == 'accepted' and len(item['jobs']) == 1
            for item in intake['items']), 'B2 requires its original two accepted attempts')
    expected = {job['id']: job for item in intake['items'] for job in item['jobs']}
    require(len(expected) == 2 and {job['variant']['label'] for job in expected.values()} == set(B_LABELS),
            'B2 original route scope differs')
    mapped = {p['proposal_id']: p['record_id'] for p in publication.details['plans']}
    require(len(mapped) == 2 and set(mapped) == set(inputs['variants']), 'B2 publication Plan scope differs')
    for item in intake['items']:
        job = item['jobs'][0]
        require(job['plan_id'] == item['plan_id'] == mapped[item['proposal_id']]
                and inputs['variants'][item['proposal_id']] == [job['variant']]
                and job['strategy_id'] == item['strategy_id'] == spec['strategies'][0]
                and job['context_id'] == item['context_id'] == spec['context_id']
                and job['parent_id'] == expected_parent_id
                and job['variant']['device'] == spec['runtime_id'] and job['variant']['cost'] == COST,
                'B2 Plan, variant, runtime or Context changed')
    jobs = check_scope(state, checkpoint, spec, expected)
    require(len(jobs) == 2 and all(j['reserved'] is True and not j.get('operation')
            and j['state'] in ('completed', 'failed', 'cancelled') and j.get('result') is not None
            for j in jobs.values()), 'B2 completion requires two integrated terminal attempts')
    require(completion['results'] == {key: job['result'] for key, job in jobs.items()},
            'B2 completion results differ from Snapshot Journal')
    for job in jobs.values():
        result = frozen.get(stable_id('workflow-result', job['run_id']))
        require(type(result) is Decision and result.kind == 'note' and result.task_id == spec['task_id']
                and result.target_id == job['run_id'] and result.details.get('type') == 'workflow_result.v1'
                and result.details['job_id'] == job['id'] and result.details['result'] == job['result'],
                'B2 result differs from original Evaluator marker')
    return jobs


def verify_b2_final_memory(directory, spec, verification_report):
    directory = Path(directory)
    require(type(spec) is dict and set(spec) == SPEC_FIELDS and spec['schema_version'] == 1
            and spec['runtime_generation'] == 'runtime7' and spec['round_id'] == ROUND
            and spec['grant_key'] == GRANT_KEY and type(spec['strategies']) is list
            and len(spec['strategies']) == 1, 'Only the original Runtime 7 B2 scope is supported')
    report = {'schema_version': 1, 'checked_at_utc': datetime.now(timezone.utc).isoformat(),
        'runtime_generation': spec['runtime_generation'], 'round_id': spec['round_id'], 'task_id': spec['task_id'],
        'store_writes': 0, 'network_calls': 0, 'original_second_round_scope_complete': False,
        'limitations': ['Memory/identity reconciliation is not proof of stable performance or all P3.5 Gates.',
            'Original saved verifier reports are trusted inputs; receipt identities are rebound here, NPZ values are not reread.',
            'Historical Context projections are checked structurally, historical execution evidence is not reverified.',
            'Large Analyzer manifest/result bodies and raw XPlane protobuf are not rehashed or reparsed here.']}
    target = target_for(directory, spec)
    required = [target / 'completed.json', Path(verification_report)]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        return {**report, 'status': 'pending_completion_or_verification', 'missing': missing}
    require(same(read_json(target / 'spec.json'), spec), 'Frozen B2 spec differs')
    completion = read_json(target / 'completed.json')
    require(set(completion) == {'spec_digest', 'round_id', 'context_id', 'snapshot', 'grant_key', 'results'}
            and completion['spec_digest'] == digest(spec) and completion['round_id'] == spec['round_id']
            and completion['context_id'] == spec['context_id'] and completion['grant_key'] == spec['grant_key'],
            'B2 completion identity differs')
    b2_report, b2_source = read_report(verification_report)
    require(b2_report['status'] == 'selected_terminal_jobs_verified' and b2_report['round_id'] == ROUND
            and b2_report['runtime_generation'] == 'runtime7' and b2_report['snapshot'] == completion['snapshot']
            and b2_report['recompute_npz_requested'] is True and b2_report['counts'] == {
                'verified': 2, 'pending': 0, 'failed_verification': 0}, 'Original B2 execution verification is incomplete')
    pin = spec['milestone_validation']['final_memory_report']
    prior_report, prior_source = read_report(pin['path'], pin['sha256'])
    require(prior_report['status'] == 'verified' and prior_report['task_id'] == spec['task_id']
            and prior_report['runtime_generation'] == 'runtime7'
            and prior_report['missing_verified_runs'] == prior_report['missing_verification_reports'] == []
            and prior_report['final_snapshot'] == spec['milestone']['snapshot'], 'Prior A2/diagnostic Memory binding differs')
    with TransactionStore(directory / 'experiments.sqlite3', directory / 'artifacts') as store:
        frozen = MilestoneStore(store, completion['snapshot'])
        view = MemoryReader(store).open_snapshot(completion['snapshot']['id'])
        require(view.snapshot == completion['snapshot'], 'Final Memory Snapshot digest differs')
        records = frozen.history(spec['task_id'])
        state = WorkflowJournal(frozen, spec['task_id']).load()
        require(state is not None and not state['cancelled'], 'Final Snapshot Task is missing or cancelled')
        entries = [e for key, e in state['commands'].items() if key.startswith('budget:')]
        require(len(entries) == 1, 'B2 final Snapshot must contain its sole original grant')
        entry, grant = entries[0], entries[0]['grant']
        require(entry['key'] == spec['grant_key'] and entry['digest'] == spec['grant_digest'] == digest(grant)
                and grant['next_batch']['round_id'] == ROUND and grant['next_batch']['cost'] == BATCH_COST
                and grant['milestone'] == spec['milestone'] and grant['old_limits'] == validate_limits(LIMITS)
                and grant['new_limits'] == state['limits'] == {**validate_limits(LIMITS), 'executions': 18, 'time_s': 32400}
                and grant['spent'] == {'executions': 15, 'tokens': 0, 'time_s': 27000},
                'B2 cumulative grant identity or preserved spending differs')
        prior = MilestoneStore(frozen, prior_report['final_snapshot'])
        prior_records = prior.history(spec['task_id'])
        prior_state = WorkflowJournal(prior, spec['task_id']).load()
        require(prior_state['revision'] == grant['expected_revision'] and digest(prior_state) == grant['expected_state_digest']
                and prior_state['spent'] == grant['spent']
                and all(frozen.get(r.id) == r for r in prior_records)
                and prior.metadata['high_watermark'] < frozen.metadata['high_watermark'], 'Prior milestone records or order changed')
        # Independently bind the B2 parent route to the prior milestone's
        # branch-best Candidate. Intake/Plan records are self-consistent by
        # construction, so this cross-snapshot check prevents a different
        # ParentCandidate from passing merely by being internally coherent.
        prior_view = MemoryReader(store).open_snapshot(prior_report['final_snapshot']['id'])
        require(prior_view.snapshot == prior_report['final_snapshot'],
                'Prior parent-binding Snapshot metadata differs')
        cards = [card for card in prior_view.status_cards(spec['context_id'])
                 if card['strategy_id'] == spec['strategies'][0]]
        require(len(cards) == 1 and cards[0]['branch_best'] is not None,
                'Prior B branch-best parent binding is missing')
        branch_best = cards[0]['branch_best']
        parent_candidate = frozen.get(branch_best['candidate_id'])
        require(type(parent_candidate) is Candidate and parent_candidate.id == branch_best['candidate_id'],
                'Prior B branch-best Candidate identity differs')
        expected_parent_id = _id('wf-parent', spec['task_id'], spec['strategies'][0], parent_candidate.id)
        selected_parent = frozen.get(expected_parent_id)
        require(type(selected_parent) is ParentCandidate
                and selected_parent.candidate_id == parent_candidate.id
                and selected_parent.identity == parent_candidate.identity,
                'Prior B branch-best ParentCandidate binding differs')
        journal_id = WorkflowJournal(frozen, spec['task_id']).prefix + str(entry['applied_revision'])
        anchor = frozen.get(journal_id)
        checkpoint = anchor.details['state']
        require(read_json(target / 'checkpoint.json') == {'journal_id': journal_id, 'state_digest': digest(checkpoint)}
                and checkpoint['commands']['budget:' + spec['grant_key']] == entry
                and checkpoint['jobs'] == prior_state['jobs'] and checkpoint['spent'] == prior_state['spent'],
                'Original B2 post-grant checkpoint differs')
        b2_jobs = admitted_b2(frozen, target, spec, state, checkpoint, completion, expected_parent_id)
        prior_jobs = {jid: j for jid, j in prior_state['jobs'].items() if j['round_id'] in {
            'real-runtime7-recovery-round:2', 'p35-runtime7-single-tile-diagnostic:1'}}
        require(len(prior_jobs) == 4 and {j['variant']['label'] for j in prior_jobs.values()} == {
            'length-row', 'length-smem', 'diagnostic-baseline', 'diagnostic-single-tile'}
            and {r['run_id'] for r in prior_report['verified_runs']} == {j['run_id'] for j in prior_jobs.values()},
            'Prior Memory must retain the exact four A2/diagnostic Runs')
        jobs = {**prior_jobs, **b2_jobs}
        require(len(jobs) == 6 and all(state['jobs'][jid] == job for jid, job in jobs.items()),
                'Completed six-run feedback differs from final Snapshot')
        charged = {key: sum(j['variant']['cost'][key] for j in state['jobs'].values() if j['reserved']) for key in COST}
        batch_cost = {key: sum(j['variant']['cost'][key] for j in b2_jobs.values() if j['reserved']) for key in COST}
        require(batch_cost == BATCH_COST and state['spent'] == charged == {
            key: checkpoint['spent'][key] + batch_cost[key] for key in COST}, 'Final cumulative reservations or spending differ')
        report.update(prior_snapshot=prior.metadata, final_snapshot=view.snapshot,
            final_budget={'revision': state['revision'], 'spent': state['spent'], 'limits': state['limits'],
                'remaining': remaining_budget(state), 'prior_spent': prior_state['spent'], 'batch_cost': batch_cost,
                'grant_key': entry['key'], 'grant_digest': entry['digest'], 'grant_sequence': entry['sequence'],
                'applied_revision': entry['applied_revision'], 'source': 'B2 completion Snapshot Journal'},
            contexts=verify_projection_bindings(view, records))
        strategies = sorted({job['strategy_id'] for job in jobs.values()})
        require(len(strategies) == 2, 'Final feedback must contain both A and B strategies')
        histories = view.histories(strategies)
        require(all(h['complete'] for h in histories.values()), 'Final participating history is incomplete')
        report['participating_histories'] = {sid: {**{k: h[k] for k in
            ('total', 'owned_total', 'dependency_total', 'complete')}, 'records_digest': digest(h['records'])}
            for sid, h in histories.items()}
        source_reports = [(b2_report, b2_source)]
        for source in prior_report['verification_sources']:
            source_reports.append(read_report(source['path'], source['sha256']))
        verified, rows = {}, {}
        for body, source in source_reports:
            for row in body.get('jobs', []):
                jid = row.get('id')
                if jid not in jobs or row.get('verification') != 'verified':
                    continue
                job = jobs[jid]
                bound = bind_verification(store, frozen, job, row)
                require(all(row[key] == job['result'][key] for key in ('classification', 'correctness', 'confirmed', 'timing_validity')),
                        'Reused verification conclusion differs from original result')
                receipts = {stage: json.loads(store.artifacts.read(ArtifactRef(**ref)))
                            for stage, ref in job['result']['receipt_refs'].items()}
                require(row['receipt_statuses'] == {stage: receipt['status'] for stage, receipt in receipts.items()},
                        'Reused receipt statuses differ')
                if 'correctness' in receipts and 'cases' in receipts['correctness']['outputs']:
                    require(row['npz_comparison_status'] == 'recomputed_from_raw_npz', 'Correctness arrays lack original NPZ verification')
                require(jid not in verified or verified[jid] == bound, 'Conflicting verification reports')
                verified[jid], rows[jid] = bound, row
        report['verification_sources'] = [source for _, source in source_reports]
        report['prior_memory_source'] = prior_source
        report['verified_runs'] = list(verified.values())
        report['missing_verified_runs'] = sorted(j['run_id'] for jid, j in jobs.items() if jid not in verified)
        feedback = {j['variant']['label']: bool(j['state'] in ('completed', 'failed') and jid in rows
                    and rows[jid]['receipt_statuses'].get('compile') in ('succeeded', 'failed'))
                    for jid, j in b2_jobs.items()}
        report['pending_recovery_candidates'] = sorted(label for label, present in feedback.items() if not present)
        report['b2_successful_runs'] = sum(j['state'] == 'completed' and j['result']['correctness'] == 'passed'
            and j['result']['timing_validity'] == 'valid' and j['result']['confirmed'] is True for j in b2_jobs.values())
        report['original_second_round_scope_complete'] = not report['missing_verified_runs'] and all(feedback.values())
        current_records = store.history(spec['task_id'])
        watermark = store.connection.execute('SELECT MAX(seq) FROM records WHERE task_id=?', (spec['task_id'],)).fetchone()[0]
        same_watermark = watermark == view.snapshot['high_watermark']
        live_state = WorkflowJournal(store, spec['task_id']).load()
        report['current'] = {'record_count': len(current_records), 'high_watermark': watermark,
            'same_high_watermark': same_watermark,
            'journal': {key: live_state[key] for key in ('revision', 'spent', 'limits', 'cancelled')},
            'projection_comparison': 'equal_at_same_watermark' if same_watermark else 'different_timepoints_not_equated'}
        if same_watermark:
            for cid, context in report['contexts'].items():
                require(store.frontier(spec['task_id'], cid) == context['frontier'], 'Current Frontier differs at same watermark')
                for card in context['cards']:
                    for field, sid in (('branch_best', card['strategy_id']), ('task_best', None)):
                        best = store.best(spec['task_id'], cid, sid)
                        require((best.id if best is not None else None) ==
                                (card[field]['evaluation_id'] if card[field] is not None else None),
                                'Current Best differs at same watermark')
        report['status'] = ('pending_verification' if report['missing_verified_runs'] else
                            'verified' if all(feedback.values()) else 'incomplete_execution_feedback')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--verification-report', type=Path, required=True)
    args = parser.parse_args()
    result = verify_b2_final_memory(args.directory, read_json(args.spec), args.verification_report)
    output = args.directory / ('p35-b2-final-memory-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(canonical_json({'report': str(output), 'status': result['status'],
        'original_second_round_scope_complete': result['original_second_round_scope_complete']}))
    sys.exit(0 if result['status'] == 'verified' else 1)
