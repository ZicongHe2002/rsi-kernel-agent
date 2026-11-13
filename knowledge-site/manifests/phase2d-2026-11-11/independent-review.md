# Phase 2D+ independent review

2026-11-11. Fresh reviewer received the user request, implementation plan, file locations and review scope without the implementation conversation. Reviewed current workflow source and tests directly against existing Store, contracts, associations, Memory and Profiler APIs. No runtime or test files were edited by this reviewer.

## Assessment

No unresolved Critical or Important finding remains in the reviewed implementation after the fixes below. This is an independent code and targeted-test assessment, not a claim that the final aggregate regression run has completed. Final root/MCP suite and standalone CLI demo results are recorded by the coordinating agent.

## Findings and recheck

| Severity | Finding | Resolution and evidence |
|---|---|---|
| Important | A durably submitted operation whose response is unknown becomes blocked. On later lookup returning running, Controller skipped polling forever, retaining its concurrency/device reservation. | Independently reproduced: first state blocked/running; five further steps still blocked/running with zero poll calls. Controller now only keeps unknown blocked and reconciles running into normal polling. The exact new regression passed independently: 1 test, 6.108 s. |
| Important | A later cancellation could change the final evaluation marker payload after result effects had already committed, conflicting with the same idempotency key during recovery. | Identified during review and independently reproduced by recovery audit with actual child-process termination after the result marker. Evaluator now persists workflow_evaluation_intent.v1 before integration effects, binding job, request digest, receipt references and cancellation mode. Inspected the fix; execution audit reports 19 component tests and the actual-process marker/cancel regression passing. |
| Important, found by recovery audit | Pause Decision could commit while the workflow journal still permitted new admissions. | Inspected the journal-first control_events design and replay into original paused/resumed Decisions. Recovery audit and root report the actual-process pause-commit regression passing. |
| Important boundary hardening | Controller.accept_intake accepted a matching result inside an unrelated persisted Decision without requiring the workflow intake completion envelope. | The entrypoint now requires a note Decision, matching Task and Task target, the exact type/request_digest/result field set, workflow_intake.v1 type, and canonical result equality before any state or Candidate writes. Inspected the change and independently observed the unrelated-note rejection test pass: 1 test, 1.212 s. |

The initially known retry Candidate-source-note conflict was also reviewed after the change: origin_job_id stays stable for the same Candidate while retries use new Runs and independently charged attempts. Zero-budget deferral now synchronizes the original Run scheduling state before returning idle.

## Independently observed validation

- `python -m unittest discover -s tests -p test_workflow_execution.py -v`: 17/17 passed in 24.000 s before the additional evaluator recovery tests.
- Controller suite loaded during concurrent fixes: 12 tests in 129.135 s; its sole failure was the already-reproduced unknown-to-running recovery defect. The other 11 passed. This is a recorded intermediate result, not a final-suite failure claim.
- `python -m unittest test_workflow_controller.WorkflowControllerTests.test_unknown_submission_reconciles_running_operation_and_finishes_same_attempt -v`: 1/1 passed in 6.108 s after the fix.
- Independent exact-parent comparison probe: baseline 10 ms; child 5 ms classified correct_faster, 15 ms correct_slower, 10 ms correct_inconclusive; final Task Best remained 5 ms.
- Attempted a database-symlink lock-alias portability probe; this Windows account cannot create symlinks (WinError 1314). No concurrency defect was reproduced by that probe, and no runtime fix is claimed for it.

All test executions used the existing `knowledge-site/.venv-mcp/Scripts/python.exe`, temporary local databases, the existing ArtifactStore and synthetic backend. No packages, credentials, model APIs, real Kernel, TPU or external execution service were used.

## Requirement and boundary review

Checked the A-G acceptance mapping in the plan and actual tests: variable variants and independent branch progress; mixed failure classifications; bounded repair with exact ancestry; subprocess recovery; second-round frozen Memory histories and existing session Analyzer; duplicate intake/results and out-of-order promotion. Also inspected the added actual-Controller out-of-order case: all six jobs are admitted, the older A job is delayed, later A/B results finish first, the older completion cannot replace better Best values or the newer Latest Attempt, and repeated recovery leaves record count and spend unchanged. The coordinator reports its targeted execution passed in 19.920 s; this reviewer did not independently rerun that test.

The implementation reuses schema v1 and original Store.consider rather than duplicating promotion. Execution requests and raw receipt artifacts bind Candidate/parent identity, Run/context, source/config/build references, fixed Task/Workload/Context contract, device, costs and synthetic provenance. Invalid/missing stage prefixes and corrupt/cross-run receipts fail closed. Budget reservations, retries and repair limits remain deterministic. Correct but slower routes remain eligible for Frontier. Existing source is materialized into isolated workspaces; synthetic compile/correctness/timing do not execute or validate a real Kernel.

Reviewed `docs/phase2d-workflow-controller.md` against current APIs. It accurately describes the source-materialization boundary, declared cost budgets, immutable limits, current-parent policy, single-host locks, persisted evaluation intent, explicit repair responses, canonical receipts and synthetic-only demo. The doc explicitly limits claims about hardware, remote exactly-once execution, scale, power-loss durability and low-level trusted Store writers.

## Nonblocking limits

The documented trusted low-level writer boundary permits callers with direct ExperimentStore access to append arbitrary Decisions; the workflow protocol is not an authorization system. The local lock assumes consistent canonical database addressing. Database hard-link aliases and differently named database symlinks were not qualified by the accepted tests; use the same canonical database path for Controllers. Distributed scheduling and remote execution durability remain Phase 3 work.

Final intake-boundary recheck: ordinary unrelated-note impersonation is now rejected and is closed as a finding. A caller able to write a fully forged workflow_intake.v1 envelope directly through ExperimentStore is already inside the documented trusted low-level writer boundary; the protocol does not authenticate publishers. That existing boundary is nonblocking for this Phase 2D scope and does not require replaying the Analyzer on every Controller acceptance. No unresolved Critical or Important finding remains after this final recheck.
