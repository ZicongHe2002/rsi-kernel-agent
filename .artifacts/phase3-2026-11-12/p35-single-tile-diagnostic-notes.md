# P3.5 single-tile zero-length diagnostic preparation

`p35_single_tile_diagnostic.py` is a pure local preparation helper. It produces detached Workload/EvaluationContext payloads, identical baseline/single-tile configurations, and two unadmitted run intents. It does not create Plans, Candidates, Runs, receipts, artifacts, reservations, or Store records, and it does not execute a kernel.

The archived original matrix exercises `decode-small` at capacity/block size 1024 with generated lengths, while `decode-boundary` has lengths `[0, 1, 129]` at capacity 256 and block size 128. Consequently the existing boundary run does not exercise the candidate's `capacity == block_size` specialization. The diagnostic uses only these two cases in an independent Workload:

| Case | Batch | Capacity | Block size | Lengths | Opaque NaN tail |
| --- | --- | --- | --- | --- | --- |
| decode-small | 1 | 1024 | 1024 | `[0]` | true |
| decode-boundary | 3 | 256 | 256 | `[0, 1, 129]` | true |

The original attention and full cache output shapes, BF16 dtype, reference tolerance, Oracle bundle, runner, timer, warmups, sample count, and Profile settings remain in the copied contract. The frozen runner already accepts case-level lengths and creates opaque NaN cache tails. The helper pins the existing baseline-repair-1 and round1-single-tile source digests and evaluates only the actual static specialization predicate from the archived candidate AST. This is static coverage evidence; it does not prove compiled execution or correctness.

The frozen entry returns attention and full cache only. **LSE is not exposed and is not tested.** Real correctness must preserve finite attention under the original allclose contract, byte-exact full cache including opaque NaN tails, and unchanged cache input. The runner and original verifier remain responsible for those checks.

Call `prepare_diagnostic(workload_payload, context_payload, configs, source_bytes)`. The first two arguments are detached dictionaries from the same original job contract. `configs` and `source_bytes` both have exactly `baseline` and `single_tile` keys; configs are decoded original config JSON and sources are the archived bytes. The result is JSON-compatible and includes `workload`, `context`, `configs`, `source_digests`, `branch_audit`, `output_contract`, `run_intents`, and `budget`. `validate_diagnostic(spec, configs)` can reject a modified preparation artifact before subsequent admission.

The source job's environment is copied, not reobserved by this helper. Before admission, the caller must supply the current admitted Runtime contract and verify its source/config/build/Oracle evidence. Historical Runtime 2 inputs can be used to test preparation locally but do not authorize execution under that old Runtime identity.

Later admission requires a genuine Analyzer explanation and Plans, followed by the existing publication and Intake path. It must create two distinct Runs (baseline and single-tile) in the new Context. Each intent declares one execution, zero tokens, and 1800 declared seconds: **two exploratory executions and 3600 declared seconds must be charged by the original task Controller**. Preparation itself charges nothing and creates no new budget authority. These diagnostic measurements are separate from the original workload, ABBA confirmation, and Round 2; their timings cannot be substituted for any of those results.

Local TDD: the first five tests failed because the helper was absent; all five passed after implementation. Additional tests exercise corrupted lengths, opaque-tail changes, accidental multi-tile configurations, unequal VMEM overrides, and a detached archived job/config transform. The tests use the original runner validators and scheduler reservation function on in-memory fixtures only.
