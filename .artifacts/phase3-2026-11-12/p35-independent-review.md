# P3.5 independent local code review

2026-11-13, Asia/Shanghai. This review is limited to host-side code and temporary CPU/protocol fixtures. It did not call a remote service, write the live experiment Store, execute a TPU candidate, or establish any hardware performance result.

## Final findings

- **Critical:** none found in the reviewed scope.
- **Important:** no unresolved finding in the reviewed confirmation preparation, dispatch, publication and Round 2 evidence gates after the corrections described below.
- **Minor / execution prerequisite:** `run_real_round.py:60` checks exact PlanSet coverage and freezes execution input at line 68, but does not independently enforce four total Round 2 variants or compare their source/config/build identities with previous rounds. Before real Round 2 dispatch, review and freeze at most four genuinely new candidates and their concrete changes. Distinct IDs, labels or Plan wording alone do not establish a new experiment. This is a pending operational check because no real Round 2 PlanSet currently exists; this review does not approve an unseen PlanSet.

## Findings corrected during review

1. Missing local policy previously disabled the Round 2 gate. A recovery directory missing `p35-confirmation-policy.json` could proceed without the ABBA report. `p35_round2_gate.py:145` now requires the policy; both Round 2 drivers call the gate before preparation/publication side effects.
2. The confirmation execution entry point previously accepted a prepared round with no frozen policy Decision. A temporary synthetic fixture reproduced four accepted ABBA jobs and one reservation while the policy Decision count was zero; budget changed from 8 executions / 2 seconds to 9 / 1802. `prepare_confirmation_round.py:64` now freezes unconditionally, and `run_confirmation_round.py:43` requires the original frozen policy before dispatch.
3. A persisted Intake without Controller acceptance was previously absent from the late-freeze check. `p35_publish_confirmation.py:33` now inspects persisted Intake records, and `require_frozen_policy` at line 48 checks task/target, exact selection, policy artifact and policy-before-Intake order. Both publication (`:91`) and the Round 2 reader (`p35_round2_gate.py:180`) reuse this check, closing the Intake-to-Run recovery window.
4. The reviewed origin correction (`p35_round2_gate.py:127`) binds each replica's source/config/build identity to the selected role's Candidate and validates Candidate→Plan→Strategy/task ownership. Publication calls it at `p35_publish_confirmation.py:117` before writing result records. The reader repeats it and compares normalized rows with verified original receipts. It also requires the report to cover exactly the four jobs in that round.

## Scope checked

Read the protocol and the requested seven helpers, corresponding `test_p35_*.py` fixtures, plus `run_confirmation_round.py`, `real_round_aux.py`, `verify_real_round.py` and the relevant original Intake/Controller/receipt/Memory code to verify call contracts. No original Store, Memory, Controller or Oracle implementation was edited by this reviewer.

Confirmed by code inspection and scoped fixture results: immutable policy/selection recovery, original task limits retained without budget reset, independent ABBA Run/capture identities, chronological benchmark/Profile ordering, fixed Context/runtime/protocol/input identity, exact source/config/build role binding, raw host/profile samples preserved, Run-level 2%/5% classification without significance claims, full Round 1 history and ABBA association/report inclusion in the new Snapshot, and detached single-tile Workload/Context preparation with legal tile predicates and `hardware_executed: false`.

## Validation evidence and limits

- Fresh independent command: `python -B -X utf8 -m unittest -v test_p35_publish_confirmation.PolicyFreezeTests` — **6 tests, exit 0** after the helper corrections.
- Fresh independent command: `python -B -X utf8 -m unittest -v test_p35_round2_gate.ConfirmationGateTests.test_policy_after_intake_but_before_runs_is_rejected` — **1 test, exit 0** after the reader correction.
- An earlier combined local run showed individual passing results for statistics, publication, Round 2 gate, control admission and single-tile diagnostics, plus confirmation scheduler tests and recovery tests for changed artifacts, partial restart/budget/order preservation and immutable preparation. It was intentionally interrupted while another agent ran the final full suite, so this review does **not** claim that combined command completed or that its exit status was successful.
- The policy-missing dispatch reproduction used only a temporary `SyntheticBackend` fixture. It demonstrates host control behavior, not TPU execution.
- Raw XPlane protobuf was not independently decoded by this reviewer. Local fixtures and saved-parser/NPZ verification must not be described as a new TPU measurement or proof of hardware speedup. Real admission, ABBA measurements, actual Round 2 Plans and diagnostic hardware results remain to be collected.

## Additional review during real ABBA execution, 2026-11-13

The independent `p35_statistics` reviewer inspected the actual confirmation response, variants, prepared history and Analyzer result. It found no blocking issue: the B history is complete (315 records, 186 owned and 129 dependencies), equals the prepared history, and references snapshot version 7 / 579 records / high-watermark 604. Four variants preserve their admitted source/config/build/bundle identities except the explicit labels, priorities and costs. Control uses the archived original B repaired baseline; treatment uses archived delayed-store-wait. These are confirmation roles, not Round 2 optimization Plans. All sixty current treatment Profile events were inspected; program/prepare/attention medians were 6.205/0.719375/4.61875 microseconds. This was a saved-evidence review, not an independent XPlane protobuf decode or new hardware measurement.

The same reviewer inspected `p35_prepare_diagnostic_round.py`, `p35_run_diagnostic_round.py` and their fixtures. No Critical/Important execution or charging issue was reported. One Minor recovery issue was reproduced: selecting an existing conflicting round could freeze diagnostic scope before rejection. The author moved the read-only prepared-round compatibility check ahead of all writes. The added regression observed 64 versus 61 records before the fix, and passed after the fix while checking unchanged artifact bytes, no frozen scope or target, and successful corrected preparation. The final fix and valid idempotent preparation tests passed (2 tests, 12.597 seconds). The prior ten-test full diagnostic run passed in 84.348 seconds; these overlapping counts are not additive. The separate diagnostic driver lock does not coordinate older scripts; actual host execution must remain single-writer.
