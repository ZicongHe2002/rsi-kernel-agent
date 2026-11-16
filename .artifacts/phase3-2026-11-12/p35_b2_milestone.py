"""Read-only admission for the two remaining B routes after verified A2/diagnostics.

This module creates no grant and never contacts a Runtime. Old fixed-limit guards
read only the immutable pre-grant Snapshot, never the later live Journal.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from rsi_kernel_agent.contracts import EvaluationContext, Run, RunEvent, canonical_json
from rsi_kernel_agent.store import StoreError
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from p35_runtime_generations import generation_number
from verify_real_round import require
from admit_runtime4 import require_generation_archive

COST = {'executions': 1, 'tokens': 0, 'time_s': 1800}
BATCH_COST = {'executions': 2, 'tokens': 0, 'time_s': 3600}
B_LABELS = ('query-in-attention', 'b1-fused-prepare')
STAGES = {'generate', 'compile', 'correctness', 'benchmark', 'profile'}


def digest(value):
    return sha256(canonical_json(value).encode('utf-8')).hexdigest()


class MilestoneStore:
    """Public read surface pinned to one complete original Snapshot."""
    def __init__(self, store, metadata):
        page = store.snapshot_page(metadata['id'], limit=metadata['record_count'])
        require(page['complete'] and page['snapshot'] == metadata
                and page['total'] == metadata['record_count'], 'Incomplete grant milestone Snapshot')
        self._store = store
        self.db_path = store.db_path
        self.artifacts = store.artifacts
        self.metadata = deepcopy(metadata)
        self._records = deepcopy(page['records'])
        self._by_id = {r.id: r for r in self._records}

    def get(self, record_id):
        if record_id not in self._by_id:
            # Preserve the original Store absence contract without consulting
            # later live records; execution._maybe recognizes this exact form.
            raise StoreError(f'Missing record: {record_id}')
        return deepcopy(self._by_id[record_id])

    def history(self, task_id):
        require(task_id == self.metadata['task_id'], 'Snapshot Task differs')
        return deepcopy(self._records)

    def run_state(self, run_id):
        require(type(self.get(run_id)) is Run, 'Snapshot identity is not a Run')
        events = [r for r in self._records if type(r) is RunEvent and r.run_id == run_id]
        if not events:
            return {'execution_status': 'not_run', 'scheduling_status': 'ready', 'event_id': None}
        event = events[-1]
        return {'execution_status': event.execution_status, 'scheduling_status': event.scheduling_status,
                'event_id': event.id}

    def snapshot_page(self, snapshot_id, offset=0, limit=100):
        first = self._store.snapshot_page(snapshot_id)
        metadata = first['snapshot']
        require(metadata['task_id'] == self.metadata['task_id']
                and metadata['high_watermark'] <= self.metadata['high_watermark'],
                'Requested Snapshot is later than grant milestone')
        whole = self._store.snapshot_page(snapshot_id, limit=metadata['record_count'])
        require(whole['complete'] and all(self._by_id.get(r.id) == r for r in whole['records']),
                'Requested Snapshot is outside grant milestone history')
        return deepcopy(self._store.snapshot_page(snapshot_id, offset=offset, limit=limit))


def successful(job):
    result = job.get('result') or {}
    return (job['state'] == 'completed' and result.get('provenance') == 'real'
            and result.get('correctness') == 'passed' and result.get('timing_validity') == 'valid'
            and result.get('confirmed') is True and set(result.get('receipt_refs', {})) == STAGES)


def validate_b2_variants(spec, plans, variants):
    require(len(plans) == len(variants) == 2 and len(spec['strategies']) == 1
            and all(p['strategy_id'] == spec['strategies'][0] and len(p['variants']) == 1 for p in plans)
            and {p['variants'][0] for p in plans} == set(B_LABELS)
            and {v['label'] for v in variants} == set(B_LABELS)
            and all(v['cost'] == COST for v in variants),
            'B2 must retain exactly the two B routes and their one-execution/1800-second costs')


def validate_batch_evidence(spec, grant, state, gates, contexts):
    generation_number(spec['runtime_generation'], minimum=6)
    recovery_id = 'real-' + spec['runtime_generation'] + '-recovery-round:2'
    diagnostic_id = 'p35-' + spec['runtime_generation'] + '-single-tile-diagnostic:1'
    require(spec['strategies'] == [gates['baseline-b']['strategy_id']], 'B2 must use the admitted B strategy')
    require(grant['next_batch']['round_id'] == spec['round_id'] and grant['next_batch']['cost'] == BATCH_COST
            and grant['old_limits'] == state['limits'] and grant['spent'] == state['spent']
            and state['limits']['executions'] == 16 and state['limits']['time_s'] == 28800
            and state['spent'] == {'executions': 15, 'tokens': 0, 'time_s': 27000}
            and grant['new_limits'] == {**state['limits'], 'executions': 18, 'time_s': 32400},
            'B2 requires the explicit two-execution grant without resetting or refunding history')
    milestone = grant['milestone']
    rows = milestone['rounds']
    require(milestone == spec['milestone'] and milestone['successful_round_id'] == recovery_id
            and len(rows) == 2 and {r['round_id'] for r in rows} == {recovery_id, diagnostic_id},
            'B2 requires the actual A2 and diagnostic milestone, not historical success')
    batches = []
    for round_id, labels in ((recovery_id, {'length-row', 'length-smem'}),
                             (diagnostic_id, {'diagnostic-baseline', 'diagnostic-single-tile'})):
        jobs = {k: j for k, j in state['jobs'].items() if j['round_id'] == round_id}
        row = next(r for r in rows if r['round_id'] == round_id)
        require(len(jobs) == 2 and {j['variant']['label'] for j in jobs.values()} == labels
                and len(row['run_ids']) == 2 and set(row['run_ids']) == {j['run_id'] for j in jobs.values()},
                'Milestone must retain both exact candidates in each completed batch')
        context = contexts[row['context_id']]
        require(type(context) is EvaluationContext and context.provenance == 'real'
                and context.task_id == spec['task_id']
                and context.environment['runtime_session_id'] == spec['runtime_id'],
                'Milestone belongs to another Task or Runtime')
        for job in jobs.values():
            require(job['task_id'] == spec['task_id'] and job['context_id'] == context.id
                    and job['strategy_id'] == gates['baseline-repair']['strategy_id']
                    and job['state'] in ('completed', 'failed') and job['reserved'] is True
                    and job['variant']['cost'] == COST and job['attempt'] == job['repair_depth'] == 0
                    and not job.get('retry_of') and not job.get('repair_of') and not job.get('operation')
                    and job['result'].get('provenance') == 'real'
                    and {'generate', 'compile'} <= set(job['result'].get('receipt_refs', {})),
                    'Milestone contains unfinished, replaced or unexecuted attempts')
        if round_id == recovery_id:
            require(context.id == spec['context_id'] and any(successful(j) for j in jobs.values()),
                    'At least one actual A2 candidate must pass all five stages')
        else:
            require(context.id != spec['context_id'] and all(successful(j) for j in jobs.values()),
                    'Both independent diagnostic roles must pass all five stages')
        batches.append(jobs)
    return tuple(batches)


def _read_report(path, expected_sha):
    path = Path(path).resolve()
    raw = path.read_bytes()
    require(sha256(raw).hexdigest() == expected_sha, 'Final Memory report bytes changed')
    report = json.loads(raw)
    require(type(report) is dict and report.get('status') == 'verified',
            'Final Memory report is not verified')
    return report


def _check_authorization(store, grant):
    auth = grant.get('authorization')
    require(type(auth) is dict and type(auth.get('quote')) is str and auth['quote'].strip(),
            'B2 grant authorization is missing')
    require('第二轮' in auth['quote'] and '一直自己迭代' in auth['quote'],
            'B2 grant is not bound to the continuing user authorization')
    # ArtifactRef is deliberately imported lazily so this helper remains usable
    # with the immutable Store adapters used by local tests.
    from rsi_kernel_agent.contracts import ArtifactRef
    source = store.artifacts.read(ArtifactRef(**auth['artifact_ref'])).decode('utf-8')
    require(auth['quote'] in source, 'B2 authorization quote is absent from its source artifact')


def validate_p35_milestone(store, directory, spec, grant, gates):
    """Validate the pre-grant A2/diagnostic milestone before B2 dispatch.

    The caller still owns grant creation. This function only reads the current
    Journal and the grant Snapshot; it does not append records or allocate TPU.
    """
    generation_number(spec.get('runtime_generation', ''), minimum=6)
    validation = spec.get('milestone_validation')
    require(type(validation) is dict and set(validation) == {'final_memory_report'},
            'B2 spec must pin one final Memory report')
    pin = validation['final_memory_report']
    require(type(pin) is dict and set(pin) == {'path', 'sha256'}
            and type(pin['path']) is str and type(pin['sha256']) is str
            and len(pin['sha256']) == 64, 'B2 report pin is malformed')
    _check_authorization(store, grant)
    state = WorkflowJournal(store, spec['task_id']).load()
    require(state is not None and not state['cancelled'], 'B2 requires an active Task')
    require(grant['task_id'] == spec['task_id'] and grant['next_batch']['round_id'] == spec['round_id'],
            'B2 grant does not fund this continuation')
    require(grant['milestone'] == spec['milestone'], 'B2 grant milestone differs from frozen spec')
    frozen = MilestoneStore(store, grant['milestone']['snapshot'])
    frozen_state = WorkflowJournal(frozen, spec['task_id']).load()
    require(frozen_state is not None and frozen_state['revision'] == grant['expected_revision']
            and digest(frozen_state) == grant['expected_state_digest'],
            'B2 grant Snapshot differs from its declared pre-grant Journal state')
    recovery, diagnostics = validate_batch_evidence(spec, grant, frozen_state, gates,
                                                     {c.id: c for c in frozen.history(spec['task_id'])
                                                      if type(c) is EvaluationContext})
    report = _read_report(pin['path'], pin['sha256'])
    require(report.get('runtime_generation') == spec['runtime_generation']
            and report.get('task_id') == spec['task_id'],
            'Final Memory report is bound to another milestone')
    completion_store = MilestoneStore(frozen, report['final_snapshot'])
    require(WorkflowJournal(completion_store, spec['task_id']).load() == frozen_state,
            'Final Memory report and grant contain different Journal states')
    require(report.get('missing_verification_reports') == []
            and report.get('missing_verified_runs') == []
            and len(report.get('verified_runs', [])) == 4,
            'Final Memory report is missing one of the four verified Runs')
    for row in report['verified_runs']:
        require(row.get('verification_report_reused') is True
                and (row['correctness'] != 'passed' or row.get('npz_comparison_status') == 'recomputed_from_raw_npz'),
                'Final Memory report lacks raw NPZ verification for a passing Run')
    require(report.get('verification_sources'), 'Final Memory report lacks verification sources')
    for source in report.get('verification_sources', []):
        source_path = Path(source['path'])
        raw = source_path.read_bytes()
        require(sha256(raw).hexdigest() == source['sha256'],
                'Final Memory verification source bytes changed')
    from p35_prepare_diagnostic_round import target_for
    from real_round_aux import read_json
    from verify_p35_final_memory import verify_final_memory
    selection = read_json(Path(directory) / ('p35-diagnostic-selection-' + spec['runtime_generation'] + '.json'))
    repeated = verify_final_memory(directory,
        Path(directory) / ('real-round-2-' + spec['runtime_generation']) / 'completed.json',
        target_for(directory, selection) / 'completed.json', generation=spec['runtime_generation'],
        verification_reports=[Path(source['path']) for source in report['verification_sources']])
    stable_fields = ('status', 'runtime_generation', 'task_id', 'recovery_snapshot', 'final_snapshot',
        'contexts', 'milestone_budget', 'original_second_round_scope_complete', 'pending_recovery_candidates',
        'participating_histories', 'verification_sources', 'verified_runs',
        'missing_verification_reports', 'missing_verified_runs')
    require(all(repeated.get(k) == report.get(k) for k in stable_fields),
            'Final Memory claims differ from original Snapshot and bound execution reports')
    # This guard is intentionally passed the frozen adapter. It can inspect the
    # old Runtime loss archives while remaining blind to later live records.
    require_generation_archive(frozen, directory, spec['runtime_generation'])
    return {'status': 'verified', 'runtime_generation': spec['runtime_generation'],
            'final_memory_report': {'path': str(Path(pin['path']).resolve()), 'sha256': pin['sha256']},
            'recovery_run_ids': sorted(j['run_id'] for j in recovery.values()),
            'diagnostic_run_ids': sorted(j['run_id'] for j in diagnostics.values()),
            'frozen_snapshot': grant['milestone']['snapshot']}
