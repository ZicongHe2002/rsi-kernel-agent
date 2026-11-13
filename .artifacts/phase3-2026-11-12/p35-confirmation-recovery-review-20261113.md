# P3.5 confirmation recovery scope review

Review date: 2026-11-13, Asia/Shanghai. Reviewer: p35_statistics.

No blocking issue was found within the observed recovery scope. This review
read source, saved JSON, the recovery log, and content-addressed receipt files.
It did not open the experiment Store, call the bridge, execute a real driver,
or mutate experiment records. A second reviewer independently examined the
wrapper source and Controller reservation/recovery logic.

## Reviewed evidence

- `recover_confirmation_controller.py`, SHA-256
  `374fbf4ec956a2361a984d3bf8e42953ebc6c2e14006e01979167f0de5ddd9f6`.
- `confirmation-f686274a57bb7ebb/selection.json`, `execution-input.json`,
  `intake.json`, and the saved `controller-status.json` at revision 136.
- `real-round-1/controller-status.json`, used as the five-job pre-ABBA reference.
- `p35-abba-recovery-loop.txt`: 38 parsed rows, indices 0-37,
  observed revisions 92-136.
- Existing receipt blobs referenced by the four saved ABBA jobs.

The coordinator identified session 45018 as the only active writer and stated
that all other task work was terminal at startup. The first saved recovery row
is revision 92. This report does not claim to identify the active process's
exact loaded bytes from its current file.

## Scope and budget checks

Read-only Python assertions passed for the following checks:

- The root selection, prepared selection, and execution-input selection are
  equal. The frozen order is C1/T1/T2/C2 with priorities 40/30/20/10.
- The saved journal contains exactly the four ABBA jobs for this round from accepted intake
  `wf-intake:2355339dca70711dac815beef6518acf286182f7`. Every admitted field,
  including source/config/build/bundle, contract, parent binding, and Runtime,
  remains equal to that intake. The variants equal `execution-input.json`.
- Candidate and Run IDs match the original Controller's deterministic IDs.
  All four attempts have `attempt=0`, `repair_depth=0`, and no `retry_of` or
  `repair_of`; no additional ABBA attempt appears in the saved journal.
- The five other jobs are equal as parsed JSON to the pre-ABBA
  reference. They remain terminal. The only added intake is the original ABBA
  intake, and its journal digest matches its canonical JSON.
- Task limits remain unchanged. Each ABBA job has one reservation for
  1 execution, 0 tokens, and 1800 seconds. Prior spending was 5 executions /
  9000 seconds; ABBA adds exactly 4 / 7200, yielding 9 / 16200. No recovery
  row adds spending without the next original ABBA job starting.
- Every observed receipt blob matches its declared size and SHA-256 and the
  saved receipt object. Receipt stage, Candidate ID, and Run ID match its job.
  This checks archival consistency; it is not a new independent rerun of the
  complete real receipt verifier.

| Order | Label | Run | Saved state at revision 136 |
|---|---|---|---|
| C1 | p35-control-before | wf-r:52bab2381770e5ba2fa1c9bfcb721f3f | completed / evaluate |
| T1 | p35-treatment-first | wf-r:5457d24165692d83527fabe4434f0496 | completed / evaluate |
| T2 | p35-treatment-second | wf-r:396d629c0dee5027ce556961ec76b841 | completed / evaluate |
| C2 | p35-control-after | wf-r:8395d67f949b364daf53a8197620bef7 | blocked / benchmark |

Across all 38 saved log rows, first starts follow C1/T1/T2/C2. Started jobs
always form a prefix of that sequence, with at most one active/evaluating/
blocked job. Completed C1, T1, and T2 benchmark/Profile lease intervals follow
the same order without overlap. This review does not claim four completed
runs or an ABBA statistical outcome: C2 was not complete in the audited state.

## Running-wrapper limitation

The wrapper constructs the original Controller, reads status, and calls
`Controller.step()` for a bounded 240-iteration loop, sleeping 15 seconds
between steps. It does not call publication, intake, `accept_intake`, retry,
repair, or a direct budget initialization API. With the existing task journal,
Controller construction verifies immutable limits; this observed recovery
did not initialize a new budget. Existing reservations remain reserved and
unknown operations retain their concurrency slot.

The coordinator stated that scope assertions now at lines 22-28 and 33-35
were added on disk after session 45018 had started and imported its code.
Those added assertions therefore were **not executed by that running
process** and are not credited as runtime protection here. The executed
constraints are those in the original Controller and immutable execution
intents, together with the stated one-writer/no-other-pending-work operating
conditions. The saved-state checks above corroborate the observed scope but
do not retroactively execute the added guards. The current file hash is not
presented as a hash of the already-loaded earlier version.

## Confirmation driver regression evidence

Separately, `run_confirmation_round.py` was changed only to keep querying a
blocked job with a persisted operation within the caller's explicit `steps`.
Operationless blocks still stop; interval validation and original Controller
attempt/reservation behavior remain unchanged. Three temporary-Store fixture
tests cover transient recovery without another submit/charge, permanent
unknown bounded by steps, and operationless early stopping.

The initial targeted TDD run observed two expected failures and one pass
(tool session 27871, 301.164 seconds). The first full 18-test run passed 17
and failed only an old error-message assertion (tool session 15323,
741.043 seconds). Updating that assertion to recognize the earlier selection
conflict passed a targeted rerun (session 73656, 54.598 seconds, exit 0).
These earlier commands did not redirect output to disk; older similarly named
log files are not evidence of those runs.

A fresh full-file run completed with redirected output in
`p35-confirmation-recovery-tests-20261113.txt`: **18 tests passed in 695.708
seconds, process exit 0**. The log includes all three new recovery tests and
the corrected prior-round selection assertion. The command was the local
Python launcher with `-B -X utf8 test_confirmation_round.py`, with stdout and
stderr captured together in a newly created file. Fixtures use temporary
Stores and synthetic backend transport; this verification did not access TPU,
the bridge, or the real Store.

## Diagnostic preparation follow-up

The earlier Minor finding about freezing a conflicting round selection before
checking its existing marker is closed. `p35_prepare_diagnostic_round.py`
now calls `load_prepared()` at lines 134-135 before scope/artifact/Store writes.
The new conflicting-round regression asserts no writes and successful later
preparation after the round ID is corrected. This follow-up was source review;
its execution evidence is maintained by the diagnostic implementation owner.
