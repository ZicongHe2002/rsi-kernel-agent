# Runtime 3 replacement — pending hardware

2026-11-12. Runtime 2 disappeared after all five round-1 jobs completed and their artifacts were downloaded. The existing allocation loop continues with the persisted notebook ID. No duplicate allocator should be started. Timeouts and HTTP 503 are not evidence of successful allocation.

## Order

1. Require allocation-intent status `allocated`; reconcile any accelerated unrelated assignment before proceeding. Do not terminate user work.
2. Run `setup_runtime_environment.py --runtime3`; use `--poll --runtime3` on resume. Original bridge and isolated environment only. Generation/runtime guard runs before remote work; old observation bytes are archived.
3. Run `probe_isolated.py --runtime3`; require synchronized result 140, actual v6e devices and parser import. Never infer environment from Runtime 2.
4. Run `admit_runtime3.py --bootstrap`. It verifies old completed evidence, uses the original SQLite/ArtifactStore, creates new Contexts/Runs, and freezes immutable jobs/config. A uses original repaired baseline; B remeasures the completed delayed-store-wait candidate. Both retain source/config/build identity. Negative controls use their original mutation identities.
5. Run `advance_runtime2.py --runtime3` (the existing prerequisite driver, now generation-selectable). Query original operation IDs before any submit. Failed Oracle gate blocks progress; unknown continues query without resubmission. Results must pass `require_gates(..., {'generation':'runtime3'})` and local raw evidence verification.
6. Only after those gates pass, write active-runtime-binding.json as `{"generation":"runtime3"}`. Round 1 is permanently pinned to runtime2. Round 2 keeps its existing opaque ID format `real-runtime2-round:2`, while its actual immutable Context and binding identify Runtime 3. The identifier text is not hardware evidence.
7. Prepare round 2 with original Memory/Analyzer mechanisms, read all participating complete histories including round 1, and author new reasoning in the current conversation. No independent model API.

No second-round session, proposal, or remote experiment has been created by these preparation scripts yet.

## Budget and comparison

Original exploration limit remains16executions/28800declaredseconds;5 executions already spent. Runtime prerequisites are separately recorded through original prerequisite reservation Decisions and are not included in Controller.spent. This is not a total hardware-cost cap. No old operation is re-executed.

All formal comparisons must be within the new context and frozen workload/protocol. Old results may inform hypotheses but cannot supply a new-Runtime denominator. Two rounds and controlled replication remain required; the first-round Store Best is not a repeatability claim. The single-tile zero-length branch still needs separate diagnostic coverage.

## Local verification and review

Binding2, real-round11, confirmation7, prior-runtime auxiliary6, and readmission5 tests passed (31total). They use local CPU/synthetic fixtures and do not establish new hardware readiness. The readmission tests observed missing implementation, then passed; added observation guards likewise first failed due to missing helpers. Root full regression is archived separately when finished.

Independent source reviewer found a wrong-generation output overwrite risk, fixed by pre-bridge binding checks and preservation of original bytes. Re-review closed the Important finding. Minor: observation read/check/replace is not a multiwriter atomic CAS; host orchestration must remain sequential. Reviewer did not execute tests or remote code.
