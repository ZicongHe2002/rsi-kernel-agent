"""Serialize a review draft from the completed Runtime 7 diagnostic read packet.

No ExperimentStore, submit_response, publication, intake, remote or budget writes.
Run only after the root agent confirms prepare and the original reads completed.
The resulting response is a local draft for root review, not a submitted final.
"""
import argparse
from hashlib import sha256
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.profiler.outputs import review_proposal, require
from rsi_kernel_agent.contracts import canonical_json
from real_round_aux import read_json, write_once

DIRECTORY = Path(__file__).resolve().parent
TARGET = DIRECTORY / 'single-tile-diagnostic-e18e6e9bf4a98216'
SOURCES = {
    'baseline': '2fc23aa9d0358ea18698f916cbc7ed5e02a4218dec2de2fc955661a8b513ebae',
    'single_tile': '325e0e9098633d625bcac832f9cb043935c7ce205df64a13b53fc84e22133e23',
}


def build_draft(target=TARGET):
    target = Path(target)
    selection = read_json(target / 'selection.json')
    require(selection['runtime_generation'] == 'runtime7'
            and selection['round_id'] == 'p35-runtime7-single-tile-diagnostic:1', 'Wrong diagnostic selection')
    require(read_json(target / 'session.json')['result_digest'] is None, 'Session already completed')
    packet = read_json(target / 'packet.json')
    require(packet.get('protocol') == 'rsi-analyzer-session.v1', 'Need the actual incomplete Analyzer packet')
    manifest = packet['manifest']
    sid = selection['strategy_id']
    require(set(manifest['strategies']) == {sid}, 'Diagnostic must contain exactly A')
    row = manifest['strategies'][sid]
    history = row['history']
    require(history['complete'] and history['next_offset'] is None
            and len(history['records']) == history['total'], 'Incomplete diagnostic A history')
    require(row['run'] is None and row['benchmarks'] == [] and row['profiles'] == [],
            'A fresh diagnostic Context must not inherit old performance denominators')
    diagnostic = read_json(target / 'diagnostic-packet.json')
    require(row['context'] == diagnostic['context']
            and diagnostic['original_context_id'] == selection['context_id']
            and row['context']['id'] != selection['context_id'], 'Diagnostic Context binding differs')
    require(diagnostic['source_digests'] == SOURCES and diagnostic['hardware_executed'] is False,
            'Diagnostic preparation identities differ')
    require(sha256(row['source'].encode('utf-8')).hexdigest() == SOURCES['single_tile'],
            'New manifest source is not the archived single-tile parent')
    variants = read_json(target / 'variants.json')
    require(set(variants) == {'diagnostic-baseline', 'diagnostic-single-tile'}, 'Need two fixed diagnostic variants')
    for role, label in (('baseline', 'diagnostic-baseline'), ('single_tile', 'diagnostic-single-tile')):
        require(len(variants[label]) == 1 and variants[label][0]['source_ref']['digest'] == SOURCES[role]
                and variants[label][0]['device'] == row['context']['environment']['runtime_session_id']
                and variants[label][0]['cost'] == {'executions': 1, 'tokens': 0, 'time_s': 1800},
                'Frozen diagnostic variant changed')
    require(variants['diagnostic-baseline'][0]['config_ref'] == variants['diagnostic-single-tile'][0]['config_ref'],
            'Both roles must have the same diagnostic configuration')
    methods = ('hist-decode-prepare-padding', 'hist-fwd-state-lifetime', 'official-jax-synchronized-benchmark')
    expected_reads = {'diagnostic-source', 'diagnostic-history', *('diagnostic-' + mid for mid in methods)}
    results = {item['id']: item for item in packet['tool_results']}
    require(expected_reads <= set(results) and all(not results[key].get('error') for key in expected_reads),
            'Original source/history/Knowledge reads have not completed successfully')
    src, hist = 'source:' + sid, 'history:' + sid
    refs = [src, hist, *('knowledge:' + mid for mid in methods)]
    require(all(manifest['references'][ref]['availability'] == 'available' for ref in refs),
            'A cited source/history/Knowledge reference is unavailable')
    records = history['records']
    by_id = {item['payload']['id']: (index, item) for index, item in enumerate(records)}
    observations, feedback = [], []

    def fact(oid, text, record_id, path, value):
        index, record = by_id[record_id]
        actual = record['payload']
        for part in path:
            actual = actual[part]
        require(actual == value, 'A proposed historical fact differs from the new packet')
        observations.append({'id': oid, 'strategy_id': sid, 'category': 'historical_claim',
            'text': text, 'refs': [hist],
            'predicate': {'ref': hist, 'path': ['records', index, 'payload', *path], 'value': value}})

    # Labels originate from the actual frozen R7 source package, while all Run,
    # Evaluator and numeric facts must exist in this new diagnostic history.
    origins = read_json(DIRECTORY / 'real-round-2-runtime7' / 'candidate-artifacts.json')
    require(len(origins) == 2 and {item['label'] for item in origins} == {'length-row', 'length-smem'},
            'R7 feedback must preserve exactly the two A candidates')
    for origin in origins:
        label = origin['label']
        candidate_ids = {item['payload']['id'] for item in records if item['kind'] == 'Candidate'
            and item['payload']['identity']['source_digest'] == origin['variant']['source_ref']['digest']
            and item['payload']['identity']['config_digest'] == origin['variant']['config_ref']['digest']
            and item['payload']['identity']['build_digest'] == origin['variant']['build_ref']['digest']}
        runs = [item['payload'] for item in records if item['kind'] == 'Run'
            and item['payload']['candidate_id'] in candidate_ids
            and item['payload']['context_id'] == selection['context_id']]
        require(len(runs) == 1, 'Expected one actual R7 Run for ' + label)
        run = runs[0]
        markers = [item['payload'] for item in records if item['kind'] == 'Decision'
            and item['payload'].get('details', {}).get('type') == 'workflow_result.v1'
            and item['payload']['details']['result']['run_id'] == run['id']]
        require(len(markers) == 1 and markers[0]['details']['cancelled'] is False,
                'Missing exact R7 fixed Evaluator result for ' + label)
        marker = markers[0]
        result = marker['details']['result']
        require(result['provenance'] == 'real' and result['correctness'] == 'passed'
                and result['timing_validity'] == 'valid' and result['confirmed'] is True
                and set(result['receipt_refs']) == {'generate', 'compile', 'correctness', 'benchmark', 'profile'},
                'Both observed R7 candidates must have their full real outcome before this draft')
        for field in ('classification', 'correctness', 'timing_validity', 'confirmed', 'latency_ms'):
            fact('R7-' + label + '-' + field,
                'Runtime 7 ' + label + ' fixed Evaluator records ' + field + '=' + str(result[field])
                + '. This is one independent formal-workload Run, not replicated speedup evidence.',
                marker['id'], ['details', 'result', field], result[field])
        feedback.append({'label': label, 'run_id': run['id'], 'result_marker_id': marker['id'],
                         **{key: result[key] for key in ('classification', 'correctness', 'confirmed', 'latency_ms')}})
    abba = [item['payload'] for item in records if item['kind'] == 'Decision'
            and item['payload'].get('details', {}).get('type') == 'p35_confirmation_association.v1'
            and item['payload']['target_id'] == sid]
    require(len(abba) == 1, 'Need the preserved historical ABBA association')
    fact('historical-host-drift',
         'Runtime 3 ABBA host result remains inconclusive_drift; its measurements are historical and no Runtime 7 denominator.',
         abba[0]['id'], ['details', 'summary', 'dimensions', 'host', 'classification'], 'inconclusive_drift')
    observations.append({'id': 'remaining-coverage-question', 'strategy_id': sid, 'category': 'hypothesis',
        'text': 'Both R7 length-representation candidates retain the general recurrence. Their three-case correctness does not answer whether the archived single-tile specialization preserves the zero-length contract. Use the two fixed source roles in an independent workload.',
        'refs': [src, hist], 'predicate': None})
    code = {'strategy_id': sid,
        'stages': ['The contiguous BF16 public call prepares padded Q, complete-batch padded new KV, and repeated int32 lengths; attention appends KV, computes attention and returns a separate full cache output.'],
        'loops': ['Attention grids over batch and visits every capacity/block_size tile. The diagnostic uses exactly one tile for both small and boundary; this is a static shape condition, not a branch on lengths or benchmark labels.'],
        'movement': ['Load and preserve every raw cache tile, append only legal positions, store the full output before zeroing undefined tail/padding in the arithmetic value. Opaque NaNs must not enter QK or PV and must remain bit-exact in retained output regions.'],
        'dependencies': ['In the archived static single-tile branch, m_next=max(scores), masked p=exp(scores-m_next), total=sum(p), acc=PV. All-invalid rows force p=0 and use denominator 1 when total is zero. The baseline uses max/alpha and online state updates. This source reasoning still requires the independent device checks.'],
        'layout_memory': ['Small is B1/H16/capacity1024/latent512/RoPE64 with padded width640; boundary is B3/H16/capacity256/latent128/RoPE64 with padded width256. Both roles share exactly the frozen diagnostic config; no VMEM, DMA or utilization measurements are invented.'],
        'synchronization': ['The fixed baseline and single-tile sources both wait after load and store copies. This diagnostic does not import delayed-store-wait, length-row, length-smem, fusion or other optimization changes.'],
        'edge_cases': ['Small lengths [0], boundary lengths [0,1,129], and opaque NaN tails directly exercise all-invalid rows and legal positive lengths inside capacity==block_size. Require attention shape/dtype/finite and unchanged tolerances, bit-exact full cache output and unchanged input cache. LSE is not exposed.'],
        'refs': refs}
    validation = {'correctness': [
        'Use the fixed independent CPU Oracle and original real runner. Keep BF16 attention shape/dtype/finite and atol=rtol=0.01, complete cache raw-bit comparison including opaque NaN tails and exact input cache preservation.',
        'Run both fixed cases: decode-small lengths [0] with capacity/block1024/1024; decode-boundary lengths [0,1,129] with capacity/block256/256. Both enter the static single-tile predicate. Do not delete failed cases, change tolerance or report LSE coverage.',
        'Preserve the exact archived baseline/single-tile sources, builds, source bundles and shared diagnostic config. Save distinct five-stage Runs, raw NPZ and fixed Evaluator results; verify both roles before declaring this coverage gap closed.'],
        'performance': [
        'Use the same Runtime 7 but the independent diagnostic Context, with parent_evaluation_id=None. Retain compile separation, five warmups, twenty all-output synchronized host samples without outlier removal and separate Profile/XPlane with the original parser.',
        'Zero-length diagnostic timings do not replace original three-case workload results or historical ABBA. The two R7 A outcomes are single Runs; neither their classification nor any diagnostic latency proves stable end-to-end benefit.',
        'Reserve exactly two executions/3600 declared seconds through the original Controller: cumulative spent13/23400 becomes15/27000 within16/28800, leaving1/1800. Do not refund prior lost-Runtime reservations, add repair/retry or apply a B2 grant before final Memory verification.']}
    plans = []
    definitions = (
        ('diagnostic-baseline', 'Re-establish general online softmax behavior under the independent all-invalid single-tile matrix',
         'Use the fixed Runtime 7 admitted baseline source with general max/alpha/total/acc recurrence. Keep the full preparation/cache path and run the common independent small/boundary one-tile matrix; no source edit is proposed.',
         'Provides a same-environment semantic control for the new matrix, but is not a performance denominator from a prior Context.'),
        ('diagnostic-single-tile', 'Directly test zero-length behavior of the archived static single-tile max/sum/PV specialization',
         'Use the exact original first-round single-tile source and static capacity==block_size branch. Omit only its already archived initial online-state update as that source defines; run the identical diagnostic matrix and preserve all output obligations.',
         'Tests the previously uncovered specialization path, but the all-invalid matrix and two diagnostic Runs cannot establish broad performance benefit.'),
    )
    for pid, mechanism, modification, tradeoff in definitions:
        plans.append({'id': pid, 'strategy_id': sid, 'parent_id': row['parent_id'],
            'mechanism': mechanism, 'modification': modification, 'refs': refs,
            'observation_ids': [item['id'] for item in observations], 'calculation_ids': [],
            'tradeoffs': [tradeoff],
            'risks': ['NaN tail leakage, all-invalid normalization, B3 layout/padding or cache retention could still fail on hardware; retain real failure evidence.'],
            'unknowns': ['The new diagnostic has no hardware outcome yet; static predicate evaluation and prior three-case success are insufficient.'],
            'variants': [pid], 'validation': validation})
    proposal = {'code_understanding': [code], 'observations': observations,
        'hypotheses': [{'id': 'single-tile-zero-equivalence', 'strategy_id': sid,
            'text': 'The fixed single-tile max/sum/PV path should preserve zero attention and opaque-cache semantics when no token is valid, but only an independent same-Runtime baseline/specialization pair can supply the missing direct coverage.',
            'refs': refs, 'alternatives': ['The general baseline itself may expose a diagnostic matrix lowering or layout problem.',
                'The specialization may mishandle all-invalid arithmetic or introduce tail NaNs despite the intended masks.',
                'Both roles may pass correctness while their host timing varies, which gives coverage rather than stable speedup.']}],
        'plans': plans,
        'unknowns': ['This draft has not submitted Analyzer output, created diagnostic Runs or executed hardware.',
            'LSE, Forward, paged, other dtype/window/cap and broader performance remain outside this diagnostic.',
            'The two remaining B proposals are pending; two A outcomes plus this diagnostic are not the entire original four-candidate second round.',
            'Only actual full diagnostic evidence, raw NPZ verification and final Memory/Best/Frontier/Current Parent/budget reconciliation can satisfy the next continuation milestone.']}
    reviewed = review_proposal(proposal, manifest, packet['calculations'], evidence_review=True)
    require(not reviewed['review'], 'Draft contains downgraded or unsupported observations')
    response = {'final': proposal}
    require(len(canonical_json(response)) <= packet['remaining']['response_chars'], 'Draft exceeds response limit')
    metadata = {'status': 'draft_only_not_submitted', 'manifest_digest': packet['manifest_digest'],
                'snapshot': history['snapshot'], 'parent_id': row['parent_id'], 'feedback': feedback,
                'review': reviewed['review'], 'plan_ids': [plan['id'] for plan in plans],
                'source_digests': SOURCES, 'store_writes': 0, 'network_calls': 0}
    return response, metadata


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, default=TARGET)
    args = parser.parse_args()
    response, metadata = build_draft(args.target)
    output = args.target / 'response-2-final.draft.json'
    write_once(output, response)
    write_once(args.target / 'response-2-final.draft-metadata.json', metadata)
    print(canonical_json({'draft': str(output), **metadata}))
