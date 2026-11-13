# RSI Kernel Agent

An experimental workflow for iterative TPU kernel optimization, combining an experiment store, Knowledge/Memory, Profiler/Analyzer, and a deterministic execution Controller. The current hardware validation scope is **MLA Decode contiguous BF16 on Colab TPU v6e**.

## Current status

As of **2026-11-13 19:07 Asia/Shanghai**, P3.5 is **in progress and not finally accepted**.

- Round 1 completed four Plans and five real candidates, with correctness, benchmark, profile, and Store integration.
- Four independent C–T–T–C confirmation Runs completed. Program medians improved by about 6.50% and 5.92% in the two comparisons. Host latency drift leaves the joint result inconclusive; stable end-to-end improvement is not confirmed.
- Round 2 produced four new Plans and candidate implementations. Runtime 3 disappeared before any of them was submitted remotely. Those attempts were archived without refunding the reserved budget or inventing execution results.
- Runtime 4 was acquired and its fixed environment and synchronized JAX probe were verified. Readmission is incomplete. The local prerequisite driver is stopped; the attention negative-control correctness result has not been collected locally. Recovery Round 2 and the separate single-tile zero-length diagnostic have not executed.
- The original exploration budget remains 10 of 16 executions used, with 6 remaining. Runtime admission has its own prerequisite accounting.

See the [Phase 3 implementation report](knowledge-site/manifests/phase3-2026-11-12/implementation-report.md), [P3.5 validation record](docs/phase3/phase3-p35-final-validation.md), and [ABBA results](docs/phase3/phase3-p35-abba-results.md).

## Repository layout

- `src/rsi_kernel_agent/`: contracts, experiment store, Knowledge/Memory, analysis, and execution workflow.
- `tests/`: local CPU and synthetic regression tests.
- `docs/`: design, execution contracts, recovery guides, and validation reports.
- `knowledge-site/`: local Markdown knowledge application and project validation manifests.
- `rsi-codex-knowledge-site-bootstrap/`: reusable setup prompts and templates.
- `.artifacts/phase3-2026-11-12/`: selected versioned host helpers, fixture tests, candidate sources/diffs, and compact evidence summaries. Other execution state in this directory remains local.

## Local verification

From the repository root, with the project's existing Python dependencies available:

```text
python -B -m unittest discover -s tests -v
```

Latest completed run: **321 tests, 318 passed, 3 skipped, 0 failures/errors, 423.235 seconds, exit 0**. Local tests do not establish TPU correctness or performance. Setup for the knowledge application is documented in [knowledge-site/README.md](knowledge-site/README.md).

## Evidence and execution boundaries

Large content-addressed artifacts, raw NPZ/XPlane files, the live SQLite Store, downloaded dependencies, credentials, access state, and personal knowledge notes are not part of this source upload. Reports retain local evidence references; some resolve only in the original workspace. Selected host scripts also depend on the preserved local Store and the original Colab CLI installation, and are not standalone hardware benchmarks.

Use the [recovery guide](docs/phase3/phase3-worker-recovery.md) and [Runtime 4 recovery plan](docs/phase3/phase3-p35-runtime4-recovery-plan.md) before continuing the existing journal. Completed operations must be collected by their original identity, and results from different Runtime Contexts must not be merged into one performance comparison.
