# Phase 2D+ implementation review and regression ledger

2026-11-11. Independent assessment is [independent-review.md](independent-review.md). This ledger joins component audits with the coordinator's actual failing integration runs; it does not replace independent review.

| Finding | Actual regression and resolution |
|---|---|
| Detached journal save replaced state while the loop retained old job objects | Controller first integrated run produced KeyError operation for the second admitted job. Iterate stable job IDs and reacquire from the returned state. Multi-job and restart tests pass. |
| Backend successful receipt label differed from Controller's first assumption | Actual backend uses succeeded. Stage advancement aligned with that protocol; no new state enum in 2A. |
| Same Candidate retry changed source note payload under its original key | retry_retains failed with ConflictError. origin_job_id stays stable for Candidate; new attempt has a new Run and budget charge. Target recheck passed18.676s. |
| Cancel after unknown outcome could submit an operation later confirmed unstarted | unknown_outcome regression showed an unexpected execution_request. Controller now integrates cancellation without starting it. |
| Budget-deferred RunEvents only appeared on the next call | zero_budget failed and five actual-process tests observed extra records on second run_until_idle. Deferred projections now commit before idle return. Target passed1.271s. |
| Unknown outcome later running remained blocked forever | Fresh reviewer reproduced zero polls; target regression failed then passed4.786s; reviewer recheck6.108s. Existing operation is polled, never assigned a new ID. |
| Pause Decision could commit before journal pause state | Real child process exited after paused append; restart still scheduled A. Journal-first control_events and idempotent projection fixed it. Target passed5.293s. |
| Late cancel changed partially/fully committed evaluator payload | Actual child process exit after result marker, plus early Evidence and failed terminal tests. Persist evaluation intent before effects; replay original receipt set/request/mode. Execution19/19 and recovery9/9 pass. |
| Generic note could impersonate intake marker | arbitrary_note expected refusal but was accepted. Strict marker type/fields/Task target added; regression passes1.286s. Fully privileged low-level forged Store writes remain outside protocol trust boundary. |
| Across-stage variant changes, unscheduled execution, zero-cost attempts, predicted timing ancestry | Execution regressions require matching stage request identity, scheduled Run, at least one execution reservation, and measured ancestry. Fixed without old module edits. |
| Duplicate publication in another round, indirect Knowledge references, partial intake prefix | Intake tests enforce cross-round uniqueness and indirect freshness; aggregate per-Plan outcomes commit atomically in one Decision. |

New module tests first observed missing-feature failures; later defects above were reproduced before fixes. The already-composed standalone Demo and subsequently added full Controller out-of-order acceptance passed on first execution; they are not claimed to have been missing-implementation RED tests. Out-of-order Controller case passed19.920s with late9ms preserving newer7ms A Best and2ms Task Best, all synthetic.

The full suite is the final authority for counts and current state; see [tests.txt](tests.txt) and [test-environment.json](test-environment.json). Intermediate failures are preserved in the work log and auxiliary artifacts instead of being presented as final failures. No old tests were removed or relaxed.
