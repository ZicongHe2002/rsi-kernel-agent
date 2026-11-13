# Phase 2C independent code review

Review date: 2026-11-11 (Asia/Shanghai). Reviewer: independent `/root/phase2c_review` worker, using the conversation model. Status: final code, regression and actual model-evaluation review complete.

## Scope and method

Read the exact request in `sources/phase2c-request.txt`, the implementation plan, the requesting-code-review skill and reviewer template, all current Profiler source files, and the associated input/profile/math/Analyzer/integration/evaluation tests. Examined protected Phase 2A/2B interfaces read-only. Git CLI was unavailable; the supplied base was `260de63fb57f5f494326936cdfbead188e10df01`. All 10 files in `protected-baseline.json` matched their SHA-256 baselines during review.

Reproductions used the installed Python interpreter and temporary synthetic SQLite/artifact stores. No dependency/skill installation, credentials, external kernel repository changes, Colab access, or TPU execution occurred. Source review was read-only; this report is the only reviewer-authored workspace file.

## Strengths

- Input capture binds independent strategy/run/parent/context records, complete Memory snapshots, source/config artifacts and pinned Knowledge captures without creating another experiment database.
- Normalization preserves missing values, distinguishes clock domains and merges overlapping intervals instead of double counting nested program/kernel events.
- Four small executable mathematical models record formulas, units, assumptions, omitted costs and explicit prediction classification, with hand-calculated tests.
- Tool dispatch is allowlisted and offline. Conversation packet/response exchange is distinct from deterministic test replay.
- Repaired publication now reconstructs the entire accepted exchange against the reloaded manifest before writing immutable compatible records. Best is not updated.

## Open code findings

None in the reviewed scope. The four actual model samples were reviewed separately from deterministic regression tests; their limited acceptance result is described below.

## Findings repaired during review

All items below were independently reproduced before a fix; severity was Important.

| Finding | Initial evidence | Fix observed / closure evidence |
| --- | --- | --- |
| Captured MethodRecord/History predicates became observed facts | Actual captured catalog kinds were not recognized; both printed `observed_fact` | Correct kind mapping in `outputs._predicate`; focused Analyzer regression passes |
| Caller could modify reviewed observations/calculations before publication | Persisted `observed_fact: Measured TPU speedup was 500x` after editing only result.analysis | Full deterministic exchange replay and canonical result comparison; integration tamper regressions pass |
| Replay trusted caller manifest body despite checking its artifact | Forged `AnalysisInputManifest` body changed timing 10 to 999 while retaining original artifact ref; published as observed fact | Replay now uses reloaded `pinned`; independent closure printed `FORGED_BODY_REJECTED` |
| Failed/refuted Evidence became observed fact | Available Evidence with request failed/claim refuted and value 999 passed predicate promotion | Capture qualification plus predicate status check; focused regression passes |
| Benchmark scalar disagreed with its timing evidence | Confirmed benchmark 999 ms referenced 10 ms timing and was accepted as an observed value | Capture now requires a matching supported/completed timing record; derived-timing compatibility was also restored and checked |
| Missing raw Profile reported available at outer level | Reports contained only unavailable placeholder, outer availability was available | Aggregate states from normalized quality and bundle availability; integration regression passes |
| Rejected response poisoned direct Analyzer replay | Invalid final raised, valid retry completed, publication then failed replaying rejected final | Structural validation precedes accepted transcript mutation; independent closure printed `VALID_RETRY_PUBLICATION_OK` |
| Valid long strategy ID could not publish | 100-character Strategy ID passed input/analysis then failed generated bundle ID validation | Bundle IDs hash strategy ID; independent closure printed `VALID_100CHAR_STRATEGY_PUBLICATION_OK` |
| Fixed-suite answer pass count hid invalid Analyzer outputs | Correct answers produced 10/10 even with all Plan parents and references invalid | New `overall_accepted` separately gates applicable metrics; independent closure returned false with binding/reference_validity/semantic_legality failures |
| Benchmark qualification rejected supported derived timing | Existing store accepted supported trace-derived 10 ms evaluation while capture marked unavailable | Capture accepts the existing measured_timing/trace_derived/derived_measurement timing classes; independent checks returned available for both derived classes |
| Legacy Profile adapter promoted predictions, including through derived input ancestry | Direct model_predicted evidence and a derived_measurement wrapper initially normalized as measured available events | Adapter rejects direct non-measurement kinds; capture now recursively qualifies prediction/compiler/source/history ancestry. Independent two-wrapper closure returned Profile unavailable, leaf Evidence unavailable, no measured events, and identical offline manifest replay |

## Verification evidence

- Installed interpreter: `knowledge-site/.venv-mcp/Scripts/python.exe`, invoked with `-B`; sandbox execution initially failed at the uv launcher, and scoped automatic approval allowed the same installed executable. No environment changes were made.
- `-m unittest discover -s tests -p test_analyzer.py -v`: **11 tests passed**, exit 0.
- `-m unittest discover -s tests -p test_phase2c_integration.py -v`: **4 tests passed**, exit 0.
- Independent synthetic closure script: forged in-memory body rejected; valid retry after malformed response published; valid 100-character strategy ID published; exit 0.
- Independent baseline comparison: **10 checked, 0 mismatches**.
- Re-read root's refreshed complete `tests.txt`: **162 tests run, 161 passed, 1 skipped, 52.115 seconds**; `test-environment.json` records exit 0. The one skipped symlink-object check requires Windows symlink permission and was not silently claimed passing.
- Re-read refreshed `existing-mcp-tests.txt`: **5 tests passed, 0.733 seconds**; `test-environment.json` records exit 0. Root ran these full suites; this reviewer independently inspected their saved results rather than repeating an unchanged full suite.
- Final protected baseline comparison remains **10 checked, 0 mismatches**.
- Reviewed `docs/phase2c-profiler-analyzer.md`: it documents actual interfaces, legacy parser dependency limits, qualified model estimates, single-writer session/retry boundaries, non-atomic publication completion markers, evaluation limitations, and a proposed-only Phase 2D direction.
- Final ancestry closure used `model_predicted -> derived_measurement -> trace_derived -> ProfileBundle`: `TRANSITIVE_PREDICTION_PROFILE unavailable`, `LEAF_EVIDENCE unavailable`, `MEASURED_EVENTS_PRESENT False`, `PINNED_REPLAY_EQUAL True`; exit 0. The full logs above were refreshed after this fix.

## Actual model-evaluation review

Independently recomputed every score in `evaluation-results.json` from each arm's exported packet, result and case answers, using the current evaluator and pinned offline artifacts. Every computed field matched the saved result. Independently replayed every accepted Analyzer transcript; all four reconstructed results were canonically identical. The frozen dataset SHA-256 matched `evaluation-protocol.json`. This verification exited 0 and did not modify the samples.

| Configuration | Case-answer rubrics | Overall accepted | Actual model-response events | Tool calls | Successful tool replay | Assertion support proxy |
| --- | --- | --- | --- | --- | --- | --- |
| base | 10/10 | No | 3 | 0 | N/A, 0/0 | 12/12 assertions not mechanically supported |
| skills | 9/10 | No | 3 | 0 | N/A, 0/0 | 15/15 assertions not mechanically supported |
| tools | 9/10 | No | 4 | 12 | 11/11 successful calls; one explicit rejected call | 21/21 assertions not mechanically supported |
| review | 10/10 | Yes | 5 | 12 | 12/12 | N/A, 0/0 retained assertions |

The skills/tools rubric miss is the conclusion category for `faster-unknown-cause`. The tools arm also attempted a calculation mixing A and B references; the tool explicitly rejected it. The successful-call replay ratio does not erase that failure, and overall acceptance correctly includes `tool_execution`.

For base, skills and review, compared `case_answers-initial.json` to the final answer: the only change was removal of a cross-route citation in `multi-strategy-plans:refs`. Verdicts, categories, controls, numeric outputs, reasons and Plans were preserved. Original responses and protocol notes remain available. These structural corrections counted toward actual response events, all within the six-response ceiling. The model responses were each within 60,000 canonical JSON characters, and all arms remained within 12 tool calls. Initial scores remain separate in `evaluation-initial-results.json`.

The support proxy must not be read as a semantic falsehood rate. It accepts only canonical exact predicates/rendering, so the base arm's 100% means its free prose was not machine-certified, not that all of it was false. The review result retained **17 hypotheses and 3 unknowns, with no asserted facts**: all 17 proposed assertions were downgraded. Many predicates incorrectly prepend `value` even though traversal already starts at the reference value; other assertions have no predicate. Its `0/0` is **N/A and absent verified-assertion coverage**, not a zero unsupported-claim rate or proof of comprehensive fact extraction. The frozen sample was not repaired to improve that score.

Independently read all ten review-case answers and the four final Plans. The answers correctly distinguish high DMA count from critical path, candidate mismatch, tile/dtype confounding, condition-specific historical failure, hypothetical 14-second pipeline prediction, retention of both strategies, unknown causal mechanism behind faster timing, stale revisions, missing versus zero metrics, and the 12-microsecond interval union within one device/clock. Numeric units, synthetic provenance and causal qualifications are explicit.

The review PlanSet has substantive, not just textual, diversity:

- **A-P1 / A-parent:** group adjacent output-column tiles to share A loads, trading additional accumulators for reuse while preserving dtype and tile.
- **A-P2 / A-parent:** double-buffer and prefetch reduction operands with explicit producer/consumer synchronization, conditional on actual runtime support.
- **B-P1 / B-parent:** pack the right operand with an explicit logical mapping, including conversion/storage cost and invalidation controls.
- **B-P2 / B-parent:** isolate tile-size changes (8 control, 16 variant, 32 conditional) while holding dtype, layout and load policy fixed.

All four include candidate variants, numerical/tail/alias or lifetime correctness checks, matched-condition timing, resource risks and unknowns. None promises speedup, selects a winner or treats the hypothetical pipeline cost as the workload's measured time. These are useful exploration proposals; executable transformation correctness still requires later validation.

This is one grouped synthetic sample per arm, with shared system instructions and equal action/output ceilings. Exact provider model version and token accounting are unavailable. The observed comparison establishes a local bounded workflow acceptance result, not statistical evidence that skills, tools or review generally improve model quality.

## Declined to judge

- Actual TPU throughput, kernel correctness or speedup: explicitly reserved for Phase 3; this review uses synthetic/offline evidence.
- Multiple concurrent writers for the file-based exchange: current transport explicitly documents single-writer operation; no concurrent deployment is claimed.
- General semantic equivalence of arbitrary natural-language Plans: string diversity remains a limited mechanical check. The four concrete final review Plans were independently inspected as described above.
- Authenticity of external Knowledge producer claims beyond the recorded revision/content capture: capture/replay can pin data and expose freshness, but cannot prove the external author or underlying experiment was truthful.
- Statistical ranking of the four configurations: one grouped sample with unavailable exact model/token accounting cannot support a general superiority claim.

## Assessment

**Ready for local Phase 2C acceptance with the documented limitations.** All 11 Important findings were repaired and independently checked through their regression paths. No Critical issue or unresolved Important code finding remains in the reviewed scope. Refreshed regression logs pass with one disclosed platform skip; the actual review arm passes the fixed case/output acceptance checks and produces four useful, independently bound exploration Plans. This conclusion does not claim TPU validation, verified free-prose entailment, complete assertion coverage or statistically superior model quality. Stop after Phase 2C for user review.
