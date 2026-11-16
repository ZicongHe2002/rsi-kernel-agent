"""Read-only P3.5 completion-Snapshot reconciliation; never advances hardware or Store.

Execution validation reuses explicitly supplied verify_job reports and rebinds their
result and small receipt hashes. It does not reread large NPZ/XPlane artifacts.
"""
import argparse
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.contracts import (ArtifactRef, Candidate, Decision, EvaluationBundle,
    EvaluationContext, ParentCandidate, Run, canonical_json)
from rsi_kernel_agent.memory import MemoryReader
from rsi_kernel_agent.workflow.controller import stable
from rsi_kernel_agent.workflow.execution import stable_id
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from rsi_kernel_agent.workflow.scheduling import remaining_budget, validate_limits
from real_round_aux import LIMITS, read_json
from p35_recovery_round import recovery_identity, two_candidate_recovery
from p35_prepare_diagnostic_round import target_for, validate_selection
from verify_real_round import ReadOnlyStore, require

COST = {'executions': 1, 'tokens': 0, 'time_s': 1800}
PRIOR_SPENT = {'executions': 10, 'tokens': 0, 'time_s': 18000}


def digest(value):
    return sha256(canonical_json(value).encode('utf-8')).hexdigest()


class TransactionStore(ReadOnlyStore):
    """Reuse Store public reads within one explicitly read-only SQLite transaction."""
    def __enter__(self):
        self.connection = sqlite3.connect(self.db_path.as_uri() + '?mode=ro', uri=True)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('PRAGMA query_only=ON')
        self.connection.execute('BEGIN')
        return self

    def __exit__(self, *exc):
        self.connection.rollback()
        self.connection.close()

    @contextmanager
    def _connection(self):
        yield self.connection


class SnapshotRecords:
    """Only the original Journal.load read interface, pinned to immutable records."""
    def __init__(self, store, records):
        self.db_path = store.db_path
        self.records = records
        self.by_id = {record.id: record for record in records}

    def get(self, record_id):
        return self.by_id[record_id]

    def history(self, task_id):
        require(self.get(task_id).id == task_id, 'Snapshot Task differs')
        return list(self.records)


def open_completion(store, path, target, round_id, count):
    require(Path(path).resolve() == (target / 'completed.json').resolve(), 'Completion path differs from frozen round')
    completion = read_json(path)
    require(completion['round_id'] == round_id, 'Completion round differs')
    view = MemoryReader(store).open_snapshot(completion['snapshot']['id'])
    require(view.snapshot == completion['snapshot'], 'Snapshot metadata/digest differs from completion')
    page = store.snapshot_page(view.snapshot['id'], limit=view.snapshot['record_count'])
    require(page['complete'], 'Snapshot is incomplete')
    frozen = SnapshotRecords(store, page['records'])
    task_id = view.snapshot['task_id']
    state = WorkflowJournal(frozen, task_id).load()
    require(state is not None and not state['cancelled'], 'Snapshot Journal is missing or Task cancelled')
    jobs = {jid: job for jid, job in state['jobs'].items() if job['round_id'] == round_id}
    require(len(jobs) == count and all(j['state'] in ('completed', 'failed') and j['reserved'] is True
                                      and j.get('result') is not None for j in jobs.values()),
            'Completion must retain every reserved executed attempt, including failures')
    require(completion['results'] == {jid: job['result'] for jid, job in jobs.items()},
            'Completion results differ from Snapshot Journal')
    publication, intake = frozen.get(completion['publication_id']), frozen.get(completion['intake_id'])
    require(type(publication) is Decision and publication.kind == 'note'
            and publication.task_id == task_id and publication.details.get('type') == 'analysis_result.v1',
            'Completion publication differs')
    manifest_ref = read_json(target / 'manifest-ref.json')
    require(publication.details['manifest'] == manifest_ref, 'Publication manifest binding differs')
    require(type(intake) is Decision and intake.kind == 'note' and intake.task_id == task_id
            and intake.details.get('type') == 'workflow_intake.v1', 'Completion intake differs')
    admitted = intake.details['result']
    require(all(admitted[key] == value for key, value in
                {'id': intake.id, 'round_id': round_id, 'task_id': task_id, 'publication_id': publication.id}.items()),
            'Intake does not bind the completed round')
    intake_jobs = {j['id']: j for item in admitted['items'] if item['status'] == 'accepted' for j in item['jobs']}
    require(set(intake_jobs) == set(jobs), 'Completion omits or adds an admitted job')
    for jid, job in jobs.items():
        require(all(job[key] == value for key, value in intake_jobs[jid].items())
                and job['candidate_id'] == stable('wf-c', jid)
                and job['run_id'] == stable('wf-r', jid + ':0')
                and job['attempt'] == job['repair_depth'] == 0
                and not job.get('retry_of') and not job.get('repair_of'),
                'Completed job differs from immutable intake')
        marker = frozen.get(stable_id('workflow-result', job['run_id']))
        require(type(marker) is Decision and marker.kind == 'note' and marker.task_id == task_id
                and marker.target_id == job['run_id'] and marker.details.get('type') == 'workflow_result.v1'
                and marker.details['job_id'] == jid and marker.details['result'] == job['result'],
                'Completion result marker differs')
    return completion, view, page, frozen, state, jobs


def verify_projection_bindings(view, records):
    """Cross-bind original MemoryReader cards to actual decisions; do not rerank."""
    by_id = {record.id: record for record in records}
    contexts = {r.id: r for r in records if type(r) is EvaluationContext}
    promoted, frontier = {}, {cid: set() for cid in contexts}
    for record in records:
        if type(record) is not Decision:
            continue
        if record.kind == 'promotion':
            ev = by_id[record.target_id]
            require(type(ev) is EvaluationBundle, 'Promotion target is not an Evaluation')
            run = by_id[ev.run_id]
            candidate = by_id[run.candidate_id]
            strategy = by_id[candidate.plan_id].strategy_id
            require(record.details['context_id'] == run.context_id, 'Promotion Context differs')
            for flag, scope in (('branch_promoted', strategy), ('task_promoted', None)):
                if record.details[flag]:
                    require(record.details['eligible'] is True, 'Ineligible promotion flag')
                    promoted[(run.context_id, scope)] = ev.id
        elif record.kind == 'frontier':
            cid = record.details['context_id']
            (frontier[cid].add if record.details['included'] else frontier[cid].discard)(record.target_id)
    output = {}
    for cid, context in contexts.items():
        cards = view.status_cards(cid)
        for card in cards:
            for field, scope in (('branch_best', card['strategy_id']), ('task_best', None)):
                best = card[field]
                require((None if best is None else best['evaluation_id']) == promoted.get((cid, scope)),
                        'Snapshot Best differs from original promotion Decisions')
                if best is not None:
                    ev, run = by_id[best['evaluation_id']], by_id[best['run_id']]
                    candidate = by_id[best['candidate_id']]
                    require(type(ev) is EvaluationBundle and type(run) is Run and type(candidate) is Candidate
                            and ev.run_id == run.id and run.candidate_id == candidate.id and run.context_id == cid
                            and best['context_id'] == cid and best['latency_ms'] == ev.latency_ms,
                            'Best Evaluation/Run/Candidate/Context differs')
            for field in ('latest_attempt', 'last_correct_candidate'):
                value = card[field]
                if value is not None and value['run_id'] is not None:
                    run = by_id[value['run_id']]
                    require(run.context_id == cid and run.candidate_id == value['candidate_id'],
                            'Original Memory status card Run/Context differs')
                    if field == 'last_correct_candidate':
                        ev = by_id[value['evaluation_id']]
                        require(ev.run_id == run.id and ev.correctness == 'passed', 'Last Correct differs')
            parent = card['current_parent_candidate']
            if parent is not None:
                decision, source = by_id[parent['decision_id']], by_id[parent['parent_id']]
                require(type(source) is ParentCandidate and decision.details == {
                    'type': 'parent_selection.v1', 'strategy_id': card['strategy_id'],
                    'context_id': cid, 'parent_id': source.id}
                    and parent['candidate_id'] == source.candidate_id and parent['identity'] == asdict(source.identity),
                    'Current Parent differs from explicit original selection')
        require({candidate for card in cards for candidate in card['frontier']} == frontier[cid],
                'Snapshot Frontier differs from original Frontier Decisions')
        output[cid] = {'environment': context.environment, 'workload_id': context.workload_id,
                       'provenance': context.provenance, 'cards': cards,
                       'task_best': cards[0]['task_best'] if cards else None, 'frontier': sorted(frontier[cid])}
    return output


def verify_budget(records, state, recovery_jobs, diagnostic_jobs, generation='runtime5'):
    new_ids = set(recovery_jobs) | set(diagnostic_jobs)
    prior_spent = {'executions': 11, 'tokens': 0, 'time_s': 19800} if two_candidate_recovery(generation) else PRIOR_SPENT
    run_count = 4 if two_candidate_recovery(generation) else 6
    require(state['limits'] == validate_limits(LIMITS), 'Milestone budget limits differ from original 16/28800')
    prior = [r.details['state'] for r in records if type(r) is Decision
             and r.details.get('type') == 'workflow_state.v1'
             and not (set(r.details['state']['jobs']) & new_ids)]
    require(bool(prior) and prior[-1]['spent'] == prior_spent and prior[-1]['limits'] == state['limits'],
            'Missing preserved pre-subbatch budget checkpoint')
    require({jid: job for jid, job in state['jobs'].items() if jid not in new_ids} == prior[-1]['jobs'],
            'Prior jobs changed at the milestone')
    require(all(job['variant']['cost'] == COST and job['reserved'] is True
                for job in [*recovery_jobs.values(), *diagnostic_jobs.values()]),
            'Milestone reservation cost differs')
    charged = {key: sum(job['variant']['cost'][key] for job in state['jobs'].values() if job['reserved'])
               for key in COST}
    expected = {key: prior_spent[key] + run_count * COST[key] for key in COST}
    require(state['spent'] == charged == expected, 'Milestone spending/refund differs from original reservations')
    return {'revision': state['revision'], 'spent': state['spent'], 'limits': state['limits'],
            'remaining': remaining_budget(state), 'prior_spent': prior_spent,
            'new_reserved_runs': run_count, 'source': 'final completion Snapshot Journal, not current live Journal'}


def bind_verification(store, frozen, job, row):
    result = job['result']
    require(all(row.get(key) == job[key] for key in ('id', 'strategy_id', 'plan_id', 'candidate_id', 'parent_id',
                                                   'parent_evaluation_id', 'run_id', 'context_id')),
            'Verification report job/Run/Context binding differs')
    require(row['result_digest'] == digest(result) and row['result_marker_id'] == stable_id('workflow-result', job['run_id'])
            and row['receipt_refs'] == result['receipt_refs']
            and row['runtime_id'] == job['contract']['context']['environment']['runtime_session_id']
            and row.get('all_stage_artifacts_reachable_from_evaluation') is True,
            'Verification result/receipt binding differs')
    run, candidate = frozen.get(job['run_id']), frozen.get(job['candidate_id'])
    context = frozen.get(job['context_id'])
    require(type(run) is Run and type(candidate) is Candidate and type(context) is EvaluationContext
            and run.candidate_id == candidate.id and run.context_id == context.id
            and candidate.parent_id == job['parent_id'] and candidate.plan_id == job['plan_id']
            and asdict(context) == job['contract']['context'] and context.provenance == result['provenance'] == 'real'
            and result['run_id'] == run.id and result['candidate_id'] == candidate.id,
            'Executed result identity/Context differs')
    require(job['contract_digest'] == digest(job['contract']), 'Job contract digest differs')
    parent = frozen.get(job['parent_id'])
    evaluation = frozen.get(result['evaluation_id'])
    require(type(evaluation) is EvaluationBundle and evaluation.run_id == run.id
            and all(getattr(evaluation, key) == result[key] for key in
                    ('correctness', 'timing_validity', 'latency_ms', 'confirmed')), 'Result Evaluation differs')
    parent_evaluation = job['parent_evaluation_id']
    if parent_evaluation is not None:
        ev = frozen.get(parent_evaluation)
        parent_run = frozen.get(ev.run_id)
        parent = frozen.get(job['parent_id'])
        require(parent_run.context_id == context.id and parent_run.candidate_id == parent.candidate_id
                and ev.latency_ms == result['parent_latency_ms'], 'Parent comparison belongs to another Context or source')
    else:
        require(result['parent_latency_ms'] is None, 'Unexpected parent latency without bound Evaluation')
    for stage, raw_ref in result['receipt_refs'].items():
        body = json.loads(store.artifacts.read(ArtifactRef(**raw_ref)))
        op = stable_id('operation', run.id, stage)
        marker = frozen.get(stable_id('execution-receipt', op))
        require(body['stage'] == stage and body['operation_id'] == op and body['provenance'] == 'real'
                and all(body[key] == job[key] for key in ('run_id', 'candidate_id', 'parent_id', 'context_id'))
                and body['contract_digest'] == job['contract_digest']
                and body['candidate_identity'] == asdict(candidate.identity)
                and body['parent_identity'] == asdict(parent.identity)
                and type(marker) is Decision and marker.task_id == job['task_id'] and marker.target_id == run.id
                and marker.details == {'type': 'execution_receipt.v1', 'operation_id': op,
                                       'request_digest': body['request_digest'], 'artifact_ref': raw_ref},
                'Small receipt differs from original operation publication')
    return {'run_id': run.id, 'context_id': context.id, 'classification': result['classification'],
            'correctness': result['correctness'], 'confirmed': result['confirmed'],
            'result_marker_id': row['result_marker_id'], 'result_digest': row['result_digest'],
            'receipt_refs': row['receipt_refs'], 'npz_comparison_status': row.get('npz_comparison_status'),
            'verification_report_reused': True}


def verify_final_memory(directory, recovery_completed, diagnostic_completed, *, generation='runtime5',
                        verification_reports=()):
    directory = Path(directory)
    report = {'schema_version': 1, 'checked_at_utc': datetime.now(timezone.utc).isoformat(),
              'runtime_generation': generation, 'store_writes': 0, 'network_calls': 0,
              'historical_execution_evidence_not_reverified': True,
              'limitations': ['Memory reconciliation is not a claim that all P3.5 Gates or performance goals passed.',
                  'Supplied execution verification conclusions for the milestone Runs are trusted inputs, rebound to results and small receipt hashes.',
                  'Historical Best/Frontier/Parent are checked structurally against original Contexts and Decisions; their execution evidence is not reverified.',
                  'Analyzer manifest references are matched to immutable publications; large manifest bodies are not rehashed.',
                  'This pass does not reread NPZ values, reparse XPlane protobuf, or rerun hardware.']}
    missing = [str(path) for path in (recovery_completed, diagnostic_completed) if not Path(path).is_file()]
    if missing:
        return {**report, 'status': 'pending_completion', 'missing': missing}
    rec_target, rec_id = recovery_identity(directory, generation)
    selection = validate_selection(read_json(directory / ('p35-diagnostic-selection-' + generation + '.json')))
    require(selection.get('runtime_generation') == generation, 'Diagnostic selection generation differs')
    diag_target = target_for(directory, selection)
    require(read_json(diag_target / 'selection.json') == selection, 'Frozen diagnostic selection differs')
    for target in (rec_target, diag_target):
        require(read_json(target / 'runtime-binding.json') == {'generation': generation}, 'Frozen Runtime binding differs')
    packet = read_json(diag_target / 'diagnostic-packet.json')
    require(packet['original_context_id'] == selection['context_id'] != packet['context']['id'],
            'Diagnostic Context must be independent of the original workload')
    runtime_id = read_json(directory / ('colab-config-' + generation + '.json'))['runtime_id']
    with TransactionStore(directory / 'experiments.sqlite3', directory / 'artifacts') as store:
        recovery_count = 2 if two_candidate_recovery(generation) else 4
        rc, rv, rp, rf, rs, recovery_jobs = open_completion(store, recovery_completed, rec_target, rec_id, recovery_count)
        dc, view, page, frozen, state, diagnostic_jobs = open_completion(
            store, diagnostic_completed, diag_target, selection['round_id'], 2)
        task_id = selection['task_id']
        require(rv.snapshot['task_id'] == view.snapshot['task_id'] == task_id
                and rv.snapshot['high_watermark'] < view.snapshot['high_watermark'], 'Completion Snapshot order/Task differs')
        require(all(frozen.get(record.id) == record for record in rp['records']), 'Final Snapshot changed recovery history')
        require(all(state['jobs'][jid] == job for jid, job in recovery_jobs.items()), 'Recovery jobs changed before diagnostic completion')
        require(dc['context_id'] == packet['context']['id'] and dc['charged_cost'] ==
                {'executions': 2, 'tokens': 0, 'time_s': 3600}, 'Diagnostic completion Context/cost differs')
        jobs = {**recovery_jobs, **diagnostic_jobs}
        for job in jobs.values():
            expected_context = selection['context_id'] if job['id'] in recovery_jobs else packet['context']['id']
            require(job['context_id'] == expected_context and job['task_id'] == task_id
                    and job['contract']['context']['environment']['runtime_session_id'] == runtime_id,
                    'Completed job belongs to another Runtime or Context')
        require(asdict(frozen.get(packet['context']['id'])) == packet['context'], 'Frozen diagnostic packet differs from Store')
        report.update(task_id=task_id, recovery_snapshot=rv.snapshot, final_snapshot=view.snapshot,
                      contexts=verify_projection_bindings(view, page['records']),
                      milestone_budget=verify_budget(page['records'], state, recovery_jobs, diagnostic_jobs, generation))
        # Validate the earlier completion's projections too, without comparing it to later live Best.
        verify_projection_bindings(rv, rp['records'])
        strategy_ids = sorted({job['strategy_id'] for job in recovery_jobs.values()})
        if two_candidate_recovery(generation):
            require(strategy_ids == [selection['strategy_id']], 'Recovery subbatch must preserve the selected A strategy')
            report.update(original_second_round_scope_complete=False,
                          pending_recovery_candidates=['query-in-attention', 'b1-fused-prepare'])
        else:
            require(len(strategy_ids) == 2, 'Recovery must preserve both participating strategies')
        histories = view.histories(strategy_ids)
        require(all(h['complete'] for h in histories.values()), 'Participating history is incomplete')
        report['participating_histories'] = {sid: {**{k: h[k] for k in
            ('total', 'owned_total', 'dependency_total', 'complete')}, 'records_digest': digest(h['records'])}
            for sid, h in histories.items()}
        verified, sources, missing_reports = {}, [], []
        for path in verification_reports:
            if not Path(path).is_file():
                missing_reports.append(str(path))
                continue
            raw = Path(path).read_bytes()
            body = json.loads(raw)
            sources.append({'path': str(path), 'sha256': sha256(raw).hexdigest()})
            for row in body.get('jobs', []):
                if row.get('id') not in jobs or row.get('verification') != 'verified':
                    continue
                bound = bind_verification(store, frozen, jobs[row['id']], row)
                require(row['id'] not in verified or verified[row['id']] == bound, 'Conflicting reused verification reports')
                verified[row['id']] = bound
        report['verification_sources'] = sources
        report['missing_verification_reports'] = missing_reports
        report['verified_runs'] = list(verified.values())
        report['missing_verified_runs'] = sorted(job['run_id'] for jid, job in jobs.items() if jid not in verified)
        current = store.history(task_id)
        watermark = store.connection.execute('SELECT MAX(seq) FROM records WHERE task_id=?', (task_id,)).fetchone()[0]
        same = watermark == view.snapshot['high_watermark']
        current_state = WorkflowJournal(store, task_id).load()
        report['current'] = {'record_count': len(current), 'high_watermark': watermark, 'same_high_watermark': same,
            'journal': {key: current_state[key] for key in ('revision', 'spent', 'limits', 'cancelled')},
            'projection_comparison': 'equal_at_same_watermark' if same else 'different_timepoints_not_equated'}
        if same:
            for cid, context in report['contexts'].items():
                require(store.frontier(task_id, cid) == context['frontier'], 'Current Frontier differs at same watermark')
                for card in context['cards']:
                    for field, sid in (('branch_best', card['strategy_id']), ('task_best', None)):
                        best = store.best(task_id, cid, sid)
                        require((None if best is None else best.id) ==
                                (None if card[field] is None else card[field]['evaluation_id']),
                                'Current Best differs at same watermark')
        report['small_artifacts_hashed'] = len(store.artifacts.checked)
        report['status'] = 'pending_verification' if report['missing_verified_runs'] or missing_reports else 'verified'
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--generation', default='runtime5', help='runtimeN, N >= 4')
    parser.add_argument('--recovery-completed', type=Path, required=True)
    parser.add_argument('--diagnostic-completed', type=Path, required=True)
    parser.add_argument('--verification-report', action='append', type=Path, default=[])
    args = parser.parse_args()
    result = verify_final_memory(args.directory, args.recovery_completed, args.diagnostic_completed,
                                 generation=args.generation, verification_reports=args.verification_report)
    output = args.directory / ('p35-final-memory-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(canonical_json({'report': str(output), 'status': result['status'],
                          'missing_verified_runs': result.get('missing_verified_runs')}))
