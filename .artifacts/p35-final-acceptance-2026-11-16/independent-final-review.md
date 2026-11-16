# P3.5 independent final evidence and report review

Reviewer: `/root/accept_final_review`, 2026-11-16, independent task with the original user specification and no inherited implementation conversation. Base/HEAD supplied for this review: `76f27a41c48a3a8b4756860d16c25773f25ea45f`. Review covered the current uncommitted acceptance artifacts and report; pre-existing unrelated modifications are not defects and were not reverted.

**Final independent review: APPROVED within the declared scope; no open Critical, Important or Minor findings.** The retained hardware/Memory/traceability evidence, freshly verified post-fix full regressions, and final document support **P3.5 System Workflow: PASS_WITH_LIMITATIONS** and **Stable End-to-End Improvement: NOT_CONFIRMED**. Root: **343 executed / 340 passed / 3 skipped**, 568.923 seconds, exit 0. Auxiliary: **383 executed entries / 376 distinct IDs / 383 passed**, 2501.671 seconds, exit 0. Both have **0 failures / 0 errors**. This reviewer independently counted the actual verbose logs, matched all four log hashes, and rehashed each current 243-file source inventory with **0 changes**. The final report has been reread and signed off at SHA256 **`2c9ac93134154d5d039b02e1dcabefd3ec9d4e993e412629f0df4b5f69c20bf3`**.

The earlier failed auxiliary run remains part of the review record: at **2026-11-16 10:54:45 Asia/Shanghai**, it ended **383 tests / 10 failure entries / 23 error entries / exit 1**, 2493.076 unittest seconds. That failure initially withheld acceptance and led to the two minimal fixture repairs documented below; it was not discarded or relabeled as passing.

The original wrapper's `passed=350` is not accepted as a method count: its `total - failures - errors` arithmetic counts repeated failing subtests as different failed tests. The untouched original failure log/result are preserved in `regression-attempt-1/`. Independently reparsing its verbose outcomes gives **353 passed methods + 7 failed methods + 23 error methods = 383**; four failing subtests belong to one method, explaining ten failure entries. The corrected accounting is saved separately in `first-run-corrected-accounting.json`. This reviewer checked all four archived root/auxiliary stdout/stderr SHA256 values against the original result metadata; all match.

## Findings

| Severity | Location at reviewed draft | Finding / requested action | Status |
| --- | --- | --- | --- |
| Critical | Reviewed scope below | None found | — |
| Important | `regression-attempt-1/auxiliary-tests.stderr.txt`, final summary and error/failure sections; original result JSON | First full auxiliary regression failed. Confirmation fixture setup omitted spent; uninitialized nested async fixture cleanup leaked a recovery mock. Both root causes were reproduced, fixed without weakening assertions/production guards, and reviewed. | Resolved: targeted evidence plus fresh complete root/auxiliary suites pass; all current source hashes match the tested versions |
| Minor | `docs/phase3/phase3-final-acceptance-report.md`, Final Acceptance Decision / Multi-Round RSI Workflow, initially line 254 | “独立源码” can imply that every Candidate has different source bytes. R1 tile512/tile256 intentionally share source and differ in configuration. Prefer “独立 Candidate 及 source/config 身份”. Reproduce by comparing these two rows' `declared_identity.source_digest` and `config_digest` in `raw-audit-results.json`. | Resolved and reread: final table now uses “独立 Candidate 及 source/config 身份” |
| Minor | Same report, Evidence Inventory / Analyzer row, initially line 46 | Add a direct link to `traceability-review.md` next to the machine JSON. The human report supplies the Plan/source-line/Knowledge details referenced later. Existing JSON link is valid; this is navigation, not missing evidence. | Resolved and reread: row now links both 溯源报告 and 机器事实 |

The software completion gate is closed by the fresh post-fix full suites. Their actual results supersede the initial software checkpoint while preserving the failed run. Auxiliary discovery's seven repeated entries are disclosed rather than described as seven additional distinct test IDs.

Historical draft hash after checking both editorial resolutions: `0127392834cbf145e25424c31e0b8e45a3ba8d67ca4f6dd7c2e3969f6e6a106e`. It is superseded by the signed final-report hash above.

## Final document sign-off

Reread the complete finalized `docs/phase3/phase3-final-acceptance-report.md`, including the fresh Software Tests, Performance Confidence, Independent Review, Limitations and Final Acceptance Decision sections. Its numerical tables match the reviewed artifacts; the seven performance answers preserve the distinction between single-run improvement, limited program replication, host drift and stable end-to-end benefit. The original failed regression is disclosed with corrected counts, minimal repairs and final complete reruns. The final verdict does not conceal Runtime 4 unknown, retained input-cache limitations, narrower fusion branch coverage, trust assumptions, workload limits, or the 28-Run numerical-audit scope.

Reread `README.md` and the new final-status banners in `knowledge-site/manifests/phase3-2026-11-12/implementation-report.md`, `docs/phase3/phase3-p35-progress-report-2026-11-14.md` and `docs/phase3/phase3-p35-final-validation.md`. Current status agrees with the final report and old checkpoints remain explicitly historical. Independently resolved **30 final-report links, 6 README links, 107 implementation-report links, 38 progress-report links and 33 final-validation links**, with no missing local target. Reviewed `verify_delivery.py` and its read-only verification result; it checks test hashes/source identity and unchanged Store/Snapshot/Journal state without modifying original records. The main agent will refresh delivery hashes after this review document and the separately authorized weekly note update.

The final report's SHA256 above was unchanged across the reread and link verification. This approval is an evidence-based review of the stated acceptance scope; it does not certify every possible defect, physical hardware attestation, or any broader performance claim.

## Regression repair review and complete reruns

- `test_p35_round2_gate.py:105`: inspected the fixture-only fix. Four fixture jobs are already reserved/completed, but their initial Journal state had `limits` without `spent`. Adding spent as the sum of the reserved jobs' actual `variant.cost` is consistent with the existing guard and real ledger (4 executions / 4 declared seconds / 0 tokens). The production budget guard remains unchanged; no correctness, rejection, promotion or tamper assertion is removed. The pre-fix focused run reproduced the same `Workflow limits require spending fields` error (one test, exit 1). Independently read `budget-fixture-after.log` and `budget-fixture-verification.json`: both complete affected modules pass **29/29 tests, 110.751 seconds, exit 0**. This minimal repair and its complete regression results are accepted.
- `run_regressions.py`: reviewed audit-wrapper-only correction from subtracting failure entries to counting verbose successful `ok` outcomes. Independently confirmed the corrected first-run count above. This parser is appropriate to the fixed `unittest -v` command and current output; it is not claimed to support arbitrary custom unittest descriptions. Original wrapper/results/logs are preserved separately. A final green-run assertion now requires passed + skipped + expected-failure counts to equal the unittest total.
- Independently compared all five files under `traceability-cleanup-before/` with current files. The only edits are six instances of assigning child `addCleanup` to the active parent's method **before** child `asyncSetUp`, replacing registration of the uninitialized child's `doCleanups`. No assertion or tested recovery branch changed. All five current hashes match `traceability-cleanup-green.json`. The repaired paths are `test_p35_diagnostic_dispatch.py:443`, `test_resume_p35_diagnostic.py:25`, `test_resume_p35_diagnostic_independent.py:15` and `:39`, `test_verify_p35_continuation.py:20`, `test_verify_p35_continuation_independent.py:13`.
- The cleanup diagnosis is supported by execution-order reproduction, not just absent mock calls. The nested `IsolatedAsyncioTestCase` had no initialized runner: its private cleanup failed while the external owner continued, leaving the module-level recovery mock installed. The identical 17-test ordering changes from **10 failure entries / 2 errors** to **17 passed / 0 failures / 0 errors**, and the saved harness explicitly observes **no unsuccessful cleanup returns** and **the original `require_recovery_context` object restored**. Additional nested diagnostic/continuation consumers pass **3/3**, 67.891 seconds, with the same cleanup/identity checks. Reviewer read the harness, original/current fixture code, JSON and actual log endings. Delegating registration before setup is sound for partial setup failures and multiple nesting levels. This minimal test-lifecycle repair is accepted.
- Independently hashed all files listed in the original 243-file source inventory. Exactly the expected **six auxiliary fixture files** differ; no production `src` or root test file differs from the first full run. The audit wrapper is separately documented above. No hardware, Store, Oracle, benchmark or historical experiment content was changed by these repairs.
- New full root discovery ran **2026-11-16 11:22:05–11:31:35 Asia/Shanghai**: **343 executed / 340 passed / 3 skipped / 0 failures / 0 errors**, 568.923 unittest seconds, exit 0. Auxiliary discovery ran **11:22:28–12:04:10**: **383 executed / 383 passed / 0 skipped / 0 failures / 0 errors**, 2501.671 unittest seconds, exit 0. Their correct complete commands use the same existing Python 3.13.14. Reviewer independently reparsed actual log outcomes and IDs: root 343 distinct IDs, auxiliary 376 distinct IDs (seven repeated executions); all counts reconcile.
- Reviewer independently recomputed all four final stdout/stderr SHA256 values and matched the final result JSON. Final stderr SHA256: root **`dc3f1f0c0f59a5bf560ff30bdad0b08082d2c8bbbf3a21ba0bc185b5fd5b5590`**, auxiliary **`e558b40f674c200eba4474304ecf4dbf1e7c308e80446bdfbcb83494ff8ce0e8`**. Both wrappers record `source_changed=[]`; reviewer separately rehashed all **243 current files against each suite's captured source identities**, with **0 mismatches**. No duplicate full-suite or hardware run was launched by the reviewer.

## What was independently checked

1. Read the original acceptance request, execution plan, all of `raw-audit.py`, `memory-audit.py`, `memory-audit-recovery.py`, `traceability-audit.py`, relevant original `verify_real_round.py`, receipt/parent identity validators, audit JSON and human reports, and the complete final report draft. Reviewed the new methods rather than accepting their success fields alone.
2. Raw audit scope is precisely **28 Runs, 76 correctness cases, 22 XPlanes**. Its artifact closure checks use SHA256/size and the existing fixed contract verifier, while retained NPZ attention/cache, finite metadata, host statistics and XPlane decoding are recomputed. Reuse of fixed contract validators is disclosed; CPU Oracle execution is not repeated. It does not claim fresh numerical verification of all 52 historical Runs.
3. Independently parsed original NPZ containers using `zipfile`, NPY headers, `struct` and BF16 bits, without calling the audit comparison function. For Runtime 7 `length-row`, `decode-boundary`: **0 attention violations, 0 mismatching cache words, 171648 nonfinite BF16 cache words, 0 infinities**. Runtime 7 attention negative control has **1 attention violation / 0 cache mismatches**; cache negative control has **0 attention violations / 1 cache-word mismatch**. All arrays' internal SHA256 values and outer artifact size/hash were checked. Attention tolerance follows the frozen Oracle's argument order: `abs(actual-reference) <= .01 + .01*abs(actual)`.
4. Independently read and hashed benchmark receipts and recomputed medians for `length-row`, `b1-fused-prepare` and all four ABBA Runs. Each has 5 warmups and 20 raw samples. Medians respectively: **0.20482499940044363**, **0.21370000467868522**, **C1 0.22209999997357954**, **T1 0.22394500047084875**, **T2 0.2712400000746129**, **C2 0.24252000002888963** ms. These agree with report rounding. ABBA pairs have negative host reductions; program replication cannot establish host improvement.
5. Independently loaded the retained generated protobuf schema and selected raw `length-row` TPU `XLA Modules` events by its operation-derived program name, without calling the audit XPlane helper or original duration parser. Found **20 events, median 6.62625 μs, no XSpace errors/warnings**. This agrees with the saved parser and fresh audit. Full audit method also compares duration, offset, step and run lineage for all 22 selected captures.
6. Independently opened original SQLite with `mode=ro`, `query_only=ON`, and a read transaction. Stream-hashed **all 689053158 bytes** of Snapshot 26, then separately reconstructed it from the frozen metadata/projections plus all **1505** original record payloads/seq values at high-watermark **1530**. Both yield **`25c8c6475b595fbd471b15077625a36e1e3a7dbeec04415ac9253c6586cfd372`**. Each included record's SHA256 also matched. The full memory audit additionally reconstructs Snapshots 4/10/19/23/25, checks typed links, replays promotion/frontier, and validates 247 Journal transitions/reservations. Its compact in-memory view is used only after full bytes are checked, and is not written back.
7. Independently enumerated original non-journal Decisions: **170 durable requests / 169 receipts**, one unmatched operation: **`operation:079d2d129595c886b74f35bdc44630a39e7ab03cc9817e67719659c733f64c3a`**, stage correctness, Run **`runtime-run:c9e383063a3da8b8500b2a36596049ea28b0920595d66d4c229133f5626699a1`**. This is the same Runtime 4 historical unknown explicitly retained by the recovery audit and report. Local terminal jobs and archived nonempty intents are not used to infer current remote success or cancellation.
8. Independently read/hash-checked the original A2/B2 manifest artifacts and `session.json`/`result.json`. Recorded `responses == exchanges`, final response equals original proposal, and first responses actually request the `history` tool. A2 has original `A-R1-tile512`, `A-R1-tile256`, `A-R1-single-tile`, `A-changed-direction` observations linked to both A Plans. B2 actually reads its 718-record history and new A2/diagnostic feedback. Earliest explicit B reasons are correctly attributed to original interrupted Runtime 3 analysis; the report does not pretend B2 invented them again. Original four source identities are reused with newly bound Runtime 7 admissions, which is recovery of the second round rather than extra optimization rounds.
9. Across all 28 audited Runs, Oracle digest is one unchanged value (`e53b0e5ccff3ecf18192f30ef179cdb207c177f2f2dac48b2a08f40acc5000a6`), runner digest is one unchanged value (`f3de4fa2e1c9ddadd15bbb12f2617a3957dfa74c3119777a0cc12478ff9a72f2`), and frozen benchmark/correctness dictionaries are identical. The latter remain `mla-contiguous-host-sync-v1`, warmup 5, measurements 20, no outlier removal, attention atol/rtol .01.
10. Read the initial root suite (**343 tests, 540.690 seconds, OK with 3 skipped**) and, following fixture repair, both fresh complete suites recorded above. Final root is **343 / 340 passed / 3 skipped, 568.923 seconds**; final auxiliary is **383 executed / 383 passed, 2501.671 seconds**, both exit 0. No completion is inferred from partial output. Initial failed logs, final complete logs, corrected subtest accounting and duplicated-ID accounting remain separate and reviewable.
11. Resolved every relative file link in the reviewed final report and three supporting human reports: **19 / 11 / 6 / 4 links**, respectively, with **0 missing targets** at that checkpoint. Report edits may subsequently add citations.

## Reproducible entry points

Use the existing interpreter, without dependency changes, from the repository root:

```powershell
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B -X utf8 '.artifacts/p35-final-acceptance-2026-11-16/raw-audit.py'
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B -X utf8 '.artifacts/p35-final-acceptance-2026-11-16/memory-audit.py'
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B -X utf8 '.artifacts/p35-final-acceptance-2026-11-16/memory-audit-recovery.py'
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B -X utf8 '.artifacts/p35-final-acceptance-2026-11-16/traceability-audit.py'
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B -X utf8 '.artifacts/p35-final-acceptance-2026-11-16/traceability-audit.py' --original-analysis-only
& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B -X utf8 '.artifacts/p35-final-acceptance-2026-11-16/traceability-source-supplement.py'
```

These existing scripts write only separate acceptance audit outputs, not original Store/history. This reviewer did not rerun these full audits or modify their outputs: reviewer spot checks ran separate inline Python through the same interpreter. Its `uv` trampoline required narrow sandbox escalation after an initial permission denial; the approved read-only call succeeded. The initial launcher denial was not counted as a product/test failure.

To independently reproduce the full Snapshot 26 digest/reconstruction check without either audit module, run this Python under that interpreter:

```python
from pathlib import Path
import hashlib, json, sqlite3
p = Path('.artifacts/phase3-2026-11-12/experiments.sqlite3').resolve()
c = sqlite3.connect(p.as_uri() + '?mode=ro', uri=True)
c.execute('PRAGMA query_only=ON')
c.execute('BEGIN')
row = c.execute("SELECT rowid,digest FROM snapshots WHERE task_id='mla-decode-v1' AND version=26").fetchone()
h, size = hashlib.sha256(), 0
with c.blobopen('snapshots', 'body', row[0], readonly=True) as b:
    prefix = b.read(65536).decode('utf-8')
    b.seek(0)
    while chunk := b.read(8 * 1024 * 1024):
        h.update(chunk)
        size += len(chunk)
dec = json.JSONDecoder()
meta, at = dec.raw_decode(prefix, len('{"metadata":'))
_, at = dec.raw_decode(prefix, at + len(',"projections":'))
reconstructed = hashlib.sha256((prefix[:at] + ',"records":[').encode())
count = 0
for seq, kind, payload, digest in c.execute(
    "SELECT seq,kind,payload,digest FROM records WHERE task_id='mla-decode-v1' AND seq<=? ORDER BY seq",
    (meta['high_watermark'],)
):
    assert hashlib.sha256(payload.encode()).hexdigest() == digest
    if count:
        reconstructed.update(b',')
    reconstructed.update(('{"kind":' + json.dumps(kind) + ',"payload":' + payload + ',"seq":' + str(seq) + '}').encode())
    count += 1
reconstructed.update(b']}')
assert h.hexdigest() == row[1] == reconstructed.hexdigest()
assert count == meta['record_count'] == 1505
assert size == 689053158 and meta['high_watermark'] == 1530
print(size, count, h.hexdigest())
c.close()
```

For NPZ/trace spot checks, the exact original artifact references are the `decode-boundary` entries under the three selected `audit_name` values `real-runtime7-recovery-round:2/length-row`, `runtime7/oracle-negative-attention`, `runtime7/oracle-negative-cache` in `raw-audit-results.json`; each `digest` resolves to `.artifacts/phase3-2026-11-12/artifacts/sha256/<first-two>/<digest>`. Parse NPY raw uint16 BF16 storage, compare full cache words, and classify nonfinite with `(word & 0x7f80) == 0x7f80`. The length-row `raw_xplane_refs[0]` and `operation_ids.profile` identify the independently spot-decoded trace. The schema path/version/digest are fixed in `xplane_decoder`.

## Explicit limits and declined-to-judge items

- No live TPU/remote query, new Candidate/Run, budget change, source/Oracle/contract change, dependency install, or original Store mutation was performed by this review. Only this review document is written by the reviewer.
- No claim that all 52 historical Runs were freshly NPZ-verified. Historical lost/unknown operations receive structural/recovery review only, as stated.
- Retained outputs verify saved Oracle agreement, not an independently re-executed Oracle. Device post-execution input-cache state was not retained; input immutability remains **not independently verifiable from retained artifacts**.
- Trace bytes and source/receipt linkage are not independent hardware attestation, absolute device-exclusivity proof or a guarantee of distributed exactly-once. Original worker/runner/bridge/local artifact trust remains required.
- No assertion of independent DMA/waits/resource utilization, model causal effectiveness, random-search superiority, general kernel optimization capability, or broad workload coverage.
- B1 fusion small correctness cannot inherit the different single-tile source's zero-length/multi-tile/opaque-NaN diagnostic coverage. The final report explicitly preserves this limitation.
- Knowledge sources are individually pinned; no whole-corpus/Memory cross-service atomic snapshot is asserted.
- Both full software suites and the final report reread are complete and independently verified. No requested new hardware evidence is necessary to support the bounded system verdict; stable host performance requires a separately authorized experiment design and budget.
