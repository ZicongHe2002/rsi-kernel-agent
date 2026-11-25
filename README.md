# RSI Kernel Agent

An experimental workflow for iterative TPU kernel optimization, combining an experiment store, Knowledge/Memory, Profiler/Analyzer, and a deterministic execution Controller. The current hardware validation scope is **MLA Decode contiguous BF16 on Colab TPU v6e**.

## Current status

The **2026-11-20 six-step implementation** now includes shared frozen route progress, complete metric histories with on-demand candidate evidence, same-source CPU candidate checks, Runtime-binding delta review, and a continuous single-Run TPU worker with per-stage receipts. Local full regression passed for the frozen implementation: root **666 tests (663 passed, 3 platform skips)** and auxiliary **383/383 passed**, both exit 0 with 361 source/test/helper identities unchanged. New experiment-entry helpers are undergoing separate integration checks; three hardware optimization rounds have not completed. The new CPU checker has independently rerun the saved A/B/D drafts on all three frozen cases (9/9 passed). Hardware use is restricted to one TPU v6e through the existing `Desktop/colab/google-colab-cli` installation; source upload is blocked until the corporate file-security checker is available and returns `allow`. See the [current implementation and validation record](docs/phase3/next-stage-completion-2026-11-16.md#2026-11-20-六步闭环实现与验证).

Start with the [documentation index](docs/README.md) for current reports, implementation guides, and historical checkpoints. The [next-stage continuation record](docs/phase3/next-stage-completion-2026-11-16.md) separates software delivery and Runtime admission from optimization acceptance. Runtime 13 completed all four prerequisite gates on November 17; the saved November 18 liveness observation records its assignment absent. New A/B/D optimization feedback and D's five effective rounds remain incomplete.

The local workflow now supports [offline preparation before allocation](docs/superpowers/plans/2026-11-17-runtime10-execution-handoff.md#offline-preparation-before-allocation). Research, candidate drafts and local checks stay non-executable; a new measured Context and explicit route-by-route review are required before publication and the existing serial queue. This does not allocate hardware or grant execution budget. Fresh November 18 verification is complete: root **553 tests (550 passed, 3 platform skips)**, auxiliary **383/383 passed**, and focused preparation/helper checks **62/62 passed**, all exit 0. The 272-file frozen source/helper inventory is unchanged; see the [software verification record](docs/phase3/next-stage-completion-2026-11-16.md#2026-11-18-准入终态核对与离线准备软件增量). These are local software checks, not new candidate numerical or TPU results.

As of **2026-11-16 Asia/Shanghai**, **P3.5 System Workflow: PASS_WITH_LIMITATIONS** within the declared MLA Decode contiguous BF16 scope. Both real optimization rounds and the independent single-tile diagnostic are complete and audited. **Stable End-to-End Improvement: NOT_CONFIRMED**.

- Round 1 completed four Plans and five real candidates, with correctness, benchmark, profile, and Store integration.
- Four independent C–T–T–C confirmation Runs completed. Program medians improved by about 6.50% and 5.92% in the two comparisons. Host latency drift leaves the joint result inconclusive; stable end-to-end improvement is not confirmed.
- Round 2 completed four Plans and four real candidates on Runtime 7. The Analyzer read prior history; all four passed the frozen correctness checks and wrote results back through the original Evaluator and Store. `length-row` and `b1-fused-prepare` were faster in one Run each; stable end-to-end improvement remains unconfirmed.
- Two independent single-tile diagnostic Runs completed in their own Context. They cover the original single-tile source's declared zero-length cases, not the new B1 fusion branch's wider boundary behavior.
- The offline audit verified 28 Runs, recomputed 76 correctness cases, independently decoded 22 XPlanes, and reconstructed Snapshot 26 and current Memory projections. Post-execution input-cache state was not retained for independent rechecking. The historical Runtime 4 correctness outcome remains unknown and archived as incomplete.
- Snapshot 26 has 1,505 records / high-watermark 1,530. Journal revision 247 records 17 of 18 executions used, with 1 execution / 1,800 declared seconds remaining. Runtime admission has separate prerequisite accounting. This acceptance audit makes no new hardware requests or experiment Store writes.

See the [final acceptance report](docs/phase3/phase3-final-acceptance-report.md), [Runtime 7 progress report](docs/phase3/phase3-p35-progress-report-2026-11-14.md), and [ABBA results](docs/phase3/phase3-p35-abba-results.md). Earlier dated checkpoints remain in the [implementation report](knowledge-site/manifests/phase3-2026-11-12/implementation-report.md).

## Repository layout

- `src/rsi_kernel_agent/`: contracts, experiment store, Knowledge/Memory, analysis, and execution workflow.
- `tests/`: local CPU and synthetic regression tests.
- `docs/`: design, execution contracts, recovery guides, and validation reports.
- `.artifacts/p35-final-acceptance-2026-11-16/`: selected final audit reports, compact results, test logs, and verification scripts; see the [evidence guide](.artifacts/p35-final-acceptance-2026-11-16/README.md).
- `knowledge-site/`: local Markdown knowledge application and project validation manifests.
- `rsi-codex-knowledge-site-bootstrap/`: reusable setup prompts and templates.
- `.artifacts/phase3-2026-11-12/`: selected versioned host helpers, fixture tests, candidate sources/diffs, and compact evidence summaries. Other execution state in this directory remains local.

## Local verification

From the repository root, with the project's existing Python dependencies available:

```text
python -B -m unittest discover -s tests -v
python -B -m unittest discover -s .artifacts/phase3-2026-11-12 -v
```

Fresh post-fix regressions passed on 2026-11-16: root **343 tests, 340 passed, 3 skipped, 568.923 seconds**; auxiliary **383 executed entries / 376 distinct test IDs, all entries passed, 2501.671 seconds**. Both exited 0 with no failures/errors. The first auxiliary failure, minimal repairs to six test-fixture files, and targeted red/green evidence are retained in the acceptance report. Production code and test assertions were unchanged. Local tests do not establish TPU correctness or performance. Setup for the knowledge application is documented in [knowledge-site/README.md](knowledge-site/README.md).

## Evidence and execution boundaries

Large content-addressed artifacts, raw NPZ/XPlane files, the live SQLite Store, downloaded dependencies, credentials, access state, and personal knowledge notes are not part of this source upload. Reports retain local evidence references; some resolve only in the original workspace. Selected host scripts also depend on the preserved local Store and the original Colab CLI installation, and are not standalone hardware benchmarks.

Use the [recovery guide](docs/phase3/phase3-worker-recovery.md) and current acceptance evidence before any separately authorized continuation. Completed operations must be collected by their original identity, and results from different Runtime Contexts must not be merged into one performance comparison. The old Runtime 4 recovery plan is a historical checkpoint, not the current execution starting point.
