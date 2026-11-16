# Independent retained hardware evidence audit

**2026-11-16: verified, 28/28 Runs, zero verification failures, process exit 0.**
Started 02:18:56.589744 UTC and finished 02:21:41.736881 UTC (165.147137 seconds).
No network, TPU execution, original Store/artifact changes, budget changes, or dependency installation occurred.

## Scope and verification

- 15 exploration/confirmation/diagnostic Runs: first round 5; Runtime 3 ABBA 4; Runtime 7 second round A2 2 and B2 2; independent single-tile diagnosis 2.
- 13 prerequisite Runs: Runtime 2/3/7 each supplied two baseline/parent Runs and two deliberately wrong Runs; Runtime 3 also supplied one independent confirmation control.
- SQLite was opened with `mode=ro`, `query_only=ON`, and a single `BEGIN` transaction pinned at Journal revision 247. Records were retrieved by identity without expanding the large Snapshot/history.
- The existing `verify_job` checked fixed contracts, all receipts, source/config/build identities, loaded module paths, Runtime/Context, exact parent Evaluation, Evaluator and promotion Decisions, and Evidence closure. **925 unique artifacts / 545,089,987 bytes** were freshly SHA256 checked.
- **76 correctness cases** were recomputed from retained candidate/oracle NPZ. Attention retained `atol=rtol=0.01`; complete BF16 cache used exact raw bits including opaque NaNs. This compares saved Oracle outputs; **the CPU Oracle was not executed again**.
- Cache finite/NaN/infinity counts were independently reconstructed using BF16 exponent/mantissa bit scans and compared with receipt metadata. There were 9 unique cache arrays after SHA-based deduplication. The scanner was checked on all 65,536 BF16 bit patterns (256 nonfinite, including 2 infinities).
- All 22 successful benchmark/Profile Runs had host and Profile samples recomputed for median, mean, population standard deviation, min/max and P25/P75. Parent comparisons use the original exact same-Context parent Evaluation.
- **22 retained XPlane files were independently protobuf decoded.** The audit process used existing cached protobuf 7.36.2 in pure-Python mode and an existing TensorFlow-generated XPlane schema, without importing TensorFlow or installing/upgrading anything. Program names were derived from operation IDs; TPU module/custom-call groups and all duration/offset/step/run sample fields were reconstructed and matched the saved parser results.

Full identities, module paths, receipt references, per-case NPZ references, raw samples, statistics, decoded XPlane samples and artifact digest inventory are retained in [raw-audit-results.json](raw-audit-results.json). Reproduction: [script](raw-audit.py), [log](raw-audit.log), [command and environment](raw-audit-environment.json).

## Candidate results

Reduction is `(parent-candidate)/parent`; speedup is `parent/candidate`. These are same-Context single-Run observations. Runtime 2 and Runtime 7 are not pooled or ranked against one another.

| Round / Candidate | Parent host ms | Host ms | Program us | Host reduction | Speedup | Original classification / interpretation |
|---|---:|---:|---:|---:|---:|---|
| R1 / tile512 | 0.193195 | 0.225485 | 8.090000 | -16.71368% | 0.856798 | correct_slower / slower |
| R1 / tile256 | 0.193195 | 0.202570 | 11.127500 | -4.85261% | 0.953720 | correct_slower / slower |
| R1 / single-tile | 0.193195 | 0.193550 | 6.580625 | -0.18375% | 0.998166 | correct_slower / slower |
| R1 / delayed-store-wait | 0.215365 | 0.183795 | 6.190000 | 14.65883% | 1.171767 | correct_faster / single_run_faster |
| R1 / compact-new-row | 0.215365 | 0.221875 | 6.588750 | -3.02278% | 0.970659 | correct_slower / slower |
| R2 / length-row | 0.211950 | 0.204825 | 6.626250 | 3.36164% | 1.034786 | correct_faster / single_run_faster |
| R2 / length-smem | 0.211950 | 0.216060 | 6.504375 | -1.93914% | 0.980978 | correct_slower / slower |
| R2 / query-in-attention | 0.223225 | 0.236630 | 6.831250 | -6.00515% | 0.943350 | correct_slower / slower |
| R2 / b1-fused-prepare | 0.223225 | 0.213700 | 5.890000 | 4.26699% | 1.044572 | correct_faster / single_run_faster |

Every optimization candidate has only one independent Run in its original round. The B2 `b1-fused-prepare` trace has no separate prepare event; **absence is not a measured zero**. Its attention median is 5.088125 us. `query-in-attention` prepare/attention medians are 0.675625 / 5.333125 us.

Original completion promotion flags (promotion does not establish stable speedup):

| Round / Candidate | eligible | branch_promoted | task_promoted |
|---|---|---|---|
| R1 / tile512 | true | false | false |
| R1 / tile256 | true | false | false |
| R1 / single-tile | true | false | false |
| R1 / delayed-store-wait | true | true | true |
| R1 / compact-new-row | true | false | false |
| R2 / length-row | true | true | true |
| R2 / length-smem | true | false | false |
| R2 / query-in-attention | true | false | false |
| R2 / b1-fused-prepare | true | true | false |

## ABBA

Benchmark lease start/end values confirm C-T-T-C execution order. Source/config/build identities agree within each role; Context, Runtime, benchmark version and input digest agree across all four Runs. Recomputed frozen `p35-abba-v1` results:

| Dimension | Pair 1 reduction | Pair 2 reduction | Control spread | Treatment spread | Classification |
|---|---:|---:|---:|---:|---|
| Host | -0.83071% | -11.84232% | 8.78998% | 19.10195% | inconclusive_drift |
| Program | 6.49584% | 5.92180% | 0.11340% | 0.72544% | replicated_observed_improvement |

Joint outcome is **inconclusive**. Limited program replication cannot establish stable host public-call improvement. Each role has two independent Runs; 20 within-Run samples do not become 40 independent experiments per role.

## Correctness, negative controls and diagnosis

- All frozen cases from the 22 correct Runs passed. All 6 wrong Runs (attention/cache for Runtime 2/3/7) were `incorrect`: generate/compile succeeded, correctness failed, no benchmark/Profile followed, and promotion had `eligible=false`, `branch_promoted=false`, `task_promoted=false`.
- The formal matrix retained lengths `[1024]`, `[0,1,129]`, and eight `[16384]` values. Independent diagnosis retained `[0]` and `[0,1,129]`, with capacity equal to block in both cases and a separate diagnostic Context.
- Formal boundary cache contains 171,648 NaN BF16 words and zero infinities. The independently scanned `finite=false` agrees with the receipt and complete opaque-byte equality passes. These opaque regions were not treated as invalid attention values.
- The original single-tile diagnostic validates the original single-tile source. It **does not establish zero-length or multi-tile coverage of the new B1-fusion branch**; those B==1 inputs are outside its frozen three-case formal matrix.

## Limitations

1. **Device input-cache immutability: not independently verifiable from retained artifacts.** Input/output NPZ are retained, but the post-device input cache is not. The original runner's live check remains the evidence; receipt JSON or output cache equality cannot substitute for independent input immutability verification.
2. Raw XPlane re-decoding is now complete for these 22 captures. This checks retained event content and statistics, not absolute physical device exclusivity or honesty of the original remote capture. Independent DMA/VMEM/MXU/full Wait/Sync/critical-path metrics remain unavailable.
3. Bundle bytes and loaded module paths establish identity, not execution of every bundled file. Receipts retain the existing trusted runner/worker/Runtime boundary.
4. This raw audit covers the stated 28 Runs. It does not recompare every historical lost/unknown Runtime Run; recovery semantics and historical status are covered by the separate Store/Journal audit.
5. Coverage does not extend to LSE, Forward, paged, other dtype/shape/window/cap combinations. Formal performance coverage remains decode-small.
6. **Stable end-to-end performance improvement: not confirmed.** Single-Run improvements, Best promotions and limited program replication do not prove general stable end-to-end speedup.
