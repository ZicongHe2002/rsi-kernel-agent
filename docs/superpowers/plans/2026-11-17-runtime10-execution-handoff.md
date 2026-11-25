# Runtime10 Execution Handoff Plan

> **2026-11-20 implementation update:** This is a historical Runtime10 handoff, not permission to resume its operations. Current prepared routes require v2 CPU candidate checks, not only Oracle reference success. New formal bindings with `runtime_delta=1` read an explicit delta projection whose digest is distinct from the full immutable manifest; archived research is available on demand, and delta delivery never claims full-history rereading. New `candidate.v1` Contexts use one clean process per Run with continuous stages, same-source CPU gating at Intake and before budget reservation, and per-stage receipts. Old preparation/check records remain readable but cannot authorize new execution without current candidate numerics. See the [current six-step record](../../phase3/next-stage-completion-2026-11-16.md#2026-11-20-六步闭环实现与验证); the following dated requirements and test counts describe earlier versions.

> **2026-11-18 execution clarification:** Independent A/B/C/D analysis means separate model conversation inputs, not separate TPU allocations, Colab kernels or notebooks. Prepare route research, candidate drafts and CPU-verifiable checks locally; use one available TPU Runtime for the whole batch's necessary admission/parent measurements and all eligible candidates, serially. Keep that Runtime and its pinned control kernel across route changes. Separate notebook cells may organize calls, but do not provide model-context isolation or independent Python namespaces. Preserve distinct candidate source paths, operation/Run identities and results through the existing executor; no kernel/runtime restart is required merely to change routes. Device loss is a recovery event, not a planned route boundary.
>
> The stages below retain the historical Runtime10 handoff. Formal publication still requires the four admission gates. The November 18 software path now supports offline research and candidate preparation separately, followed by new Context/parent/manifest binding and actual formal review; usage and verification boundaries are below. An expired Runtime is not reusable, and no new hardware optimization feedback is claimed. One batch is one design-and-feedback round; later rounds incorporate feedback and may reuse the same live Runtime.

## Offline preparation before allocation

The `preparation-*` commands use the original database and ArtifactStore. They do not allocate a Runtime, change parent selections, publish executable Plans, reserve optimization budget or create Candidate/Run records. A `--historical-context` is an immutable reference, not a claim that its device is available.

Commands share `PYTHONPATH=src knowledge-site/.venv-mcp/Scripts/python.exe -B -X utf8 -m rsi_kernel_agent.workflow --db <existing-db> --artifacts <existing-artifact-root>`; each command also offers `--help`.

| Stage | Command and inputs | Output / boundary |
| --- | --- | --- |
| Freeze offline inputs | `preparation-begin --task <id> --historical-context <id> --preparation-key <key> --assessments <json> --parents <json> --website knowledge-site [--knowledge-refs <json>]` | All-route assessments and one snapshot; each route receives only its own full history and explicit dependencies |
| Open each independent route | `preparation-route --preparation <id> --strategy <id> --session <new-directory>` | Standard conversation packet; pages must actually be delivered/read, not copied from another session |
| Submit local analysis | `respond --session <directory> --response <json>` | Tool responses or `draft_complete`, never a publication; no `--key` required for offline drafts |
| Check and seal candidates | `preparation-finish --preparation <id> --strategy <id> --session <directory> --drafts <json> [--cpu-python <existing-compatible-python>]` | Immutable route drafts and checks; failure does not close the route; completed replay reuses saved checks |
| Seal whole preparation | `preparation-complete --preparation <id>` | Non-executable preparation completion, not `design_complete` |
| Bind a measured Runtime | Local helper `prepare_design.py --generation <runtimeN> --round <new-round> --preparation <id>` | Original four gates plus exact parent measurements, then a new formal batch; no rewriting old publication Contexts |
| Review formal inputs | Existing `design-route`, `respond --key <new-key>`, `design-finish`, `design-complete` | Current input includes the route's draft/source/checks and Context changes; new responses explicitly retain/revise/defer every draft Plan |
| Export checked implementations | `preparation-variants --batch <id> [--revisions <json>] [--checks-dir <directory>] [--cpu-python <python>]` | Variants keyed by published Plan record ID, bound to the one formal Runtime; compatible with the existing batch executor |

`assessments`, `parents` and per-route `knowledge-refs` use the existing design JSON shapes. Offline `drafts` maps each local proposal ID to every proposed variant in label order. Each variant contains exactly `label`, `source_ref`, `config_ref`, `build_ref`, `source_bundle_ref`, `priority`, `cost`; do not supply `device`. No-Plan outcomes use `{}` and retain their specific reason. All artifact references are `{digest,size}` in the existing ArtifactStore.

Static checks verify source/config/build/Oracle/trusted-file identity, complete variant coverage, syntax and frozen workload restrictions without importing candidate code. Optional CPU reference execution uses the pinned runner, a separate workspace and `JAX_PLATFORMS=cpu`; missing interpreter/dependencies is `unavailable`, a submitted local execution failure or timeout is `failed`. An unresolved reference failure is not cleared by omitting `--cpu-python` or changing scheduling metadata. CPU reference success is **not** candidate numerical correctness; current TPU Pallas candidates remain `candidate_numerics=unavailable`, requiring the original TPU correctness gate. The current local Python 3.13 environment has no JAX/NumPy; nothing is installed automatically.

Formal responses with a prepared draft add `draft_review` with `preparation_id`, `route_result_digest`, and `plan_decisions` entries `{draft_plan_id,status,final_plan_ids,reason}`. Status is `retained`, `revised` or `deferred`; retained source/config/labels cannot change, and deferral must state real conditions/evidence, not a predicted-speed ranking. Revised or new candidates require new local checks against the formal result. For `preparation-variants --revisions`, provide `publication_id -> {proposal_id: [offline-variant,...]}` for each revised route's complete candidate set, including unchanged retained variants. The resulting export uses globally unique published Plan IDs, so independent routes may both name a draft `P1`.

The native page helper also accepts an offline preparation ID in `prepare --batch`; its `submit --drafts <json> [--cpu-python <python>]` seals offline results without a publication key. Formal native sessions retain the original publication and route-close behavior. All formal pages must still be read: preparation reuses research and implementations, not fabricated reading coverage or an old model response under a new Context.

The normal execution path remains `design_complete → all Plans accepted by Intake → exact budget grant/checkpoint → one serial Runtime`. CPU fixtures and local CLI regression runs do not establish TPU results. Final November 18 local verification is complete: root 553 tests (550 passed, 3 platform skips), auxiliary 383/383, and preparation/helper checks 62/62, all exit 0. Both complete suites match the same 267-file source inventory; the 272-file inventory including five changed helpers is also unchanged. Evidence is saved under `.artifacts/next-stage-completion-2026-11-16/completion/offline-preparation-20261118-final/`, separately from the historical `pinned-kernel-final` gate. The three final review findings were independently reverified as addressed. Actual A/B/D research, candidate numerical validation, TPU allocation and optimization execution were not started by this software acceptance.

> **For agentic workers:** Execute these checked stages through the existing helpers and production APIs. The parent coordinator retains execution ownership; this document does not itself apply a grant or submit an experiment.

**Goal:** Run the completed independent A/B/D design on a newly measured Runtime10 Context while preserving all historical identities and cumulative spending.

**Architecture:** Keep Runtime9's frozen batch as unexecuted preparation. After Runtime10 admission, freeze a new all-route batch and complete fresh route analysis, then use original Intake, Controller, ColabBackend and fixed Evaluator through the completed `runtime10_batch_executor.py` artifact wrapper. Extend the existing Task budget through its cumulative grant chain only after the final candidate costs are known.

**Tech Stack:** Existing Python helpers, SQLite ExperimentStore, v2 AnalysisRequest, conversation_session, Colab TPU v6e, JAX 0.8.1 protocol.

**Spec:** `.artifacts/next-stage-completion-2026-11-16/request.txt`; current continuing TPU authorization is archived in that directory's `tpu-authorization.md`. Parent instruction on 2026-11-17: do not read Runtime9 model pages; perform fresh A/B/D analysis after Runtime10 admission.

## Verified constraints

- Read-only SQLite audit on 2026-11-17 found journal `wf-state:54d1478defde5639f8eb82791ef1b8e9:247`, spent `{executions:17,tokens:0,time_s:30600.0}`, limits `{executions:18,time_s:32400,tokens:100000,max_concurrency:1,max_repairs:1,max_retries:1,weights:{}}`; all 23 Controller jobs are terminal (15 completed, 8 cancelled).
- The final existing grant is sequence 1, key `p35-runtime7-b2-after-a2-diagnostic-v1`, applied revision 210. Its declared next batch is `real-runtime7-b2-continuation-round:2`, whose two reserved jobs completed with confirmed real correct/valid results.
- Runtime9's four prerequisite results are real, but its assignment disappeared. They remain Runtime9 evidence. Runtime8 queued records and Runtime4's unknown historical operation retain their original identities.
- `budget._grant_shape` and `budget._milestone` passed against the original read-only Store using snapshot version 29 and the two completed B2 runs. This checked fixed records and bound small receipts; it did not recompute historical NPZ/XPlane measurements. This was an in-memory conditional example, not a grant. Evidence: `runtime10-execution-handoff-state.json` and `runtime10-budget-preflight.json` in the completion artifact directory.

## Why Runtime10 needs a new design batch

`intake_publication(store, publication_id, round_id, variant_specs, knowledge=None, *, retry_key=None)` has no Context override (`workflow/intake.py:95`). It takes `context_id` and `fixed_contract` from the published frozen manifest (`intake.py:188`, `:199`, `:230`, `:235`). `require_design_complete` binds a v2 publication to its original batch and round (`workflow/design.py:198`). `make_request` rechecks the exact immutable Context and requires variant.device to equal its runtime_session_id (`workflow/execution.py:150`, `:165`); ColabBackend independently checks its runtime (`workflow/colab.py:38`).

Therefore neither changing a variant's device, using a new Intake retry key, nor retaining an old round with a new Context migrates a Runtime9 Plan. A v2 retry is only an identical request after recoverable prerequisites return; changed knowledge pins require new analysis (`intake.py:108`). Old Plan/publication IDs remain immutable. The minimal valid transition is a new Runtime10 batch/manifest and fresh publication, with exact parent versions retained and measured again. No implementation change is needed for this rule.

## Stage 1: Allocate and admit Runtime10

- [x] Allocate serially with the existing v6e helper in a new `runtime10-allocation/` directory. Runtime10 was confirmed at 2026-11-17 04:17:05 UTC; see `runtime10-allocation/allocation-receipt-runtime10.json`. The allocator has exited. Retain this allocation under the user's latest instruction; do not start a second allocator.
- [ ] On allocation confirmation, the single admission owner runs `.artifacts/next-stage-completion-2026-11-16/admit_allocated_runtime.py --intent <runtime10-intent> --generation runtime10`. It reuses `prepare_runtime.py`, `preview_runtime_admission.make_preview(...)`, and `runtime8_executor.advance(..., generation='runtime10')`.
- [ ] Require all four fresh Runtime10 gates and `runtime10/admission-ready.json`: wrong-attention and wrong-cache controls must fail correctness; exact A/B parents must pass original correctness/benchmark/profile and independent local verification. A/B share the new measured Context. The four prerequisites cost 4 executions/7200 declared seconds in the existing separate `prerequisite_reservation.v1` domain; they do not change the optimization journal or count as D exploration feedback.

## Stage 2: Freeze and finish the new independent batch

- [ ] Run `prepare_design.py --generation runtime10 --round next-stage-runtime10-independent:1`. It verifies four gates, exact historical parents, unchanged journal, all-route assessments and knowledge pins, then calls `begin_design_round(store, knowledge, task_id, context_id, round_id, assessments, *, parents=None, knowledge_refs=None)` (`workflow/design.py:41`).
- [ ] Use the returned batch's actual order. For each pending A/B/D route, use `native_route_session.py prepare/page/coverage/submit` in a distinct fresh model context. Read every page of that route's full captured history and current inputs; publish the actual validated model response. `submit` calls `publish_result(...)` and `finish_route_design(...)` only after exact page replay and coverage checks (`native_route_session.py:140`, `:193`).
- [ ] Retain every valid Plan and variant. A route without a Plan needs an explicit supported outcome; do not invent one to reduce cost. Oracle controls remain a non-optimization terminal assessment. Runtime9 remains frozen unexecuted preparation, with no model-reading or design-complete claim.
- [ ] Call `complete_design_round(store, batch_id)` only after every route closes (`workflow/design.py:180`). New evidence that changes hypotheses belongs in the next batch; implementation repairs remain within the published Plan.

## Stage 3: Finalize implementations and grant preview

- [ ] Materialize each published variant's immutable source/config/build/source-bundle refs and accurate published label; device must be Runtime10's actual runtime ID. Use `validate_variant(..., provenance='real')`; retain exact Plan/parent binding. Fix all candidate cost bounds before grant application.
- [ ] Ensure the batch includes at least one new D candidate and at least one old-route candidate, as the user's minimum real acceptance requires. If A and B both publish valid candidates, preserve both. Do not discard reasonable published variants based on predicted speed.
- [ ] Let `C` be the exact sum of **all** final variants' cost dictionaries. For conventional 1 execution/1800 seconds per variant and N variants, `C={executions:N,tokens:0,time_s:1800*N}`. The minimal old+D batch has N=2; one A/B/D candidate each has N=3. Counts are examples until final publications exist.
- [ ] Re-read the live journal immediately before applying the grant, under the existing Task lock. Require its latest prior grant and all old jobs to match the audited chain; no active/evaluating/blocked job may remain. Create a fresh snapshot containing that exact current journal state, or use an already verified snapshot containing it. Never reuse the 2026-11-17 preflight revision blindly. Run `runtime10_batch_executor.freeze(store, spec, variants, grant, runtime_config)` to validate and seal the exact final design, costs and pre-grant state without applying the grant.

## Stage 4: Append one cumulative grant, then Intake

Use `Controller.extend_budget(grant, key)` (`workflow/controller.py:57`), not a new Controller with edited limits. The grant has exactly these fields (`workflow/budget.py:31`):

```python
grant = {
    'schema_version': 1, 'task_id': 'mla-decode-v1',
    'expected_revision': before['revision'],
    'expected_state_digest': budget.digest(before),
    'old_limits': before['limits'], 'spent': before['spent'],
    'new_limits': {**before['limits'],
        'executions': before['limits']['executions'] + C['executions'],
        'time_s': before['limits']['time_s'] + C['time_s']},
    'next_batch': {'round_id': 'next-stage-runtime10-independent:1',
        'purpose': 'All completed independent Runtime10 A/B/D design variants', 'cost': C},
    'authorization': verified_archived_user_authorization,
    'milestone': {'snapshot': exact_current_snapshot,
        'rounds': [{'round_id': 'real-runtime7-b2-continuation-round:2',
            'context_id': 'runtime-context:b60862eb713e457a38d5a704bb3d7f1ceae68a118e919a326b0da13f50689b62',
            'run_ids': ['wf-r:fb51ab1de12a6f5fbb079485c3d73086',
                        'wf-r:bcde96344bd5eacd7e9b35468716156c']}],
        'successful_round_id': 'real-runtime7-b2-continuation-round:2'},
}
entry = controller.extend_budget(grant, 'next-stage-runtime10-independent-v1')
```

- `authorization` must retain `source`, exact `quote`, and verified `artifact_ref`. The existing continuation authorization passed the read-only preflight; any substituted latest authorization must first be archived and verified through the same mechanism.
- The immediately preceding grant's next_batch must be this milestone's successful round (`budget.py:69`). Runtime9/10 admission runs are outside the Controller optimization journal and cannot replace that successful round. Include **both** B2 runs, not just the faster one. Earlier A2/diagnostic milestones were consumed by the prior grant and cannot be reused.
- `next_batch.cost` equals the **increment**, not new remaining capacity (`budget.py:48`), and its tokens field must be zero under the current API. The fixed real variants use zero declared model tokens; reject a nonzero variant-token sum in this wrapper rather than silently altering it. Adding full cost C preserves the existing one-execution/1800-second reserve. For N=3: pre-grant 17/18 becomes 17/21, then 20/21 after three reservations. For N=2: 17/20 then 19/20. No grant changes spent; `Controller.step()` charges each attempt once and never refunds early failures (`scheduling.py:67`). Tokens, concurrency, retries, repairs and weights remain unchanged.
- This core API does not by itself bind the additional capacity to exactly the declared future job set. The execution wrapper must verify sum(final variant costs)==C, all job round/batch/context IDs, unchanged old jobs, and spent==checkpoint.spent+sum(new reserved costs). Do not consume the preserved reserve implicitly for repair/retry.
- [ ] After the grant and design_complete are verified, call `intake_publication` for every route publication with the **same** Runtime10 round. Persist and inspect every per-Plan accepted/waiting/rejected outcome; use identical retry inputs and unique retry keys only for recovered temporary prerequisites, within the existing cap of three.
- [ ] Call `controller.accept_intake(intake)` for each validated Intake. This is where new Candidate/Run records appear (`controller.py:97`); it does not execute TPU. Require all design-complete Plan IDs to be accepted before first step. `ready_design_jobs` enforces this complete Plan coverage (`design.py:220`).

## Stage 5: Execute and verify

- [ ] Use `ColabBackend(store, colab_remote(runtime10_config))` and the existing Controller without replacing limits. Freeze the checkpoint and exact allowed job IDs before stepping. The retained `max_concurrency=1` serializes the device; design-batch first-pass scheduling gives one eligible candidate per route before a second from the same route (`scheduling.py:105`).
- [ ] Before each step, verify live runtime identity and no unrelated nonterminal jobs. Advance bounded steps and archive state/receipts. Retain original operation IDs; submit only `definitely_not_started`, poll `running`, preserve `unknown` for reconciliation. Never resubmit old Runtime4/8 operations as Runtime10 work.
- [ ] Recompute correctness/NPZ and fixed timings via the existing local verifier, retain all route outcomes and exact parent comparison, then record exploration feedback through existing APIs. Same-batch multiple candidates count at most one route round. Do not claim 5–8 D rounds or stable end-to-end improvement from this first batch.

## Helper reuse boundaries and review focus

- `p35_continuation_round.py` is a useful pattern for bounded job/cost checks, but is **not** a drop-in executor: `validate_spec` requires the target Context equal its successful milestone Context and its B2 validator hardcodes historical counts; it also uses legacy `prepare_round`, whereas this Task now uses frozen v2 design. Do not weaken those old guards to run Runtime10.
- The completed wrapper is `.artifacts/next-stage-completion-2026-11-16/runtime10_batch_executor.py`; its Python API usage is in `runtime10-batch-executor.md` beside it. Use its `freeze(...)`, `await intake(..., retry_keys=None)`, and bounded `advance(..., steps=1, reconcile=False)` stages. It does not allocate a Runtime or apply a grant. `runtime10-review/binding-fix-review.md` records the closed binding review. The handoff was independently reviewed against the live Context/grant/API assumptions; no blocking sequencing error was found within that scope. [Review method, inputs, finding and limits](../../../.artifacts/next-stage-completion-2026-11-16/runtime10-handoff-review.md) are archived separately; the review did not repeat the wrapper code review or hardware verification.
- This planning work created only this document and two read-only audit artifacts. It did not create a Plan, Candidate, Run, grant, snapshot, TPU workload, or Controller instance. Allocation is a separately authorized following action.
