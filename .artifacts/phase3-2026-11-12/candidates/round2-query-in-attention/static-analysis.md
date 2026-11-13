Static candidate note: R2-B-query-in-attention

Parent source: round1-delayed-store-wait/mla_decode.py
Parent source SHA256: 0579beaa21cbf1119b64585c11334ab7051976a0f4b22400997fa510bd3b3a34

Change intent:
- Keep contiguous entry validation, config values, full newKV padding, per-head length broadcast, cache traversal, masks, recurrence, output aliasing, and delayed store.wait unchanged.
- Remove q from the independent prepare Pallas call and remove its qp output.
- Pass raw q to attention with BlockSpec shape (1, heads, dim); pad q inside the attention program to padded_dim before dot_general.
- Keep prepare outputs np/lp and their full batch semantics unchanged.

Static scope checks:
- Only the build_mla_decode_contiguous body changes.
- Non-contiguous/paged code and public configuration signatures are byte-identical to the parent outside this body.
- AST parse succeeds. TPU compilation, numerical correctness, and performance are NOT established by this static artifact; root must run the authorized fixed workflow.

Verified on 2026-11-13:
- Python AST parse/compile passes without importing the candidate or JAX.
- Raw byte prefix before the contiguous entry and suffix after it match the parent.
- Config signature, other top-level AST, input validation, and attention from offsets through final normalization are unchanged.
- Prepare AST equals the parent after removing q/qp handling; raw q call and BlockSpec match the planned interface.
- change.diff reproduces the current source difference.
- Source SHA256: be122d552e40e9501055a9643ca349871cfae726f7740c27e1e03cc99e1c258f.
