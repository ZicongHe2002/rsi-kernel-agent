"""Serialize the current conversation's confirmation analysis; no model API."""
from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.contracts import canonical_json
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.profiler.session import submit_response
from real_round_aux import read_json, write_once
from prepare_confirmation_round import target_for

d = Path(__file__).resolve().parent
s = read_json(d / 'p35-confirmation-selection.json')
target = target_for(d, s)
state = read_json(target / 'session.json')
if state['result_digest'] is not None:
    print(canonical_json({'status': 'already_complete', 'digest': state['result_digest']}))
    raise SystemExit(0)
p = read_json(target / 'packet.json')
sid = s['strategy_id']
m = p['manifest']['strategies'][sid]
assert {r['name'] for r in p['tool_results']} >= {'source', 'history', 'profile', 'knowledge'}
hist = next(r['result'] for r in p['tool_results'] if r['name'] == 'history')
assert hist['complete'] and hist['next_offset'] is None and len(hist['records']) == hist['total']
src, history = 'source:' + sid, 'history:' + sid
bench = 'benchmark:' + m['benchmarks'][0]['id']
profile = 'profile:' + m['profiles'][0]['id']
refs = [src, history, bench, profile, 'knowledge:hist-decode-dma2',
        'knowledge:hist-profile-accounting', 'knowledge:official-jax-synchronized-benchmark']
code = {'strategy_id': sid,
    'stages': ['Contiguous BF16 entry prepares padded Q, broadcast new KV and lengths, then produces attention and a separate complete updated cache.'],
    'loops': ['Attention grids batch and visits every capacity/block_size tile, including opaque tail tiles; small is one tile, boundary/default are multiple tiles.'],
    'movement': ['Load raw cache tile, insert the new token under the clipped-length append mask, store its complete opaque bytes, then mask invalid values only in the arithmetic operand.'],
    'dependencies': ['QK FP32 accumulation and scale precede validity masking and online softmax. P is cast BF16 for PV with FP32 accumulation; final denominator handles zero valid entries.'],
    'layout_memory': ['Small uses H16,N1024,Dpad640,L512,T1024. Required prepare, full cache output and input cache preservation stay in the original public call. Logical tensor extents do not establish physical VMEM use.'],
    'synchronization': ['Pinned treatment starts the cache store before QK and waits immediately before body return. The original repaired control waits immediately after store.start. Both finish before tile/semaphore reuse; no cross-tile prefetch is added.'],
    'edge_cases': ['All three frozen cases retain exact BF16 cache checks, opaque NaN tails, B3 padding and lengths [0,1,129]. Nonpositive lengths require zero attention and unchanged cache. Single-tile zero-length specialization is a separate future diagnostic.'],
    'refs': refs}
observations = [
    {'id': 'C-host-readmission', 'strategy_id': sid, 'category': 'observed_fact',
     'text': 'The current-runtime treatment readmission has one valid synchronized host median. This is not an independent replication conclusion.',
     'refs': [bench], 'predicate': {'ref': bench, 'path': ['latency_ms'], 'value': m['benchmarks'][0]['latency_ms']}},
    {'id': 'C-profile-readmission', 'strategy_id': sid, 'category': 'observed_fact',
     'text': 'Current treatment has its own available Profile. Program duration is trace-derived and separate from host latency.',
     'refs': [profile], 'predicate': {'ref': profile, 'path': ['availability'], 'value': 'available'}},
    {'id': 'C-history-complete', 'strategy_id': sid, 'category': 'historical_claim',
     'text': 'The frozen input includes the complete participating history with dependencies, including prior failures, regressions, source parents and new-runtime readmission.',
     'refs': [history], 'predicate': {'ref': history, 'path': ['complete'], 'value': True}},
    {'id': 'C-need-replication', 'strategy_id': sid, 'category': 'hypothesis',
     'text': 'Runtime2 identical-source baseline host medians differed substantially while program medians were close. The single delayed-store-wait win and new-runtime admission samples justify controlled replication, without proving stable host improvement or DMA overlap.',
     'refs': refs, 'predicate': None},
]
validation = {
    'correctness': [
        'Each independent Run executes all original small/boundary/default checks with unchanged CPU Oracle and atol=rtol=0.01; attention shape/dtype/finite, full BF16 cache bytes and input cache preservation remain required.',
        'Reuse the admitted source/config/build/bundle bytes exactly for each role; actual loaded identity and environment must match the frozen Context. No Oracle, runner or benchmark changes.'],
    'performance': [
        'Execute C1,T1,T2,C2 sequentially on the same Runtime and input digest, using original five stages, five warmups, twenty host samples, all-output synchronization and separate XPlane capture. Keep every sample and time/lease order.',
        'Use the predeclared p35-abba-v1 policy: for host and program separately, both C1/T1 and C2/T2 median reductions must be at least 2%, all treatment medians strictly below all control medians, each role spread at most 5%; drift is inconclusive.',
        'Two independent Runs per role provide limited replication only. Do not treat twenty within-Run samples as twenty independent experiments or claim statistical significance, universal speedup, resource utilization or DMA causality.',
        'Publish all four outcomes and the confirmation conclusion into the original Memory before Round2; original Evaluator/consider bookkeeping remains distinct from this confirmation verdict.']}
plans = []
roles = {
    'baseline': ('Temporal control with the original repaired implementation',
        'Revert the selected treatment parent to the exact archived original B baseline source and source bundle, keeping its admitted config/build. Execute unchanged control bytes first and fourth to expose temporal drift.',
        ['The baseline control consumes two independent executions and brackets the treatment; it introduces no optimization and does not count as Round2.']),
    'candidate': ('Independent replication of the delayed cache-store wait schedule',
        'Keep the selected delayed-store-wait source and source bundle byte-identical, retaining store.wait before body return. Execute two distinct treatment Runs second and third with identical admitted config/build.',
        ['Replicating this scheduling change may reveal program improvement without host benefit; no new scheduling changes are allowed during confirmation.'])}
variants = {}
for role, (mechanism, modification, tradeoffs) in roles.items():
    rows = [r for r in s['sequence'] if r['role'] == role]
    pid = s['plan_roles'][role]
    plans.append({'id': pid, 'strategy_id': sid, 'parent_id': m['parent_id'],
        'mechanism': mechanism, 'modification': modification, 'refs': refs,
        'observation_ids': [o['id'] for o in observations], 'calculation_ids': [],
        'tradeoffs': tradeoffs, 'risks': ['Host/runtime drift or scheduling noise can prevent confirmation even with valid correctness and timing; no run may be selectively omitted.'],
        'unknowns': ['Independent DMA durations, critical path and measured VMEM/MXU utilization remain unavailable; the source change alone cannot establish their behavior.'],
        'variants': [r['label'] for r in rows], 'validation': validation})
    origin = read_json(d / ('confirmation-control-runtime3-job.json' if role == 'baseline' else 'baseline-b-runtime3-job.json'))
    assert origin['candidate_id'] == s['baseline_candidate_id' if role == 'baseline' else 'candidate_id']
    variants[pid] = []
    for row in rows:
        variant = deepcopy(origin['variant'])
        variant.update(label=row['label'], priority=row['priority'], cost={'executions': 1, 'tokens': 0, 'time_s': 1800.0})
        variants[pid].append(variant)
proposal = {'code_understanding': [code], 'observations': observations,
    'hypotheses': [{'id': 'C-causal-alternatives', 'strategy_id': sid,
        'text': 'The delayed wait may shorten device execution, but the prior apparent host benefit may be dominated by host scheduling variation.',
        'refs': refs, 'alternatives': ['Compiler scheduling changes may improve only device duration.', 'There may be no reproducible improvement in either metric.', 'Runtime drift may make this finite batch inconclusive.']}],
    'plans': plans, 'unknowns': ['These are two controlled confirmation roles, not two new optimization mechanisms or a completed second RSI round.',
        'Neither role is preselected as a winner. New environment admission samples and old Runtime2 results are excluded from the four-run confirmation statistic.',
        'Only contiguous Decode BF16 small is timed; other frozen cases establish correctness only. Further shape, dtype, window, cap and Forward performance remain unmeasured.']}
response = target / 'response-2-final.json'
write_once(response, {'final': proposal})
write_once(target / 'variants.json', variants)
result = submit_response(ExperimentStore(d / 'experiments.sqlite3', d / 'artifacts'), target, response)
print(canonical_json({'plan_ids': [q['id'] for q in result['plan_set']['plans']],
    'variants': sum(len(q['variants']) for q in result['plan_set']['plans']),
    'winner_selected': result['winner_selected'], 'review': result['analysis']['review']}))
