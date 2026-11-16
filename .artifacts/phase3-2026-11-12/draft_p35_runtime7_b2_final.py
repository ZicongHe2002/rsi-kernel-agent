"""Build a local-only Runtime 7 B2 Analyzer final draft.

The script deliberately does not open ExperimentStore, submit a response,
publish an analysis, create Runs, apply a grant, or contact a Runtime.  It
requires a saved post-read packet so source/history/profile/Knowledge reads are
bound to the same immutable manifest as the later math/final packet.
"""
import argparse
from hashlib import sha256
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.contracts import canonical_json
from rsi_kernel_agent.profiler.outputs import review_proposal, require
from real_round_aux import read_json, write_once

B_SOURCE = '0579beaa21cbf1119b64585c11334ab7051976a0f4b22400997fa510bd3b3a34'
B_LABELS = ('query-in-attention', 'b1-fused-prepare')
B_PLAN_IDS = ('R2-B-query', 'R2-B-b1-fusion')
READ_NAMES = {'source', 'history', 'profile', 'knowledge'}


def _tool_names(packet):
    return {row.get('name') for row in packet.get('tool_results', []) if isinstance(row, dict)}


def _history_rows(history):
    return history.get('records', [])


def _find_decisions(history, predicate):
    return [row for row in _history_rows(history)
            if row.get('kind') == 'Decision' and predicate(row.get('payload', {}))]


def _fact(observations, *, oid, sid, text, ref, path, value, history):
    actual = history
    for part in path:
        actual = actual[part]
    require(actual == value, 'Historical predicate does not match packet: ' + oid)
    observations.append({'id': oid, 'strategy_id': sid, 'category': 'historical_claim',
                         'text': text, 'refs': [ref],
                         'predicate': {'ref': ref, 'path': path, 'value': value}})


def _calc_ids(packet):
    ids = set(packet.get('calculations', {}))
    require({'R2-B-query-prepared', 'R2-B-query-raw',
             'R2-B-prepare-interface', 'R2-B-raw-interface'} <= ids,
            'B2 math results are incomplete')
    return ['R2-B-query-prepared', 'R2-B-query-raw',
            'R2-B-prepare-interface', 'R2-B-raw-interface']


def build_draft(target, reads_packet_path=None):
    target = Path(target)
    packet = read_json(target / 'packet.json')
    session = read_json(target / 'session.json')
    require(packet.get('protocol') == 'rsi-analyzer-session.v1', 'Not an Analyzer packet')
    require(session.get('result_digest') is None, 'B2 Analyzer session is already complete')
    manifest = packet['manifest']
    strategies = manifest['strategies']
    require(len(strategies) == 1, 'B2 must analyze exactly one strategy')
    sid = next(iter(strategies))
    row = strategies[sid]
    require(row['parent']['identity']['source_digest'] == B_SOURCE,
            'B2 parent is not the Runtime 7 delayed-store-wait source')
    require(row['run'] is not None and row['benchmarks'] and row['profiles'],
            'B2 manifest lacks the selected current-context parent benchmark/Profile')
    require(row['history']['complete'] and row['history']['next_offset'] is None
            and len(row['history']['records']) == row['history']['total'],
            'B2 participating history is incomplete')
    history_ref, source_ref = 'history:' + sid, 'source:' + sid
    bench_ref = 'benchmark:' + row['benchmarks'][0]['id']
    profile_ref = 'profile:' + row['profiles'][0]['id']
    knowledge_refs = sorted(ref for ref in manifest['references']
                            if ref.startswith('knowledge:'))
    require(knowledge_refs, 'B2 packet has no pinned Knowledge references')
    calc_ids = _calc_ids(packet)
    refs = [source_ref, history_ref, bench_ref, profile_ref, *knowledge_refs,
            *['calculation:' + cid for cid in calc_ids]]

    if reads_packet_path is not None:
        reads = read_json(reads_packet_path)
        require(reads.get('manifest_digest') == packet.get('manifest_digest'),
                'Saved pre-math packet belongs to another manifest')
        require(READ_NAMES <= _tool_names(reads),
                'Saved pre-math packet lacks source/history/profile/Knowledge reads')

    observations = []
    benchmark = row['benchmarks'][0]
    profile = row['profiles'][0]
    observations.extend([
        {'id': 'B-current-parent-host', 'strategy_id': sid, 'category': 'observed_fact',
         'text': 'The selected Runtime 7 B delayed-store-wait parent has one valid synchronized host median. This admission Run is the current-context parent datum, not replication evidence.',
         'refs': [bench_ref], 'predicate': {'ref': bench_ref, 'path': ['latency_ms'], 'value': benchmark['latency_ms']}},
        {'id': 'B-current-parent-profile', 'strategy_id': sid, 'category': 'observed_fact',
         'text': 'The selected parent has an available trace-derived Profile; program, prepare and attention events remain separate from host latency.',
         'refs': [profile_ref], 'predicate': {'ref': profile_ref, 'path': ['availability'], 'value': 'available'}},
        {'id': 'B-history-complete', 'strategy_id': sid, 'category': 'historical_claim',
         'text': 'The new Runtime 7 B input contains a complete participating history with dependencies.',
         'refs': [history_ref], 'predicate': {'ref': history_ref, 'path': ['complete'], 'value': True}},
    ])

    # Bind the exact continuation milestone if the preparation note is in the
    # new B history.  It is the authoritative bridge to A2/diagnostic feedback;
    # no old fixed record index is used.
    continuation = _find_decisions(
        row['history'], lambda p: p.get('details', {}).get('type') == 'workflow_continuation_inputs.v1')
    if continuation:
        note = continuation[-1]['payload']
        details = note['details']
        nidx = next(i for i, item in enumerate(row['history']['records'])
                    if item['payload']['id'] == note['id'])
        hpath = ['records', nidx, 'payload', 'details']
        _fact(observations, oid='B-continuation-grant', sid=sid,
              text='This B2 input is bound to the exact finite continuation grant and its preserved milestone.',
              ref=history_ref, path=hpath + ['type'], value='workflow_continuation_inputs.v1',
              history={'records': row['history']['records']})
        # The preceding helper used the full history wrapper; add precise
        # milestone predicates without assuming a record number.
        grant_body = details['grant'].get('grant', details['grant'])
        grant_path = ['grant', 'grant'] if 'grant' in details['grant'] else ['grant']
        for suffix, value in [
            (grant_path + ['next_batch', 'cost', 'executions'], grant_body['next_batch']['cost']['executions']),
            (grant_path + ['next_batch', 'cost', 'time_s'], grant_body['next_batch']['cost']['time_s']),
            (['milestone', 'successful_round_id'], details['milestone']['successful_round_id']),
        ]:
            _fact(observations, oid='B-grant-' + '-'.join(suffix), sid=sid,
                  text='The saved B2 continuation input records the exact finite grant/milestone field ' + '.'.join(suffix) + '.',
                  ref=history_ref, path=hpath + suffix, value=value,
                  history={'records': row['history']['records']})
    else:
        observations.append({'id': 'B-feedback-bridge-missing', 'strategy_id': sid,
            'category': 'unknown',
            'text': 'The B history does not expose a workflow continuation input note; A2 and diagnostic feedback must remain an explicit unknown until the new packet binds it.',
            'refs': [history_ref], 'predicate': None})

    # Bind the verified A2/diagnostic milestone association when it is present
    # in this new B packet.  The association is discovered by its typed
    # decision payload and its actual record id; no stale fixed index or old
    # report is used as a current denominator.
    feedback = _find_decisions(
        row['history'], lambda p: p.get('details', {}).get('type') == 'p35_b2_feedback_association.v1')
    if feedback:
        note = feedback[-1]['payload']
        details = note['details']
        nidx = next(i for i, item in enumerate(row['history']['records'])
                    if item['payload']['id'] == note['id'])
        base = ['records', nidx, 'payload', 'details']
        for group, label in (('a2_results', 'A2'), ('diagnostic_results', 'diagnostic')):
            values = details.get(group, [])
            require(len(values) == 2,
                    'Feedback association must contain two ' + label + ' results')
            for index, item in enumerate(values):
                for field in ('classification', 'correctness', 'timing_validity',
                              'confirmed', 'latency_ms'):
                    if field in item:
                        _fact(
                            observations,
                            oid='B-feedback-' + group + '-' + str(index) + '-' + field,
                            sid=sid,
                            text='The new B2 history binds verified ' + label +
                                 ' feedback field ' + field + ' for result ' + str(index) + '.',
                            ref=history_ref,
                            path=base + [group, index, field],
                            value=item[field],
                            history={'records': row['history']['records']})
        for field, value in [
            ('status', details['final_memory']['status']),
            ('original_second_round_scope_complete',
             details['final_memory']['original_second_round_scope_complete']),
        ]:
            _fact(
                observations,
                oid='B-feedback-final-memory-' + field,
                sid=sid,
                text='The new B2 history binds final Memory field ' + field +
                     ' from the verified A2/diagnostic milestone.',
                ref=history_ref,
                path=base + ['final_memory', field],
                value=value,
                history={'records': row['history']['records']})
    else:
        observations.append({
            'id': 'B-feedback-association-missing',
            'strategy_id': sid,
            'category': 'unknown',
            'text': 'The new B2 packet does not contain the expected verified A2/diagnostic feedback association; no old result is substituted.',
            'refs': [history_ref],
            'predicate': None})

    # Preserve ABBA as historical context only when the new packet actually
    # carries the association.  This is deliberately discovered by type, not
    # by a stale record index.
    abba = _find_decisions(row['history'], lambda p: p.get('details', {}).get('type') == 'p35_confirmation_association.v1')
    if abba:
        note = abba[-1]['payload']; details = note['details']
        nidx = next(i for i, item in enumerate(row['history']['records'])
                    if item['payload']['id'] == note['id'])
        path = ['records', nidx, 'payload', 'details', 'summary', 'dimensions', 'host', 'classification']
        if all(part in details for part in ('summary',)):
            _fact(observations, oid='historical-ABBA-host-drift', sid=sid,
                  text='Historical Runtime 3 ABBA host evidence remains inconclusive_drift and is not a Runtime 7 denominator.',
                  ref=history_ref, path=path, value='inconclusive_drift',
                  history={'records': row['history']['records']})

    code = {'strategy_id': sid,
        'stages': ['The contiguous BF16 public call prepares padded Q/new-KV/lengths, appends KV, computes attention and returns an independent full cache output. The delayed store wait remains in the selected B parent.'],
        'loops': ['Attention runs over every capacity/block tile and preserves the complete cache tail; static batch is part of the compiled shape.'],
        'movement': ['The query candidate moves only Q padding into attention. The batch-one fusion candidate constructs padded Q/new row/lengths in attention while retaining full cache traversal and output.'],
        'dependencies': ['Validity masks feed the unchanged online max/alpha/total/acc recurrence; store.wait ordering and tile/semaphore reuse remain correctness constraints.'],
        'layout_memory': ['Raw query width is 576 for the small B1 case and 192 for boundary B3; padded widths are 640 and 256. Logical interface byte counts are estimates, not VMEM/DMA measurements.'],
        'synchronization': ['Both candidates retain load waits and the delayed store.wait before body return. B1 fusion removes a separate prepare call only for static batch==1; missing prepare is not zero duration.'],
        'edge_cases': ['Run unchanged small/boundary/default BF16 correctness, opaque NaN tails, lengths [0,1,129], full cache exact bytes and input cache preservation. LSE is not exposed.'],
        'refs': refs}

    validation = {'correctness': [
        'Run unchanged small/boundary/default cases against the fixed CPU Oracle with BF16 shape/dtype/finite checks, atol=rtol=0.01, exact full cache bytes including opaque NaN tails and input cache preservation.',
        'Keep the exact candidate source/config/build/bundle and Runtime 7 environment; no Oracle, runner, workload or tolerance changes. A failure remains a real failed/incorrect result.',
    ], 'performance': [
        'Use the selected Runtime 7 B delayed-store-wait parent and original five stages: five warmups, twenty all-output synchronized host samples, no outlier removal, separate Profile/XPlane and original parser.',
        'Compare each candidate only to this current-context parent. Report host and trace-derived program/prepare/attention separately; a fused B1 path may have no separate prepare event, which is absent rather than measured zero.',
        'The bounded B2 grant raises cumulative limits to 18 executions / 32400 declared seconds while preserving prior spent 15 / 27000. This is a finite two-Run batch; no retry, repair, refund or winner is preselected.',
        'B1 fusion is exercised by the formal small B=1 case only. Boundary B=3 (including lengths [0,1,129]) and default B=8 use the parent fallback; the earlier independent single-tile diagnostic does not cover B1 fusion zero-length or B1 multi-tile behavior.',
    ]}

    plans = [
        {'id': 'R2-B-query', 'strategy_id': sid, 'parent_id': row['parent_id'],
         'mechanism': 'Construct query padding in the attention consumer',
         'modification': 'Remove q input and qp output from prepare; pass raw q with BlockSpec (1, heads, dim), then concatenate the existing zero padding inside attention. Keep new-KV/length preparation, masks, full cache output and delayed store wait unchanged.',
         'refs': refs, 'observation_ids': [o['id'] for o in observations if o['strategy_id'] == sid],
         'calculation_ids': ['R2-B-query-prepared', 'R2-B-query-raw'],
         'tradeoffs': ['Prepare has one fewer output, but padding moves into attention and may alter layouts or register lifetime.'],
         'risks': ['Raw D576/D192 BlockSpec and in-attention concatenation may fail lowering or increase attention/program/host cost.'],
         'unknowns': ['Logical interface reduction does not establish physical transfer or VMEM savings; only the fixed TPU protocol can resolve it.'],
         'variants': ['query-in-attention'], 'validation': validation},
        {'id': 'R2-B-b1-fusion', 'strategy_id': sid, 'parent_id': row['parent_id'],
         'mechanism': 'Fuse the necessary preparation into attention for static batch one',
         'modification': 'For static batch==1 only, skip the separate prepare call and pass raw q/new/lengths through VMEM inputs; pad and broadcast them inside attention. Keep the parent prepare and path byte-equivalent for B>1.',
         'refs': refs, 'observation_ids': [o['id'] for o in observations if o['strategy_id'] == sid],
         'calculation_ids': ['R2-B-prepare-interface', 'R2-B-raw-interface'],
         'tradeoffs': ['B1 may remove an interface call while moving work and live temporaries into attention; the logical raw payload is not a physical allocation measurement.'],
         'risks': ['The fused B1 layout may fail lowering or regress attention/host time; boundary/default fallback must remain unchanged.'],
         'unknowns': ['No independent B1 zero-length, opaque-NaN or B1 multi-tile evidence exists; the original single-tile diagnostic is a different source and workload.'],
         'variants': ['b1-fused-prepare'], 'validation': validation},
    ]
    proposal = {'code_understanding': [code], 'observations': observations,
        'hypotheses': [{'id': 'B2-interface-vs-schedule', 'strategy_id': sid,
            'text': 'Moving or fusing preparation may reduce interface work, but the same operations can add attention layout and scheduling cost; host drift can hide a program change.',
            'refs': refs,
            'alternatives': ['Compiler folding eliminates the apparent interface cost.',
                             'New layouts or live temporaries regress compile/program/host behavior.',
                             'A valid program improvement does not produce stable public-call improvement.']}],
        'plans': plans,
        'unknowns': ['The two B candidates are newly analyzed and have no hardware result yet; no winner is selected.',
                     'A2 and diagnostic feedback is used only through references actually present in this new packet/history; old Runtime timings are not relabeled as Runtime 7 denominators.',
                     'Only contiguous Decode BF16 is in scope. Forward, paged, other dtypes/window/cap and broader performance remain unvalidated.',
                     'A single Run per candidate is exploratory and cannot establish stable end-to-end speedup.']}
    reviewed = review_proposal(proposal, manifest, packet.get('calculations', {}), evidence_review=True)
    require(not reviewed['review'], 'B2 draft contains unsupported factual claims')
    response = {'final': proposal}
    require(len(canonical_json(response)) <= packet['remaining']['response_chars'], 'B2 draft exceeds response limit')
    metadata = {'status': 'draft_only_not_submitted', 'manifest_digest': packet['manifest_digest'],
                'plan_ids': B_PLAN_IDS, 'parent_id': row['parent_id'],
                'source_digest': row['parent']['identity']['source_digest'],
                'calculation_ids': calc_ids, 'review': reviewed['review'],
                'store_writes': 0, 'network_calls': 0}
    return response, metadata


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--target', type=Path, required=True)
    ap.add_argument('--reads-packet', type=Path, required=True)
    ap.add_argument('--output', type=Path)
    args = ap.parse_args()
    response, metadata = build_draft(args.target, args.reads_packet)
    output = args.output or (args.target / 'response-final.draft.json')
    write_once(output, response)
    write_once(output.with_name(output.stem + '-metadata.json'), metadata)
    print(canonical_json({'draft': str(output), **metadata}))
