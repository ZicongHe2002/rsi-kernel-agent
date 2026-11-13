Static candidate note: R2-B1-fused-prepare

Parent source: round1-delayed-store-wait/mla_decode.py
Parent source SHA256: 0579beaa21cbf1119b64585c11334ab7051976a0f4b22400997fa510bd3b3a34

Change intent:
- Keep the original prepare path exactly for static batch > 1.
- For static batch == 1, skip the independent prepare Pallas call and pass raw q/new/lengths through VMEM attention inputs.
- Inside attention for batch == 1, pad q and pad the complete newKV row before broadcasting it to block_size; broadcast lengths to the original head/block mask shape.
- Keep full cache traversal/output, masks, online recurrence, and delayed store.wait behavior unchanged.

Static scope checks:
- Only the build_mla_decode_contiguous body changes.
- Non-contiguous/paged code and public configuration signatures are byte-identical to the parent outside this body.
- AST parse succeeds. TPU compilation, numerical correctness, and performance are NOT established by this static artifact; root must run the authorized fixed workflow.

Verified on 2026-11-13:
- Python AST parse/compile passes without importing the candidate or JAX.
- Raw byte prefix before the contiguous entry and suffix after it match the parent.
- Config signature, other top-level AST, input validation, and attention from offsets through final normalization are unchanged.
- Specializing the static batch==1 branches to false makes the complete contiguous function AST equal to the parent.
- change.diff reproduces the current source difference.
- Source SHA256: dcd7c717156e21141294cf2eb7ce881fd408d833fd9af9f71dad987966ddba73.
