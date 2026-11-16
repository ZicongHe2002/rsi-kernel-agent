"""Retained request/receipt/recovery reconciliation; strictly offline/read-only."""
from pathlib import Path
from hashlib import sha256
from collections import Counter, defaultdict
from datetime import datetime, timezone
import sqlite3,json,sys,importlib.util

HERE=Path(__file__).parent
spec=importlib.util.spec_from_file_location('memory_audit',HERE/'memory-audit.py')
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)
from rsi_kernel_agent.contracts import canonical_json, ArtifactRef, record_to_dict
from rsi_kernel_agent.workflow.execution import stable_id
from rsi_kernel_agent.store import ExperimentStore
need=audit.need
c=sqlite3.connect(audit.DB.resolve().as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
ExperimentStore._check_schema(c)
need(c.execute('PRAGMA user_version').fetchone()[0]==1,'Store schema version mismatch')
need(list(c.execute('PRAGMA foreign_key_check'))==[],'Store foreign-key violation')
store=audit.Readonly(c)
requests,receipts,events,results,notes={}, {}, {}, {}, {}
separate_reservations=Counter();separate_costs=defaultdict(lambda:Counter())
run_states={json.loads(r[0])['id']:('not_run','ready') for r in c.execute("SELECT payload FROM records WHERE task_id=? AND kind='Run'",(audit.TASK,))}
allowed={('not_run','ready'):{('not_run','deferred'),('running','active')},('not_run','deferred'):{('not_run','ready'),('running','active')},('running','active'):{(x,'finished') for x in ('succeeded','failed','invalid','incomplete')}}
for row in c.execute("SELECT * FROM records WHERE task_id=? AND kind IN ('RunEvent','Decision') ORDER BY seq",(audit.TASK,)):
 if row['id'].startswith('wf-state:'):continue
 p=json.loads(row['payload']);typ=p.get('details',{}).get('type')
 if row['kind']=='RunEvent':
  old=run_states[p['run_id']];new=(p['execution_status'],p['scheduling_status'])
  need(new in allowed.get(old,set()),'Invalid retained RunEvent transition')
  run_states[p['run_id']]=new;events[p['run_id']]=p;continue
 if typ in ('baseline_reservation.v1','oracle_negative_reservation.v1','prerequisite_reservation.v1'):
  separate_reservations[typ]+=1
  separate_costs[typ].update(p['details']['cost'])
 if typ=='colab_execution_request.v1':
  d=p['details'];r=d['request'];op=r['operation_id'];job=r['job']
  need(op not in requests,'Duplicate durable operation')
  need(p['id']==stable_id('execution-request',op) and op==stable_id('operation',job['run_id'],r['stage']),'Unstable operation identity')
  need(sha256(canonical_json({k:v for k,v in r.items() if k!='request_digest'}).encode()).hexdigest()==r['request_digest'],'Request digest mismatch')
  need(p['target_id']==job['run_id'],'Request target mismatch')
  run=store.get(job['run_id']);can=store.get(job['candidate_id']);par=store.get(job['parent_id']);ctx=store.get(job['context_id'])
  need(run.candidate_id==can.id and run.context_id==ctx.id and can.parent_id==par.id,'Request record binding')
  need(record_to_dict(can)['identity']==r['candidate_identity'] and record_to_dict(par)['identity']==r['parent_identity'],'Request source/config/build binding')
  need(record_to_dict(ctx)==job['contract']['context'] and ctx.environment['runtime_session_id']==d['runtime_id']==job['variant']['device'],'Request relabelled context/runtime')
  requests[op]={'record_id':p['id'],'record_digest':row['digest'],'request':r,'runtime_id':d['runtime_id']}
 elif typ=='execution_receipt.v1':
  d=p['details'];op=d['operation_id'];need(op not in receipts,'Duplicate receipt operation')
  raw=store.artifacts.read(ArtifactRef(**d['artifact_ref']));r=json.loads(raw)
  need(canonical_json(r).encode()==raw,'Noncanonical receipt')
  need(p['id']==stable_id('execution-receipt',op) and d['request_digest']==r['request_digest'],'Receipt marker identity')
  receipts[op]={'record_id':p['id'],'receipt':r,'artifact_ref':d['artifact_ref']}
 elif typ=='workflow_result.v1':results[p['target_id']]=p['details']
 if typ and ('loss' in typ):notes[p['id']]=p
need(set(receipts)<=set(requests),'Orphan receipt without original request')
for op,got in receipts.items():
 r=got['receipt'];q=requests[op]['request'];job=q['job']
 need(all(r[k]==q[k] for k in ('operation_id','request_digest','stage','candidate_identity','parent_identity','provenance')),'Receipt/request mismatch')
 need(all(r[k]==job[k] for k in ('run_id','candidate_id','parent_id','context_id','contract_digest')),'Receipt Run binding')

archive_rows=[]
for generation,name in [('runtime3','real-round-2/runtime-loss-archive.json'),('runtime5','real-round-2-runtime5/runtime-loss-archive.json'),('runtime6','runtime6-completed-prerequisite-loss-archive.json')]:
 path=audit.BASE/name;raw=path.read_bytes();p=json.loads(raw);summary=p['summary'];stored=notes[p['decision_id']]['details']
 need(stored['summary']==summary and stored['intent_id']==p['intent_id'],'Archive Store summary mismatch')
 row={'generation':generation,'path':str(path.relative_to(audit.ROOT)),'sha256':sha256(raw).hexdigest(),'decision_id':p['decision_id'],'intent_id':p['intent_id']}
 if generation in ('runtime3','runtime5'):
  ids={summary['started_run_id'],*summary['not_started_run_ids']}
  need(not any(q['request']['job']['run_id'] in ids for q in requests.values()),'Archived unsubmitted Run has durable request')
  need(events[summary['started_run_id']]['execution_status']=='incomplete' and results[summary['started_run_id']]['result']['classification']=='cancelled','Started cancellation classification')
  need(all(rid not in events for rid in summary['not_started_run_ids']),'Unsubmitted queued Run gained an event')
  need(summary['spent_before']==summary['spent_after'],'Archived loss refunded spending')
  row.update(round_id=summary['round_id'],run_ids=sorted(ids),durable_requests=0,durable_receipts=0,started_run=summary['started_run_id'],started_status='incomplete / cancelled',
      not_started_runs=summary['not_started_run_ids'],spent_preserved=summary['spent_after'],retained_operation_id=summary['original_operation']['operation_id'])
 else:
  contexts=summary['context_ids'];gates=set(summary['gate_run_ids'])
  run_ids={json.loads(r[0])['id'] for r in c.execute("SELECT payload FROM records WHERE kind='Run' AND task_id=?",(audit.TASK,)) if json.loads(r[0])['context_id'] in contexts}
  need(run_ids==gates,'Runtime6 unexpected exploration Run')
  need(not summary['exploration_run_ids'] and not summary['exploration_job_ids'],'Runtime6 unexpected job claim')
  need(all(rid in results for rid in gates),'Runtime6 gate result missing')
  row.update(context_ids=contexts,gate_run_ids=sorted(gates),exploration_runs=0,exploration_requests=0,exploration_receipts=0,spent_preserved=summary['spent'])
 archive_rows.append(row)

p=json.loads((audit.BASE/'runtime4-prerequisite-loss-archive.json').read_text(encoding='utf-8'));s=p['summary'];d=notes[p['decision_id']]['details']
need(d['result']['summary']==s,'Runtime4 archived unknown summary mismatch')
op=s['original_operation_id'];need(op in requests and op not in receipts,'Runtime4 missing unknown request or fabricated receipt')
q=requests[op]['request'];rid=q['job']['run_id']
need(events[rid]['execution_status']=='incomplete' and rid not in results,'Runtime4 unknown result mislabeled')
prefix={q0['request']['stage'] for op0,q0 in requests.items() if q0['request']['job']['run_id']==rid}
done={r0['receipt']['stage'] for r0 in receipts.values() if r0['receipt']['run_id']==rid}
need(prefix=={'generate','compile','correctness'} and done=={'generate','compile'},'Runtime4 prefix differs')
archive_rows.append({'generation':'runtime4','path':str((audit.BASE/'runtime4-prerequisite-loss-archive.json').relative_to(audit.ROOT)),
 'sha256':sha256((audit.BASE/'runtime4-prerequisite-loss-archive.json').read_bytes()).hexdigest(),
 'decision_id':p['decision_id'],'run_id':rid,'operation_id':op,'run_status':'incomplete','remote_correctness_outcome':'unknown',
 'durable_request_stages':sorted(prefix),'receipt_stages':sorted(done),'evaluation_created':False,'remote_cancelled':False,'budget_refunded':False})

memory=json.loads((HERE/'memory-audit-result.json').read_text(encoding='utf-8'))
labels={j['candidate_id']:j['label'] for j in memory['journal']['jobs']}
for r in requests.values():labels.setdefault(r['request']['job']['candidate_id'],r['request']['job']['variant']['label'])
strategies={json.loads(r[0])['id']:json.loads(r[0]) for r in c.execute("SELECT payload FROM records WHERE task_id=? AND kind='Strategy'",(audit.TASK,))}
context_rows=[]
for cid,ctx in memory['contexts'].items():
 cards=[]
 for card in ctx['cards']:
  out={'strategy':strategies[card['strategy_id']]['name'],'strategy_id':card['strategy_id'],'paused':card['paused'],'deferred':card['deferred']}
  for field in ('branch_best','task_best','latest_attempt','last_correct_candidate','current_parent_candidate'):
   v=card[field];out[field]=None if v is None else {**v,'candidate_label':labels.get(v['candidate_id'],v['candidate_id'])}
  out['frontier']=[{'candidate_id':can,'label':labels.get(can,can)} for can in card['frontier']]
  cards.append(out)
 context_rows.append({'context_id':cid,'runtime_id':ctx['environment'].get('runtime_session_id'),'workload_id':ctx['workload_id'],'cards':cards})
unmatched=[{'operation_id':op,'request_id':r['record_id'],'run_id':r['request']['job']['run_id'],'stage':r['request']['stage'],'runtime_id':r['runtime_id'],
 'local_run_state':events[r['request']['job']['run_id']]['execution_status']} for op,r in requests.items() if op not in receipts]
summary={'status':'verified','checked_at_utc':datetime.now(timezone.utc).isoformat(),'store_writes':0,'network_calls':0,
 'schema_v1_exact_match':True,'foreign_key_check':'passed','run_event_transition_replay':'passed',
 'run_states':dict(Counter(s[0] for s in run_states.values())),
 'separate_reservations':{k:{'records':n,'cost':dict(separate_costs[k])} for k,n in separate_reservations.items()},
 'durable_request_count':len(requests),'durable_receipt_count':len(receipts),'requests_without_receipts':unmatched,
 'all_operation_ids_unique':True,'all_request_digests_verified':True,'all_receipt_artifact_hashes_verified':True,
 'all_request_receipt_runtime_context_candidate_bindings_verified':True,'archives':archive_rows,'context_summary':context_rows,
 'scope':'Metadata/receipt envelopes plus recovery archives; full NPZ/Profile verification belongs to raw audit.',
 'limitations':['Unique retained requests/receipts and safe code recovery branches do not prove physical exactly-once execution in arbitrary distributed failures.',
  'Runtime4 historical remote correctness outcome remains unknown; no present remote query was made.',
  'No live Runtime availability, external Notebook activity or hidden processes are inferred by this offline audit.']}
audit.save('memory-audit-recovery.json',summary)
print(json.dumps({k:v for k,v in summary.items() if k not in ('archives','context_summary')},indent=2))
