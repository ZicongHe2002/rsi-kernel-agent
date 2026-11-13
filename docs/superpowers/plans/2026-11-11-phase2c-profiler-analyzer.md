# Phase 2C Profiler / Analyzer Implementation Plan

> **For agentic workers:** Execute the tasks below with native workers and independent review. The named superpowers execution subskills are not installed; ordinary tools provide the same task boundaries. User authorization already covers implementation.

**Goal:** Reproducible evidence-driven, tool-using analysis that proposes multiple Plans for each participating strategy.

**Architecture:** Add a `profiler` package under the existing RSI package. Preserve schema v1: immutable artifacts hold the multi-strategy manifest and rich PlanSet; existing run-scoped AnalysisBundle and Plan/parent Decisions retain compatibility. A bounded single Analyzer requests reasoning from an injected model transport; Python validates references, normalizes traces, executes math, and records tool exchanges.

**Tech Stack:** Existing Python standard library, ExperimentStore/ArtifactStore, MemoryReader, KnowledgeClient. No dependency installs.

**Spec:** [Exact user request](../../../knowledge-site/manifests/phase2c-2026-11-11/sources/phase2c-request.txt).

## Global Constraints

- Phase 2C only; do not start 2D or 3; no kernels, TPU, Colab CLI, external-repository writes, auth or account changes.
- Preserve 2A/2B contracts, historical reports and schema. No second database or replacement MCP.
- Synthetic inputs/results remain synthetic. Missing metrics are null, predictions are not measurements.
- Same model and action/output budget across the four evaluation configurations; disclose limits of session-model evaluation.
- No automatic winner or best update. Immutable exact identities and honest consistency.

## Review Focus

1. Transitive cross-strategy evidence laundering: reject incompatible measured/trace input ancestry.
2. Mutable knowledge and opaque BOM revisions: pin content artifacts separately from source revision.
3. Partial/missing evidence: retain explicit issues; do not synthesize timing or unavailable counters.
4. Nested events, clock domains and unit conversions: same-domain interval union, no program/kernel double count.
5. Model output unsupported prose and partial writes: validate schemas and downgrade unsupported claims; results use immutable artifacts before explicit persistence.

## Task 1: Inputs and immutable manifest

Create `src/rsi_kernel_agent/profiler/inputs.py`, `tests/test_analysis_inputs.py`, `tests/phase2c_fixtures.py`.
Interfaces: `AnalysisRequest.from_dict/to_dict`, async `capture_inputs(store, knowledge, request)`, `load_manifest(store, artifact_ref)`, immutable `AnalysisInputManifest.to_dict`.

- [x] Test and observe rejection of wrong parent/run/context/profile, transitive cross-strategy evidence, mismatched source/config digest, malformed schema and incomplete snapshots.
- [x] Implement pinned source/config artifacts, independent full Memory histories, Knowledge revision/status/content pins, environment/tool versions, and a typed reference catalog.
- [x] Test replay after Knowledge edits, missing/corrupt artifacts, detached returned data, source revision drift and no best mutation.

## Task 2: Reuse Profile parser

Create `profiler/profiles.py`, `tests/test_profiles.py`, and `manifests/phase2c-2026-11-11/parser-audit.md`.
Existing `tools/tpu_duration_profile.py::read_profile` is audited read-only; normalize its saved output. Optional injected offline reader supports installed producer dependencies without importing capture/runtime.

- [x] Test explicit binding, raw sample statistics, malformed durations, missing metrics, nested/overlap union and separate device clocks; observe RED.
- [x] Implement `normalize_duration_report(report, *, binding, artifact_refs)` and explicit normalized event input for synthetic/saved DMA/wait/compiler fields.
- [x] Verify target module/custom-call separation; DMA/waits/LLO/counters absent from legacy output remain unavailable.

## Task 3: Executable mathematical tools

Create `profiler/models.py`, `tests/test_models.py`.
Interface: `calculate(model, inputs, *, evidence_refs=(), assumptions=()) -> dict` with four named categories: compute_memory, tile, live_memory, pipeline.

- [x] Write hand-derived numeric/units/boundary tests and observe RED.
- [x] Record formula/model revision, input units, assumptions, omitted costs, evidence refs, outputs and prediction classification. Hardware constants optional and explicitly sourced/assumed.
- [x] Verify zero/unknown/nonfinite/negative/boolean behavior, padding, buffer coexistence and ideal fill/steady/drain overlap.

## Task 4: Single Analyzer and compatible output

Create `profiler/analyzer.py`, `profiler/tools.py`, `profiler/outputs.py`, tests for each public behavior.
Interfaces: `Analyzer(manifest, store, ...).run(reasoner)`; reasoner consumes packet and returns tool calls or final structured proposal. An exchange transport permits host/session model use; replay is a separate test transport.

- [x] Test actual deterministic tool dispatch and captured results, model-selected tools, full A/B history and pinned source/method inspection.
- [x] Implement bounded reasoning loop with explicit observation, code structure, hypothesis, plan and evidence-review phases in its prompt/protocol.
- [x] Validate 2+ materially distinct Plans per strategy, exact parent binding, references and calculations; downgrade unsupported claims without discarding legal exploration.
- [x] Persist aggregate artifact and existing per-run AnalysisBundles/Plans/parent Decisions using explicit idempotent keys; never call consider/best mutation.

## Task 5: Fixed evaluation and integration

Create `tests/fixtures/analyzer_cases.json`, `profiler/evaluation.py`, `tests/test_analyzer_evaluation.py`, evaluation runner/artifacts.

- [x] Encode the ten user-specified failure cases with independent expected properties and synthetic provenance.
- [x] Measure reference validity, unsupported assertions, uncertainty, legality, diversity/duplication, parent/strategy binding, and tool replay; not length/self-rating.
- [x] Compare base, skills, tools and review configurations on identical cases/model/action/output budget. Preserve raw responses and tool calls; report capability regression separately from actual model results.
- [x] Run end-to-end source + profiles + Benchmark + full A/B histories + Knowledge + math -> multi-Plan result. Export concrete manifest and bundle examples.

## Task 6: Review, final checks and docs

- [x] Fresh independent reviewer examines code, tests and actual spec; reproduce and fix Important/Critical findings with regression tests.
- [x] Run full root unittest suite and affected existing MCP tests as appropriate. Preserve command, exit status and complete logs.
- [x] Verify protected hashes, relative links, reproducible examples and evaluation results. Update roadmap/index, interface guide, progress/final report and current weekly completion record.
- [x] Stop at Phase 2C review. No Git commit requested.

Primary test command: `& '.\knowledge-site\.venv-mcp\Scripts\python.exe' -B -m unittest discover -s tests -v`.
