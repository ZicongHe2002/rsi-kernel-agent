# Confirmation fixture budget regression

2026-11-16. Investigator: `/root/accept_memory_recovery`.

The initial full auxiliary discovery reported setup errors in the confirmation gate and publication tests. The stack ended at `workflow/budget.py::validate_transition`, which correctly rejects states carrying limits without spending fields.

An independent single-test run reproduced the same failure before any edit: **1 test, 1 error, 2.444 seconds, exit 1**. See [before log](budget-fixture-before.log).

The shared fixture constructed four completed and reserved protocol jobs, then saved an initial Journal state with `task_id`, `revision`, `limits`, and `jobs` only. `PublicationTests` calls the same setup, so its failures had the same source. Current Controller initialization and `remaining_budget` require the exact spending fields `executions`, `tokens`, and `time_s`. A valid initial fixture must preserve the declared costs of its existing reserved jobs.

The only change is in `test_p35_round2_gate.py`: derive these three spending totals from reserved fixture jobs, then include `spent` in the initial state. The current fixture totals are 4 executions, 0 tokens, and 4 declared seconds. No production guard, test expectation, assertion, Oracle, timing contract, or real Store record changed.

After the fix, both complete affected modules passed: **29 tests, 29 passed, 0 skipped/failures/errors, 110.751 seconds, exit 0**. This includes negative-policy, missing-result, corrupted-receipt, origin mismatch, incomplete publication and idempotent publication checks. See [after log](budget-fixture-after.log) and [structured verification](budget-fixture-verification.json).

The original failed full-suite logs remain preserved. This targeted result does not replace the required full root and auxiliary regressions being performed by the parent agent.
