# Runtime10 Batch Wrapper Implementation Plan

**Goal:** Bind an existing v2 design batch and final implementations to one exact cumulative grant and bounded Controller execution.

**Architecture:** Add one artifact helper, leaving production APIs and historical helpers unchanged. Freeze validated publications, variants, configuration and pre-grant journal; accept every Plan through production Intake only after the exact grant is applied; step the original Controller with guards inside its existing Task lock.

**Tech Stack:** Python, unittest, temporary ExperimentStore, production design/Intake/Controller/ColabBackend.

**Spec:** `docs/superpowers/plans/2026-11-17-runtime10-execution-handoff.md`.

## Constraints and review focus

- This implementation session performs no live database mutation, grant, allocation or TPU work.
- Freeze actual runtime, Context, round, batch, publication, parent, variant and cost identities; no invented Runtime10 IDs.
- Reject incomplete design, omitted Plan/variant, stale grants, changed prior jobs, unbudgeted retries/repairs and unknown operations.
- Preserve all exact production validation; grants are supplied and applied separately.
- Recheck scope inside the Controller Task lock to close the validation/step race.
- Temporary real-protocol fixtures are CPU-only software evidence, never real TPU feedback.

## Implementation

- [x] Add a failing availability/guard test for the missing wrapper; run it.
- [x] Add `.artifacts/next-stage-completion-2026-11-16/runtime10_batch_executor.py` with `freeze`, `intake`, and `advance` functions. Use the caller-owned Python API as directed by the coordinator; no unused CLI was added.
- [x] Add temporary-store tests using real v2 publications and the real protocol fixture; cover accepted execution and every scope guard above.
- [x] Run the focused suite and document exact usage and boundaries. Final result: 13 tests passed in 107.115 seconds, exit 0, source unchanged during verification. Raw log and SHA256 result are saved beside the helper. Existing production suites were already verified and were not rerun for artifact-only changes.

## Independent-review correction: actual backend binding

The independent review found that an unrelated liveness callback could describe the frozen Runtime while the actual execution backend used another nonce/spool or runtime. A wrong runtime could therefore fail only after a reservation. The original 13-test evidence and exact files are retained in `runtime10-backend-binding-fix/before/`.

- [x] Reproduce with production ColabBackend plus recording offline remote; the new mismatch test failed on the old implementation and observed changed journal state.
- [x] Validate actual ColabBackend/ColabRemote/ColabBridge, same Store object and all frozen configuration routing fields inside the original Task lock, before liveness calls, reservations or operation lookups.
- [x] Automatically probe the same execution remote; retain an optional caller callback only as an additional assertion. Recheck the binding after probes to reject callback-induced miswiring.
- [x] Cover nine configuration/Store mismatches with unchanged state/history and zero remote effects; cover the normal production factory path and an unrelated callback that cannot override wrong remote liveness. The three focused tests passed in 24.403 seconds.
- [x] Run the entire corrected helper suite and retain its raw output/source hashes separately from the original acceptance. Result: **16 tests passed, exit 0, 147.642 seconds**, source files unchanged during testing; see `runtime10-backend-binding-fix/suite.log` and `suite.result.json`.
