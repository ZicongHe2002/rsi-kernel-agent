"""Current conversation's explicit four-Plan response; no model API or hardware call."""
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.contracts import canonical_json
from rsi_kernel_agent.profiler.session import submit_response
from real_round_aux import write_once
d=Path(__file__).resolve().parent
target=d/'real-round-1'
packet=json.loads((target/'packet.json').read_text('utf-8'))
m=packet['manifest']
A='mla-strategy:deee0989771642ee8e066be07adb0370a6c7a3bca6be04ebc8ab50e145c5b05e'
B='mla-strategy:ad3416851dfcc72142373541d6dc1f47121b390cce460b7fa479e9436b97e3cd'
code=[];obs=[];hyp=[];plans=[]
for label,sid in [('A',A),('B',B)]:
    data=m['strategies'][sid]
    src='source:'+sid;hist='history:'+sid
    bench='benchmark:'+data['benchmarks'][0]['id']
    profile='profile:'+data['profiles'][0]['id']
    code.append({'strategy_id':sid,
      'stages':['Frozen contiguous decode entry prepares padded Q, broadcast new KV and lengths; attention appends the token, computes attention and returns an independent complete updated cache.',
                'Current real baseline includes the compatibility repair that pads the complete new-KV batch before row slicing. It does not alter the frozen independent Oracle.'],
      'loops':['Prepare statically loops batch rows. Attention grids over batch and iterates every capacity/block_size tile even after valid sequence ends to preserve the complete cache output.',
               'Timed small B1,H16,N1024,L512,R64 uses T1024 and one tile; boundary B3,N256,L128,R64 uses T128; default B8,N16384,L512,R64 uses T1024.'],
      'movement':['Each tile loads raw cache into VMEM, substitutes appended features, writes the full raw tile to the separate output, then masks invalid values only in the local attention operand.',
                  'Prepared new tensor is logically B*T*Dpad; the same masked KV feeds QK and PV. Existing reuse must not be claimed as a new optimization.'],
      'dependencies':['QK uses the concatenated latent and rope query; scale, validity mask and optional cap precede online softmax. P is cast BF16 before PV; sums and accumulators are FP32.',
                      'Full cache copy and prepare remain inside the original compiled call and synchronized benchmark boundary. Input cache must remain unchanged.'],
      'layout_memory':['Dpad is lkv_dim plus rope/features rounded to128. Padded invalid features are zeroed only for arithmetic; opaque cache tails including NaNs are preserved byte-exactly.',
                       'Logical tensor size is not physical VMEM occupancy. Saved HLO scope sizes and duration traces do not establish spill or resource utilization.'],
      'synchronization':['Current shared DMA semaphore is drained by load.wait before store.start; store.wait occurs before QK/PV. Any reschedule must finish store before tile/semaphore reuse.',
                         'Small-case one-tile execution cannot benefit from cross-tile double buffering; same-tile store overlap is a distinct hypothesis.'],
      'edge_cases':['Zero length yields zero attention and unchanged cache; lengths clip through masks, full capacity tail copying remains required.',
                    'Boundary lengths [0,1,129], B3 padding offsets, NaN opaque tails, original positive lengths and all three frozen cases must pass. Other dtypes/mask/window/cap configurations remain unvalidated.'],
      'refs':[src,hist,profile,bench]})
    for suffix,ref,path,value,category,text in [
      ('host-baseline',bench,['latency_ms'],data['benchmarks'][0]['latency_ms'],'observed_fact','Synchronized host-call baseline median in milliseconds; not pure device kernel duration and not evidence of a bottleneck.'),
      ('profile-available',profile,['availability'],'available','observed_fact','Own Run Profile is available; the original duration parser provides trace-derived program/custom-call durations, not measured independent DMA or utilization.')]:
        obs.append({'id':label+'-'+suffix,'strategy_id':sid,'category':category,'text':text,'refs':[ref],
                    'predicate':{'ref':ref,'path':path,'value':value}})
    obs.append({'id':label+'-history-context','strategy_id':sid,'category':'hypothesis','text':
        ('Complete A history preserves the original boundary compile failure, explicit full-batch padding repair, prior runtime evidence and new-runtime readmission.' if label=='A' else
         'Complete B history includes the A repair ancestry and its own current-runtime baseline; B measured evidence is not borrowed from A.')+
        ' Historical DMA2 and forward state-lifetime ideas have different implementations and are not current TPU performance proof.',
        'refs':[hist,'knowledge:hist-decode-dma2','knowledge:hist-fwd-state-lifetime'],'predicate':None})
    hyp.append({'id':label+'-performance-causality','strategy_id':sid,
      'text':'Device duration is much shorter than synchronized host-call latency; a source improvement may reduce device work without producing a robust host latency improvement.',
      'refs':[src,bench,profile,'knowledge:official-jax-synchronized-benchmark','knowledge:hist-profile-accounting'],
      'alternatives':['Host dispatch or scheduling noise dominates the measured boundary.',
                      'Compiler already folds proposed redundant work; extra tiling or layout changes can regress.',
                      'Profile captures are separate from benchmark samples and cannot be substituted into their timing evidence.']})

correctness=[
 'Use the unchanged independent Oracle and frozen BF16 tolerances (atol=rtol=0.01 for attention); all three cases small/boundary/default must pass shape/dtype/finite and recorded errors.',
 'Compare complete updated cache byte-exactly including opaque NaN tails; verify input cache is unchanged, zero-length output and B3 padding case.',
 'Record actual loaded entry/dependency/build identity. Do not alter Oracle, runner, worker, workload semantics, output requirements or tolerances.']
performance=[
 'Original protocol: compile and warmup separately, time synchronized whole-call attention plus full cache output; preserve every raw sample and environment fingerprint.',
 'Profile separately with the frozen real parser, retain raw XPlane and own-Run bindings. Compare program and component durations without summing nested intervals or claiming DMA/resource counters.',
 'Benchmark small only; correctness of medium/default does not imply measured speed there. Same-runtime baseline host spread requires repeated or interleaved confirmation before asserting performance improvement.',
 'Original Evaluator/consider decides eligibility and bookkeeping; a lower median or Best promotion alone does not establish a reproducible speedup.']
def plan(pid,sid,mechanism,change,methods,cids,tradeoffs,risks,unknowns,variants):
    data=m['strategies'][sid];label='A' if sid==A else 'B'
    plans.append({'id':pid,'strategy_id':sid,'parent_id':data['parent_id'],'mechanism':mechanism,'modification':change,
      'refs':['source:'+sid,'history:'+sid,'benchmark:'+data['benchmarks'][0]['id'],'profile:'+data['profiles'][0]['id']]+
             ['knowledge:'+x for x in methods]+['calculation:'+x for x in cids],
      'observation_ids':[label+'-host-baseline',label+'-profile-available',label+'-history-context'],
      'calculation_ids':cids,'tradeoffs':tradeoffs,'risks':risks,'unknowns':unknowns,'variants':variants,
      'validation':{'correctness':correctness,'performance':performance}})
plan('R1-A-tile',A,'Reduce token tile working set while increasing loop count',
     'Keep repaired source bytes unchanged. Override only decode-small tiling.block_size to512 or256; preserve boundary128 and default1024. Each variant is independently compiled and evaluated.',
     ['hist-decode-dma2','hist-fwd-state-lifetime'],['A-QK-tile-1024','A-QK-tile-512','A-QK-tile-256'],
     ['QK arithmetic stays20,971,520 ideal FLOP; tile iterations increase1→2→4 and ideal query rereads increase. Smaller working tensors may help layout but add DMA/softmax/loop overhead.'],
     ['More online-softmax updates change BF16 roundoff and may regress latency; tile256/512 may expose compiler layout constraints.'],
     ['No measured spill pressure or DMA duration; the model is a dense-QK idealization, not total MLA traffic or TPU mapping.'],['tile512','tile256'])
plan('R1-A-single',A,'Eliminate zero old-state work in the statically single-tile specialization',
     'Only inside contiguous attention body, when the Python-static capacity==block_size, compute max(scores), masked p, total=sum(p), acc=BF16-P times KV directly. Preserve original online branch for every multi-tile workload, all DMA/cache operations and final safe normalization.',
     ['hist-fwd-state-lifetime','hist-profile-accounting'],['A-small-QK','A-small-PV'],
     ['Targets online-state max/exp/multiply bookkeeping around unchanged QK and PV. Dense model work remains unchanged; gain depends on whether compiler already eliminates zero state.'],
     ['All-invalid row must remain total=0, acc=0 and denominator1; changing P cast or FP32 accumulation would violate the contract.'],
     ['Compiler may already fold these expressions; static branch must not become Python branching on traced i.'],['single-tile'])
plan('R1-B-store',B,'Permit outstanding cache-store DMA while same-tile attention computes',
     'Move only contiguous body store.wait() from immediately after store.start() to immediately before body return. Keep load.wait(), raw tile store, semaphore reuse ordering and all numerical expressions unchanged; never write the DMA source tile while the store is outstanding.',
     ['hist-decode-dma2','hist-profile-accounting'],['B-single-item-pipeline-limit'],
     ['Potential same-tile store/compute overlap without a second tile or cross-tile prefetch; keeping transfer live may constrain compiler scheduling or VMEM reuse.'],
     ['DMA source tile lifetime and shared semaphore must remain valid; compiler may serialize or reject the schedule. Full output cache must still complete before return.'],
     ['No standalone measured DMA time; illustrative pipeline model explicitly predicts no cross-item benefit for one tile and does not predict this change.'],['delayed-store-wait'])
plan('R1-B-compact',B,'Reduce the logical prepared new-KV interface to one row per batch',
     'Keep full-batch new_rows padding before row slicing, then write np_ref[b]=row into prepared new shape[B,1,Dpad]. Change only corresponding attention BlockSpec to[1,1,Dpad], and broadcast that new row inside attention to[T,Dpad]. Keep lengths[B,H,T], all output/cache semantics and arithmetic unchanged.',
     ['hist-decode-prepare-padding','hist-fwd-state-lifetime'],['B-new-row-broadcast','B-new-row-compact'],
     ['Small logical new interface payload changes1,310,720→1,280bytes; this is not measured allocation savings because compiler may fold broadcasts. A narrower interface may introduce layout conversion.'],
     ['Pallas VMEM alignment/sublane constraints may reject one-row layout; retain full-batch padding to avoid the known B3 offset failure.'],
     ['No claimed physical memory reduction or measured bandwidth bottleneck; actual compiled layout, correctness and durations decide.'],['compact-new-row'])
proposal={'code_understanding':code,'observations':obs,'hypotheses':hyp,'plans':plans,'unknowns':[
 'No winner selected. Four independently attributable mechanisms, five variants, all require real execution.',
 'Identical-source A/B host timing spread is not an optimization result; benchmark scope is whole synchronized call, Profile scope is separate device tracing.',
 'No independent DMA timing, measured VMEM/MXU utilization, complete LLO or cross-runtime comparability. Historical text and theory are not current measured evidence.',
 'This round covers contiguous MLA Decode BF16 only. Forward and other API parameter configurations remain separate future Tasks.',
 'After all accepted attempts reach terminal state, read fresh all-strategy cards and complete participating histories; second-round decisions must change in response to these results.']}
path=target/'response-3-final.json'
write_once(path,{'final':proposal})
s=ExperimentStore(d/'experiments.sqlite3',d/'artifacts')
state=json.loads((target/'session.json').read_text('utf-8'))
if state['result_digest'] is None:
    result=submit_response(s,target,path)
else:
    result=json.loads((target/'result.json').read_text('utf-8'))
print(canonical_json({'plan_ids':[p['id'] for p in result['plan_set']['plans']],
    'variants':sum(len(p['variants']) for p in result['plan_set']['plans']),
    'review':result['analysis']['review'],'winner_selected':result['winner_selected']}))
