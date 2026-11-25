# Offline Validation Reuse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove repeated manifest reconstruction and Analyzer replay inside one synchronous `read_route` call, preserving validation and rejection behavior.

**Architecture:** Public validation functions still load and verify their inputs independently. Private inner functions let `read_route` reuse only values it has verified in the current call. No cross-call cache or change to ArtifactStore, snapshots, execution or experiment records.

**Tech Stack:** Existing Python 3.13 environment, unittest, temporary SQLite Stores.

**Spec:** `.artifacts/next-stage-completion-2026-11-16/runtime16-local-readiness/preparation-performance-diagnosis-20261119/diagnosis-and-minimal-proposal.md` and root's scoped validation-reuse task.

## Global Constraints

- Do not change production files while the original preparation-complete process is running; root coordinates the mutation boundary.
- Retain public validation signatures and every manifest, replay, report, candidate and artifact assertion.
- Use only disposable Stores for tests; no TPU calls, dependency installs or experiment Store mutations.
- Preserve existing working-tree changes. No commit is requested.

## Review Focus

- Outer hashes can be consistent while a producer has altered a PlanSet: route replay must still reject it.
- A supplied check report can disagree with archived bytes: route check validation must still reject it.
- Candidate-only artifacts are outside the parent manifest: they must still be verified.
- Files can change between calls: successful prior validation must never authorize a later read.
- Public callers can retain a manifest object while its bytes change: public validators must reload it.

## One task: synchronous route validation reuse

**Files:** Add `tests/test_offline_preparation_validation_reuse.py`; after root releases the production-edit boundary, make the narrow extraction in `profiler/outputs.py`, `workflow/offline_checks.py` and `workflow/offline_preparation.py` under `src/rsi_kernel_agent/`.

**Interfaces:** Preserve `validate_result(store, manifest, result)`, `validate_checks(store, manifest, result, drafts, checks)` and `read_route(store, route_id)`. Private inner helpers may share the already verified inputs only within the same call.

- [x] Run the new five-test module against unchanged production source. On 2026-11-19 14:47:52–14:47:56 Asia/Shanghai it reported `manifest_loads=3`, `analysis_replays=2` against expected `1/1`; all four rejection tests passed. Five tests, one anticipated assertion failure (not a unittest expected-failure outcome), zero errors, unittest 3.250 seconds, process exit 1. Raw logs and source identities: `red-tests.result.json`, `red-tests.stdout.txt`, `red-tests.stderr.txt` in the spec's diagnosis directory.
- [x] Root reviewed the red evidence and coordinated the three-file extraction after collecting the original process exit. The independent static review found no required changes; public entry points retain independent validation, and reuse lasts only for the same synchronous route read.
- [x] Reran the unchanged five-test module on 2026-11-19 14:59:08–14:59:11 Asia/Shanghai: all five passed, zero failures/errors, unittest 2.547 seconds, process exit 0. Valid route reads leave temporary Store history unchanged; manifest reconstruction and Analyzer replay are each called once; all corruption cases remain rejected. The six recorded source/test identities stayed unchanged during the run.
- [x] Per root's subsequent instruction, ran one complete root discovery instead of additional focused modules or the auxiliary full suite. It ran on 2026-11-19 15:00:59–15:29:03 Asia/Shanghai: 558 distinct tests, 555 passed, three platform skips, zero failures/errors, unittest 1682.368 seconds, wrapper 1683.419 seconds, actual process exit 0. The 268 recorded source files and 91 supplemental helpers retained their hashes and file sets; both full log digests match. The test process was collected, not left running.
- [x] Recorded exact commands, source identities and retained red/green/full-suite logs in the independent diagnosis directory. `validation-report.md`, `red-green-independent-verification.json` and `root-tests.independent-verification.json` record the completed verification and limits. This software regression does not establish original preparation completion, TPU correctness or a completed feedback round.

Targeted command: `knowledge-site/.venv-mcp/Scripts/python.exe -B -X utf8 -m unittest discover -s tests -p test_offline_preparation_validation_reuse.py -v`.
