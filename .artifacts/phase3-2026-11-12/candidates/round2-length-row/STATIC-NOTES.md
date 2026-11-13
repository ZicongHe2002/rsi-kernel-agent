R2-A-length-row

Parent: baseline-repair-1, SHA256 2fc23aa9d0358ea18698f916cbc7ed5e02a4218dec2de2fc955661a8b513ebae.

Only length intermediate storage changes from [B,H,T] to [B,1,T]; attention broadcasts that row back to [H,T].

Only build_mla_decode_contiguous changed. Prefix, suffix, other top-level AST definitions, attention body, cache DMA ordering, masks, numerical recurrence and normalization are unchanged. Config, oracle, runner and benchmark were not modified.

Isolate redundant length transport after R1 tile regressions and ABBA program improvement with host drift; no promised performance gain.

Singleton VMEM length row lowering and in-attention broadcast must be verified on actual TPU. No candidate import, execution, TPU compilation, numerical verification or timing measurement was performed here.

See static-verification.json and change.diff for exact replacements and hashes.
