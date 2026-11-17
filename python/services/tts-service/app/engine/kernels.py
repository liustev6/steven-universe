"""Our own GPU kernels, in Triton (tts-service.md T11, 4.5c, fusion). Triton is Python-like GPU code:
you write what one *program* does with a block of data; the GPU runs many programs at once, and Triton
turns each into the warps, loads and registers CUDA would make you write by hand.

Why fuse: PyTorch runs Qwen's RMSNorm as 8 kernels, each reading its input from GPU memory and writing
its output back (spikes/04: 4,665 kernels a frame in the predictor, ~1 µs each however little they do).
One fused kernel reads the row once, does everything in registers, writes once.

Rounding: Qwen's RMSNorm computes in float32, rounds to bfloat16, then multiplies by the weight in
bfloat16. The kernel keeps those exact steps. What it can't keep is the order the mean adds its 1,024
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
                                BLOCK=block, num_warps=4 if block >= 1024 else 1)
    return out.view(x.shape)
