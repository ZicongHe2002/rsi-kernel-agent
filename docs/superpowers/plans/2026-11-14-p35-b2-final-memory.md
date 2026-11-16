# P3.5 B2 Final Memory Implementation Plan

> Execution is already authorized by the parent task. Implement natively with TDD, then obtain an independent review; do not pause for another approval.

**Goal:** Reconcile the actual Runtime 7 B2 completion Snapshot, all Task Memory projections, six new result bindings, and cumulative grant budget without mutating Store or running hardware.

**Architecture:** Add one narrow read-only helper beside existing Phase 3 auxiliaries. Reuse `TransactionStore`, `MilestoneStore`, original `WorkflowJournal`, `check_scope`, `verify_projection_bindings` and `bind_verification`; retain the old A2/diagnostic final Memory report as immutable prior evidence. The new helper consumes the actual B2 completion and saved original continuation verifier report, and does not repeat large Analyzer/NPZ replay.

**Tech Stack:** Python standard library, existing RSI Store/Memory/Workflow APIs, unittest temporary real-protocol fixtures.

**Spec:** Parent task `/root`: only `real-runtime7-b2-continuation-round:2`, original grant from 16/28800 to 18/32400, expected completion 17/30600 derived from real reservations; genuine compile/correctness failures remain feedback, unexecuted/cancelled attempts do not complete original scope.

## Constraints and review focus

- Do not change original final Memory helper, continuation driver/verifier, or core modules; no real Store/remote/report writes during implementation.
- Pin B2 completion Snapshot, exact spec/grant/intake/publication/variants and original accepted pair. Read grant history through `MilestoneStore`, not the old adapter lacking grant APIs.
- Verify all prior records/jobs remain unchanged, all Task Context projections and participating A/B histories, and six execution rows through original receipt binding.
- Derive actual cumulative spending from reservations and applied original grant; reject refunds, extra attempts and changed grant identity.
- Require real compile feedback for both B labels before setting scope complete. Genuine compile/correctness failure is valid evidence; administrative cancellation or generation-only result is not.
- Separate current live watermark from frozen final Snapshot. A later valid continuation must not relabel the completed batch's budget/Best.

## Task 1: New narrow helper and original-chain fixture

Create `.artifacts/phase3-2026-11-12/verify_p35_b2_final_memory.py` with `verify_b2_final_memory(directory, spec, verification_report)` and a CLI saving only its new local report. Create `test_verify_p35_b2_final_memory.py` using temporary original real protocol receipts and a genuine budget grant chain; fake only external backend and unrelated historical admission setup.

1. Write a test that the completed original B2 pair returns `verified`, exact six evidence bindings and original scope complete, derived 17/18 spending, preserved earlier Contexts and no Store writes. Run RED before implementation.
2. Implement the minimum Snapshot/grant/intake/source-report binding and reuse original Memory projection/receipt functions.
3. Add regressions for genuine compile/correctness failure, cancellation/generation-only feedback, changed completion/spec/intake/report/receipt, missing prior run report, refund/extra job, and later current watermark separation. Observe each failure before adding the corresponding guard where not already covered.
4. Run the focused suite and relevant existing Memory/continuation compatibility tests. Record exact counts/exit statuses; do not claim hardware or performance validation.
5. Independent review by `/root/round2_candidate_review`; resolve substantive findings with regressions and publish a compact handoff with SHA256 and commands. No commit or GitHub push in this subtask.
