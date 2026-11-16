"""Offline read-only reconstruction of frozen snapshots, projections and budget.

This audit never constructs the writable ExperimentStore. Snapshot hashes are
streamed through SQLite's read-only blob API. All immutable records are hashed,
decoded by the original schema and reconstructed into each frozen snapshot hash.
"""
from pathlib import Path
from hashlib import sha256
from datetime import datetime, timezone
from collections import Counter, defaultdict
import sqlite3, json, sys, copy, time, traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.contracts import canonical_json, record_from_dict, record_to_dict, ArtifactRef
from rsi_kernel_agent.artifacts import ArtifactStore
from rsi_kernel_agent.memory import _Index, MemoryView
from rsi_kernel_agent.workflow.budget import _grant_shape, grants, digest, validate_transition
from rsi_kernel_agent.workflow.scheduling import remaining_budget
from rsi_kernel_agent.workflow.execution import stable_id

OUT = Path(__file__).parent
BASE = ROOT / '.artifacts/phase3-2026-11-12'
DB = BASE / 'experiments.sqlite3'
TASK = 'mla-decode-v1'

def need(ok, message):
    if not ok:
        raise AssertionError(message)

def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def log(message):
    print(datetime.now(timezone.utc).isoformat(), message, flush=True)

class Readonly:
    def __init__(self, con):
        self.con = con
        self.db_path = DB
        self.artifacts = object.__new__(ArtifactStore)
        self.artifacts.root = (BASE / 'artifacts').resolve()

    def get(self, rid):
        row = self.con.execute('SELECT kind,payload,digest FROM records WHERE id=?', (rid,)).fetchone()
        need(row is not None, 'Missing record: ' + rid)
        need(sha256(row['payload'].encode()).hexdigest() == row['digest'], 'Record digest: ' + rid)
        return record_from_dict(row['kind'], json.loads(row['payload']))

def pin(con):
    n, water = con.execute('SELECT count(*),max(seq) FROM records WHERE task_id=?', (TASK,)).fetchone()
    snap = dict(con.execute('SELECT id,version,digest FROM snapshots WHERE task_id=? ORDER BY version DESC LIMIT 1', (TASK,)).fetchone())
    row = con.execute("SELECT payload FROM records WHERE task_id=? AND id LIKE 'wf-state:%' ORDER BY seq DESC LIMIT 1", (TASK,)).fetchone()
    state = json.loads(row[0])['details']['state']
    return {'record_count': n, 'high_watermark': water, 'snapshot': snap,
            'journal': {k: state[k] for k in ('revision', 'spent', 'limits', 'cancelled')}}

def snapshot_header(con, row):
    with con.blobopen('snapshots', 'body', row['rowid'], readonly=True) as blob:
        data = blob.read(65536).decode('utf-8')
    dec = json.JSONDecoder()
    need(data.startswith('{"metadata":'), 'Unexpected snapshot encoding')
    metadata, at = dec.raw_decode(data, len('{"metadata":'))
    need(data[at:].startswith(',"projections":'), 'Missing frozen projections')
    projections, at = dec.raw_decode(data, at + len(',"projections":'))
    need(data[at:].startswith(',"records":['), 'Missing frozen records')
    prefix = data[:at] + ',"records":['
    h, size = sha256(), 0
    with con.blobopen('snapshots', 'body', row['rowid'], readonly=True) as blob:
        while chunk := blob.read(8 * 1024 * 1024):
            h.update(chunk)
            size += len(chunk)
    need(h.hexdigest() == row['digest'], f'Snapshot {row["version"]} byte digest mismatch')
    return {'metadata': metadata, 'projections': projections, 'prefix': prefix,
            'actual_sha256': h.hexdigest(), 'bytes': size,
            'reconstructed': sha256(prefix.encode()), 'rows': 0}

def expected_links(kind, p):
    refs = []
    if kind == 'Task':
        return refs
    if kind in ('Workload', 'Strategy', 'EvaluationContext', 'ParentCandidate', 'Decision'):
        refs.append(('task_id', p['task_id']))
        if kind == 'EvaluationContext': refs.append(('workload_id', p['workload_id']))
        if kind == 'ParentCandidate' and p['candidate_id'] is not None: refs.append(('candidate_id', p['candidate_id']))
        if kind == 'Decision': refs.append(('target_id', p['target_id']))
    elif kind == 'Plan': refs.append(('strategy_id', p['strategy_id']))
    elif kind == 'Candidate': refs.extend((k, p[k]) for k in ('plan_id', 'parent_id'))
    elif kind == 'Run': refs.extend((k, p[k]) for k in ('candidate_id', 'context_id'))
    else:
        refs.append(('run_id', p['run_id']))
        for field in ('input_ids', 'evidence_ids', 'profile_ids'):
            refs.extend((field, x) for x in p.get(field, []))
    return refs

def grant_check(store, before, after, entry, snap_meta, state_digests):
    grant = entry['grant']
    new = _grant_shape(grant, before)
    need(entry['type'] == 'workflow_budget_grant.v1' and entry['digest'] == digest(grant), 'Grant identity')
    need(entry['sequence'] == len(grants(before)) + 1 and entry['applied_revision'] == after['revision'], 'Grant sequence')
    expected = copy.deepcopy(before)
    expected.update(limits=new, revision=after['revision'])
    expected['commands']['budget:' + entry['key']] = entry
    need(expected == after, 'Grant changed unrelated state')
    auth = grant['authorization']
    need(auth['quote'] in store.artifacts.read(ArtifactRef(**auth['artifact_ref'])).decode(), 'Grant source quote not retained')
    meta = grant['milestone']['snapshot']
    need(meta == {**snap_meta[meta['version']]['metadata'], 'digest': snap_meta[meta['version']]['actual_sha256']}, 'Grant milestone Snapshot identity')
    need(state_digests[before['revision']] == grant['expected_state_digest'], 'Grant expected state differs')
    need(not before['cancelled'] and not any(j['state'] in ('active','evaluating','blocked') for j in before['jobs'].values()), 'Unresolved operation at grant')
    checked = []
    for round_row in grant['milestone']['rounds']:
        jobs = [j for j in before['jobs'].values() if j['round_id'] == round_row['round_id']]
        need({j['run_id'] for j in jobs} == set(round_row['run_ids']), 'Grant milestone Run set')
        for job in jobs:
            need(job['state'] in ('completed','failed') and not job.get('operation') and job['reserved'], 'Grant job not terminal')
            need(job['context_id'] == round_row['context_id'], 'Grant context mismatch')
            marker = store.get(stable_id('workflow-result', job['run_id']))
            need(marker.details == {'type':'workflow_result.v1','job_id':job['id'],'result':job['result'],'cancelled':False}, 'Milestone fixed result marker')
            for stage, ref in job['result']['receipt_refs'].items():
                receipt = json.loads(store.artifacts.read(ArtifactRef(**ref)))
                need(receipt['stage'] == stage and all(receipt[k] == job[k] for k in ('run_id','candidate_id','parent_id','context_id','contract_digest')), 'Milestone receipt identity')
                published = store.get(stable_id('execution-receipt', receipt['operation_id']))
                need(published.details == {'type':'execution_receipt.v1','operation_id':receipt['operation_id'], 'request_digest':receipt['request_digest'],'artifact_ref':ref}, 'Milestone receipt marker')
            checked.append(job['run_id'])
    return {'key':entry['key'],'revision':entry['applied_revision'],'source':auth['source'],
            'quote':auth['quote'],'artifact_ref':auth['artifact_ref'],'old_limits':grant['old_limits'],
            'new_limits':grant['new_limits'],'spent_preserved':grant['spent'],
            'milestone_snapshot':meta,'milestone_runs_checked':checked,'next_batch':grant['next_batch']}

def main():
    started = time.perf_counter()
    c = sqlite3.connect(DB.resolve().as_uri() + '?mode=ro', uri=True, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA query_only=ON'); c.execute('BEGIN')
    store = Readonly(c)
    before_pin = pin(c)
    save('memory-audit-current-state.json', {'checked_at_utc':datetime.now(timezone.utc).isoformat(), **before_pin,
                                          'mode':'readonly transaction', 'store_writes':0,'network_calls':0})
    snapshots = {}
    for row in c.execute('SELECT rowid,id,version,digest FROM snapshots WHERE task_id=? AND version IN (4,10,19,23,25,26) ORDER BY version', (TASK,)):
        log('Hashing snapshot ' + str(row['version']))
        snapshots[row['version']] = snapshot_header(c, row)
    prefix = 'wf-state:' + sha256(TASK.encode()).hexdigest()[:32] + ':'
    previous = None
    previous_id = None
    state_digests, journal_changes, grant_checks, state_seqs = {}, [], [], {}
    thin, metadata, derived_links, reservations = [], {}, set(), []
    schema_count = 0
    for row in c.execute('SELECT * FROM records WHERE task_id=? ORDER BY seq', (TASK,)):
        raw, kind, rid = row['payload'], row['kind'], row['id']
        need(sha256(raw.encode()).hexdigest() == row['digest'], 'Immutable digest ' + rid)
        payload = json.loads(raw)
        obj = record_from_dict(kind, payload)
        need(canonical_json(record_to_dict(obj)) == raw and row['schema_version'] == 1, 'Immutable schema/canonical mismatch ' + rid)
        schema_count += 1
        fragment = ('{"kind":' + canonical_json(kind) + ',"payload":' + raw + ',"seq":' + str(row['seq']) + '}').encode()
        for snap in snapshots.values():
            if row['seq'] <= snap['metadata']['high_watermark']:
                if snap['rows']: snap['reconstructed'].update(b',')
                snap['reconstructed'].update(fragment); snap['rows'] += 1
        metadata[rid] = {k: row[k] for k in ('seq','kind','task_id','strategy_id','digest')}
        derived_links.update((rid, field, target) for field, target in expected_links(kind, payload))
        if kind == 'Decision' and (rid.startswith(prefix) or str(payload['details'].get('type','')).startswith('workflow_state.')):
            d, state = payload['details'], payload['details']['state']
            revision = len(state_digests) + 1
            need(payload['kind'] == 'note' and d['type'] == 'workflow_state.v1' and set(d) == {'type','revision','previous_id','state','state_digest'}, 'Journal shape')
            need(d['revision'] == state['revision'] == revision and rid == prefix + str(revision) and d['previous_id'] == previous_id and state['task_id'] == TASK, 'Journal chain')
            need(d['state_digest'] == digest(state), 'Journal state digest')
            state_digests[revision] = d['state_digest']
            state_seqs[revision] = row['seq']
            added = set(grants(state)) - set(grants(previous or {}))
            if added:
                need(len(added) == 1 and previous is not None, 'Multiple/initial grants')
                entry = grants(state)[next(iter(added))]
                grant_checks.append(grant_check(store, previous, state, entry, snapshots, state_digests))
            else:
                validate_transition(store, previous, state)
            charged = {k:sum(j['variant']['cost'][k] for j in state['jobs'].values() if j['reserved']) for k in ('executions','time_s','tokens')}
            need(charged == state['spent'], 'Reservation/spending mismatch revision ' + str(revision))
            if previous:
                for jid, old in previous['jobs'].items():
                    need(jid in state['jobs'], 'Job removed from journal')
                    new = state['jobs'][jid]
                    need(not old['reserved'] or new['reserved'], 'Reservation refunded')
                    need(old['variant']['cost'] == new['variant']['cost'], 'Reservation cost changed')
                need(all(state['spent'][k] >= previous['spent'][k] for k in charged), 'Spending decreased')
            new_reserved = [j for jid,j in state['jobs'].items() if j['reserved'] and (previous is None or not previous['jobs'].get(jid,{}).get('reserved'))]
            for j in new_reserved:
                reservations.append({'revision':revision,'job_id':j['id'],'run_id':j['run_id'],'round_id':j['round_id'],'label':j['variant']['label'],'cost':j['variant']['cost']})
            if previous is None or state['spent'] != previous['spent'] or state['limits'] != previous['limits']:
                journal_changes.append({'revision':revision,'spent':state['spent'],'limits':state['limits']})
            previous, previous_id = state, rid
            if revision % 50 == 0: log('Validated journal revision ' + str(revision))
        # Only bodies irrelevant to Memory status/ownership are omitted from the
        # in-memory status view; their complete original bytes were hashed above.
        if kind == 'Decision' and payload['kind'] == 'note' and payload['details'].get('type') not in ('parent_selection.v1','plan_parent.v1','knowledge_link.v1'):
            payload = {**payload, 'details':{'type':payload['details'].get('type'), 'audit_omitted_body_sha256':row['digest']}}
        thin.append({'kind':kind,'payload':payload})
    state = previous
    need(len(state_digests) == state['revision'], 'Final journal revision mismatch')
    for ver, snap in snapshots.items():
        snap['reconstructed'].update(b']}')
        need(snap['reconstructed'].hexdigest() == snap['actual_sha256'], 'Snapshot reconstruction mismatch ' + str(ver))
        need(snap['rows'] == snap['metadata']['record_count'], 'Snapshot count mismatch')
        snap['reconstructed_sha256'] = snap.pop('reconstructed').hexdigest()
        snap.pop('prefix')
        snap['last_journal_revision'] = max((rev for rev, seq in state_seqs.items() if seq <= snap['metadata']['high_watermark']), default=None)
    for grant in grant_checks:
        need(snapshots[grant['milestone_snapshot']['version']]['last_journal_revision'] == grant['revision'] - 1,
             'Grant milestone is not the exact prior Journal state')
    actual_links = {tuple(r) for r in c.execute('SELECT l.record_id,l.field,l.target_id FROM links l JOIN records r ON r.id=l.record_id WHERE r.task_id=?', (TASK,))}
    need(actual_links == derived_links, 'Immutable links differ from exact typed fields')
    for rid, field, target in derived_links:
        need(target in metadata, 'Cross-task/missing link target ' + target)
        need(metadata[target]['seq'] < metadata[rid]['seq'], 'Forward/noncausal link ' + rid)
    latest = snapshots[26]
    body = {'snapshot':{**latest['metadata'],'digest':latest['actual_sha256']},'records':thin,'projections':latest['projections']}
    index = _Index(body)
    for row in thin:
        kind, p = row['kind'], row['payload']
        typed = {'task_id':'Task', 'workload_id':'Workload', 'strategy_id':'Strategy',
                 'plan_id':'Plan', 'parent_id':'ParentCandidate', 'candidate_id':'Candidate', 'run_id':'Run'}
        for field, target_kind in typed.items():
            if p.get(field) is not None:
                need(index.by_id[p[field]]['kind'] == target_kind, 'Wrong typed reference ' + p['id'] + '/' + field)
        for field, target_kind in (('input_ids','Evidence'),('evidence_ids','Evidence'),('profile_ids','ProfileBundle')):
            for rid in p.get(field,[]):
                need(index.by_id[rid]['kind']==target_kind,'Wrong typed evidence dependency')
                target = index.by_id[rid]['payload']
                if field!='input_ids': need(target['run_id']==p['run_id'],'Cross-Run bundle dependency')
                else:
                    need(index.expect(target['run_id'])['context_id']==index.expect(p['run_id'])['context_id'], 'Cross-Context derived evidence')
        if kind=='ParentCandidate' and p['candidate_id'] is not None:
            need(index.expect(p['candidate_id'])['identity']==p['identity'],'Parent exact identity mismatch')
        if kind=='Evidence':
            need(p['source']['provenance']==index.expect(index.expect(p['run_id'])['context_id'])['provenance'], 'Evidence provenance mismatch')
    view = MemoryView(canonical_json(body))
    need(all(index.owner(rid) == row['strategy_id'] for rid,row in metadata.items()), 'Persisted ownership differs from Memory')
    live_proj = {table:[dict(r) for r in c.execute(f'SELECT * FROM {table} WHERE task_id=? ORDER BY 1,2,3', (TASK,))] for table in ('best','frontier')}
    need(live_proj == latest['projections'], 'Live projections differ from frozen same-watermark projections')
    promoted, frontier = {}, defaultdict(set)
    for row in thin:
        if row['kind'] != 'Decision': continue
        p = row['payload']
        if p['kind'] == 'promotion':
            d = p['details']; ev = index.expect(p['target_id'],'EvaluationBundle'); run = index.expect(ev['run_id'],'Run')
            need(d['context_id'] == run['context_id'], 'Promotion context mismatch')
            for flag, scope in (('branch_promoted','strategy:' + index.owner(run['id'])),('task_promoted','task')):
                key = (run['context_id'],scope)
                prior = promoted.get(key)
                should = d['eligible'] and (prior is None or ev['latency_ms'] < prior['latency_ms'])
                need(d[flag] == should, 'Promotion decision violates min-latency rule')
                if d[flag]:
                    need(ev['confirmed'] and ev['correctness'] == 'passed' and ev['timing_validity'] == 'valid', 'Invalid best eligibility')
                    promoted[key] = {'task_id':TASK,'context_id':run['context_id'],'scope':scope,'evaluation_id':ev['id'],'latency_ms':ev['latency_ms']}
        elif p['kind'] == 'frontier':
            d = p['details']; (frontier[d['context_id']].add if d['included'] else frontier[d['context_id']].discard)(p['target_id'])
    need(sorted(promoted.values(),key=lambda x:(x['context_id'],x['scope'])) == sorted(live_proj['best'],key=lambda x:(x['context_id'],x['scope'])), 'Promotion replay/live Best mismatch')
    expected_frontier = {(cid,can) for cid,cans in frontier.items() for can in cans}
    need(expected_frontier == {(r['context_id'],r['candidate_id']) for r in live_proj['frontier']}, 'Frontier decision replay mismatch')
    contexts = {}
    for row in thin:
        if row['kind'] != 'EvaluationContext': continue
        p = row['payload']; cards = view.status_cards(p['id'])
        contexts[p['id']] = {'environment':p['environment'],'workload_id':p['workload_id'],'provenance':p['provenance'],
            'cards':[{k:v for k,v in card.items() if k not in ('recent_plans','evidence_refs')} for card in cards],
            'frontier':sorted(frontier[p['id']])}
    jobs = []
    for j in state['jobs'].values():
        op = j.get('operation')
        jobs.append({k:j.get(k) for k in ('id','round_id','strategy_id','candidate_id','run_id','context_id','state','stage','reserved','retry_of','repair_of','attempt','repair_depth')} | {
            'label':j['variant']['label'],'cost':j['variant']['cost'],'operation_id':op.get('operation_id') if op else None,
            'operation_stage':op.get('stage') if op else None,'classification':(j.get('result') or {}).get('classification'),
            'receipt_stages':list((j.get('result') or {}).get('receipt_refs',{}))})
    run_states = index.states()
    running_runs = [{'run_id':rid,**value} for rid,value in run_states.items() if value['execution_status']=='running']
    unresolved_jobs = [j for j in jobs if j['state'] not in ('completed','failed','cancelled')]
    counts_by_kind = dict(Counter(r['kind'] for r in thin))
    result = {'status':'verified','checked_at_utc':datetime.now(timezone.utc).isoformat(),'store_writes':0,'network_calls':0,
        'before':before_pin,'snapshots':snapshots,'record_validation':{'records':schema_count,'typed_links':len(actual_links),
          'kind_counts':counts_by_kind,'all_digests_verified':True,'all_schema_canonical_roundtrips_verified':True,
          'typed_link_exact_set_verified':True,'all_links_prior_same_task':True,'memory_ownership_verified':True},
        'projection_replay':{'best_rows':len(live_proj['best']),'frontier_rows':len(live_proj['frontier']),
            'snapshot_live_equal':True,'promotion_and_frontier_decisions_replayed':True},
        'contexts':contexts,'journal':{'revisions_verified':len(state_digests),'state_digest':state_digests[state['revision']],
            'spent':state['spent'],'limits':state['limits'],'remaining':remaining_budget(state),'cancelled':state['cancelled'],
            'changes':journal_changes,'reservations':reservations,'grants':grant_checks,
            'retry_jobs':sum(bool(j['retry_of']) for j in jobs),'repair_jobs':sum(bool(j['repair_of']) for j in jobs),
            'jobs':jobs,'unresolved_jobs':unresolved_jobs,'running_runs':running_runs,
            'terminal_archived_intents':[j for j in jobs if j['state']=='cancelled' and j['operation_id']]},
        'limitations':['Offline audit does not query current remote operation/Runtime state.',
            'Immutable hash/receipt linkage assumes the existing trusted local Store and original execution producer.',
            'Memory status cards are produced by the original Memory code over a projection retaining all ownership, relation and status fields; irrelevant large note bodies are omitted only after complete-byte hashing and snapshot reconstruction.',
            'This module does not recompute NPZ or XPlane; those are covered by the separate raw evidence audit.']}
    after_transaction = pin(c)
    need(before_pin == after_transaction, 'Store changed within readonly transaction')
    c.rollback(); c.close()
    verify = sqlite3.connect(DB.resolve().as_uri()+'?mode=ro',uri=True); verify.row_factory=sqlite3.Row; verify.execute('PRAGMA query_only=ON')
    result['after_new_connection'] = pin(verify); verify.close()
    need(result['after_new_connection']==before_pin,'Store changed while auditing')
    result['duration_seconds'] = time.perf_counter()-started
    save('memory-audit-result.json', result)
    log('Verified records=%s links=%s journal=%s snapshots=%s duration=%.2fs' % (schema_count,len(actual_links),len(state_digests),list(snapshots),result['duration_seconds']))

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        save('memory-audit-error.json', {'status':'error','type':type(error).__name__,'error':str(error),'traceback':traceback.format_exc()})
        raise
