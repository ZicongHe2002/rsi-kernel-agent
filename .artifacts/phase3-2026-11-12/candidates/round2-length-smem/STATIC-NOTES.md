R2-A-length-smem

Parent: baseline-repair-1, SHA256 2fc23aa9d0358ea18698f916cbc7ed5e02a4218dec2de2fc955661a8b513ebae.

Prepare has no lengths input/output. Attention receives raw [B] int32 lengths through an SMEM BlockSpec and broadcasts len_ref[b] to [H,T].

Only build_mla_decode_contiguous changed. Prefix, suffix, other top-level AST definitions, attention body, cache DMA ordering, masks, numerical recurrence and normalization are unchanged. Config, oracle, runner and benchmark were not modified.

Isolate redundant length transport after R1 tile regressions and ABBA program improvement with host drift; no promised performance gain.

Mixed SMEM/VMEM/HBM input lowering and scalar SMEM length broadcast must be verified on actual TPU; existing SMEM API examples do not prove this exact combination. No candidate import, execution, TPU compilation, numerical verification or timing measurement was performed here.

See static-verification.json and change.diff for exact replacements and hashes.
