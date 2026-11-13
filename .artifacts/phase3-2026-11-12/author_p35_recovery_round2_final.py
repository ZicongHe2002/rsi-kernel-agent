"""Serialize this conversation's four new, evidence-driven Round 2 Plans."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.contracts import canonical_json
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.profiler.session import submit_response
from real_round_aux import read_json, write_once

d = Path(__file__).resolve().parent
t = d / 'real-round-2-runtime4'
assert read_json(t / 'session.json')['result_digest'] is None
p = read_json(t / 'packet.json')
m = p['manifest']
A = 'mla-strategy:deee0989771642ee8e066be07adb0370a6c7a3bca6be04ebc8ab50e145c5b05e'
B = 'mla-strategy:ad3416851dfcc72142373541d6dc1f47121b390cce460b7fa479e9436b97e3cd'
prepared = read_json(t / 'prepared.json')
assert len(prepared['cards']) == 3 and prepared['snapshot']['version'] > 11
assert all(not card['paused'] for card in prepared['cards'])
assert set(p['calculations']) == {'R2-A-length-expanded','R2-A-length-row','R2-A-length-scalar',
                                'R2-B-query-prepared','R2-B-query-raw','R2-B-prepare-interface','R2-B-raw-interface'}
assert [c['outputs']['peak_bytes'] for c in p['calculations'].values()]  # actual numeric tool output exists
r1 = read_json(d / 'real-round-1/controller-status.json')
r1_jobs = [j for j in r1['jobs'].values() if j['round_id'] == 'real-runtime2-round:1']
code, observations, hypotheses, plans = [], [], [], []
refs_by_strategy = {}

def hist_observation(sid, oid, text, record_id, path, value):
    records = m['strategies'][sid]['history']['records']
    index = next(i for i,r in enumerate(records) if r['payload']['id'] == record_id)
    current = records[index]['payload']
    for part in path:
        current = current[part]
    assert current == value
    ref = 'history:' + sid
    observations.append({'id':oid,'strategy_id':sid,'category':'historical_claim','text':text,
                         'refs':[ref], 'predicate':{'ref':ref,'path':['records',index,'payload',*path],'value':value}})

for label, sid in (('A',A),('B',B)):
    row = m['strategies'][sid]
    hist = row['history']
    assert hist['complete'] and hist['next_offset'] is None and len(hist['records']) == hist['total']
    assert hist == prepared['histories'][sid]
    src, history = 'source:' + sid, 'history:' + sid
    bench = 'benchmark:' + row['benchmarks'][0]['id']
    profile = 'profile:' + row['profiles'][0]['id']
    refs = [src,history,bench,profile,'knowledge:hist-decode-prepare-padding',
            'knowledge:hist-profile-accounting','knowledge:official-jax-synchronized-benchmark']
    refs_by_strategy[sid] = refs
    code.append({'strategy_id':sid,
        'stages':['Contiguous BF16 public call prepares padded Q, replicated new KV and int32 lengths, then appends KV and returns attention plus an independent full updated cache. All necessary preparation remains inside the timed call.'],
        'loops':['Prepare loops over static batch rows; attention grids batch and iterates every capacity/block_size tile to copy opaque cache tail. Small B1/H16/N1024/Dpad640 is one tile; boundary B3 is two tiles and default B8 has sixteen.'],
        'movement':['Prepared lengths are repeated over heads and token positions although each batch has one scalar length. Query padding and new-row broadcasts are separate prepare outputs.',
                    'The complete raw cache tile is stored before invalid tails/padding are zeroed only in the arithmetic value; full cache output and input cache preservation cannot be removed.'],
        'dependencies':['Masked QK and scaled/capped scores feed online FP32 max/sum/acc recurrence; P is BF16 for PV. Zero valid rows use zero numerator and a safe denominator. This round leaves this recurrence unchanged.'],
        'layout_memory':['Raw feature width is 576 or 192 and padded width 640 or 256. New KV must be padded as a complete batch before extracting rows. Narrow/singleton and scalar-memory layouts require real compilation.',
                         'Logical payload reductions calculated below do not measure physical VMEM, transfer bytes, spills or compute utilization.'],
        'synchronization':[('A parent waits immediately after store.start before QK/PV.' if sid==A else 'B parent retains delayed store.wait immediately before the body returns, draining before tile/semaphore reuse.'),
                           'Load completes before cache use; no cross-tile double buffering is proposed because the timed small case has one tile.'],
        'edge_cases':['Preserve B3 lengths [0,1,129], opaque NaN tails, every padded cache bit, zero attention for zero length and unchanged input cache.',
                      'The archived single-tile recurrence specialization has not yet passed an independent single-tile zero-length diagnostic; it is not incorporated into these new candidates. LSE is not part of this interface.'],
        'refs':refs})
    observations.append({'id':label+'-current-host','strategy_id':sid,'category':'observed_fact',
        'text':'The exact selected parent has a valid current-runtime synchronized host-call median; this one admission Run is not a stable-speedup conclusion.',
        'refs':[bench], 'predicate':{'ref':bench,'path':['latency_ms'],'value':row['benchmarks'][0]['latency_ms']}})
    observations.append({'id':label+'-current-profile','strategy_id':sid,'category':'observed_fact',
        'text':'The selected parent has its own available trace-derived Profile. Independent DMA/waits/resources and critical path remain unavailable.',
        'refs':[profile], 'predicate':{'ref':profile,'path':['availability'],'value':'available'}})
    note = next(r['payload'] for r in hist['records'] if r['kind']=='Decision'
                and r['payload'].get('details',{}).get('type')=='p35_confirmation_association.v1'
                and r['payload']['target_id']==sid)
    hist_observation(sid,label+'-ABBA-host','Historical Runtime3 ABBA evidence classifies host latency as inconclusive_drift; neither pairing reduced host median and control/treatment role spreads exceeded 5%.',
                     note['id'],['details','summary','dimensions','host','classification'],'inconclusive_drift')
    hist_observation(sid,label+'-ABBA-program','Historical Runtime3 ABBA evidence passed the predeclared program criterion with paired median reductions 6.49584% and 5.92180%. This is two Runs per role only, not universal or whole-call speedup.',
                     note['id'],['details','summary','dimensions','program','classification'],'replicated_observed_improvement')
    for j in r1_jobs:
        if j['strategy_id']==sid:
            recorded_result = next(r['payload'] for r in hist['records']
                if r['kind']=='Decision' and r['payload'].get('details',{}).get('type')=='workflow_result.v1'
                and r['payload']['details'].get('result',{}).get('run_id')==j['run_id'])
            hist_observation(sid,label+'-R1-'+j['variant']['label'],
                'Preserved Round1 '+j['variant']['label']+' outcome is '+j['result']['classification']+'. Its own Runtime2 result remains historical and is not compared numerically to the Runtime4 parent.',
                recorded_result['id'],['details','result','classification'],j['result']['classification'])
    observations.append({'id':label+'-changed-direction','strategy_id':sid,'category':'hypothesis',
        'text':('R1 tile512/256 increased program duration to 8.09/11.1275 us versus the 6.624375 us A baseline; single-tile was close at 6.580625 us and all three host outcomes were slower. Keep original tiles and recurrence; isolate length representation as a new optimization axis.' if sid==A else
                'R1 compact-new-row reduced the logical prepared payload but did not improve program materially. ABBA supports the delayed schedule in program only and shows nearly unchanged prepare. Retain delayed wait while testing query-placement and static B1 fusion as distinct alternatives; do not repeat compact-new-row or sum hypothetical gains.'),
        'refs':[src,history,'knowledge:hist-decode-dma2','knowledge:hist-fwd-state-lifetime'], 'predicate':None})
    hypotheses.append({'id':label+'-interface-cost','strategy_id':sid,
        'text':'Reducing a prepared interface or moving its construction into the consumer may reduce program work, but can add vector-layout and broadcast cost or fail lowering. Host scheduling variation can mask any program improvement.',
        'refs':refs, 'alternatives':['Compiler already folds the redundant values; no program gain.',
            'Different layouts or larger live temporaries regress performance.',
            'Valid program improvement does not translate to host improvement under the observed drift.']})

validation = {'correctness':[
    'Compile and validate all three original small/boundary/default cases against the unchanged CPU Oracle; attention shape/dtype/finite and atol=rtol=0.01, complete cache exact BF16 bytes including opaque NaN tails, and unchanged input cache remain mandatory.',
    'Only isolated candidate entry source may change. Keep registry, Oracle, runner, build options, config, workload, benchmark, worker and environment unchanged; source bundle entry and build bundle digest must match the new source.',
    'Failed compilation or correctness remains an archived failed/incorrect Candidate; never drop a case, widen tolerance, retrofit another source or relabel it as an infrastructure failure. Any repair requires a new Candidate and remaining original budget.'],
    'performance':[
    'Use the original five-stage Controller and same newly admitted Runtime4 Context. Compile separately, five warmups and twenty all-output synchronized host samples, no removal; Profile separately with original parser and save XPlane.',
    'Compare each independent Candidate only to its selected current-context parent via original Evaluator/consider; report device program and actual custom-call categories separately. A fused implementation can legitimately lack a separate prepare event; missing is not measured zero.',
    'Four variants cost four executions/7200 declared seconds. Preserve two executions/3600 seconds for separate single-tile zero-length diagnostic. Current cumulative budget is ten/18000 of sixteen/28800 and is not reset; there is no repair reserve.',
    'ABBA demonstrated host drift; a single Round2 win is exploratory and cannot prove stable host benefit. No winner is selected before measurement and no hardware rates or resource utilization are invented.']}

def plan(pid,sid,mechanism,modification,cids,tradeoffs,risks,unknowns,label):
    plans.append({'id':pid,'strategy_id':sid,'parent_id':m['strategies'][sid]['parent_id'],
        'mechanism':mechanism,'modification':modification,
        'refs':refs_by_strategy[sid]+['calculation:'+cid for cid in cids],
        'observation_ids':[o['id'] for o in observations if o['strategy_id']==sid], 'calculation_ids':cids,
        'tradeoffs':tradeoffs,'risks':risks,'unknowns':unknowns,'variants':[label],'validation':validation})
plan('R2-A-length-row',A,'One token-width length row with broadcast inside attention',
     'From the exact A repaired parent, change lp from [B,H,T] to [B,1,T], prepare broadcast to [1,T], and attention length BlockSpec to [1,1,T]. Broadcast len_ref[0] to [H,T] inside attention. Preserve clipping masks, int32 transpose, DMA timing and recurrence.',
     ['R2-A-length-expanded','R2-A-length-row'],
     ['Small logical length interface changes 65536 to4096 bytes; extra in-attention head broadcast may cancel the reduction. This does not change the token tile or new-KV representation.'],
     ['Singleton-head VMEM layout or broadcast may fail lowering or add layout conversions.'],
     ['Physical allocation, vector instruction count and host benefit are unknown.'],'length-row')
plan('R2-A-length-smem',A,'Load raw per-batch length through scalar memory',
     'Independently from the same A parent, remove lengths input and lp output from prepare. Pass raw [B] lengths to attention using pl.BlockSpec(memory_space=pltpu.SMEM); read len_ref[b] and broadcast to [H,T]. Preserve mask clipping and avoid signed min/max. Other prepare outputs, cache DMA and recurrence stay unchanged.',
     ['R2-A-length-expanded','R2-A-length-scalar'],
     ['Small raw length payload is4 bytes rather than a65536-byte prepared tensor; this is a logical interface comparison only. Scalar load/broadcast differs materially from retaining a VMEM token row.'],
     ['Mixed scalar/VMEM/HBM grid lowering and scalar-to-vector broadcast require actual TPU compilation.'],
     ['Existing SMEM examples elsewhere in source establish API use, not correctness or speed of this contiguous combination.'],'length-smem')
plan('R2-B-query',B,'Construct query padding in the attention consumer',
     'From the exact B delayed-wait parent, remove q input and qp output from prepare. Attention receives raw q using [1,H,D] BlockSpec and pads q_ref[0] to Dpad with the existing zero-concatenate expression. Keep new-KV full-batch padding and length prepare, cache output and delayed store wait unchanged.',
     ['R2-B-query-prepared','R2-B-query-raw'],
     ['Small padded query intermediate is20480 bytes; raw query input is18432 bytes. Padding arithmetic moves and is still timed; the prepare call remains for new/lengths.'],
     ['Raw D576/D192 block layout and concat offsets can fail lowering; moving work may increase attention duration.'],
     ['A smaller interface is not a measured DMA saving or promised program gain.'],'query-in-attention')
plan('R2-B-b1-fusion',B,'Fuse necessary preparation into attention for static batch one',
     'Independently from the same B parent, use only batch==1 as a static specialization. Skip separate prepare and pass full raw q/new/lengths via the original prepare-style VMEM BlockSpecs. Inside attention perform complete new-row padding before broadcast, query padding and length broadcast. For B>1 retain the parent prepare and attention-input path. Preserve delayed wait, all cache output and recurrence.',
     ['R2-B-prepare-interface','R2-B-raw-interface'],
     ['Small separate prepared interface logically totals1396736 bytes versus19588 raw input bytes, excluding unchanged full cache. Equivalent temporaries can remain inside attention, so this is not physical memory savings.',
      'One separate custom call may disappear for B1, at the cost of longer attention and altered scheduling. No runtime condition, benchmark label or tensor content selects the branch.'],
     ['Fusion can increase live temporaries and regress layout/scheduling or fail compilation. B3/B8 fallback must remain byte-semantically equivalent.'],
     ['No independent DMA/VMEM/MXU measurement or critical path is available; absence of a prepare event must be reported as absent, never fabricated as zero.'],'b1-fused-prepare')

proposal = {'code_understanding':code,'observations':observations,'hypotheses':hypotheses,'plans':plans,
    'unknowns':['These four source mechanisms were already proposed in the original Round2 analysis, but Runtime3 disappeared before any remote submission. The guarded archive retained its charged reservation and all not-run records. This recovery keeps those untested hypotheses, reads a new snapshot, and obtains new Runtime4 parents; it is not a claim that the abandoned round executed.',
                'Runtime3 ABBA remains historical and its outcome is inconclusive due host drift despite limited repeated program improvement. None of those times becomes a Runtime4 control or parent denominator; historical Best is not rewritten.',
                'Only BF16 contiguous Decode small is benchmarked. Forward, paged, other dtypes/mask/window/cap and broader shape performance remain unvalidated.',
                'Single-tile zero-length diagnostic is separate and pending. All three original cases are still required for these four candidates.']}
path = t / 'response-3-final.json'
write_once(path, {'final':proposal})
result = submit_response(ExperimentStore(d / 'experiments.sqlite3', d / 'artifacts'), t, path)
print(canonical_json({'plans':[q['id'] for q in result['plan_set']['plans']],
                      'review':result['analysis']['review'],'winner_selected':result['winner_selected']}))
