# Copyright 2026 The PallasKernels Authors.

"""MLA decode TPU kernels for dense and four-input packed KV cache APIs.

Supports both full-sequence dense attention and continuous KV cache modes.
In cache mode the caller provides a pre-allocated ``kv`` buffer and a
dynamic ``seq_len``; the kernel masks positions beyond the valid prefix.
The default mla_decode config selects the packed path via build_mla_decode_paged.
It uses the notebook-validated 1024-token KV tile and returns attention plus cache.

Compute flow: ``docs/kernels/mla_decode.md``.
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import math
import threading
import types

import jax
import jax.numpy as jnp
import numpy as np
from jax import lax
from jax.experimental import pallas as pl
from jax.experimental.pallas import tpu as pltpu

from jax_ops.mla_decode import _pad_last_dim, validate_mla_decode_shapes
from pallas_kernels.kernels.utils.interpret import warn_if_interpret

TRANS_B_DIM_NUMBERS = (((1,), (1,)), ((), ()))


def _mla_decode_kernel(
  q_ref,
  kv_hbm_ref,
  out_ref,
  block_ref,
  sems_ref,
  *,
  seq_len: int,
  sm_scale: float,
  sliding_window: int,
  soft_cap: float,
  mask_value: float,
  lkv_dim: int,
  block_size: int,
  max_blocks: int,
):
  batch_idx = pl.program_id(0)
  num_heads = q_ref.shape[1]

  q = q_ref[0].astype(jnp.float32)
  acc = jnp.zeros((num_heads, lkv_dim), dtype=jnp.float32)
  m_prev = jnp.full((num_heads,), -jnp.inf, dtype=jnp.float32)
  l_prev = jnp.zeros((num_heads,), dtype=jnp.float32)
  token_offsets = lax.broadcasted_iota(jnp.int32, (block_size,), 0)
  q_pos = seq_len - 1

  for block_i in range(max_blocks):
    block_start = block_i * block_size
    flat_block_idx = batch_idx * max_blocks + block_i

    load_block = pltpu.make_async_copy(
      kv_hbm_ref.at[flat_block_idx],
      block_ref,
      sems_ref.at[0],
    )
    load_block.start()
    load_block.wait()

    kv_block = block_ref[...].astype(jnp.float32)
    k = kv_block
    v = kv_block[:, :lkv_dim]

    scores = lax.dot_general(
      q, k, TRANS_B_DIM_NUMBERS, preferred_element_type=jnp.float32
    )
    scores *= sm_scale
    if soft_cap > 0.0:
      scores = soft_cap * jnp.tanh(scores / soft_cap)

    token_ids = block_start + token_offsets
    valid = token_ids < seq_len
    if sliding_window > 0:
      valid = valid & (token_ids > q_pos - sliding_window)

    scores = jnp.where(valid[None, :], scores, mask_value)

    m_curr = jnp.max(scores, axis=1)
    m_next = jnp.maximum(m_prev, m_curr)
    alpha = jnp.exp(m_prev - m_next)
    p = jnp.exp(scores - m_next[:, None])
    p = jnp.where(valid[None, :], p, 0.0)
    l_next = l_prev * alpha + jnp.sum(p, axis=1)
    pv = lax.dot(p.astype(v.dtype), v, preferred_element_type=jnp.float32)
    acc = acc * alpha[:, None] + pv
    m_prev = m_next
    l_prev = l_next

  output = acc / jnp.where(l_prev <= 0.0, 1.0, l_prev)[:, None]
  out_ref[0] = output.astype(out_ref.dtype)


@functools.partial(
  jax.jit,
  static_argnames=[
    "seq_len",
    "sm_scale",
    "sliding_window",
    "soft_cap",
    "mask_value",
    "block_size",
    "lkv_dim",
    "interpret",
  ],
)
def _mla_decode_pallas(
  q: jax.Array,
  kv: jax.Array,
  *,
  seq_len: int,
  sm_scale: float,
  sliding_window: int,
  soft_cap: float,
  mask_value: float,
  block_size: int,
  lkv_dim: int,
  interpret: bool,
) -> jax.Array:
  """Pallas lowering wrapper.

  ``kv`` is pre-reshaped by the public ``mla_decode`` entry point
  to ``[B * max_blocks, block_size, padded_kv_dim]``.

  ``seq_len`` is a static int giving the current number of valid KV
  positions.  When ``seq_len`` equals the full buffer length the
  kernel behaves as a dense decode; when shorter only the prefix is
  attended to (continuous KV cache mode).
  """
  batch, num_heads, kv_dim = q.shape
  num_total_blocks = kv.shape[0]
  max_blocks = num_total_blocks // batch

  out_shape = jax.ShapeDtypeStruct((batch, num_heads, lkv_dim), q.dtype)

  def batch_index_map(batch_idx, *_scalars):
    return (batch_idx, 0, 0)

  q_spec = pl.BlockSpec((1, num_heads, kv_dim), batch_index_map)
  out_spec = pl.BlockSpec((1, num_heads, lkv_dim), batch_index_map)

  return pl.pallas_call(
    functools.partial(
      _mla_decode_kernel,
      seq_len=seq_len,
      sm_scale=sm_scale,
      sliding_window=sliding_window,
      soft_cap=soft_cap,
      mask_value=mask_value,
      lkv_dim=lkv_dim,
      block_size=block_size,
      max_blocks=max_blocks,
    ),
    out_shape=out_shape,
    grid_spec=pltpu.PrefetchScalarGridSpec(
      num_scalar_prefetch=0,
      in_specs=[
        q_spec,
        pl.BlockSpec(memory_space=pltpu.HBM),
      ],
      out_specs=out_spec,
      grid=(batch,),
      scratch_shapes=[
        pltpu.VMEM((block_size, kv_dim), q.dtype),
        pltpu.SemaphoreType.DMA((1,)),
      ],
    ),
    compiler_params=pltpu.CompilerParams(
      dimension_semantics=("parallel",),
      disable_bounds_checks=True,
    ),
    interpret=interpret,
    name="mla_decode",
  )(q, kv)


def mla_decode(
  q: jax.Array,
  kv: jax.Array,
  *,
  lkv_dim: int,
  seq_len: int | None = None,
  sm_scale: float = 1.0,
  sliding_window: int | None = None,
  soft_cap: float | None = None,
  mask_value: float | None = None,
  block_size: int = 16,
  interpret_mode: bool = False,
) -> jax.Array:
  """Single-token MLA dense decode with continuous KV cache.

  Args:
    q: Single-token query ``[B, H, kv_dim]`` (``concat(ql_nope, q_pe)``).
    kv: Full KV buffer ``[B, buffer_len, kv_dim]`` (``concat(kv_c, k_pe)``).
      In full-sequence mode this is the entire KV tensor; in cache mode
      this is the pre-allocated continuous KV cache buffer.
    lkv_dim: Logical latent KV dimension (``V = kv[:, :, :lkv_dim]``).
    seq_len: Current number of valid KV positions.  When ``None``
      (default) the entire ``kv`` buffer is used (full-sequence dense
      decode).  When set, ``kv`` is treated as a continuous cache of
      length ``buffer_len`` and only positions ``[0, seq_len)`` are
      attended to.  Must satisfy ``0 < seq_len <= buffer_len``.
    sm_scale: Scale applied to QK scores.
    sliding_window: Optional decode sliding window.
    soft_cap: Optional logit soft cap.
    mask_value: Score used for masked positions.
    block_size: Tokens per DMA block (must divide ``buffer_len``).
    interpret_mode: Run ``pallas_call`` in interpret mode.

  Returns:
    ``out`` shaped ``[B, H, padded_LKV]``.
  """
  batch, _num_heads, padded_lkv_dim, _, padded_kv_dim, buffer_len = (
    validate_mla_decode_shapes(q, kv, lkv_dim)
  )
  if block_size <= 0:
    raise ValueError(f"block_size must be positive, got {block_size}")
  if buffer_len % block_size != 0:
    raise ValueError(
      f"buffer_len ({buffer_len}) must be divisible by block_size ({block_size})"
    )
  if seq_len is not None:
    if not isinstance(seq_len, int) or seq_len <= 0:
      raise ValueError(f"seq_len must be a positive int, got {seq_len}")
    if seq_len > buffer_len:
      raise ValueError(f"seq_len ({seq_len}) must not exceed buffer_len ({buffer_len})")
  else:
    seq_len = buffer_len
  if sliding_window is not None and sliding_window <= 0:
    raise ValueError(f"sliding_window must be positive, got {sliding_window}")
  # Zero is an exact API sentinel.
  # ruff: ignore[float-equality-comparison]
  if soft_cap is not None and soft_cap == 0.0:
    raise ValueError("soft_cap must not be 0.0")
  if mask_value is None:
    mask_value = float(jnp.finfo(jnp.float32).min)
  if interpret_mode:
    warn_if_interpret("mla_decode")

  q = _pad_last_dim(q, padded_kv_dim)
  kv = _pad_last_dim(kv, padded_kv_dim)

  max_blocks = buffer_len // block_size
  kv_blocks = kv.reshape(batch * max_blocks, block_size, padded_kv_dim)

  out = _mla_decode_pallas(
    q,
    kv_blocks,
    seq_len=seq_len,
    sm_scale=float(sm_scale),
    sliding_window=0 if sliding_window is None else int(sliding_window),
    soft_cap=-1.0 if soft_cap is None else float(soft_cap),
    mask_value=float(mask_value),
    block_size=int(block_size),
    lkv_dim=padded_lkv_dim,
    interpret=interpret_mode,
  )
  return out


def _mla_decode_metadata(lengths, *, pages, decode_batch, interpret):
  batch = lengths.shape[0]
  shapes = [
    (batch,),
    (batch,),
    (batch,),
    (batch * pages,),
    (batch + 1,),
    (2,),
    (2,),
    (3,),
    (4,),
    (decode_batch, 6),
    (1, 6),
  ]

  def kernel(
    src,
    ll,
    order,
    inverse,
    page,
    cu,
    bounds_b,
    bounds_s,
    sem,
    bo,
    update_b,
    update_s,
  ):
    values = [jnp.minimum(jnp.maximum(src[i], 0), pages * 128) for i in range(batch)]
    active = [v > 0 for v in values]
    total = sum(a.astype(jnp.int32) for a in active)
    before = jnp.int32(0)
    for i in range(batch):
      rank = jnp.where(active[i], before, total + i - before)
      ll[rank] = values[i]
      order[rank] = i
      inverse[i] = rank
      before += active[i].astype(jnp.int32)

      # Scalar SMEM stores: no vector broadcast or sort in outer HLO.
      def page_body(p, _, rank=rank, i=i):
        page[rank * pages + p] = i * pages + p

      jax.lax.fori_loop(0, pages, page_body, None)
      cu[i] = i
    cu[batch] = batch
    split = (total // decode_batch) * decode_batch
    bounds_b[0], bounds_b[1] = 0, split
    bounds_s[0], bounds_s[1] = split, total
    for i in range(3):
      sem[i] = 0
    for i in range(4):
      bo[i] = -1
    for i in range(decode_batch):
      for j in range(6):
        update_b[i, j] = -1
    for j in range(6):
      update_s[0, j] = -1

  smem = pl.BlockSpec(memory_space=pltpu.SMEM)
  return pl.pallas_call(
    kernel,
    out_shape=tuple(jax.ShapeDtypeStruct(s, jnp.int32) for s in shapes),
    in_specs=(smem,),
    out_specs=(smem,) * len(shapes),
    interpret=interpret,
    name="mla_decode_metadata",
  )(lengths)


def _mla_decode_prepare(q, new, order, *, latent, packing, interpret):
  batch, heads, dim = q.shape
  rope = dim - latent
  padded_rope = (rope + 127) // 128 * 128
  padded_batch = (batch + packing * 8 - 1) // (packing * 8) * (packing * 8)
  packed_batch = (batch + packing - 1) // packing * packing
  shapes = [
    (padded_batch, heads, latent),
    (batch, heads, padded_rope),
    (packed_batch // packing, packing, latent),
    (packed_batch // packing, packing, padded_rope),
  ]

  def kernel(order_ref, q_ref, new_ref, ql, qp, nl, np):
    for i in range(padded_batch):
      ql[i, :, :] = jnp.zeros((heads, latent), q.dtype)
    for i in range(batch):
      source = order_ref[i]
      qr = q_ref[source, :, :]
      ql[i, :, :] = qr[:, :latent]
      qp[i, :, :] = jnp.concatenate(
        (qr[:, latent:], jnp.zeros((heads, padded_rope - rope), q.dtype)), axis=1
      )

    # Dynamic BF16 row loads cannot satisfy VMEM sublane alignment. Load at
    # offset zero, then select statically sliced rows in vector registers.
    new_rows = new_ref[...]
    for group in range(packed_batch // packing):
      rows = []
      for slot in range(packing):
        i = group * packing + slot
        row = jnp.zeros((1, dim), new.dtype)
        if i < batch:
          source = order_ref[i]
          for candidate in range(batch):
            row = jnp.where(
              source == candidate, new_rows[candidate : candidate + 1, :], row
            )
        rows.append(row)
      packed = jnp.concatenate(rows, axis=0)
      # Store complete packed tiles, including the zero row for odd batches.
      nl[group, :, :] = packed[:, :latent]
      np[group, :, :] = jnp.concatenate(
        (packed[:, latent:], jnp.zeros((packing, padded_rope - rope), new.dtype)),
        axis=1,
      )

  vmem = pl.BlockSpec(memory_space=pltpu.VMEM)
  return pl.pallas_call(
    kernel,
    out_shape=tuple(jax.ShapeDtypeStruct(s, q.dtype) for s in shapes),
    in_specs=(pl.BlockSpec(memory_space=pltpu.SMEM), vmem, vmem),
    out_specs=(vmem,) * 4,
    interpret=interpret,
    name="mla_decode_prepare",
  )(order, q, new)


def _mla_decode_restore(out, inverse, lengths, *, interpret):
  batch, heads, latent = inverse.shape[0], out.shape[1], out.shape[2]

  def kernel(inv, ll, src, dst):
    for i in range(batch):
      rank = inv[i]
      dst[i, :, :] = jnp.where(
        ll[rank] > 0, src[rank, :, :], jnp.zeros((heads, latent), out.dtype)
      )

  smem = pl.BlockSpec(memory_space=pltpu.SMEM)
  vmem = pl.BlockSpec(memory_space=pltpu.VMEM)
  return pl.pallas_call(
    kernel,
    out_shape=jax.ShapeDtypeStruct((batch, heads, latent), out.dtype),
    in_specs=(smem, smem, vmem),
    out_specs=vmem,
    interpret=interpret,
    name="mla_decode_restore",
  )(inverse, lengths, out)


def _build_mla_decode_native(
  upstream, *, latent, sm_scale, window, cap, vmem_limit, interpret, pages_per_block=8
):
  """Decode only, X0, FP32 scores, BF16 probabilities; runtime active count."""

  def attention(
    ql, qp, nl, np, cache, ll, page, cu, bounds, sem, bo, update, *, batch_size
  ):
    # Matches upstream run_mla_kernel for BATCHED_DECODE and DECODE.
    packing, page_words = cache.shape[2], cache.shape[1]
    heads, rope = ql.shape[1], qp.shape[2]
    tile_words = pages_per_block * page_words + 2
    qbuf = pltpu.VMEM((2, batch_size, 1, heads, latent), ql.dtype)
    scratch = [
      pltpu.VMEM((2, batch_size, tile_words, packing, latent), cache.dtype),
      pltpu.VMEM((2, batch_size, tile_words, packing, rope), cache.dtype),
      qbuf,
      pltpu.VMEM((2, batch_size, 1, heads, rope), qp.dtype),
      qbuf,
      pltpu.SemaphoreType.DMA((4, batch_size, 2)),
      pltpu.VMEM((batch_size, heads, 128), jnp.float32),
      pltpu.VMEM((batch_size, heads, 128), jnp.float32),
      pltpu.VMEM((batch_size, heads, latent), jnp.float32),
    ]
    body = functools.partial(
      upstream._mla_ragged_paged_attention_kernel,
      sm_scale=sm_scale,
      sliding_window=window or None,
      soft_cap=cap or None,
      mask_value=jnp.finfo(jnp.float32).min,
      q_scale=None,
      k_scale=None,
      v_scale=None,
      static_q_len=1,
      bq_sz=1,
      bkv_p=pages_per_block,
      batch_size=batch_size,
      q_split=1,
      s_dtype=jnp.float32,
      transpose_kv_cache=False,
      two_step_flash_attention=True,
      p_same_dtype_as_v=True,
      debug_mode=False,
    )
    hbm = pl.BlockSpec(memory_space=pltpu.HBM)
    return upstream.pl.pallas_call(
      body,
      grid_spec=pltpu.PrefetchScalarGridSpec(
        num_scalar_prefetch=7,
        in_specs=[hbm] * 5,
        out_specs=[hbm] * 2,
        # Runtime active bounds are checked inside Mosaic; no scalar outputs.
        grid=(max(1, ll.shape[0] // 2) if batch_size == 2 else 1,),
        scratch_shapes=scratch,
      ),
      compiler_params=pltpu.CompilerParams(
        dimension_semantics=("arbitrary",),
        vmem_limit_bytes=vmem_limit,
        disable_bounds_checks=True,
      ),
      out_shape=[
        jax.ShapeDtypeStruct(ql.shape, ql.dtype),
        jax.ShapeDtypeStruct(cache.shape, cache.dtype),
      ],
      input_output_aliases={7: 0, 11: 1},
      name=f"mla_decode_attention_b{batch_size}",
    )(ll, page, cu, bounds, sem, bo, update, ql, qp, nl, np, cache)

  @functools.partial(jax.jit, donate_argnums=(2,))
  def native(q, new, cache, lengths):
    batch, heads, dim = q.shape
    if (
      batch <= 0
      or heads <= 0
      or q.dtype != jnp.bfloat16
      or new.dtype != q.dtype
      or cache.dtype != q.dtype
      or heads % 2
      or dim <= latent
      or new.shape != (batch, dim)
      or lengths.shape != (batch,)
      or lengths.dtype != jnp.int32
      or cache.ndim != 4
      or cache.shape[0] <= 0
      or cache.shape[1:3] != (64, 2)
      or cache.shape[0] % batch
      or cache.shape[-1] != latent + (dim - latent + 127) // 128 * 128
    ):
      raise ValueError(
        "kernel_io requires BF16 Google X0 cache, even heads, and s32 lengths"
      )
    ll, order, inv, page, cu, bb, bs, sem, bo, ub, us = _mla_decode_metadata(
      lengths, pages=cache.shape[0] // batch, decode_batch=2, interpret=interpret
    )
    ql, qp, nl, np = _mla_decode_prepare(
      q, new, order, latent=latent, packing=2, interpret=interpret
    )
    out, cache = attention(
      ql, qp, nl, np, cache, ll, page, cu, bb, sem, bo, ub, batch_size=2
    )
    out, cache = attention(
      out, qp, nl, np, cache, ll, page, cu, bs, sem, bo, us, batch_size=1
    )
    return _mla_decode_restore(out, inv, ll, interpret=interpret), cache

  return native


_CPU_INTERPRET_LOCK = threading.RLock()
_CPU_PROGRAM_ID = contextvars.ContextVar("mla_decode_cpu_program_id", default=None)


class _ModuleProxy:
  def __init__(self, module, **overrides):
    self._module = module
    self.__dict__.update(overrides)

  def __getattr__(self, name):
    return getattr(self._module, name)


def _view_plan(view):
  """Static bounds and inverse indexing plan for CPU-only DMA interpretation."""
  from jax._src.state import indexing
  from jax._src.state import types as state_types

  base = getattr(view, "ref", view)
  shape = tuple(base.shape)
  steps = []
  for transform in getattr(view, "transforms", ()):
    old = shape
    if isinstance(transform, indexing.NDIndexer):
      if transform.int_indexer_shape:
        raise NotImplementedError("CPU DMA shim supports scalar/slice indexing only")
      shape = tuple(
        (int(idx.size) if isinstance(idx.size, int) else old[i])
        for i, idx in enumerate(transform.indices)
        if isinstance(idx, indexing.Slice)
      )
    elif isinstance(transform, state_types.RefReshaper):
      shape = tuple(transform.shape)
    else:
      raise NotImplementedError(f"CPU DMA view transform {type(transform).__name__}")
    steps.append((transform, old, shape))
  return base, shape, steps


def _view_flat_indices(plan, copy_shape):
  from jax._src.state import indexing
  from jax._src.state import types as state_types

  base, _, steps = plan
  coords = list(jnp.indices(copy_shape, dtype=jnp.int32))
  valid = jnp.ones(copy_shape, dtype=jnp.bool_)
  for transform, old, shape in reversed(steps):
    if isinstance(transform, indexing.NDIndexer):
      expanded, axis = [], 0
      for idx in transform.indices:
        if isinstance(idx, indexing.Slice):
          valid = valid & (coords[axis] < idx.size)
          expanded.append(idx.start + coords[axis] * idx.stride)
          axis += 1
        else:
          expanded.append(jnp.broadcast_to(idx, copy_shape))
      coords = expanded
    elif isinstance(transform, state_types.RefReshaper):
      flat = sum(c * math.prod(shape[i + 1 :]) for i, c in enumerate(coords))
      coords = [(flat // math.prod(old[i + 1 :])) % old[i] for i in range(len(old))]
  flat = jnp.zeros(copy_shape, jnp.int32)
  for i, coord in enumerate(coords):
    valid = valid & (coord >= 0) & (coord < base.shape[i])
    flat = flat + coord * math.prod(base.shape[i + 1 :])
  return flat.reshape(-1), valid.reshape(-1)


class _CpuDma:
  def __init__(self, src, dst, sem):
    self.src, self.dst = src, dst

  def start(self):
    src, dst = _view_plan(self.src), _view_plan(self.dst)
    if len(src[1]) != len(dst[1]):
      raise ValueError("CPU DMA source/destination rank mismatch")
    shape = tuple(min(a, b) for a, b in zip(src[1], dst[1], strict=True))
    si, sm = _view_flat_indices(src, shape)
    di, dm = _view_flat_indices(dst, shape)
    values = src[0][...].reshape(-1)[jnp.clip(si, 0, math.prod(src[0].shape) - 1)]
    original = dst[0][...].reshape(-1)
    result = original.at[jnp.where(sm & dm, di, original.size)].set(values, mode="drop")
    dst[0][...] = result.reshape(dst[0].shape)

  def wait(self):
    return None


@contextlib.contextmanager
def _mla_decode_cpu_interpret_context():
  """Bounded interpreter-only backport, never used for native TPU lowering."""
  from jax._src.state import discharge, indexing
  from jax._src.state import types as state_types
  from jax._src.state.utils import bitcast

  with _CPU_INTERPRET_LOCK:
    original = discharge.transform_swap_array

    def swap(x, transforms, val):
      intermediates, current = [], x
      for transform in transforms or ():
        intermediates.append(current)
        current = discharge.transform_array(current, (transform,))
      updated = val
      for before, transform in reversed(
        list(zip(intermediates, transforms or (), strict=True))
      ):
        if isinstance(transform, state_types.RefReshaper):
          updated = updated.reshape(before.shape)
        elif isinstance(transform, state_types.RefBitcaster):
          updated = bitcast(updated, before.dtype)
        elif isinstance(transform, indexing.NDIndexer):
          updated = original(before, (transform,), updated)[1]
        else:
          raise NotImplementedError(
            f"CPU ref write transform {type(transform).__name__}"
          )
      return current, updated

    discharge.transform_swap_array = swap
    try:
      yield
    finally:
      discharge.transform_swap_array = original


def _interpret_call_wrapper(fn):
  @functools.wraps(fn)
  def wrapped(*args, **kwargs):
    with _mla_decode_cpu_interpret_context():
      return fn(*args, **kwargs)

  def lower(*args, **kwargs):
    with _mla_decode_cpu_interpret_context():
      return fn.lower(*args, **kwargs)

  wrapped.lower = lower
  wrapped.clear_cache = fn.clear_cache
  return wrapped


# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# Adapted from vllm-project/tpu-inference, commit
# a0135382e192ae03b4c79a5b30b740a9a31f38b1:
# tpu_inference/kernels/mla/v2/{kernel,kv_utils}.py.
# Local changes: JAX 0.8.1 compatibility and CPU-only DMA/page loops.


@functools.lru_cache(maxsize=4)
def _mla_decode_attention_components(interpret):
  """Build the in-file attention body with TPU or CPU interpreter primitives."""

  def pallas_call(*args, **kwargs):
    spec = kwargs.get("grid_spec")
    if interpret and isinstance(spec, pltpu.PrefetchScalarGridSpec):
      kernel = args[0]
      if (
        not isinstance(kernel, functools.partial) or "batch_size" not in kernel.keywords
      ):
        raise NotImplementedError("Unexpected upstream scalar-prefetch CPU kernel")
      # Exercise inactive programs too, matching the TPU's static grid.
      count = spec.grid[0]

      def loop_kernel(*refs):
        def body(pid, _):
          token = _CPU_PROGRAM_ID.set(pid)
          try:
            kernel(*refs)
          finally:
            _CPU_PROGRAM_ID.reset(token)

        jax.lax.fori_loop(0, count, body, None)

      args = (loop_kernel, *args[1:])
      kwargs["grid_spec"] = pltpu.PrefetchScalarGridSpec(
        num_scalar_prefetch=spec.num_scalar_prefetch,
        grid=(),
        in_specs=spec.in_specs,
        out_specs=spec.out_specs,
        scratch_shapes=spec.scratch_shapes,
      )
    # The altered grid is an interpreter model, with no physical grid dimensions.
    if interpret and kwargs.get("grid_spec") is not spec:
      cp = kwargs.get("compiler_params")
      if cp is not None:
        import dataclasses

        kwargs["compiler_params"] = dataclasses.replace(cp, dimension_semantics=())
    return pl.pallas_call(*args, interpret=bool(interpret), **kwargs)

  def program_id(axis):
    pid = _CPU_PROGRAM_ID.get()
    return pid if pid is not None and axis == 0 else pl.program_id(axis)

  pl_proxy = _ModuleProxy(pl, pallas_call=pallas_call, program_id=program_id)
  tpu_proxy = _ModuleProxy(pltpu, make_async_copy=_CpuDma) if interpret else pltpu

  def unsigned_mod(a, b):
    exponent = int(math.log2(b))
    if b == int(math.pow(2, exponent)):
      # Use bitmask instead of modulo for efficiency.
      return a & (b - 1)
    return a % b

  def unsigned_cdiv(a, b):
    exponent = int(math.log2(b))
    if b == int(math.pow(2, exponent)):
      # Use bit shift instead of division for efficiency.
      return (a + b - 1) >> exponent
    return (a + b - 1) // b

  def unsigned_floor_div(a, b):
    exponent = int(math.log2(b))
    if b == int(math.pow(2, exponent)):
      # Use bit shift instead of division for efficiency.
      return a >> exponent
    return a // b

  def unsigned_align_to(a, b):
    exponent = int(math.log2(b))
    if b == int(math.pow(2, exponent)):
      # Use bitmask instead of division and multiply for efficiency.
      return (a + b - 1) & (-int(b))

    return unsigned_cdiv(a, b) * b

  def align_to(a, b):
    return ((a + b - 1) // b) * b

  def get_dtype_bitwidth(dtype):
    return jnp.dtype(dtype).itemsize * 8

  def get_dtype_packing(dtype):
    bits = get_dtype_bitwidth(dtype)
    return 32 // bits

  def pack_new_kv(
    bkvc_vmem_ref, bkvpe_vmem_ref, offset, update_sz, q_end, kv_len, bkv_sz
  ):
    _, kv_packing, lkv_dim = bkvc_vmem_ref.shape
    _, _, r_dim = bkvpe_vmem_ref.shape

    num_sublanes_for_kv_packing = kv_packing // get_dtype_packing(bkvc_vmem_ref.dtype)

    update_kv_packing_iters = unsigned_cdiv(
      unsigned_mod(offset, kv_packing) + update_sz, kv_packing
    )
    kv_packing_offset = unsigned_mod(offset, kv_packing)
    new_kv_len_start = q_end - kv_len + offset
    new_kv_packing_offset = unsigned_mod(new_kv_len_start, kv_packing)

    token_offset_in_bkv = unsigned_mod(offset, bkv_sz)
    kv_packing_idx = unsigned_floor_div(token_offset_in_bkv, kv_packing)

    # Compute the shift amount for each word in bits
    offset_diff = kv_packing_offset - new_kv_packing_offset
    roll_amount = unsigned_mod(offset_diff, kv_packing)

    bits_per_element = get_dtype_bitwidth(bkvc_vmem_ref.dtype)
    shift_bits = bits_per_element * roll_amount

    roll_amount = roll_amount.astype(jnp.int32)
    roll_shift_max = 32 // bits_per_element
    roll_shift_bits = unsigned_mod(roll_amount, roll_shift_max) * bits_per_element
    roll_rolling_amount = unsigned_floor_div(roll_amount, roll_shift_max)

    shift_bits = shift_bits.astype(jnp.uint32)
    roll_shift_bits = roll_shift_bits.astype(jnp.uint32)

    # Calculate the starting index in the KV buffer corresponding to the new KV
    # to fetch the data from. This index accounts for the potential offset
    # caused by the shift_amount.
    # (-offset_diff) // kv_packing will be:
    #   0 if new_kv_packing_offset <= kv_packing_offset
    #  -1 if new_kv_packing_offset > kv_packing_offset.
    kv_packing_idx_new = unsigned_cdiv(token_offset_in_bkv, kv_packing) + (
      (-offset_diff) // kv_packing
    )
    curr_kvc_reg = bkvc_vmem_ref[kv_packing_idx_new, :, :]
    curr_kpe_reg = bkvpe_vmem_ref[kv_packing_idx_new, :, :]
    next_kvc_reg = bkvc_vmem_ref[kv_packing_idx_new + 1, :, :]
    next_kpe_reg = bkvpe_vmem_ref[kv_packing_idx_new + 1, :, :]

    def merge_loop_body(i, vals):
      (
        kv_packing_idx,
        kv_packing_idx_new,
        curr_kvc_reg,
        curr_kpe_reg,
        next_kvc_reg,
        next_kpe_reg,
      ) = vals
      if num_sublanes_for_kv_packing == 1:
        curr_kvc_reg_u32 = tpu_proxy.bitcast(curr_kvc_reg, jnp.uint32)
        curr_kpe_reg_u32 = tpu_proxy.bitcast(curr_kpe_reg, jnp.uint32)
        next_kvc_reg_u32 = tpu_proxy.bitcast(next_kvc_reg, jnp.uint32)
        next_kpe_reg_u32 = tpu_proxy.bitcast(next_kpe_reg, jnp.uint32)

        shifted_kvc_u32 = lax.bitwise_or(
          lax.shift_right_logical(curr_kvc_reg_u32, 32 - shift_bits),
          lax.shift_left(next_kvc_reg_u32, shift_bits),
        )
        shifted_kpe_u32 = lax.bitwise_or(
          lax.shift_right_logical(curr_kpe_reg_u32, 32 - shift_bits),
          lax.shift_left(next_kpe_reg_u32, shift_bits),
        )

        # If shift_bits is 0, we should use the current word. Otherwise,
        # shifting by 32 bits would result in shifted_*_u32 becoming
        # next_*_reg_u32, which is incorrect.
        rotated_kvc_u32 = lax.select(shift_bits == 0, curr_kvc_reg_u32, shifted_kvc_u32)
        rotated_kpe_u32 = lax.select(shift_bits == 0, curr_kpe_reg_u32, shifted_kpe_u32)

        rolled_kvc = tpu_proxy.bitcast(rotated_kvc_u32, next_kvc_reg.dtype)
        rolled_kpe = tpu_proxy.bitcast(rotated_kpe_u32, next_kpe_reg.dtype)
      else:
        kvc_cur_cond = (
          lax.broadcasted_iota(
            dtype=jnp.int32, shape=[kv_packing, lkv_dim], dimension=0
          )
          < roll_amount
        )
        dtype = curr_kvc_reg.dtype

        def shift_roll(reg, roll_rolling_amount, roll_shift_bits):
          reg_u32 = tpu_proxy.bitcast(reg, jnp.uint32)
          reg_u32 = tpu_proxy.roll(reg_u32, shift=roll_rolling_amount, axis=0)
          reg_u32_next = tpu_proxy.roll(reg_u32, shift=1, axis=0)
          result = lax.bitwise_or(
            lax.shift_left(reg_u32, roll_shift_bits),
            lax.shift_right_logical(reg_u32_next, 32 - roll_shift_bits),
          )
          return tpu_proxy.bitcast(result, dtype)

        rolled_kvc = lax.select(
          kvc_cur_cond,
          shift_roll(curr_kvc_reg, roll_rolling_amount, roll_shift_bits),
          shift_roll(next_kvc_reg, roll_rolling_amount, roll_shift_bits),
        )
        kpe_cur_cond = (
          lax.broadcasted_iota(dtype=jnp.int32, shape=[kv_packing, r_dim], dimension=0)
          < roll_amount
        )
        rolled_kpe = lax.select(
          kpe_cur_cond,
          shift_roll(curr_kpe_reg, roll_rolling_amount, roll_shift_bits),
          shift_roll(next_kpe_reg, roll_rolling_amount, roll_shift_bits),
        )
        rolled_kvc = lax.select(roll_amount == 0, curr_kvc_reg, rolled_kvc)
        rolled_kpe = lax.select(roll_amount == 0, curr_kpe_reg, rolled_kpe)

      offset_in_word = i * kv_packing + lax.broadcasted_iota(
        dtype=jnp.int32, shape=[kv_packing, lkv_dim], dimension=0
      )
      kvc_mask = jnp.logical_and(
        offset_in_word >= kv_packing_offset,
        offset_in_word < kv_packing_offset + update_sz,
      )
      updated_kvc_reg = lax.select(
        kvc_mask,
        rolled_kvc,
        bkvc_vmem_ref[kv_packing_idx, :, :],
      )
      offset_in_word_pe = i * kv_packing + lax.broadcasted_iota(
        dtype=jnp.int32, shape=[kv_packing, r_dim], dimension=0
      )
      kpe_mask = jnp.logical_and(
        offset_in_word_pe >= kv_packing_offset,
        offset_in_word_pe < kv_packing_offset + update_sz,
      )
      updated_kpe_reg = lax.select(
        kpe_mask,
        rolled_kpe,
        bkvpe_vmem_ref[kv_packing_idx, :, :],
      )

      # Store back the merged word
      bkvc_vmem_ref[kv_packing_idx, :, :] = updated_kvc_reg
      bkvpe_vmem_ref[kv_packing_idx, :, :] = updated_kpe_reg

      # Move to the next word.
      kv_packing_idx += 1
      kv_packing_idx_new += 1
      curr_kvc_reg = next_kvc_reg
      curr_kpe_reg = next_kpe_reg
      next_kvc_reg = bkvc_vmem_ref[kv_packing_idx_new + 1, :, :]
      next_kpe_reg = bkvpe_vmem_ref[kv_packing_idx_new + 1, :, :]
      return (
        kv_packing_idx,
        kv_packing_idx_new,
        curr_kvc_reg,
        curr_kpe_reg,
        next_kvc_reg,
        next_kpe_reg,
      )

    lax.fori_loop(
      0,
      update_kv_packing_iters,
      merge_loop_body,
      (
        kv_packing_idx,
        kv_packing_idx_new,
        curr_kvc_reg,
        curr_kpe_reg,
        next_kvc_reg,
        next_kpe_reg,
      ),
    )

  def _mla_ragged_paged_attention_kernel(
    # Prefetch
    kv_lens_ref,  # [max_num_seqs]
    page_indices_ref,  # [max_num_seqs * pages_per_seq]
    cu_q_lens_ref,  # [max_num_seqs + 1]
    start_end_seq_idx_ref,  # [2] (start_seq_idx, end_seq_idx)
    sem_ids_ref,  # [3] (bq_sem_idx, bkv_sem_idx, bo_sem_idx)
    # [4] (bo_sem_0_seq_idx, bo_sem_1_seq_idx, bo_sem_0_bo_idx, bo_sem_1_bo_idx)
    bo_ids_ref,
    # [batch_size, 6] (bkv_sem_0_seq_idx, bkv_sem_1_seq_idx, bkv_sem_0_offset,
    # bkv_sem_1_offset, bkv_sem_0_sz, bkv_sem_1_sz) * batch_size
    bkv_update_ids_ref,
    # Input
    ql_nope_hbm_ref,  # [max_num_tokens, num_q_heads, lkv_dim]
    q_pe_hbm_ref,  # [max_num_tokens, num_q_heads, r_dim]
    # [max_num_tokens_per_kv_packing, kv_packing, lkv_dim] if not transpose_kv_cache
    # else [lkv_dim, max_num_tokens]
    new_kv_c_hbm_ref,
    # [max_num_tokens_per_kv_packing, kv_packing, r_dim] if not transpose_kv_cache else
    # [r_dim, max_num_tokens]
    new_k_pe_hbm_ref,
    # [total_num_pages, page_size_per_kv_packing, kv_packing, align_to(lkv_dim + r_dim,
    # 128)] if not transpose_kv_cache else [total_num_pages, align_to(lkv_dim + r_dim,
    # 128), page_size]
    cache_kv_hbm_ref,
    # Output
    o_hbm_ref,  # [max_num_tokens, num_q_heads, lkv_dim]
    # [total_num_pages, page_size_per_kv_packing, kv_packing, align_to(lkv_dim + r_dim,
    # 128)] if not transpose_kv_cache else [total_num_pages, align_to(lkv_dim + r_dim,
    # 128), page_size]
    updated_cache_kv_hbm_ref,
    # Scratch
    # [2, batch_size, bkv_buf_sz_per_kv_packing, kv_packing, lkv_dim] if not
    # transpose_kv_cache else [2, batch_size, lkv_dim, bkv_sz + 256]
    bkvc_x2_ref,
    # [2, batch_size, bkv_buf_sz_per_kv_packing, kv_packing, r_dim] if not
    # transpose_kv_cache else [2, batch_size, r_dim, bkv_sz + 256]
    bkpe_x2_ref,
    bq_nope_x2_ref,  # [2, batch_size, bq_sz, num_q_heads, lkv_dim]
    bq_rope_x2_ref,  # [2, batch_size, bq_sz, num_q_heads, r_dim]
    bo_x2_ref,  # [2, batch_size, bq_sz, num_q_heads, lkv_dim]
    dma_semaphores,  # [4, batch_size, 2]
    l_ref,  # [batch_size, bq_sz * num_q_heads, 128],
    m_ref,  # [batch_size, bq_sz * num_q_heads, 128],
    acc_ref,  # [batch_size, bq_sz * num_q_heads, lkv_dim],
    *,
    static_q_len: int,
    sm_scale: float,
    mask_value: float,
    s_dtype: jnp.dtype,
    transpose_kv_cache: bool,
    two_step_flash_attention: bool,
    p_same_dtype_as_v: bool,
    sliding_window: int | None = None,
    soft_cap: float | None = None,
    q_scale: float | None = None,
    k_scale: float | None = None,
    v_scale: float | None = None,
    bkv_p,
    bq_sz,
    batch_size: int = 1,
    q_split: int = 1,
    debug_mode: bool = False,
  ):
    assert ql_nope_hbm_ref.shape == o_hbm_ref.shape
    # Validation checks on the dimensions
    nope_dim = ql_nope_hbm_ref.shape[-1]
    pe_dim = q_pe_hbm_ref.shape[-1]

    _, num_q_heads, lkv_dim = ql_nope_hbm_ref.shape
    r_dim = q_pe_hbm_ref.shape[-1]
    q_packing = get_dtype_packing(ql_nope_hbm_ref.dtype)
    num_q_heads_per_q_packing = num_q_heads // q_packing
    kv_dtype = cache_kv_hbm_ref.dtype
    if not transpose_kv_cache:
      total_num_pages, page_size_per_kv_packing, kv_packing, kv_dim = (
        cache_kv_hbm_ref.shape
      )
      assert kv_packing % get_dtype_packing(kv_dtype) == 0
      num_sublanes_for_kv_packing = kv_packing // get_dtype_packing(
        cache_kv_hbm_ref.dtype
      )
      bkv_sz_per_kv_packing = bkv_p * page_size_per_kv_packing
      bkv_sz = bkv_sz_per_kv_packing * kv_packing
      page_size = page_size_per_kv_packing * kv_packing
    else:
      (
        total_num_pages,
        kv_dim,
        page_size,
      ) = cache_kv_hbm_ref.shape
      bkv_sz = bkv_p * page_size
      page_size_per_lane = page_size // 128
    assert nope_dim + pe_dim == align_to(kv_dim, 128)
    kv_r_dim = kv_dim - nope_dim

    max_num_seqs = kv_lens_ref.shape[0]
    num_page_indices = page_indices_ref.shape[0]

    assert num_page_indices % max_num_seqs == 0
    pages_per_seq = num_page_indices // max_num_seqs
    q_dtype = ql_nope_hbm_ref.dtype
    # Validate against the KV dtype.
    assert q_pe_hbm_ref.dtype == q_dtype
    assert o_hbm_ref.dtype == q_dtype
    assert get_dtype_packing(q_dtype) == q_packing
    assert lkv_dim % 128 == 0
    assert r_dim % 128 == 0

    start_seq_idx = start_end_seq_idx_ref[0]
    end_seq_idx = start_end_seq_idx_ref[1]
    batch_start_seq_idx = start_seq_idx + pl_proxy.program_id(0) * batch_size
    batch_end_seq_idx = batch_start_seq_idx + batch_size - 1

    def debug_print(msg, *args):
      if debug_mode:
        pl_proxy.debug_print(msg, *args)

    debug_print(
      "[RPA debug] ======= In loop batch_start_seq_idx={}", batch_start_seq_idx
    )
    debug_print("[RPA debug] start_seq_idx={}", start_seq_idx)
    debug_print("[RPA debug] end_seq_idx={}", end_seq_idx)
    debug_print("[RPA debug] bkv_p={}", bkv_p)
    debug_print("[RPA debug] page_size={}", page_size)
    debug_print("[RPA debug] pages_per_seq={}", pages_per_seq)
    debug_print("[RPA debug] bq_sz={}", bq_sz)
    debug_print("[RPA debug] batch_size={}", batch_size)

    def batch_flash_attention(
      ql_nope,  # [batch_size, actual_bq_sz * num_q_heads, lkv_dim]
      q_pe,  # [batch_size, actual_bq_sz * num_q_heads, r_dim]
      # [batch_size, bkv_sz, lkv_dim] if not transpose_kv_cache else [batch_size,
      # lkv_dim, bkv_sz] <- Correspond to data from bkvc_x2_ref
      kv_c,
      # [batch_size, bkv_sz, r_dim] if not transpose_kv_cache else [batch_size, r_dim,
      # bkv_sz] <- Correspond to data from bpe_x2_ref
      k_pe,
      *,
      bq_idx,
      bkv_idx,
    ):
      assert len(ql_nope.shape) == 3
      assert len(q_pe.shape) == 3
      assert len(kv_c.shape) == 3
      assert len(k_pe.shape) == 3
      assert ql_nope.shape[1] % num_q_heads == 0
      assert ql_nope.shape[1] == q_pe.shape[1]
      assert q_pe.shape[1] % bq_sz == 0
      assert ql_nope.shape[2] == lkv_dim
      assert q_pe.shape[2] == r_dim
      if not transpose_kv_cache:
        assert kv_c.shape == (batch_size, bkv_sz, lkv_dim)
        assert k_pe.shape == (batch_size, bkv_sz, r_dim)
      else:
        assert kv_c.shape == (batch_size, lkv_dim, bkv_sz)
        assert k_pe.shape == (batch_size, r_dim, bkv_sz)

      head_l_ref = l_ref.at[:, : ql_nope.shape[1]]
      head_m_ref = m_ref.at[:, : ql_nope.shape[1]]
      head_acc_ref = acc_ref.at[:, : ql_nope.shape[1]]

      def load_with_init(ref, init_val):
        return jnp.where(bkv_idx == 0, jnp.full_like(ref, init_val), ref[...])

      # Follow FlashAttention-2 forward pass.
      q = jnp.concatenate([ql_nope, q_pe], axis=-1)
      k = jnp.concatenate([kv_c, k_pe], axis=-1 if not transpose_kv_cache else -2)
      s = jnp.einsum(
        "bnd,bmd->bnm" if not transpose_kv_cache else "bnd,bdm->bnm",
        q,
        k,
        preferred_element_type=jnp.float32,
      )
      s *= sm_scale
      if k_scale is not None:
        s *= k_scale
      if q_scale is not None:
        s *= q_scale

      k_span = bkv_idx * bkv_sz + lax.broadcasted_iota(jnp.int32, s.shape[1:], 1)

      mask_list = []
      for b in range(batch_size):
        seq_idx = batch_start_seq_idx + b
        q_start = cu_q_lens_ref[seq_idx]
        q_end = cu_q_lens_ref[seq_idx + 1]
        q_len = q_end - q_start
        kv_len = kv_lens_ref[seq_idx]
        q_span = (
          kv_len
          - q_len
          + bq_idx * bq_sz
          + unsigned_floor_div(
            lax.broadcasted_iota(jnp.int32, s.shape[1:], 0), num_q_heads
          )
        )
        mask = q_span < k_span
        if sliding_window is not None:
          mask = jnp.logical_or(mask, q_span - sliding_window >= k_span)
        mask_list.append(mask)
      mask = jnp.stack(mask_list, axis=0)

      if soft_cap is not None:
        s = soft_cap * jnp.tanh(s / soft_cap)
      s = s.astype(s_dtype)

      s = jnp.where(mask, mask_value, s)
      s_rowmax = jnp.max(s, axis=2, keepdims=True)
      m_prev = load_with_init(head_m_ref, -jnp.inf)
      m_curr = jnp.maximum(m_prev, s_rowmax)
      head_m_ref[...] = m_curr
      p = jnp.exp(s - broadcast_minor(m_curr, s.shape))

      p_rowsum = jnp.sum(p, axis=2, keepdims=True)
      if p_same_dtype_as_v:
        p = p.astype(kv_c.dtype)
      pv = jnp.einsum(
        "bnm,bmd->bnd" if not transpose_kv_cache else "bnm,bdm->bnd",
        p,
        kv_c,
        preferred_element_type=jnp.float32,
      )
      if v_scale is not None:
        pv *= v_scale

      exp_m_diff = jnp.exp(m_prev - m_curr)
      l_prev = load_with_init(head_l_ref, 0.0)
      l_curr = exp_m_diff * l_prev + p_rowsum
      head_l_ref[...] = l_curr
      o_prev = load_with_init(head_acc_ref, 0.0)
      o_curr = broadcast_minor(exp_m_diff, o_prev.shape) * o_prev + pv
      head_acc_ref[...] = o_curr

    def flash_attention_step1_qk_softmax(
      ql_nope,  # [actual_bq_sz * num_q_heads // q_split, lkv_dim]
      q_pe,  # [actual_bq_sz * num_q_heads // q_split, r_dim]
      # [bkv_sz, lkv_dim] if not transpose_kv_cache else [lkv_dim, bkv_sz] <- Correspond
      # to data from bkvc_x2_ref
      kv_c,
      # [bkv_sz, r_dim] if not transpose_kv_cache else [r_dim, bkv_sz] <- Correspond to
      # data from bpe_x2_ref
      k_pe,
      *,
      kv_len,
      q_len,
      bq_idx,
      bq_offset,
      bkv_idx,
      head_l_ref,
      head_m_ref,
    ):
      assert len(ql_nope.shape) == 2
      assert len(q_pe.shape) == 2
      assert len(kv_c.shape) == 2
      assert len(k_pe.shape) == 2
      assert ql_nope.shape[0] == q_pe.shape[0]
      assert ql_nope.shape[1] == lkv_dim
      assert q_pe.shape[1] == r_dim
      if not transpose_kv_cache:
        assert kv_c.shape == (bkv_sz, lkv_dim)
        assert k_pe.shape == (bkv_sz, r_dim)
      else:
        assert kv_c.shape == (lkv_dim, bkv_sz)
        assert k_pe.shape == (r_dim, bkv_sz)

      def load_with_init(ref, init_val):
        return jnp.where(bkv_idx == 0, jnp.full_like(ref, init_val), ref[...])

      # Follow FlashAttention-2 forward pass.
      if bkv_sz % 256 == 0:
        # Split QK into 256-token MXU chunks, then concatenate the results.
        # Compute each output before starting the next einsum so vector
        # arithmetic overlaps with the next chunk's MXU computation.
        s_list = []
        for partial_idx in range(bkv_sz // 256):
          partial_start = partial_idx * 256
          if not transpose_kv_cache:
            kv_c_partial = kv_c[partial_start : partial_start + 256, :]
            k_pe_partial = k_pe[partial_start : partial_start + 256, :]
          else:
            kv_c_partial = kv_c[:, partial_start : partial_start + 256]
            k_pe_partial = k_pe[:, partial_start : partial_start + 256]

          if not transpose_kv_cache:
            kv_c_partial = kv_c_partial.transpose((1, 0))

            # Bitcast trick to prevent transpose merge into vmatpush.xpose.
            # It enforce transpose using XLUs (vxpose.xlu).
            kv_c_partial = tpu_proxy.bitcast(kv_c_partial, kv_c_partial.dtype)

          s_c_partial = jnp.einsum(
            "nd,dm->nm",  # codespell:ignore nd
            ql_nope,
            kv_c_partial,
            preferred_element_type=jnp.float32,
          )
          s_pe_partial = jnp.einsum(
            (
              "nd,md->nm"  # codespell:ignore nd
              if not transpose_kv_cache
              else "nd,dm->nm"  # codespell:ignore nd
            ),
            q_pe,
            k_pe_partial,
            preferred_element_type=jnp.float32,
          )

          s_list.append(s_c_partial + s_pe_partial)

        s = jnp.concatenate(s_list, axis=1)
      else:
        q = jnp.concatenate([ql_nope, q_pe], axis=-1)
        k = jnp.concatenate([kv_c, k_pe], axis=-1 if not transpose_kv_cache else -2)
        s = jnp.einsum(
          (
            "nd,md->nm"  # codespell:ignore nd
            if not transpose_kv_cache
            else "nd,dm->nm"  # codespell:ignore nd
          ),
          q,
          k,
          preferred_element_type=jnp.float32,
        )
      s *= sm_scale
      if k_scale is not None:
        s *= k_scale
      if q_scale is not None:
        s *= q_scale

      if soft_cap is not None:
        s = soft_cap * jnp.tanh(s / soft_cap)
      s = s.astype(s_dtype)

      if s.shape[0] % num_q_heads == 0 and s.shape[1] % 128 == 0:
        # Optimized mask logic.
        threshold = kv_len - q_len + (bq_idx * bq_sz + bq_offset) - bkv_idx * bkv_sz
        iota1 = lax.broadcasted_iota(jnp.int32, (num_q_heads, 128), 1)
        s_list = []
        for s_idx in range(s.shape[1] // 128):
          s_1d_list = []
          threshold_per_s = threshold - (s_idx * 128)
          for q_idx in range(s.shape[0] // num_q_heads):
            threshold_per_sq = threshold_per_s + q_idx
            mask_part = threshold_per_sq < iota1
            if sliding_window is not None:
              mask_part = jnp.logical_or(
                mask_part, threshold_per_sq - sliding_window >= iota1
              )
            s_1d_list.append(
              jnp.where(
                mask_part,
                mask_value,
                s[
                  q_idx * num_q_heads : (q_idx + 1) * num_q_heads,
                  s_idx * 128 : (s_idx + 1) * 128,
                ],
              )
            )
          s_list.append(jnp.concatenate(s_1d_list, axis=0))
        s = jnp.concatenate(s_list, axis=1)
      else:
        # Reference mask logic.
        q_span = (
          kv_len
          - q_len
          + (bq_idx * bq_sz + bq_offset)
          + unsigned_floor_div(lax.broadcasted_iota(jnp.int32, s.shape, 0), num_q_heads)
        )
        k_span = bkv_idx * bkv_sz + lax.broadcasted_iota(jnp.int32, s.shape, 1)
        mask = q_span < k_span

        if sliding_window is not None:
          mask = jnp.logical_or(mask, q_span - sliding_window >= k_span)
        s = jnp.where(mask, mask_value, s)
      s_rowmax = jnp.max(s, axis=1, keepdims=True)
      m_prev = load_with_init(head_m_ref, -jnp.inf)
      m_curr = jnp.maximum(m_prev, s_rowmax)
      head_m_ref[...] = m_curr
      p = jnp.exp(s - broadcast_minor(m_curr, s.shape))

      exp_m_diff = jnp.exp(m_prev - m_curr)
      l_prev = load_with_init(head_l_ref, 0.0)
      p_rowsum = jnp.sum(p, axis=1, keepdims=True)
      l_curr = exp_m_diff * l_prev + p_rowsum
      head_l_ref[...] = l_curr

      return p, exp_m_diff

    def flash_attention_step2_pv(
      p,
      v,
      exp_m_diff,
      bkv_idx,
      head_acc_ref,
    ):
      def load_with_init(ref, init_val):
        return jnp.where(bkv_idx == 0, jnp.full_like(ref, init_val), ref[...])

      if p_same_dtype_as_v:
        p = p.astype(v.dtype)
      if transpose_kv_cache:
        v = v.transpose((1, 0))
        # Bitcast trick to prevent transpose merge into vmatpush.xpose.
        # It enforce transpose using XLUs (vxpose.xlu).
        v = tpu_proxy.bitcast(v, v.dtype)

      pv = jnp.einsum(
        "nm,md->nd",  # codespell:ignore nd
        p,
        v,
        preferred_element_type=jnp.float32,
      )

      if v_scale is not None:
        pv *= v_scale

      o_prev = load_with_init(head_acc_ref, 0.0)
      o_curr = broadcast_minor(exp_m_diff, o_prev.shape) * o_prev + pv
      head_acc_ref[...] = o_curr

    def _async_copy(src, dst, sem, wait):
      if debug_mode:
        # Skip DMA if debug mode is enabled.
        return
      cp = tpu_proxy.make_async_copy(src, dst, sem)
      if wait:
        cp.wait()
      else:
        cp.start()

    def _fetch_transposed_bkv(batch_start_seq_idx, bkv_idx, bkv_sem_idx, *, wait=False):
      if not wait:
        # Make sure the current bkv buffer is safe to overwrite.
        wait_update_kv_cache(bkv_sem_idx)

      offsets = []
      update_szs = []
      for b in range(batch_size):
        sem = dma_semaphores.at[0, b, bkv_sem_idx]
        # bkvc_x2_ref shape: [2, batch_size, lkv_dim_per_kv_packing, kv_packing, bkv_sz]
        bkvc_vmem_ref = bkvc_x2_ref.at[bkv_sem_idx, b]
        bkvpe_vmem_ref = bkpe_x2_ref.at[bkv_sem_idx, b]

        seq_idx = batch_start_seq_idx + b
        kv_len = kv_lens_ref[seq_idx]
        kv_len_start = bkv_idx * bkv_sz
        kv_p_start = bkv_idx * bkv_p

        q_start = cu_q_lens_ref[seq_idx]
        q_end = cu_q_lens_ref[seq_idx + 1]
        q_len = q_end - q_start

        kv_left = jnp.maximum(kv_len - kv_len_start, 0)
        kv_left_frm_cache = jnp.maximum(kv_left - q_len, 0)

        kv_left_frm_new = jnp.maximum(kv_left - kv_left_frm_cache, 0)

        bkv_sz_frm_cache = jnp.minimum(kv_left_frm_cache, bkv_sz)
        bkv_sz_frm_cache_aligned = align_to(bkv_sz_frm_cache, 128)
        bkv_sz_frm_new = jnp.minimum(bkv_sz - bkv_sz_frm_cache, kv_left_frm_new)
        new_kv_start_idx = bkv_sz_frm_cache_aligned + 128

        page_indices_offset = seq_idx * pages_per_seq + kv_p_start

        new_kv_len_start = q_end - kv_left_frm_new
        new_kv_len_end = new_kv_len_start + bkv_sz_frm_new

        aligned_new_kv_len_start = unsigned_floor_div(new_kv_len_start, 128) * 128
        aligned_new_kv_len_end = unsigned_align_to(new_kv_len_end, 128)
        aligned_new_kv_len_size = align_to(
          aligned_new_kv_len_end - aligned_new_kv_len_start, 128
        )
        aligned_new_kv_len_size = lax.select(
          bkv_sz_frm_new == 0, 0, aligned_new_kv_len_size
        )

        debug_print(
          "[RPA debug]"
          f" -----------{'wait' if wait else 'start'}_fetch_transposed_bkv-----------"
        )
        debug_print("[RPA debug] seq_idx={}", seq_idx)
        debug_print("[RPA debug] bkv_idx={}", bkv_idx)
        debug_print("[RPA debug] bkv_sem_idx={}", bkv_sem_idx)
        debug_print("[RPA debug] kv_len_start={}", kv_len_start)
        debug_print("[RPA debug] kv_p_start={}", kv_p_start)
        debug_print("[RPA debug] kv_left={}", kv_left)
        debug_print("[RPA debug] kv_left_frm_cache={}", kv_left_frm_cache)
        debug_print("[RPA debug] kv_left_frm_new={}", kv_left_frm_new)
        debug_print("[RPA debug] bkv_sz_frm_cache={}", bkv_sz_frm_cache)
        debug_print("[RPA debug] page_indices_offset={}", page_indices_offset)
        debug_print(f"[RPA debug] bkvc_vmem_ref.shape: {bkvc_vmem_ref.shape}")
        debug_print(f"[RPA debug] bkvpe_vmem_ref.shape: {bkvpe_vmem_ref.shape}")

        if not wait:
          # Fetch effective kv from kv cache. To pipeline multiple DMA calls, we
          # utilize static for loop instead of dynamic for loop.
          # Loop through all pages in a block
          def _copy_page_0(
            i,
            _unused_carry,
            kv_left_frm_cache=kv_left_frm_cache,
            page_indices_offset=page_indices_offset,
            bkvc_vmem_ref=bkvc_vmem_ref,
            bkvpe_vmem_ref=bkvpe_vmem_ref,
            sem=sem,
          ):
            # Ensure only effective kvs are copied and we don't go negative.
            copy_len = jnp.clip(
              kv_left_frm_cache - i * page_size,
              0,
              page_size,
            )
            copy_len = align_to(copy_len, 128)
            # If the page index is out of bound, we set page_idx to the last page.
            # And there will be no copy since sz will be 0.
            page_idx = jnp.minimum(page_indices_offset + i, num_page_indices - 1)
            _async_copy(
              cache_kv_hbm_ref.at[
                page_indices_ref[page_idx],
                :nope_dim,
                pl_proxy.ds(0, copy_len),
              ],
              bkvc_vmem_ref.at[:, pl_proxy.ds(i * page_size, copy_len)],
              sem,
              wait,
            )
            _async_copy(
              cache_kv_hbm_ref.at[
                page_indices_ref[page_idx],
                nope_dim:,
                pl_proxy.ds(0, copy_len),
              ],
              bkvpe_vmem_ref.at[:kv_r_dim, pl_proxy.ds(i * page_size, copy_len)],
              sem,
              wait,
            )
            debug_print(
              "[RPA debug] loop_body bkv_p={}, i={}, page_size_per_lane={},"
              " copy_len={}, page_idx={}, page_indices_ref[page_idx]={}",
              bkv_p,
              i,
              page_size_per_lane,
              copy_len,
              page_idx,
              page_indices_ref[page_idx],
            )
            return None

          if interpret:
            lax.fori_loop(0, bkv_p, _copy_page_0, None)
          else:
            for i in range(bkv_p):
              _copy_page_0(i, None)

          # Fetch new KVs by appending to the existing vmem buffers.
          # Fetch either up to the end of the buffer or kv_left_frm_new, whichever
          # is smaller. Since DMAs are word-aligned based on kv_packing, and the
          # boundary between the old cache and the new KV tokens might not be
          # word-aligned, we append the new KV words right after the last word
          # containing old cache data. This can create "holes" (misalignments
          # within the words), which we will shift and pack correctly later.
          debug_print("[RPA debug] new_kv_len_start={}", new_kv_len_start)
          debug_print(
            "[RPA debug] new_kv_len_end={}",
            new_kv_len_end,
          )
          debug_print(
            "[RPA debug] aligned_new_kv_len_start={}", aligned_new_kv_len_start
          )
          debug_print(
            "[RPA debug] new_kv_start_idx={}",
            new_kv_start_idx,
          )
          debug_print(
            "[RPA debug] aligned_new_kv_len_size={}",
            aligned_new_kv_len_size,
          )
          debug_print(f"new_kv_c_hbm_ref.shape: {new_kv_c_hbm_ref.shape}")
          debug_print(f"new_k_pe_hbm_ref.shape: {new_k_pe_hbm_ref.shape}")
          # new_kv_len_start ~ new_kv_len_start + bkv_sz_frm_new
          # new_kv_copy_size = bkv_sz
          _async_copy(
            new_kv_c_hbm_ref.at[
              :,
              pl_proxy.ds(
                aligned_new_kv_len_start,
                aligned_new_kv_len_size,
              ),
            ],
            bkvc_vmem_ref.at[
              :,
              pl_proxy.ds(
                new_kv_start_idx,
                aligned_new_kv_len_size,
              ),
            ],
            sem,
            wait,
          )
          _async_copy(
            new_k_pe_hbm_ref.at[
              :kv_r_dim,
              pl_proxy.ds(
                aligned_new_kv_len_start,
                aligned_new_kv_len_size,
              ),
            ],
            bkvpe_vmem_ref.at[
              :kv_r_dim,
              pl_proxy.ds(
                new_kv_start_idx,
                aligned_new_kv_len_size,
              ),
            ],
            sem,
            wait,
          )

        else:
          # When we wait, we can use a dummy copy to wait for DMAs to complete where
          # src == dst. However, the dma size must be correct.
          dma_sz = bkv_sz_frm_cache_aligned + aligned_new_kv_len_size
          dst_kvc = bkvc_vmem_ref.at[:, pl_proxy.ds(0, dma_sz)]
          _async_copy(
            src=dst_kvc,
            dst=dst_kvc,
            sem=sem,
            wait=True,
          )
          dst_kvpe = bkvpe_vmem_ref.at[:kv_r_dim, pl_proxy.ds(0, dma_sz)]
          _async_copy(
            src=dst_kvpe,
            dst=dst_kvpe,
            sem=sem,
            wait=True,
          )

        # This returns the (offset, size) in units of tokens:
        #   offset: starting token index where the new KV should be stored
        #   size: number of tokens of the new KV, which is 1 in decode.
        offsets.append(kv_len_start + bkv_sz_frm_cache)
        update_szs.append(bkv_sz_frm_new)

      return offsets, update_szs

    def _fetch_bkv(batch_start_seq_idx, bkv_idx, bkv_sem_idx, *, wait=False):
      if not wait:
        # Make sure the current bkv buffer is safe to overwrite.
        wait_update_kv_cache(bkv_sem_idx)

      offsets = []
      update_szs = []
      for b in range(batch_size):
        sem = dma_semaphores.at[0, b, bkv_sem_idx]
        # bkvc_x2_ref shape: [2, batch_size, bkv_sz_per_kv_packing + 2, kv_packing,
        # lkv_dim]
        bkvc_vmem_ref = bkvc_x2_ref.at[bkv_sem_idx, b]
        bkvpe_vmem_ref = bkpe_x2_ref.at[bkv_sem_idx, b]

        # [total_num_pages, page_size_per_kv_packing, kv_packing, align_to(lkv_dim +
        # r_dim, 128)]
        # [total_num_pages * page_size_per_kv_packing, kv_packing, align_to(lkv_dim +
        # r_dim, 128)]
        reshaped_cache_hbm_ref = cache_kv_hbm_ref.reshape(
          total_num_pages * page_size_per_kv_packing,
          *cache_kv_hbm_ref.shape[2:],
        )

        seq_idx = batch_start_seq_idx + b
        kv_len = kv_lens_ref[seq_idx]
        kv_len_start = bkv_idx * bkv_sz
        kv_p_start = bkv_idx * bkv_p

        q_start = cu_q_lens_ref[seq_idx]
        q_end = cu_q_lens_ref[seq_idx + 1]
        q_len = q_end - q_start

        kv_left = jnp.maximum(kv_len - kv_len_start, 0)
        kv_left_frm_cache = jnp.maximum(kv_left - q_len, 0)
        kv_left_frm_cache_per_kv_packing = unsigned_cdiv(kv_left_frm_cache, kv_packing)
        kv_left_frm_new = jnp.maximum(kv_left - kv_left_frm_cache, 0)

        bkv_sz_frm_cache = jnp.minimum(kv_left_frm_cache, bkv_sz)
        bkv_sz_frm_new = jnp.minimum(bkv_sz - bkv_sz_frm_cache, kv_left_frm_new)
        bkv_sz_frm_cache_per_kv_packing = unsigned_cdiv(bkv_sz_frm_cache, kv_packing)
        bkv_sz_frm_new_per_kv_packing = unsigned_cdiv(bkv_sz_frm_new, kv_packing)
        page_indices_offset = seq_idx * pages_per_seq + kv_p_start

        new_kv_len_start = q_end - kv_left_frm_new
        new_kv_len_start_per_kv_packing = unsigned_floor_div(
          new_kv_len_start, kv_packing
        )
        bkv_sz_frm_new_kv_packing_to_fetch = jnp.where(
          bkv_sz_frm_new > 0,
          unsigned_cdiv(new_kv_len_start + bkv_sz_frm_new, kv_packing)
          - new_kv_len_start_per_kv_packing,
          0,
        )
        dma_bkv_sz = (
          bkv_sz_frm_cache_per_kv_packing + bkv_sz_frm_new_kv_packing_to_fetch
        )

        debug_print(
          f"[RPA debug] -----------{'wait' if wait else 'start'}_fetch_bkv-----------"
        )
        debug_print("[RPA debug] seq_idx={}", seq_idx)
        debug_print("[RPA debug] bkv_idx={}", bkv_idx)
        debug_print("[RPA debug] bkv_sem_idx={}", bkv_sem_idx)
        debug_print("[RPA debug] kv_len_start={}", kv_len_start)
        debug_print("[RPA debug] kv_p_start={}", kv_p_start)
        debug_print("[RPA debug] kv_left={}", kv_left)
        debug_print("[RPA debug] kv_left_frm_cache={}", kv_left_frm_cache)
        debug_print("[RPA debug] kv_left_frm_new={}", kv_left_frm_new)
        debug_print("[RPA debug] bkv_sz_frm_cache={}", bkv_sz_frm_cache)
        debug_print(
          "[RPA debug] bkv_sz_frm_cache_per_kv_packing={}",
          bkv_sz_frm_cache_per_kv_packing,
        )
        debug_print(
          "[RPA debug] bkv_sz_frm_new_per_kv_packing={}",
          bkv_sz_frm_new_per_kv_packing,
        )
        debug_print("[RPA debug] page_indices_offset={}", page_indices_offset)
        debug_print(f"[RPA debug] bkvc_vmem_ref.shape: {bkvc_vmem_ref.shape}")
        debug_print(f"[RPA debug] bkvpe_vmem_ref.shape: {bkvpe_vmem_ref.shape}")

        if not wait:
          # Fetch effective kv from kv cache. To pipeline multiple DMA calls, we
          # utilize static for loop instead of dynamic for loop.
          # Loop through all pages in a block
          def _copy_page_1(
            i,
            _unused_carry,
            kv_left_frm_cache_per_kv_packing=kv_left_frm_cache_per_kv_packing,
            page_indices_offset=page_indices_offset,
            reshaped_cache_hbm_ref=reshaped_cache_hbm_ref,
            bkvc_vmem_ref=bkvc_vmem_ref,
            bkvpe_vmem_ref=bkvpe_vmem_ref,
            sem=sem,
          ):
            # Ensure only effective kvs are copied and we don't go negative.
            sz_per_kv_packing = jnp.clip(
              kv_left_frm_cache_per_kv_packing - i * page_size_per_kv_packing,
              0,
              page_size_per_kv_packing,
            )
            # If the page index is out of bound, we set page_idx to the last page.
            # And there will be no copy since sz will be 0.
            page_idx = jnp.minimum(page_indices_offset + i, num_page_indices - 1)
            _async_copy(
              reshaped_cache_hbm_ref.at[
                pl_proxy.ds(
                  page_indices_ref[page_idx] * page_size_per_kv_packing,
                  sz_per_kv_packing,
                ),
                ...,
                :nope_dim,
              ],
              # [bkv_sz_per_kv_packing + 2, kv_packing, lkv_dim].
              bkvc_vmem_ref.at[
                pl_proxy.ds(i * page_size_per_kv_packing, sz_per_kv_packing)
              ],
              sem,
              wait,
            )
            _async_copy(
              reshaped_cache_hbm_ref.at[
                pl_proxy.ds(
                  page_indices_ref[page_idx] * page_size_per_kv_packing,
                  sz_per_kv_packing,
                ),
                ...,
                nope_dim:,
              ],
              # [bkv_sz_per_kv_packing + 2, kv_packing, r_dim].
              bkvpe_vmem_ref.at[
                pl_proxy.ds(i * page_size_per_kv_packing, sz_per_kv_packing)
              ],
              sem,
              wait,
            )
            debug_print(
              "[RPA debug] loop_body bkv_p={}, i={}, page_size_per_kv_packing={},"
              " sz_per_kv_packing={}, page_idx={}, page_indices_ref[page_idx]={}",
              bkv_p,
              i,
              page_size_per_kv_packing,
              sz_per_kv_packing,
              page_idx,
              page_indices_ref[page_idx],
            )
            return None

          if interpret:
            lax.fori_loop(0, bkv_p, _copy_page_1, None)
          else:
            for i in range(bkv_p):
              _copy_page_1(i, None)

          # Fetch new KVs by appending to the existing vmem buffers.
          # Fetch either up to the end of the buffer or kv_left_frm_new, whichever
          # is smaller. Since DMAs are word-aligned based on kv_packing, and the
          # boundary between the old cache and the new KV tokens might not be
          # word-aligned, we append the new KV words right after the last word
          # containing old cache data. This can create "holes" (misalignments
          # within the words), which we will shift and pack correctly later.
          debug_print("[RPA debug] new_kv_len_start={}", new_kv_len_start)
          debug_print(
            "[RPA debug] new_kv_len_start_per_kv_packing={}",
            new_kv_len_start_per_kv_packing,
          )
          debug_print(f"new_kv_c_hbm_ref.shape: {new_kv_c_hbm_ref.shape}")
          debug_print(f"new_k_pe_hbm_ref.shape: {new_k_pe_hbm_ref.shape}")
          _async_copy(
            new_kv_c_hbm_ref.at[
              pl_proxy.ds(
                new_kv_len_start_per_kv_packing,
                bkv_sz_frm_new_kv_packing_to_fetch,
              )
            ],
            bkvc_vmem_ref.at[
              pl_proxy.ds(
                bkv_sz_frm_cache_per_kv_packing,
                bkv_sz_frm_new_kv_packing_to_fetch,
              )
            ],
            sem,
            wait,
          )
          _async_copy(
            new_k_pe_hbm_ref.at[
              pl_proxy.ds(
                new_kv_len_start_per_kv_packing,
                bkv_sz_frm_new_kv_packing_to_fetch,
              )
            ],
            bkvpe_vmem_ref.at[
              pl_proxy.ds(
                bkv_sz_frm_cache_per_kv_packing,
                bkv_sz_frm_new_kv_packing_to_fetch,
              )
            ],
            sem,
            wait,
          )

        else:
          # When we wait, we can use a dummy copy to wait for DMAs to complete where
          # src == dst. However, the dma size must be correct.
          dst_kvc = bkvc_vmem_ref.at[pl_proxy.ds(0, dma_bkv_sz)]
          _async_copy(
            src=dst_kvc,
            dst=dst_kvc,
            sem=sem,
            wait=True,
          )
          dst_kvpe = bkvpe_vmem_ref.at[pl_proxy.ds(0, dma_bkv_sz)]
          _async_copy(
            src=dst_kvpe,
            dst=dst_kvpe,
            sem=sem,
            wait=True,
          )
        # This returns the (offset, size) in units of tokens:
        #   offset: starting token index where the new KV should be stored
        #   size: number of tokens of the new KV, which is 1 in decode.
        offsets.append(kv_len_start + bkv_sz_frm_cache)
        update_szs.append(bkv_sz_frm_new)

      return offsets, update_szs

    def _pack_new_transposed_kv(bkv_sem_idx, bkv_idx, offsets, update_szs):
      """Packs newly computed KVs into the correct sub-word alignment in VMEM.

      Args:
        bkv_sem_idx: The semaphore index for the current KV block.
        bkv_idx: The index of the current KV block.
        offsets: A list of the starting token offset in the KV cache where the
          new KVs begin for a batch.
        update_szs: A list of the number of new tokens to be packed for a batch.
      """
      debug_print("[RPA debug] -----------_pack_new_transposed_kv-----------")
      for b in range(batch_size):
        update_sz = update_szs[b]

        @pl_proxy.when(update_sz > 0)
        def _update(b=b, update_sz=update_sz):
          bkvc_vmem_ref = bkvc_x2_ref.at[bkv_sem_idx, b]
          bkvpe_vmem_ref = bkpe_x2_ref.at[bkv_sem_idx, b]

          seq_idx = batch_start_seq_idx + b
          kv_len = kv_lens_ref[seq_idx]
          kv_len_start = bkv_idx * bkv_sz

          q_start = cu_q_lens_ref[seq_idx]
          q_end = cu_q_lens_ref[seq_idx + 1]
          q_len = q_end - q_start

          kv_left = jnp.maximum(kv_len - kv_len_start, 0)
          kv_left_frm_cache = jnp.maximum(kv_left - q_len, 0)

          kv_left_frm_new = jnp.maximum(kv_left - kv_left_frm_cache, 0)
          bkv_sz_frm_cache = jnp.minimum(kv_left_frm_cache, bkv_sz)
          bkv_sz_frm_cache_aligned = align_to(bkv_sz_frm_cache, 128)
          new_kv_start_idx = bkv_sz_frm_cache_aligned + 128

          new_kv_len_start = q_end - kv_left_frm_new
          new_kv_len_end = new_kv_len_start + update_sz
          new_kv_len_sz = new_kv_len_end - new_kv_len_start

          aligned_new_kv_len_start = unsigned_floor_div(new_kv_len_start, 128) * 128
          aligned_new_kv_len_end = unsigned_align_to(new_kv_len_end, 128)
          aligned_new_kv_len_size = unsigned_align_to(
            aligned_new_kv_len_end - aligned_new_kv_len_start, 128
          )
          aligned_new_kv_len_size = lax.select(
            update_sz == 0, 0, aligned_new_kv_len_size
          )

          new_kv_buffer_start = unsigned_mod(new_kv_len_start, 128)
          new_kv_buffer_end = new_kv_buffer_start + new_kv_len_sz
          debug_print("[RPA debug] new_kv_buffer_start={}", new_kv_buffer_start)
          debug_print(
            "[RPA debug] new_kv_buffer_end={}",
            new_kv_buffer_end,
          )
          debug_print(
            "[RPA debug] aligned_new_kv_len_start={}", aligned_new_kv_len_start
          )
          debug_print(
            "[RPA debug] aligned_new_kv_len_size={}",
            aligned_new_kv_len_size,
          )

          loop_sz = unsigned_floor_div(aligned_new_kv_len_size, 128)

          # TODO(kimjaehong) : Do we have to consider copy_sz?
          def copy_partial(new_kv_idx, new_kv_start, bkv_idx, bkv_start):
            new_kvc_idx = new_kv_start_idx + new_kv_idx * 128
            debug_print(
              "[RPA debug] copy_partial new_kv_idx={}, new_kv_start={},"
              " bkv_idx={}, bkv_start={}, new_kvc_idx={}",
              new_kv_idx,
              new_kv_start,
              bkv_idx,
              bkv_start,
              new_kvc_idx,
            )
            new_kvc_ref = bkvc_vmem_ref.bitcast(jnp.uint32).at[
              ..., pl_proxy.ds(new_kvc_idx, 128)
            ]
            bkvc_ref = bkvc_vmem_ref.bitcast(jnp.uint32).at[
              ..., pl_proxy.ds(bkv_idx * 128, 128)
            ]
            rolled_new_kvc = tpu_proxy.roll(
              new_kvc_ref[...], shift=bkv_start - new_kv_start, axis=1
            )
            bkvc_ref[...] = lax.select(
              lax.broadcasted_iota(jnp.int32, bkvc_ref.shape, 1) < bkv_start,
              bkvc_ref[...],
              rolled_new_kvc,
            )

            new_kvpe_ref = bkvpe_vmem_ref.bitcast(jnp.uint32).at[
              ..., pl_proxy.ds(new_kvc_idx, 128)
            ]
            bkvpe_ref = bkvpe_vmem_ref.bitcast(jnp.uint32).at[
              ..., pl_proxy.ds(bkv_idx * 128, 128)
            ]
            rolled_new_kvpe = tpu_proxy.roll(
              new_kvpe_ref[...], shift=bkv_start - new_kv_start, axis=1
            )
            bkvpe_ref[...] = lax.select(
              lax.broadcasted_iota(jnp.int32, bkvpe_ref.shape, 1) < bkv_start,
              bkvpe_ref[...],
              rolled_new_kvpe,
            )

          def update_body(idx, state):
            filled_bkv_sz, block_start = state
            block_end = block_start + 128
            bkv_idx = unsigned_floor_div(filled_bkv_sz, 128)
            start_in_bkv_block = unsigned_mod(filled_bkv_sz, 128)
            unfilled_block_sz = 128 - start_in_bkv_block

            buffer_start = jnp.maximum(block_start, new_kv_buffer_start)
            buffer_end = jnp.minimum(block_end, new_kv_buffer_end)

            buffer_sz_in_block = buffer_end - buffer_start
            start_in_block = unsigned_mod(buffer_start, 128)

            cur_filling_sz = jnp.minimum(unfilled_block_sz, buffer_sz_in_block)
            copy_partial(idx, start_in_block, bkv_idx, start_in_bkv_block)
            copy_partial(idx, start_in_block + cur_filling_sz, bkv_idx + 1, 0)
            return (filled_bkv_sz + buffer_sz_in_block, block_end)

          jax.lax.fori_loop(
            0, loop_sz, update_body, (bkv_sz_frm_cache, jnp.int32(0)), unroll=False
          )

    def _pack_new_kv(bkv_sem_idx, offsets, update_szs):
      """Packs newly computed KVs into the correct sub-word alignment in VMEM.

      When new KV tokens are DMA'd from HBM into VMEM, they are copied at the
      granularity of packed words (e.g., 4 tokens per word for fp8) by head
      dimension (mapped to lanes). The starting token `offset` in the KV cache,
      however, might not fall exactly on a word boundary. This means the elements
      within the packed words might be misaligned relative to their final
      destination in the cache.

      This function corrects this alignment by:
      1. Computing the bit-shift amount needed based on the difference between the
         destination token offset (`kv_packing_offset`) and the source token
         offset (`new_kv_packing_offset`).
      2. Looping over the affected words and using bitwise shifts and logical ORs
         to realign the elements across word boundaries.
      3. Merging the correctly aligned new KV elements into the VMEM buffer using
         a mask, leaving existing (older) KV elements intact.

      Args:
        bkv_sem_idx: The semaphore index for the current KV block.
        offsets: A list of the starting token offset in the KV cache where the
          new KVs begin for a batch.
        update_szs: A list of the number of new tokens to be packed for a batch.
      """
      debug_print("[RPA debug] -----------_pack_new_kv-----------")
      for b in range(batch_size):
        offset = offsets[b]
        update_sz = update_szs[b]

        @pl_proxy.when(update_sz > 0)
        def _update(b=b, offset=offset, update_sz=update_sz):
          # shape: [bkv_sz_per_kv_packing + 2, kv_packing, lkv_dim]
          bkvc_vmem_ref = bkvc_x2_ref.at[bkv_sem_idx, b]
          # shape: [bkv_sz_per_kv_packing + 2, kv_packing, r_dim]
          bkvpe_vmem_ref = bkpe_x2_ref.at[bkv_sem_idx, b]

          seq_idx = batch_start_seq_idx + b
          q_end = cu_q_lens_ref[seq_idx + 1]
          kv_len = kv_lens_ref[seq_idx]

          pack_new_kv(
            bkvc_vmem_ref, bkvpe_vmem_ref, offset, update_sz, q_end, kv_len, bkv_sz
          )

    def _update_transposed_kv_cache(
      batch_start_seq_idx,
      b,
      bkv_sem_idx,
      offset,  # In units of tokens.
      update_sz,  # In units of tokens.
      *,
      wait=False,
    ):
      debug_print("[RPA debug] -----------_update_transposed_kv_cache-----------")
      seq_idx = batch_start_seq_idx + b
      sem = dma_semaphores.at[3, b, bkv_sem_idx]

      bkvc_vmem_ref = bkvc_x2_ref.at[bkv_sem_idx, b]
      bkvpe_vmem_ref = bkpe_x2_ref.at[bkv_sem_idx, b]

      update_lane_iters = unsigned_cdiv(unsigned_mod(offset, 128) + update_sz, 128)
      debug_print(
        "[RPA debug] offset={}",
        offset,
      )
      debug_print(
        "[RPA debug] update_sz={}",
        update_sz,
      )
      debug_print(
        "[RPA debug] update_lane_iters={}",
        update_lane_iters,
      )

      # [total_num_pages, kv_dim_per_kv_packing, kv_packing, page_size]
      # cache_kv_hbm_shape = updated_cache_kv_hbm_ref.shape

      if not wait:
        # Issue DMA copy for the updated parts, page by page.
        kv_p_start = unsigned_floor_div(offset, page_size)
        kv_p_end = unsigned_cdiv(offset + update_sz, page_size)
        start_word_in_page = unsigned_floor_div(unsigned_mod(offset, page_size), 128)
        page_size_per_lane = unsigned_floor_div(page_size, 128)
        start_word_in_vmem = unsigned_floor_div(unsigned_mod(offset, bkv_sz), 128)
        words_to_transfer = update_lane_iters
        page_indices_offset = seq_idx * pages_per_seq + kv_p_start

        def loop_body(i, states):
          curr_word_in_page, words_to_transfer, curr_word_in_vmem = states
          sz_words = jnp.minimum(
            page_size_per_lane - curr_word_in_page, words_to_transfer
          )
          page_idx = page_indices_ref[page_indices_offset + i]
          debug_print(
            "[RPA debug] loop_idx={}, curr_word_in_page={}, words_to_transfer={}, "
            "curr_word_in_vmem={}, sz_words={}, page_idx={}",
            i,
            curr_word_in_page,
            words_to_transfer,
            curr_word_in_vmem,
            sz_words,
            page_idx,
          )

          _async_copy(
            # bkvc_vmem_ref shape:
            # [lkv_dim_per_kv_packing, kv_packing, bkv_sz]
            bkvc_vmem_ref.at[..., pl_proxy.ds(curr_word_in_vmem * 128, sz_words * 128)],
            updated_cache_kv_hbm_ref.at[
              page_idx, :nope_dim, pl_proxy.ds(curr_word_in_page * 128, sz_words * 128)
            ],
            sem,
            wait=False,
          )
          _async_copy(
            # bkvpe_vmem_ref shape:
            # [r_dim_per_kv_packing, kv_packing, bkv_sz]
            bkvpe_vmem_ref.at[
              :kv_r_dim, pl_proxy.ds(curr_word_in_vmem * 128, sz_words * 128)
            ],
            updated_cache_kv_hbm_ref.at[
              page_idx, nope_dim:, pl_proxy.ds(curr_word_in_page * 128, sz_words * 128)
            ],
            sem,
            wait=False,
          )
          return 0, words_to_transfer - sz_words, curr_word_in_vmem + sz_words

        lax.fori_loop(
          0,
          kv_p_end - kv_p_start,
          loop_body,
          (
            start_word_in_page,
            words_to_transfer,
            start_word_in_vmem,
          ),  # initial states
          unroll=False,
        )
      else:  # Wait
        dma_sz_words = update_lane_iters * 128
        dst_kv_nope = bkvc_vmem_ref.at[..., pl_proxy.ds(0, dma_sz_words)]
        _async_copy(
          src=dst_kv_nope,
          dst=dst_kv_nope,
          sem=sem,
          wait=True,
        )
        dst_kv_pe = bkvpe_vmem_ref.at[:kv_r_dim, pl_proxy.ds(0, dma_sz_words)]
        _async_copy(
          src=dst_kv_pe,
          dst=dst_kv_pe,
          sem=sem,
          wait=True,
        )

    def _update_kv_cache(
      batch_start_seq_idx,
      b,
      bkv_sem_idx,
      offset,  # In units of tokens.
      update_sz,  # In units of tokens.
      *,
      wait=False,
    ):
      seq_idx = batch_start_seq_idx + b
      sem = dma_semaphores.at[3, b, bkv_sem_idx]
      # shape: [bkv_sz_per_kv_packing + 2, kv_packing, lkv_dim]
      bkvc_vmem_ref = bkvc_x2_ref.at[bkv_sem_idx, b]
      # shape: [bkv_sz_per_kv_packing + 2, kv_packing, r_dim]
      bkvpe_vmem_ref = bkpe_x2_ref.at[bkv_sem_idx, b]

      update_kv_packing_iters = unsigned_cdiv(
        unsigned_mod(offset, kv_packing) + update_sz, kv_packing
      )

      # Expected shape:
      # [total_num_pages, page_size_per_kv_packing, kv_packing,
      # align_to(lkv_dim + r_dim, 128)]
      cache_kv_hbm_shape = updated_cache_kv_hbm_ref.shape
      reshaped_cache_kv_hbm_ref = updated_cache_kv_hbm_ref.reshape(
        cache_kv_hbm_shape[0] * cache_kv_hbm_shape[1],
        *cache_kv_hbm_shape[2:],
      )

      if not wait:
        # Issue DMA copy for the updated parts, page by page.
        kv_p_start = unsigned_floor_div(offset, page_size)
        kv_p_end = unsigned_cdiv(offset + update_sz, page_size)
        start_word_in_page = unsigned_floor_div(
          unsigned_mod(offset, page_size), kv_packing
        )
        start_word_in_vmem = unsigned_floor_div(
          unsigned_mod(offset, bkv_sz), kv_packing
        )
        words_to_transfer = update_kv_packing_iters
        page_indices_offset = seq_idx * pages_per_seq + kv_p_start

        def loop_body(i, states):
          curr_word_in_page, words_to_transfer, curr_word_in_vmem = states
          sz_words = jnp.minimum(
            page_size_per_kv_packing - curr_word_in_page, words_to_transfer
          )
          page_idx = page_indices_ref[page_indices_offset + i]

          _async_copy(
            # bkvc_vmem_ref shape:
            # [bkv_sz_per_kv_packing+2, kv_packing, lkv_dim]
            bkvc_vmem_ref.at[pl_proxy.ds(curr_word_in_vmem, sz_words)],
            reshaped_cache_kv_hbm_ref.at[
              pl_proxy.ds(
                page_idx * page_size_per_kv_packing + curr_word_in_page,
                sz_words,
              ),
              ...,
              :nope_dim,
            ],
            sem,
            wait=False,
          )
          _async_copy(
            # bkvpe_vmem_ref shape: [bkv_sz_per_kv_packing+2, kv_packing, r_dim]
            bkvpe_vmem_ref.at[pl_proxy.ds(curr_word_in_vmem, sz_words)],
            reshaped_cache_kv_hbm_ref.at[
              pl_proxy.ds(
                page_idx * page_size_per_kv_packing + curr_word_in_page,
                sz_words,
              ),
              ...,
              nope_dim:,
            ],
            sem,
            wait=False,
          )
          return 0, words_to_transfer - sz_words, curr_word_in_vmem + sz_words

        lax.fori_loop(
          0,
          kv_p_end - kv_p_start,
          loop_body,
          (
            start_word_in_page,
            words_to_transfer,
            start_word_in_vmem,
          ),  # initial states
          unroll=False,
        )
      else:  # Wait
        dma_sz_words = update_kv_packing_iters
        # bkvc_vmem_ref shape: [bkv_sz_per_kv_packing + 2, kv_packing, lkv_dim]
        dst_kv = bkvc_vmem_ref.at[pl_proxy.ds(0, dma_sz_words)]
        _async_copy(
          src=dst_kv,
          dst=dst_kv,
          sem=sem,
          wait=True,
        )
        dst_kv = bkvpe_vmem_ref.at[pl_proxy.ds(0, dma_sz_words)]
        _async_copy(
          src=dst_kv,
          dst=dst_kv,
          sem=sem,
          wait=True,
        )

    def _fetch_bq(batch_start_seq_idx, bq_idx, bq_sem_idx, *, wait=False):
      for b in range(batch_size):
        sem = dma_semaphores.at[1, b, bq_sem_idx]
        bq_nope_vmem_ref = bq_nope_x2_ref.at[bq_sem_idx, b]
        bq_rope_vmem_ref = bq_rope_x2_ref.at[bq_sem_idx, b]

        seq_idx = batch_start_seq_idx + b
        q_len_start = cu_q_lens_ref[seq_idx] + bq_idx * bq_sz
        q_end = cu_q_lens_ref[seq_idx + 1]
        sz = jnp.minimum(bq_sz, q_end - q_len_start)

        debug_print(
          f"[RPA debug] -----------{'wait' if wait else 'start'}_fetch_bq-----------"
        )
        debug_print("[RPA debug] seq_idx={}", seq_idx)
        debug_print("[RPA debug] bq_idx={}", bq_idx)
        debug_print("[RPA debug] bq_sem_idx={}", bq_sem_idx)
        debug_print("[RPA debug] q_len_start={}", q_len_start)
        debug_print("[RPA debug] q_end={}", q_end)
        debug_print("[RPA debug] sz={}", sz)

        @pl_proxy.when(sz > 0)
        def _copy(
          q_len_start=q_len_start,
          sz=sz,
          bq_nope_vmem_ref=bq_nope_vmem_ref,
          bq_rope_vmem_ref=bq_rope_vmem_ref,
          sem=sem,
        ):
          _async_copy(
            ql_nope_hbm_ref.at[pl_proxy.ds(q_len_start, sz)],
            bq_nope_vmem_ref.at[pl_proxy.ds(0, sz)],
            sem,
            wait,
          )

          _async_copy(
            q_pe_hbm_ref.at[pl_proxy.ds(q_len_start, sz)],
            bq_rope_vmem_ref.at[pl_proxy.ds(0, sz)],
            sem,
            wait,
          )

    def _send_bo(batch_start_seq_idx, bo_idx, bo_sem_idx, *, wait=False):
      for b in range(batch_size):
        sem = dma_semaphores.at[2, b, bo_sem_idx]
        vmem_ref = bo_x2_ref.at[bo_sem_idx, b]

        seq_idx = batch_start_seq_idx + b
        q_len_start = cu_q_lens_ref[seq_idx] + bo_idx * bq_sz
        q_end = cu_q_lens_ref[seq_idx + 1]
        sz = jnp.minimum(bq_sz, q_end - q_len_start)

        debug_print(
          f"[RPA debug] -----------{'wait' if wait else 'start'}_send_bo-----------"
        )
        debug_print("[RPA debug] seq_idx={}", seq_idx)
        debug_print("[RPA debug] bo_idx={}", bo_idx)
        debug_print("[RPA debug] bo_sem_idx={}", bo_sem_idx)
        debug_print("[RPA debug] q_len_start={}", q_len_start)
        debug_print("[RPA debug] q_end={}", q_end)
        debug_print("[RPA debug] sz={}", sz)

        @pl_proxy.when(sz > 0)
        def _copy(vmem_ref=vmem_ref, sz=sz, q_len_start=q_len_start, sem=sem):
          _async_copy(
            vmem_ref.at[pl_proxy.ds(0, sz)],
            o_hbm_ref.at[pl_proxy.ds(q_len_start, sz)],
            sem,
            wait,
          )

    # TODO(b/506245022): Split out the KV handling functions into separate files.
    if not transpose_kv_cache:
      fetch_bkv_fn = _fetch_bkv
    else:
      fetch_bkv_fn = _fetch_transposed_bkv

    def start_fetch_bkv(batch_start_seq_idx, bkv_idx, bkv_sem_idx):
      return fetch_bkv_fn(batch_start_seq_idx, bkv_idx, bkv_sem_idx)

    def wait_fetch_bkv(batch_start_seq_idx, bkv_idx, bkv_sem_idx):
      return fetch_bkv_fn(batch_start_seq_idx, bkv_idx, bkv_sem_idx, wait=True)

    def start_fetch_bq(batch_start_seq_idx, bq_idx, bq_sem_idx):
      return _fetch_bq(batch_start_seq_idx, bq_idx, bq_sem_idx)

    def wait_fetch_bq(batch_start_seq_idx, bq_idx, bq_sem_idx):
      return _fetch_bq(batch_start_seq_idx, bq_idx, bq_sem_idx, wait=True)

    def start_send_bo(batch_start_seq_idx, bo_idx, bo_sem_idx):
      bo_ids_ref[bo_sem_idx] = batch_start_seq_idx
      bo_ids_ref[bo_sem_idx + 2] = bo_idx
      _send_bo(batch_start_seq_idx, bo_idx, bo_sem_idx)

    def wait_send_bo(bo_sem_idx):
      old_batch_start_seq_idx = bo_ids_ref[bo_sem_idx]
      old_bo_idx = bo_ids_ref[bo_sem_idx + 2]

      @pl_proxy.when(
        jnp.logical_and(
          0 <= old_batch_start_seq_idx, old_batch_start_seq_idx <= batch_start_seq_idx
        )
      )
      def _():
        _send_bo(old_batch_start_seq_idx, old_bo_idx, bo_sem_idx, wait=True)

    # TODO(b/506245022): Split out the KV handling functions into separate files.
    def start_update_kv_cache(start_seq_idx, bkv_sem_idx, offsets, update_szs):
      for b in range(batch_size):
        offset = offsets[b]
        update_sz = update_szs[b]
        bkv_update_ids_ref[b, bkv_sem_idx + 4] = update_sz

        @pl_proxy.when(update_sz > 0)
        def _(b=b, update_sz=update_sz, start_seq_idx=start_seq_idx, offset=offset):
          bkv_update_ids_ref[b, bkv_sem_idx] = start_seq_idx
          bkv_update_ids_ref[b, bkv_sem_idx + 2] = offset
          update_kv_cache_fn = (
            _update_kv_cache if not transpose_kv_cache else _update_transposed_kv_cache
          )
          update_kv_cache_fn(start_seq_idx, b, bkv_sem_idx, offset, update_sz)

    def wait_update_kv_cache(bkv_sem_idx):
      for b in range(batch_size):
        update_sz = bkv_update_ids_ref[b, bkv_sem_idx + 4]

        @pl_proxy.when(update_sz > 0)
        def _(b=b, update_sz=update_sz):
          start_seq_idx = bkv_update_ids_ref[b, bkv_sem_idx]
          offset = bkv_update_ids_ref[b, bkv_sem_idx + 2]
          bkv_update_ids_ref[b, bkv_sem_idx + 4] = 0
          update_kv_cache_fn = (
            _update_kv_cache if not transpose_kv_cache else _update_transposed_kv_cache
          )
          update_kv_cache_fn(
            start_seq_idx, b, bkv_sem_idx, offset, update_sz, wait=True
          )

    def load_batch_bq(bq_sem_idx, *, actual_bq_sz=bq_sz):
      q_nope_ref = bq_nope_x2_ref.bitcast(jnp.uint32).at[bq_sem_idx]
      q_nope_ref = q_nope_ref.reshape(
        batch_size, bq_sz * num_q_heads_per_q_packing, lkv_dim
      )
      q_nope_vec = tpu_proxy.bitcast(
        q_nope_ref[:, : actual_bq_sz * num_q_heads_per_q_packing],
        q_dtype,
      ).reshape(batch_size, actual_bq_sz * num_q_heads, lkv_dim)
      q_rope_ref = bq_rope_x2_ref.bitcast(jnp.uint32).at[bq_sem_idx]
      q_rope_ref = q_rope_ref.reshape(
        batch_size, bq_sz * num_q_heads_per_q_packing, r_dim
      )
      q_rope_vec = tpu_proxy.bitcast(
        q_rope_ref[:, : actual_bq_sz * num_q_heads_per_q_packing],
        q_dtype,
      ).reshape(batch_size, actual_bq_sz * num_q_heads, r_dim)
      return q_nope_vec, q_rope_vec

    def load_bq(batch_item_idx, bq_sem_idx, *, actual_bq_sz=bq_sz):
      q_nope_ref = bq_nope_x2_ref.bitcast(jnp.uint32).at[bq_sem_idx, batch_item_idx]
      q_nope_ref = q_nope_ref.reshape(bq_sz * num_q_heads_per_q_packing, lkv_dim)
      q_nope_vec = tpu_proxy.bitcast(
        q_nope_ref[: actual_bq_sz * num_q_heads_per_q_packing],
        q_dtype,
      ).reshape(actual_bq_sz * num_q_heads, lkv_dim)
      q_rope_ref = bq_rope_x2_ref.bitcast(jnp.uint32).at[bq_sem_idx, batch_item_idx]
      q_rope_ref = q_rope_ref.reshape(bq_sz * num_q_heads_per_q_packing, r_dim)
      q_rope_vec = tpu_proxy.bitcast(
        q_rope_ref[: actual_bq_sz * num_q_heads_per_q_packing],
        q_dtype,
      ).reshape(actual_bq_sz * num_q_heads, r_dim)
      return q_nope_vec, q_rope_vec

    def load_bkv(batch_item_idx, bkv_sem_idx):
      bkvc_ref = bkvc_x2_ref.bitcast(jnp.uint32).at[
        bkv_sem_idx, batch_item_idx, :bkv_sz_per_kv_packing
      ]
      bkvc_ref = bkvc_ref.reshape(
        bkv_sz_per_kv_packing * num_sublanes_for_kv_packing, lkv_dim
      )
      bkvc_vec = tpu_proxy.bitcast(bkvc_ref[...], kv_dtype).reshape(bkv_sz, lkv_dim)
      bkpe_ref = bkpe_x2_ref.bitcast(jnp.uint32).at[
        bkv_sem_idx, batch_item_idx, :bkv_sz_per_kv_packing
      ]
      bkpe_ref = bkpe_ref.reshape(
        bkv_sz_per_kv_packing * num_sublanes_for_kv_packing, r_dim
      )
      bkpe_vec = tpu_proxy.bitcast(bkpe_ref[...], kv_dtype).reshape(bkv_sz, r_dim)
      return bkvc_vec, bkpe_vec

    def load_transposed_bkv(batch_item_idx, bkv_sem_idx):
      bkvc_vec = bkvc_x2_ref.at[bkv_sem_idx, batch_item_idx, :, :bkv_sz][...]
      bkpe_vec = bkpe_x2_ref.at[bkv_sem_idx, batch_item_idx, :, :bkv_sz][...]
      return bkvc_vec, bkpe_vec

    def load_batch_bkv(load_bkv_fn, bkv_sem_idx):
      bkvc_vecs = []
      bkpe_vecs = []
      for b in range(batch_size):
        bkvc_vec, bkpe_vec = load_bkv_fn(b, bkv_sem_idx)
        bkvc_vecs.append(bkvc_vec)
        bkpe_vecs.append(bkpe_vec)
      return jnp.stack(bkvc_vecs), jnp.stack(bkpe_vecs)

    def broadcast_minor(src, shape):
      if src.shape == shape:
        return src
      assert src.shape[:-1] == shape[:-1]
      assert src.shape[-1] % 128 == 0
      target_minor = unsigned_align_to(shape[-1], src.shape[-1])
      # no-op concatenation.
      return jnp.concatenate(
        [src for _ in range(target_minor // src.shape[-1])], axis=-1
      )[..., : shape[-1]]

    kv_len_max = kv_lens_ref[batch_start_seq_idx]
    for b in range(1, batch_size):
      kv_len_max = jnp.where(
        kv_lens_ref[batch_start_seq_idx + b] > kv_len_max,
        kv_lens_ref[batch_start_seq_idx + b],
        kv_len_max,
      )

    q_len_max = (
      cu_q_lens_ref[batch_start_seq_idx + 1] - cu_q_lens_ref[batch_start_seq_idx]
    )
    for b in range(1, batch_size):
      q_len_b = (
        cu_q_lens_ref[batch_start_seq_idx + b + 1]
        - cu_q_lens_ref[batch_start_seq_idx + b]
      )
      q_len_max = jnp.where(q_len_b > q_len_max, q_len_b, q_len_max)

    def process():
      num_bkv = unsigned_cdiv(kv_len_max, bkv_sz)
      if static_q_len is None:
        actual_bq_sz = bq_sz
        num_bq = unsigned_cdiv(q_len_max, actual_bq_sz)
      else:
        actual_bq_sz = min(bq_sz, static_q_len)
        num_bq = unsigned_cdiv(static_q_len, actual_bq_sz)

      debug_print("[RPA debug] process")
      debug_print("[RPA debug] num_bkv={}", num_bkv)  # num_bkv=3, bkv_sz=512
      debug_print("[RPA debug] bkv_sz={}", bkv_sz)
      debug_print("[RPA debug] num_bq={}", num_bq)
      debug_print("[RPA debug] kv_len_max={}", kv_len_max)
      debug_print("[RPA debug] q_len_max={}", q_len_max)

      def get_next_bq_ids(seq_idx, bq_idx, bq_sem_idx):
        next_bq_idx = bq_idx + 1
        is_last_bq = next_bq_idx == num_bq
        next_bq_idx = lax.select(is_last_bq, 0, next_bq_idx)
        next_seq_idx = lax.select(is_last_bq, seq_idx + batch_size, seq_idx)
        next_bq_sem_idx = lax.select(bq_sem_idx == 0, 1, 0)
        return next_seq_idx, next_bq_idx, next_bq_sem_idx

      def get_next_bkv_ids(seq_idx, bq_idx, bkv_idx, bkv_sem_idx):
        next_bkv_idx = bkv_idx + 1
        is_last_bkv = next_bkv_idx == num_bkv
        next_bkv_idx = lax.select(is_last_bkv, 0, next_bkv_idx)
        next_bq_idx = lax.select(is_last_bkv, bq_idx + 1, bq_idx)
        is_last_bq = next_bq_idx == num_bq
        next_bq_idx = lax.select(is_last_bq, 0, next_bq_idx)
        next_seq_idx = lax.select(is_last_bq, seq_idx + batch_size, seq_idx)
        next_bkv_sem_idx = lax.select(bkv_sem_idx == 0, 1, 0)
        return next_seq_idx, next_bq_idx, next_bkv_idx, next_bkv_sem_idx

      def compute_with_bq(bq_idx, _):
        bq_sem_idx = sem_ids_ref[0]
        next_seq_idx, next_bq_idx, next_bq_sem_idx = get_next_bq_ids(
          batch_start_seq_idx, bq_idx, bq_sem_idx
        )

        # Prefetch next bq
        @pl_proxy.when(next_seq_idx < end_seq_idx)
        def prefetch_next_bq():
          sem_ids_ref[0] = next_bq_sem_idx
          start_fetch_bq(next_seq_idx, next_bq_idx, next_bq_sem_idx)

        def compute_with_bkv(bkv_idx, _):
          # Get next bkv ids.
          bkv_sem_idx = sem_ids_ref[1]
          next_seq_idx, _, next_bkv_idx, next_bkv_sem_idx = get_next_bkv_ids(
            batch_start_seq_idx, bq_idx, bkv_idx, bkv_sem_idx
          )

          # Prefetch next bkv
          @pl_proxy.when(next_seq_idx < end_seq_idx)
          def prefetch_next_bkv():
            sem_ids_ref[1] = next_bkv_sem_idx
            start_fetch_bkv(next_seq_idx, next_bkv_idx, next_bkv_sem_idx)

          # Wait for cur bq if not ready yet
          @pl_proxy.when(bkv_idx == 0)
          def wait_cur_bq():
            wait_fetch_bq(batch_start_seq_idx, bq_idx, bq_sem_idx)

          # Wait for cur bkv
          offsets, update_szs = wait_fetch_bkv(
            batch_start_seq_idx, bkv_idx, bkv_sem_idx
          )

          # TODO(b/506245022): Split out the KV handling functions into separate
          # files.
          # Pack and align new KVs in VMEM if the block has new KVs.
          # We may have to do this for each block of KV in VMEM.
          if not transpose_kv_cache:
            _pack_new_kv(bkv_sem_idx, offsets, update_szs)
          else:
            _pack_new_transposed_kv(bkv_sem_idx, bkv_idx, offsets, update_szs)

          # Start updating bkv to kv cache if applicable.
          # Only needed in first bq loop.
          @pl_proxy.when(bq_idx == 0)
          def update_cur_bkv_to_cache():
            start_update_kv_cache(batch_start_seq_idx, bkv_sem_idx, offsets, update_szs)

          # Load bkv into vreg. There is no need to mask out invalid k/v entries,
          # because the score of invalid Q.K^T pairs are masked (to be zero) in
          # flash attention, so that the invalid kv entries
          # (as long as they are not NaN or inf) won't affect to the output.
          # TODO(b/506245022): Split out the KV handling functions into separate
          # files.
          if not transpose_kv_cache:
            load_bkv_fn = load_bkv
          else:
            load_bkv_fn = load_transposed_bkv

          if two_step_flash_attention:
            debug_print("[RPA debug] two step flash attention")
            prev_p = None
            prev_v = None
            prev_acc = None
            prev_exp_m_diff = None

            q_len = actual_bq_sz * num_q_heads
            q_block_len = q_len // q_split
            bq_sz_for_each_split = bq_sz // q_split
            for b in range(batch_size):
              bkvc, bkpe = load_bkv_fn(
                b,
                bkv_sem_idx,
              )
              bq_nope_vec, bq_pe_vec = load_bq(b, bq_sem_idx, actual_bq_sz=actual_bq_sz)
              debug_print(
                "[RPA debug] bq_nope_vec.shape={}, {}",
                bq_nope_vec.shape[0],
                bq_nope_vec.shape[1],
              )
              debug_print(
                "[RPA debug] bq_pe_vec.shape={}, {}",
                bq_pe_vec.shape[0],
                bq_pe_vec.shape[1],
              )
              debug_print("[RPA debug] bkvc.shape={}, {}", bkvc.shape[0], bkvc.shape[1])
              debug_print("[RPA debug] bkpe.shape={}, {}", bkpe.shape[0], bkpe.shape[1])

              cu_q_len = (
                cu_q_lens_ref[batch_start_seq_idx + b + 1]
                - cu_q_lens_ref[batch_start_seq_idx + b]
              )
              for q_idx in range(q_split):
                bq_start = q_block_len * q_idx
                bq_end = bq_start + q_block_len
                bq_offset = bq_sz_for_each_split * q_idx

                cur_p, cur_exp_m_diff = flash_attention_step1_qk_softmax(
                  bq_nope_vec[bq_start:bq_end],
                  bq_pe_vec[bq_start:bq_end],
                  bkvc,
                  bkpe,
                  kv_len=kv_lens_ref[batch_start_seq_idx + b],
                  q_len=cu_q_len,
                  bq_idx=bq_idx,
                  bq_offset=bq_offset,
                  bkv_idx=bkv_idx,
                  head_l_ref=l_ref.at[b, bq_start:bq_end],
                  head_m_ref=m_ref.at[b, bq_start:bq_end],
                )

                if prev_p is not None:
                  assert prev_v is not None
                  assert prev_exp_m_diff is not None
                  flash_attention_step2_pv(
                    prev_p,
                    prev_v,
                    prev_exp_m_diff,
                    bkv_idx,
                    prev_acc,
                  )

                prev_p = cur_p
                prev_v = bkvc
                prev_exp_m_diff = cur_exp_m_diff
                prev_acc = acc_ref.at[b, bq_start:bq_end]

            # last flash attention 2nd step
            assert prev_p is not None
            assert prev_v is not None
            assert prev_exp_m_diff is not None
            flash_attention_step2_pv(
              prev_p,
              prev_v,
              prev_exp_m_diff,
              bkv_idx,
              prev_acc,
            )
          else:  # One step flash attention
            # Load bkv into vreg. There is no need to mask out invalid k/v entries,
            # because the score of invalid Q.K^T pairs are masked (to be zero) in
            # flash attention, so that the invalid kv entries
            # (as long as they are not NaN or inf) won't affect to the output.
            bkvc, bkpe = load_batch_bkv(
              load_bkv_fn,
              bkv_sem_idx,
            )

            bq_nope_vec, bq_pe_vec = load_batch_bq(
              bq_sem_idx, actual_bq_sz=actual_bq_sz
            )

            debug_print("[RPA debug] one step flash attention")
            debug_print(
              "[RPA debug] bq_nope_vec.shape={}, {}, {}",
              bq_nope_vec.shape[0],
              bq_nope_vec.shape[1],
              bq_nope_vec.shape[2],
            )
            debug_print(
              "[RPA debug] bq_pe_vec.shape={}, {}, {}",
              bq_pe_vec.shape[0],
              bq_pe_vec.shape[1],
              bq_pe_vec.shape[2],
            )
            debug_print(
              "[RPA debug] bkvc.shape={}, {}, {}",
              bkvc.shape[0],
              bkvc.shape[1],
              bkvc.shape[2],
            )
            debug_print(
              "[RPA debug] bkpe.shape={}, {}, {}",
              bkpe.shape[0],
              bkpe.shape[1],
              bkpe.shape[2],
            )

            if debug_mode:
              return

            batch_flash_attention(
              bq_nope_vec,
              bq_pe_vec,
              bkvc,
              bkpe,
              bq_idx=bq_idx,
              bkv_idx=bkv_idx,
            )

        lax.fori_loop(0, num_bkv, compute_with_bkv, None, unroll=False)

        # Load acc and calculate final output.
        acc = acc_ref[...]
        normalizer = broadcast_minor(l_ref[...], acc.shape)
        out = (
          lax.div(acc, normalizer)
          if q_dtype == jnp.float32
          else (acc * pl_proxy.reciprocal(normalizer, approx=True)).astype(q_dtype)
        )

        # Wait for previous bo to be fully sent before storing new bo.
        bo_sem_idx = sem_ids_ref[2]
        sem_ids_ref[2] = lax.select(bo_sem_idx == 0, 1, 0)
        wait_send_bo(bo_sem_idx)

        # Store output from acc to bo.
        bo_x2_ref.at[bo_sem_idx].bitcast(jnp.int32).reshape(
          batch_size,
          bq_sz * num_q_heads_per_q_packing,
          lkv_dim,
        )[...] = tpu_proxy.bitcast(out, jnp.int32)

        # Send cur bo
        start_send_bo(batch_start_seq_idx, bq_idx, bo_sem_idx)

      lax.fori_loop(0, num_bq, compute_with_bq, None, unroll=False)

    # ------- Kernel start ------- ###

    @pl_proxy.when(
      (batch_start_seq_idx == start_seq_idx) & (batch_end_seq_idx < end_seq_idx)
    )
    def prologue():
      start_fetch_bq(start_seq_idx, 0, 0)

      if not transpose_kv_cache:
        # Initialize bkvc_x2_ref and bkpe_x2_ref to zeros to avoid NaN issues from
        # accessing
        # uninitialized memory. Bitcast into int32 to avoid tiling issues.
        bkvc_x2_int32_ref = bkvc_x2_ref.bitcast(jnp.int32).reshape((
          2,
          math.prod(bkvc_x2_ref.bitcast(jnp.int32).shape[1:]) // lkv_dim,
          lkv_dim,
        ))
        bkvc_zeros = jnp.zeros(bkvc_x2_int32_ref.shape[1:], jnp.int32)
        bkpe_x2_int32_ref = bkpe_x2_ref.bitcast(jnp.int32).reshape((
          2,
          math.prod(bkpe_x2_ref.bitcast(jnp.int32).shape[1:]) // r_dim,
          r_dim,
        ))
        bkpe_zeros = jnp.zeros(bkpe_x2_int32_ref.shape[1:], jnp.int32)

        # To pipeline VST and DMA, we divide the initialization into two steps.
        bkvc_x2_int32_ref[0] = bkvc_zeros
        bkpe_x2_int32_ref[0] = bkpe_zeros
        start_fetch_bkv(start_seq_idx, 0, 0)
        bkvc_x2_int32_ref[1] = bkvc_zeros
        bkpe_x2_int32_ref[1] = bkpe_zeros
      else:
        bkvc_zeros = jnp.zeros(bkvc_x2_ref.shape[1:], bkvc_x2_ref.dtype)
        bkpe_zeros = jnp.zeros(bkpe_x2_ref.shape[1:], bkpe_x2_ref.dtype)

        # To pipeline VST and DMA, we divide the initialization into two steps.
        bkvc_x2_ref[0] = bkvc_zeros
        bkpe_x2_ref[0] = bkpe_zeros
        start_fetch_bkv(start_seq_idx, 0, 0)
        bkvc_x2_ref[1] = bkvc_zeros
        bkpe_x2_ref[1] = bkpe_zeros

    @pl_proxy.when(batch_end_seq_idx < end_seq_idx)
    def active_process():
      process()

    @pl_proxy.when(batch_end_seq_idx == end_seq_idx - 1)
    def epilogue():
      for i in range(2):
        wait_send_bo(i)
        wait_update_kv_cache(i)

  return types.SimpleNamespace(
    pl=pl_proxy,
    _mla_ragged_paged_attention_kernel=_mla_ragged_paged_attention_kernel,
  )


def mla_decode_paged_reference(
  q,
  new_kv,
  cache,
  lengths,
  *,
  lkv_dim=512,
  sm_scale=0.07216878364870323,
  sliding_window=0,
  soft_cap=0.0,
):
  """Independent FP64 host reference, also supporting JAX shape tracing."""

  def host_reference(q, new_kv, cache, lengths):
    # Use FP64 host math independently of the Pallas online softmax.
    batch, heads, dim = q.shape
    packed = np.array(cache, copy=True)
    dense = packed.reshape(batch, -1, packed.shape[-1])
    ll = np.clip(np.asarray(lengths), 0, dense.shape[1])
    q64, new = np.asarray(q, np.float64), np.asarray(new_kv)
    out = np.zeros((batch, heads, lkv_dim), np.float64)
    for b, stop in enumerate(ll):
      stop = int(stop)
      if stop == 0:
        continue
      dense[b, stop - 1, :dim] = new[b]
      begin = max(0, stop - sliding_window) if sliding_window else 0
      kv = dense[b, begin:stop, :dim].astype(np.float64)
      scores = (q64[b] @ kv.T) * sm_scale
      if soft_cap:
        scores = soft_cap * np.tanh(scores / soft_cap)
      p = np.exp(scores - scores.max(axis=-1, keepdims=True))
      out[b] = p @ kv[:, :lkv_dim] / p.sum(axis=-1, keepdims=True)
    return out.astype(q.dtype), packed

  return jax.pure_callback(
    host_reference,
    (
      jax.ShapeDtypeStruct((*q.shape[:2], lkv_dim), q.dtype),
      jax.ShapeDtypeStruct(cache.shape, cache.dtype),
    ),
    q,
    new_kv,
    cache,
    lengths,
  )


@functools.lru_cache(maxsize=16)
def build_mla_decode_paged(
  *,
  lkv_dim=512,
  sm_scale=0.07216878364870323,
  pages_per_block=8,
  vmem_limit_bytes=120795955,
  sliding_window=0,
  soft_cap=0.0,
  interpret_mode=False,
):
  """Build four-input packed-cache decode with per-page DMA.

  The returned function takes q, new_kv, packed_cache, and int32 lengths.
  Lengths include the new token. Cache input 2 is donated; reuse the returned
  cache for the next step. Eight 128-token pages form each 1024-token KV tile.
  """
  if lkv_dim <= 0 or lkv_dim % 128:
    raise ValueError("lkv_dim must be a positive multiple of 128")
  if pages_per_block not in (4, 8, 16):
    raise ValueError("pages_per_block must be 4, 8 or 16")
  if vmem_limit_bytes <= 0 or sm_scale <= 0 or sliding_window < 0 or soft_cap < 0:
    raise ValueError("Invalid MLA decode scale, window, cap or VMEM budget")
  fn = _build_mla_decode_native(
    _mla_decode_attention_components(interpret_mode),
    latent=lkv_dim,
    sm_scale=sm_scale,
    window=sliding_window,
    cap=soft_cap,
    vmem_limit=vmem_limit_bytes,
    interpret=interpret_mode,
    pages_per_block=pages_per_block,
  )
  if interpret_mode:
    from pallas_kernels.kernels.utils.interpret import warn_if_interpret

    warn_if_interpret("mla_decode_paged")
    return _interpret_call_wrapper(fn)
  return fn


@functools.lru_cache(maxsize=16)
def build_mla_decode_contiguous(
  *,
  lkv_dim=512,
  sm_scale=0.07216878364870323,
  block_size=1024,
  vmem_limit_bytes=120795955,
  sliding_window=0,
  soft_cap=0.0,
  interpret_mode=False,
):
  """Four-input signoff decode with contiguous cache and VMEM length masks.

  Cache is BF16 [B, capacity, padded_dim]. Lengths include the appended token;
  nonpositive lengths leave cache unchanged and produce zero attention.
  Cache input is preserved; the updated cache is a separate output.
  No page table or scalar-prefetched metadata is used.
  """
  if lkv_dim <= 0 or lkv_dim % 128 or block_size <= 0 or block_size % 128:
    raise ValueError("lkv_dim and block_size must be positive multiples of 128")
  if sm_scale <= 0 or sliding_window < 0 or soft_cap < 0 or vmem_limit_bytes <= 0:
    raise ValueError("Invalid MLA decode scale, window, cap or VMEM budget")

  @jax.jit
  def native(q, new, cache, lengths):
    if q.ndim != 3 or cache.ndim != 3:
      raise ValueError("Contiguous decode requires q [B,H,D] and cache [B,N,Dpad]")
    batch, heads, dim = q.shape
    capacity = cache.shape[1]
    padded_dim = lkv_dim + ((dim - lkv_dim + 127) // 128) * 128
    if (
      batch <= 0
      or heads <= 0
      or heads % 16
      or dim <= lkv_dim
      or capacity <= 0
      or capacity % block_size
      or new.shape != (batch, dim)
      or cache.shape != (batch, capacity, padded_dim)
      or lengths.shape != (batch,)
      or lengths.dtype != jnp.int32
      or any(x.dtype != jnp.bfloat16 for x in (q, new, cache))
    ):
      raise ValueError("Invalid contiguous MLA decode shapes or dtypes")

    def prepare(q_ref, new_ref, len_ref, qp_ref, np_ref, lp_ref):
      # Pad before slicing so concatenate sees matching row offsets.
      new_rows = jnp.concatenate(
        (new_ref[...], jnp.zeros((batch, padded_dim - dim), new.dtype)), axis=1
      )
      lens = len_ref[...]
      for b in range(batch):
        qp_ref[b] = jnp.concatenate(
          (q_ref[b], jnp.zeros((heads, padded_dim - dim), q.dtype)), axis=1
        )
        row = new_rows[b : b + 1]
        np_ref[b] = jnp.broadcast_to(row, (block_size, padded_dim))
        lp_ref[b] = jnp.broadcast_to(lens[b : b + 1], (heads, block_size))

    vmem = pl.BlockSpec(memory_space=pltpu.VMEM)
    qp, np_, lp = pl.pallas_call(
      prepare,
      in_specs=(vmem, vmem, vmem),
      out_specs=(vmem, vmem, vmem),
      out_shape=(
        jax.ShapeDtypeStruct((batch, heads, padded_dim), q.dtype),
        jax.ShapeDtypeStruct((batch, block_size, padded_dim), new.dtype),
        jax.ShapeDtypeStruct((batch, heads, block_size), lengths.dtype),
      ),
      compiler_params=pltpu.CompilerParams(vmem_limit_bytes=vmem_limit_bytes),
      interpret=interpret_mode,
      name="mla_decode_contiguous_prepare",
    )(q, new, lengths)

    def attention(q_ref, new_ref, len_ref, cache_ref, out_ref, updated_ref, tile, sem):
      b = pl.program_id(0)
      query, new_tile, lens = q_ref[0], new_ref[0], len_ref[0]
      # Encode clipped lengths in masks; Proton signed min/max lowering fails.
      offsets = lax.broadcasted_iota(jnp.int32, (heads, block_size), 1)
      # Proton transpose supports int32, but not boolean vectors.
      cache_lens, cache_offsets = lens[:1].T, offsets[:1].T
      features = lax.broadcasted_iota(jnp.int32, (block_size, padded_dim), 1)

      def body(i, state):
        m, total, acc = state
        cache_slice = cache_ref.at[b, pl.ds(i * block_size, block_size), :]
        load = pltpu.make_async_copy(cache_slice, tile, sem)
        load.start()
        load.wait()
        tokens = offsets + i * block_size
        cache_tokens = cache_offsets + i * block_size
        append = (cache_tokens + 1 == cache_lens) | (
          (cache_tokens == capacity - 1) & (cache_lens > capacity)
        )
        kv = jnp.where(append & (features < dim), new_tile, tile[...])
        tile[...] = kv
        store = pltpu.make_async_copy(
          tile, updated_ref.at[b, pl.ds(i * block_size, block_size), :], sem
        )
        store.start()
        store.wait()
        valid = tokens < lens
        cache_valid = cache_tokens < cache_lens
        if sliding_window > 0:
          valid &= (tokens >= lens - sliding_window) | (
            (lens > capacity) & (tokens >= capacity - sliding_window)
          )
          cache_valid &= (cache_tokens >= cache_lens - sliding_window) | (
            (cache_lens > capacity) & (cache_tokens >= capacity - sliding_window)
          )
        # Undefined cache tails and padding must not enter either matmul.
        kv = jnp.where(cache_valid & (features < dim), kv, 0)
        scores = (
          lax.dot_general(
            query, kv, TRANS_B_DIM_NUMBERS, preferred_element_type=jnp.float32
          )
          * sm_scale
        )
        if soft_cap > 0:
          scores = soft_cap * jnp.tanh(scores / soft_cap)
        scores = jnp.where(valid, scores, jnp.finfo(jnp.float32).min)
        if capacity == block_size:
          m_next = jnp.max(scores, axis=1, keepdims=True)
          p = jnp.where(valid, jnp.exp(scores - m_next), 0.0)
          total = jnp.sum(p, axis=1, keepdims=True)
          acc = lax.dot(
            p.astype(q.dtype), kv[:, :lkv_dim], preferred_element_type=jnp.float32
          )
        else:
          m_next = jnp.maximum(m, jnp.max(scores, axis=1, keepdims=True))
          alpha = jnp.exp(m - m_next)
          p = jnp.where(valid, jnp.exp(scores - m_next), 0.0)
          total = total * alpha + jnp.sum(p, axis=1, keepdims=True)
          acc = acc * alpha + lax.dot(
            p.astype(q.dtype), kv[:, :lkv_dim], preferred_element_type=jnp.float32
          )
        return m_next, total, acc

      # Visit every cache block to preserve the full tail in the separate output.
      _, total, acc = lax.fori_loop(
        0,
        capacity // block_size,
        body,
        (
          jnp.full((heads, 1), jnp.finfo(jnp.float32).min),
          jnp.zeros((heads, 1), jnp.float32),
          jnp.zeros((heads, lkv_dim), jnp.float32),
        ),
      )
      out_ref[0] = (acc / jnp.where(total > 0, total, 1.0)).astype(q.dtype)

    def index(b):
      return b, 0, 0

    hbm = pl.BlockSpec(memory_space=pltpu.HBM)
    return pl.pallas_call(
      attention,
      grid=(batch,),
      in_specs=(
        pl.BlockSpec((1, heads, padded_dim), index),
        pl.BlockSpec((1, block_size, padded_dim), index),
        pl.BlockSpec((1, heads, block_size), index),
        hbm,
      ),
      out_specs=(pl.BlockSpec((1, heads, lkv_dim), index), hbm),
      out_shape=(
        jax.ShapeDtypeStruct((batch, heads, lkv_dim), q.dtype),
        jax.ShapeDtypeStruct(cache.shape, cache.dtype),
      ),
      scratch_shapes=(
        pltpu.VMEM((block_size, padded_dim), cache.dtype),
        pltpu.SemaphoreType.DMA,
      ),
      compiler_params=pltpu.CompilerParams(
        dimension_semantics=("parallel",), vmem_limit_bytes=vmem_limit_bytes
      ),
      interpret=interpret_mode,
      name="mla_decode_contiguous_attention",
    )(qp, np_, lp, cache)

  if interpret_mode:
    warn_if_interpret("mla_decode_contiguous")
  return native


__all__ = ["build_mla_decode_contiguous", "build_mla_decode_paged", "mla_decode"]
