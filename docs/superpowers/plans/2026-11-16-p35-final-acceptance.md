# P3.5 Final Acceptance & Evidence Audit Plan

**Goal:** Give an evidence-backed system acceptance verdict separately from performance confidence for the completed two-round MLA Decode workflow.

**Architecture:** Read the original SQLite Store in read-only transactions, recheck immutable artifacts and saved samples, and run current local tests. Save audit outputs separately from historical experiments; make no new hardware requests or Store writes.

**Tech Stack:** Existing Python 3.13.14, unittest, SQLite, original RSI contracts and verifiers, retained NPZ/XPlane and Analyzer artifacts.

**Spec:** User attachment `C:/Users/fengluo/.codex/attachments/a59cb336-08eb-4691-91e2-dad1cf4d96e3/Pasted text.txt`.

## Constraints

- No new TPU allocation, Candidate, Run, budget grant, experiment replay, dependency upgrade, or changes to Kernel/Oracle/benchmark/history.
- Correctness: contiguous BF16 small/boundary/default; attention atol=rtol=0.01, full cache bytes. Benchmark: decode-small, 5 warmups, 20 samples, no outlier removal.
- Preserve failures, cancellations, old Runtime Contexts and reserved cost.
- Independently unverifiable retained evidence is an explicit limitation, never fabricated verification.

## Execution

- [x] Read request, AGENTS, README, current Store and Journal before edits. Observed task records 1505/HW1530, Snapshot26, revision247, spent17/18.
- [x] Audit all current record digests, Snapshot26 integrity and frozen/live projection consistency; inspect all Context Best/Frontier/Parent/paused/deferred and reservation ledger. Verified 1505 records, 2925 typed links, 247 Journal versions and six complete Snapshot byte/reconstruction digests.
- [x] Audit first-round 5, ABBA 4, second-round 4, diagnosis 2 and relevant baseline/negative Runs: identity, receipts, artifact hashes, raw NPZ, host/Profile statistics and fixed contracts. Verified 28 Runs, 925 raw artifacts and 76 correctness cases.
- [x] Reconstruct original Analyzer reads and publication for A2/B2, with first-round Run → observation → hypothesis → Plan → source change → real feedback mappings. Four sessions audited including original interrupted analysis-only R2; current sessions each retain three responses and no preselected winner.
- [x] Recheck raw XPlane decoding and cache metadata where retained bytes and installed libraries permit; mark missing post-device input state explicitly. Independently decoded 22 XPlanes and scanned BF16 finite metadata; post-device input cache remains unavailable.
- [x] Run complete root unittest discovery and complete P3.5 auxiliary tests using existing interpreter, preserving stdout/stderr and exact summaries. First auxiliary failure preserved; six test-fixture files minimally repaired without changing assertions or production code. Post-fix root: 343 total, 340 passed, 3 skipped, 568.923 seconds; auxiliary: 383 executions / 376 distinct IDs, all passed, 2501.671 seconds. Both exit 0.
- [x] Perform independent evidence and report review, resolve findings, and verify no Store changes. Final reviewer approved the declared scope with no open Critical/Important/Minor; system PASS_WITH_LIMITATIONS, stable end-to-end improvement NOT_CONFIRMED. Fresh delivery read confirms Snapshot26 / 1505 / HW1530 and Journal247 / spent17 unchanged.
- [x] Deliver `docs/phase3/phase3-final-acceptance-report.md`, update README and historical report entry points, and append confirmed results to the 2026-11-16 weekly note through the local API. Revision guard and readback succeeded; unrelated checkboxes unchanged. Delivery verification records 72 valid links, tested source/log hashes, final document identities and unchanged original Store state. No commit/push or next optimization was started.

## Review focus

Frozen Snapshot vs live Store; archived operation intent vs active execution; trace duration vs host public-call timing; independent Run vs within-Run sample; single-run promotion vs stable performance; original Analyzer reasoning vs retrospective attribution.

The user explicitly requested immediate execution, so this plan is executed within that authorization without a separate approval step. No commit/push or new optimization is part of this acceptance task.
