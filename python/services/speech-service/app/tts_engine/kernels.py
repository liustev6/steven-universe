"""Our own GPU kernels, in Triton (speech-service.md T11, 4.5c, fusion). Triton is Python-like GPU code:
you write what one *program* does with a block of data; the GPU runs many programs at once, and Triton
turns each into the warps, loads and registers CUDA would make you write by hand.

Why fuse: PyTorch runs Qwen's RMSNorm as 8 kernels, each reading its input from GPU memory and writing
its output back (spikes/04: 4,665 kernels a frame in the predictor, ~1 µs each however little they do).
One fused kernel reads the row once, does everything in registers, writes once.

Rounding: every kernel here rounds where PyTorch's separate operations round, so it can match them
bit for bit. Two things matter:
- **No FMA contraction** (`enable_fp_fusion=False` at each launch): by default the compiler turns a
  multiply followed by an add into one fused multiply-add, which rounds once where PyTorch (a multiply
  kernel, then an add kernel) rounds twice. More accurate, but different bits: the rotary kernel was one
  bfloat16 step off until this was turned off.
- **The same rounding points:** Qwen's RMSNorm computes in float32, rounds to bfloat16, then multiplies
  by the weight in bfloat16; the rotary rounds after each multiply and after the add. The kernels keep
  those exact steps. What it can't keep is the order the mean adds its 1,024
numbers in (PyTorch's reduction kernel has its own), so the float32 result can differ in its last bit,
and after rounding to bfloat16, rarely, an element by one bfloat16 step. The tests measure how rarely.
"""

from __future__ import annotations

import triton
import triton.language as tl


@triton.jit
def _rms_norm(x_ptr, w_ptr, out_ptr, x_row_stride, out_row_stride, n_cols, eps, BLOCK: tl.constexpr):
    """One program per row: the whole row (≤ BLOCK elements) in registers."""
    row = tl.program_id(0)
    cols = tl.arange(0, BLOCK)                                     # 0 … BLOCK-1
    mask = cols < n_cols                                           # BLOCK is a power of two ≥ n_cols
    x = tl.load(x_ptr + row * x_row_stride + cols, mask=mask, other=0.0).to(tl.float32)
    variance = tl.sum(x * x, axis=0) / n_cols                      # the mean of the squares
    # rsqrt: libdevice's, the function PyTorch's torch.rsqrt calls on CUDA. Then normalize and round.
    y = (x * tl.math.rsqrt(variance + eps)).to(out_ptr.dtype.element_ty)
    w = tl.load(w_ptr + cols, mask=mask, other=0.0)
    out = (w.to(tl.float32) * y.to(tl.float32)).to(out_ptr.dtype.element_ty)  # × weight, round (as bf16 ×)
    tl.store(out_ptr + row * out_row_stride + cols, out, mask=mask)


def rms_norm(x, weight, eps: float):
    """Qwen3TTSRMSNorm's forward as one kernel: `x` (..., n) normalized over its last dimension."""
    import torch

    n = x.shape[-1]
    rows = x.reshape(-1, n)                                        # a view when x is contiguous
    if rows.stride(-1) != 1:
        rows = rows.contiguous()
    out = torch.empty(rows.shape, dtype=x.dtype, device=x.device)
    block = triton.next_power_of_2(n)
    _rms_norm[(rows.shape[0],)](rows, weight, out, rows.stride(0), out.stride(0), n, eps,
                                BLOCK=block, num_warps=4 if block >= 1024 else 1, enable_fp_fusion=False)
    return out.view(x.shape)


@triton.jit
def _rotate(x_lo, x_hi, c_lo, c_hi, s_lo, s_hi, dtype: tl.constexpr):
    """Qwen's `x * cos + rotate_half(x) * sin`, rounded where PyTorch's three bfloat16 operations round:
    after each multiply, then after the add. rotate_half(x) = [-x_hi, x_lo]."""
    lo = (x_lo * c_lo).to(dtype).to(tl.float32) + (-x_hi * s_lo).to(dtype).to(tl.float32)
    hi = (x_hi * c_hi).to(dtype).to(tl.float32) + (x_lo * s_hi).to(dtype).to(tl.float32)
    return lo.to(dtype), hi.to(dtype)


@triton.jit
def _rope_and_cache(q_ptr, k_ptr, v_ptr, cos_ptr, sin_ptr, q_out_ptr, keys_ptr, values_ptr,
                    n, start_arg, start_ptr, q_heads, kv_heads,
                    q_sb, q_st, q_sh, k_sb, k_st, k_sh, v_sb, v_st, v_sh,
                    qo_sb, qo_sh, qo_st, c_sb, c_sh, c_st,
                    HALF: tl.constexpr, START_FROM_PTR: tl.constexpr):
    """One program per (batch × position, head) row of head_dim = 2 × HALF values. Heads 0…q_heads-1:
    rotate q into `q_out` (batch, heads, n, head_dim). The next kv_heads: rotate k, write it straight
    into the key cache at position start + t. The last kv_heads: copy v into the value cache.
    START_FROM_PTR: `start` read from GPU memory, one per batch row (`start_ptr[b]`): a graph replays
    at whatever positions they hold, and each row (each sentence in a batch) has its own."""
    row, head = tl.program_id(0), tl.program_id(1)
    b, t = row // n, row % n
    if START_FROM_PTR:
        start = tl.load(start_ptr + b)
    else:
        start = start_arg
    lo = tl.arange(0, HALF)
    dtype = q_out_ptr.dtype.element_ty
    if head < q_heads + kv_heads:                                  # q or k: rotate
        c_lo = tl.load(cos_ptr + (start + t) * 2 * HALF + lo).to(tl.float32)
        c_hi = tl.load(cos_ptr + (start + t) * 2 * HALF + HALF + lo).to(tl.float32)
        s_lo = tl.load(sin_ptr + (start + t) * 2 * HALF + lo).to(tl.float32)
        s_hi = tl.load(sin_ptr + (start + t) * 2 * HALF + HALF + lo).to(tl.float32)
        if head < q_heads:
            src = q_ptr + b * q_sb + t * q_st + head * q_sh
            dst = q_out_ptr + b * qo_sb + head * qo_sh + t * qo_st
        else:
            h = head - q_heads
            src = k_ptr + b * k_sb + t * k_st + h * k_sh
            dst = keys_ptr + b * c_sb + h * c_sh + (start + t) * c_st
        out_lo, out_hi = _rotate(tl.load(src + lo).to(tl.float32), tl.load(src + HALF + lo).to(tl.float32),
                                 c_lo, c_hi, s_lo, s_hi, dtype)
        tl.store(dst + lo, out_lo)
        tl.store(dst + HALF + lo, out_hi)
    else:                                                          # v: a copy into the cache
        h = head - q_heads - kv_heads
        src = v_ptr + b * v_sb + t * v_st + h * v_sh
        dst = values_ptr + b * c_sb + h * c_sh + (start + t) * c_st
        tl.store(dst + lo, tl.load(src + lo))
        tl.store(dst + HALF + lo, tl.load(src + HALF + lo))


def rope_and_cache(q, k, v, cos, sin, keys, values, start: int, position=None):
    """Qwen's rotary embedding on q and k, and the cache write, as one kernel (6 + 6 + 2 kernels before).
    `position`: a GPU int32 tensor, one start position per batch row, read by the kernel (then `start`
    unused).

    q (batch, n, q_heads, head_dim), k and v (batch, n, kv_heads, head_dim): as the projections make
    them, before any transpose. cos, sin (16, head_dim): every cache position's angles, bfloat16.
    keys, values (batch, kv_heads, 16, head_dim): the fixed cache; positions start…start+n-1 are written.
    Returns q rotated, (batch, q_heads, n, head_dim): the layout attention takes."""
    import torch

    batch, n, q_heads, head_dim = q.shape
    kv_heads = k.shape[2]
    for x in (q, k, v, cos, sin, keys, values):
        if x.stride(-1) != 1:
            raise ValueError("the last dimension must be contiguous")
    q_out = torch.empty((batch, q_heads, n, head_dim), dtype=q.dtype, device=q.device)
    _rope_and_cache[(batch * n, q_heads + 2 * kv_heads)](
        q, k, v, cos, sin, q_out, keys, values, n, start, position if position is not None else q,
        q_heads, kv_heads,
        q.stride(0), q.stride(1), q.stride(2), k.stride(0), k.stride(1), k.stride(2),
        v.stride(0), v.stride(1), v.stride(2),
        q_out.stride(0), q_out.stride(1), q_out.stride(2),
        keys.stride(0), keys.stride(1), keys.stride(2),
        HALF=head_dim // 2, START_FROM_PTR=position is not None, num_warps=1, enable_fp_fusion=False)
    return q_out


@triton.jit
def _decode_attention(q_ptr, k_ptr, v_ptr, pos_ptr, out_ptr, scale,
                      q_sb, q_sh, k_sb, k_sh, k_st, v_sb, v_sh, v_st, o_sb, o_sh,
                      GROUP: tl.constexpr, D: tl.constexpr, BLOCK: tl.constexpr):
    """One query position (decoding), one program per (batch, query head), over cache positions
    0…position in blocks of BLOCK, with an online softmax (FlashAttention's trick): running max `m`,
    running sum of exponentials `l`, running weighted sum of values `acc`, rescaled by exp(m_old - m_new)
    whenever a block raises the max. One pass; the scores are never stored. GQA: query head h reads key
    and value head h // GROUP. Each batch row has its own position (`pos_ptr[b]`): a batch's
    sentences are at different points."""
    b, h = tl.program_id(0), tl.program_id(1)
    kv = h // GROUP
    n = tl.load(pos_ptr + b) + 1                                   # positions 0…position: from GPU memory
    d = tl.arange(0, D)
    q = tl.load(q_ptr + b * q_sb + h * q_sh + d).to(tl.float32)
    k_base, v_base = k_ptr + b * k_sb + kv * k_sh, v_ptr + b * v_sb + kv * v_sh
    m = tl.full((), float("-inf"), tl.float32)
    l = tl.zeros((), tl.float32)
    acc = tl.zeros((D,), tl.float32)
    for block in range(0, n, BLOCK):
        pos = block + tl.arange(0, BLOCK)
        valid = pos < n
        k = tl.load(k_base + pos[:, None] * k_st + d[None, :], mask=valid[:, None], other=0.0).to(tl.float32)
        s = tl.where(valid, tl.sum(k * q[None, :], axis=1) * scale, float("-inf"))   # (BLOCK,) scores
        m_new = tl.maximum(m, tl.max(s, axis=0))
        alpha = tl.exp(m - m_new)                                  # rescales what came before
        p = tl.exp(s - m_new)
        v = tl.load(v_base + pos[:, None] * v_st + d[None, :], mask=valid[:, None], other=0.0).to(tl.float32)
        acc = acc * alpha + tl.sum(p[:, None] * v, axis=0)
        l = l * alpha + tl.sum(p, axis=0)
        m = m_new
    tl.store(out_ptr + b * o_sb + h * o_sh + d, (acc / l).to(out_ptr.dtype.element_ty))


def decode_attention(q, keys, values, position, scale: float):
    """Attention for one new position over the cache, its length read from GPU memory.

    q (batch, q_heads, 1, head_dim), rotated; keys, values (batch, kv_heads, capacity, head_dim), each
    row filled through its `position` (a GPU int32 tensor, shape (batch,)). Returns (batch, 1, q_heads × head_dim): the
    layout the output projection takes, so no transpose and copy after."""
    import torch

    batch, q_heads, n, head_dim = q.shape
    if n != 1:
        raise ValueError("one query position: decoding")
    out = torch.empty((batch, 1, q_heads, head_dim), dtype=q.dtype, device=q.device)
    _decode_attention[(batch, q_heads)](
        q, keys, values, position, out, scale,
        q.stride(0), q.stride(1), keys.stride(0), keys.stride(1), keys.stride(2),
        values.stride(0), values.stride(1), values.stride(2), out.stride(0), out.stride(2),
        GROUP=q_heads // keys.shape[1], D=head_dim, BLOCK=64, num_warps=4)
    return out.view(batch, 1, q_heads * head_dim)
