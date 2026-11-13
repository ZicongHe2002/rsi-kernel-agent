# Phase 3 P3.5 Multi-Round Real Hardware Validation Implementation Plan

> **For agentic workers:** Execute this plan task-by-task, preserving the existing Store, Runtime bindings, and recovery protocol. Each task ends with a recorded verification result.

**Goal:** Revalidate the promising first-round candidate on a newly observed TPU v6e, complete one controlled performance confirmation and a distinct second real RSI round, then report whether P3.5 is system-validated with or without a confirmed performance improvement.

**Architecture:** Reuse the existing Colab Bridge, real backend, isolated worker, ExperimentStore, ArtifactStore, Memory/Knowledge, Analyzer session, Controller, Evaluator, and `consider`. Runtime replacement creates new immutable Context/Run records; the first round remains pinned to Runtime 2. ABBA confirmation and Round 2 use the same frozen correctness and benchmark contracts, with all raw receipts and profiles stored in the original Store.

**Tech Stack:** Existing Python 3.13 host environment, original Colab CLI bridge, remote isolated Python/JAX environment, SQLite ExperimentStore schema v1, existing MLA Decode runner and duration parser.

**Spec:** `C:/Users/fengluo/.codex/attachments/22911d5c-bfcf-4a85-89d8-23b523f92d8e/Pasted text.txt`; current status: `knowledge-site/manifests/phase3-2026-11-12/implementation-report.md`.

## Global Constraints

- Use only the existing `C:\Users\fengluo\Desktop\colab\google-colab-cli` bridge and its internal authentication handling.
- Target only MLA Decode contiguous BF16 in this P3.5 task; do not broaden to Forward, paged Decode, or new dtypes.
- Use the single `.artifacts/phase3-2026-11-12/experiments.sqlite3` and its ArtifactStore; never create a second experiment database.
- Runtime 2 evidence is immutable; a replacement Runtime requires a new environment fingerprint, EvaluationContext, Run, receipts, and evidence.
- Query `completed` and `running` operations before submitting; `unknown` remains blocked and is never blindly resubmitted.
- Preserve source, config, build, Oracle, benchmark, and parser identities; do not modify the authoritative PallasKernels source, reference, tolerances, or benchmark rules.
- Keep the original exploration budget (16 executions / 28,800 declared seconds; 5 already spent) and report prerequisite costs separately.
- Do not read or output credentials, cookies, tokens, `.auth`, or private keys; do not upgrade global dependencies or account permissions.
- Synthetic/CPU checks, real connectivity, real correctness, real benchmark, real profile, and verified performance improvement must remain separate evidence classes.

## Review Focus

- Replacement Runtime must not inherit Runtime 2 timing or overwrite Runtime 2 reports; test generation binding and old-byte preservation before bridge execution.
- Baseline and delayed-store-wait must be independently reloaded from immutable Candidate records; test source/config/build/entry identity and fixed Context binding.
- Oracle-negative attention and cache mutations must fail before benchmark/profile; test no accidental promotion or partial later stages.
- ABBA samples must preserve order, raw samples, and independent Run identity; test budget accounting and no duplicate operation execution after interruption.
- Round 2 must read the completed Round 1 snapshot and histories and produce changed Plans; test manifest references and saved Analyzer exchanges before accepting the Intake.

### Task 1: Inspect and Reconcile Current State

**Files:** Read existing report, Runtime ledger, allocation log, Store journal, Round 1 completion, latest snapshots, and bridge assignments. Write only a sanitized status artifact under `.artifacts/phase3-2026-11-12/`.

- [ ] Verify no active Round 2 directory, unfinished Round 1 jobs, unknown operation, or existing Runtime is being ignored.
- [ ] Verify current budget, snapshot, Best/Frontier projections, and immutable Runtime 2 binding from the Store and saved artifacts.
- [ ] Record current bridge assignment status without emitting credentials.

**Verification:** A status JSON identifies all active work, budget, and Runtime ownership; no new Store records are created.

### Task 2: Acquire and Admit a Real TPU v6e Runtime

**Files:** Reuse `.artifacts/phase3-2026-11-12/keep_requesting_v6e.py`, `reconcile_allocation.py`, `setup_runtime_environment.py`, `probe_isolated.py`, `admit_runtime3.py`, and `runtime_readmission.py`.

- [ ] If no Runtime exists, use the persisted allocation intent and continued allocation, querying before each POST; stop on `allocated` or reconciliation-required status. The user's later explicit instruction on 2026-11-13, “请你继续，一直申请”, supersedes the attachment's earlier bounded allocation restriction. Keep the same notebook identity, a 30-second interval after each response, and no overlapping loops.
- [ ] Read the real Runtime environment and require TPU v6e/v6 lite, device/process counts, Python/JAX/jaxlib/libtpu/TensorFlow/xprof, worker identity, and a synchronized minimal JAX result.
- [ ] Create `colab-config-runtime3.json`, a fresh remote spool/nonce, immutable Runtime 3 observation, and new Context/Run jobs for A baseline, delayed-store-wait parent, original B baseline confirmation control, and both negative controls.
- [ ] Do not write `active-runtime-binding.json` until all five prerequisite Gates pass. B control retains the original B Candidate/Strategy identity and is independently measured; do not relabel A's result as B.

**Verification:** Saved environment/probe/config/job records agree on Runtime ID and generation; old Runtime 2 records and bytes are unchanged.

### Task 3: Revalidate Baseline, Candidate, and Oracle Negatives

**Files:** Reuse `advance_runtime2.py --runtime3`, `real_round_aux.py`, the existing real backend/evaluator, and Runtime 3 job records.

- [ ] Query or recover each stable operation before submitting; execute generate/compile/correctness for negatives and all five stages for A/B parents.
- [ ] Require the three frozen correctness cases, exact cache bytes, input-cache contract, and original attention tolerance.
- [ ] Require both negatives to classify `incorrect` with no benchmark/profile and no promotion.
- [ ] Verify raw receipts, bundle identity, loaded module identity, source/config/build digests, and evidence closure.
- [ ] Persist the new Runtime binding only after all gates pass; preserve old Best and histories.

**Verification:** `require_gates` passes against Runtime 3, with five new Run results in appropriate new Contexts and no old result relabeling.

### Task 4: Run Controlled ABBA Confirmation

**Files:** Reuse `prepare_confirmation_round.py`, `run_confirmation_round.py`, `verify_real_round.py`, and the existing Controller.

- [ ] Prepare a control baseline and unchanged delayed-store-wait treatment using the same new Runtime, workload, protocol, and source identities.
- [ ] Execute the fixed ABBA order (baseline, candidate, candidate, baseline) or the persisted reversed order, one device job at a time.
- [ ] Preserve all 20-sample raw benchmark arrays, statistics, program/prepare/attention Profile data, operation IDs, and order timestamps.
- [ ] Freeze `p35-confirmation-policy.json` as a Store policy Decision before confirmation Intake. Independently assess host/program: both paired median reductions at least 2%, treatment Run medians strictly below both controls, and within-role Run-median spread no more than 5%. Excess spread is inconclusive; no CI/significance claim. These are new engineering confirmation criteria, not modifications to `consider`.
- [ ] Compare paired/run-level medians without treating within-Run samples as independent experiments. Publish raw rows, recomputed statistics and explicit limits to the original ArtifactStore, a Task note and A/B Strategy association notes so the next complete histories contain the conclusion.
- [ ] Do not alter the original Round 1 promotion or delete any result.

**Verification:** ABBA completion marker, receipt closure, independent Run IDs, budget charge, and stability classification are all persisted and locally verifiable.

### Task 5: Prepare and Author Round 2 from New Memory

**Files:** Reuse `prepare_real_round.py 2`, the original Analyzer session protocol, `real-round-2/`, and current Knowledge/Memory.

- [ ] Read the newest complete Snapshot, all strategy status cards, and complete A/B histories including every Round 1 failure, slower result, profile, decision, and ABBA conclusion.
- [ ] Create a new AnalysisInputManifest and current-conversation Analyzer session with source, history, Profile, methods, and mathematical evidence.
- [ ] Produce at least two distinct Plans per participating strategy where the evidence supports them; Plans must cite Round 1 outcomes and differ from the first-round variants.
- [ ] Persist the Analyzer exchanges, manifest digest, PlanSet publication, and review result before Intake.

**Verification:** Round 2 manifest includes Round 1 completion and ABBA evidence; changed Plan mechanisms and source references are reviewable and no winner is preselected by the model.

### Task 6: Execute Round 2 and Update Memory

**Files:** Reuse `run_real_round.py 2`, existing Controller/ColabBackend/Evaluator, `verify_real_round.py`, and Store projections.

- [ ] Run at least one new real Candidate per accepted Plan under the remaining budget; preserve failed, deferred, repair, and slower outcomes.
- [ ] Validate correctness, benchmark, Profile, source identity, and receipt closure before `consider`.
- [ ] Create the completed Round 2 Snapshot and verify Branch Best, Task Best, Frontier, latest attempt, last correct, current parent, paused, and deferred projections.
- [ ] If any operation becomes unknown, stop dependent jobs and recover by lookup; do not resubmit blindly.

**Verification:** Round 2 Controller journal is terminal, all accepted jobs have complete results, and Memory history contains the new evidence and decisions.

### Task 7: Final Validation and Reporting

**Files:** Update `knowledge-site/manifests/phase3-2026-11-12/implementation-report.md`, `docs/phase3/phase3-optimization-report.md`, test environment/links/changes inventories, and the authorized weekly record.

- [ ] Run the appropriate local regression suite and targeted read-only evidence verification after any code changes.
- [ ] Reconcile real evidence against P3.5 checklist; distinguish `P3.5 System Workflow Validated, Performance Improvement Not Confirmed` from a verified speedup.
- [ ] Record remaining limitations, current Runtime status, all raw evidence paths, and exact test counts. Do not claim Git cleanliness or a commit.

**Verification:** Report has no stale “Round 1 not executed” or “Runtime continuously requesting” claims; all relative links resolve; protected Phase 2 modules remain unchanged.
