# Runtime12 Bounded Recovery Implementation Plan

**Goal:** Recover the original cache-negative correctness operation, if reliable current evidence permits, with at most one additional backend submission and durable phase diagnostics.

**Architecture:** Add an artifact-only policy and fixed attempt slot around the original backend. Reuse the reviewed inspector, original Task lock, immutable request and prerequisite reservation. Preserve both historical attempts and all production execution guards.

**Tech Stack:** Existing Python, unittest, ExperimentStore, ColabBackend and local fsync/exclusive files; no dependency changes.

**Spec:** [.artifacts design review](../../../.artifacts/next-stage-completion-2026-11-16/runtime12-reconciliation-review/design-review.md).

The user has already requested continued implementation and CPU verification before TPU execution. Execute within that authorization, with independent implementation review before the live attempt; no additional plan confirmation or commit is needed. Native collaboration tools provide author/reviewer isolation.

## Constraints

- Only operation `operation:f06485ead5d942735f4fdf2d4b6722d14a5c6ec980e9651ba2f9a027c49c7009` in the original Runtime12, Store, Task and Run.
- Two historical admissions consumed; total cap 3; only fixed slot 3 is available. Unknown, crash, partial marker and pre-submit failure consume the slot once created. No refund, extension, renamed policy or automatic retry.
- Canonical namespace derives from original Store absolute path plus Task/Run/operation. Reject symlinks, junctions and path aliases. No configurable output, cap, attempt, runtime or policy identity.
- Preserve original helper, marker, outcome, intent, journal, reservations, source, Oracle and timing contracts. No new Run, grant or Controller retry.
- Policy expires 600 seconds after exclusive fsync publication; at most 3 preparatory read-only inspections. Never renew expiry. Freeze only after offline review.
- Fresh strong inspection inside original Task lock and receipt recheck are mandatory. Trace start must be durable before every external call; sanitize output and preserve original results/exceptions if posttrace fails.

## Files and interfaces

All implementation paths below are in `.artifacts/next-stage-completion-2026-11-16/`:

- Create `bounded_runtime12_recovery.py`: `freeze_policy()`, `inspect_policy()`, `attempt(evidence_path)`; fixed production bindings and append-only policy/slot/trace.
- Create `test_bounded_runtime12_recovery.py`: offline behavioral tests using temporary artifacts/Store and bridge doubles, never the live Store.
- Create `runtime12-bounded-recovery-author/`: exact commands, red/green logs, process exits and source hashes.
- Create `runtime12-bounded-recovery-review/`: independent findings, additional tests and exact accepted hashes.
- Update checkpoint, current next-stage report and revision-safe knowledge notes with observed outcomes only.

## Task 1: Policy and single-slot admission

- [ ] Write and observe failing tests for historical attempt import, canonical namespace, conflicting/partial policy, immutable expiry, fixed slot, partial/fsync failures, two-process contention, copied helper and extra sibling attempts.
- [ ] Implement exclusive fsync policy and fixed slot with original Task locking. Validate original historical artifacts and full request/intent/config/worker/inspector/journal/reservation bindings.
- [ ] Verify stale/future evidence, lock-delay expiry, unreadable processes, changed identities and newly appearing receipt all prevent submit.
- [ ] Run the complete new offline module with native process exit captured. No production or historical helper edits.

## Task 2: Trace and exact backend delegation

- [ ] Write and observe failing tests for one backend invocation, start-before-call ordering, each lookup/preparation/put/control/import failure, expiry between calls and posttrace failures preserving underlying result/exception.
- [ ] Add a recording/delegating adapter around original backend/remote calls; no copied worker protocol or direct remote submission. Include payload digest/size, code hashes, statuses, elapsed time and call counts, not raw error text or authenticated URLs.
- [ ] Verify journal/reservation before and after, old markers unchanged, no automatic retry and no new slot after restart.
- [ ] Run new module plus inherited recovery/inspector focused tests. Existing 513/383 full CPU regressions remain applicable only while all 262 bound source files match.

## Task 3: Independent review, live execution and continuation

- [ ] Independent reviewer reads implementation/spec, runs meaningful additional offline tests, closes Critical/Important findings and binds exact accepted files.
- [ ] Sole remote/Store owner rechecks review hashes, current CPU source map, local owner processes and original allocation; freezes policy immediately before bounded inspections and conditional slot-3 attempt.
- [ ] On unknown or exhausted policy, preserve evidence and diagnose from trace without another submission. On running/completed, query/recover the same operation only.
- [ ] If original recovery succeeds, finish original four prerequisite gates, obtain independent raw admission audit, then continue fresh sequential A/B/D design and unified authorized execution under the existing handoff.
- [ ] Update report, checkpoint and current weekly completion record with confirmed partial or complete results. D remains 0/5 until actual eligible route feedback is saved.

## Review focus

Task 1 tests must reject Windows junction/alias ancestry, partial policy/slot after process crash, new sibling attempts and evidence becoming stale while waiting for the lock. Task 2 tests must preserve the original result or exception when post-call trace writing fails and stop any subsequent calls. No test may create a new live policy, touch the original Store or call the real bridge.
