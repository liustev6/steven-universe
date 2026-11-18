# 06: the rotary embedding and the cache write as one kernel; FMA (4.5c, fusion A2, 2026-11-17)
**Question:** after the fused RMSNorm (spikes/05), the predictor still ran ~14 small kernels a layer for
the rotary embedding and the cache write (q and k: multiply by cos, `rotate_half` = negate + concatenate,
multiply by sin, add; then two copies into the cache). One kernel: faster, and can it be bit-exact?

**How:** `_rope_and_cache` in `app/tts_engine/kernels.py`: one program per (position, head) row; q rotated
into attention's layout, k rotated and v copied straight into the fixed cache.
`tests/test_kernels.py::test_rope_and_cache_is_bit_exact` (`torch.equal` against Qwen's formula);
`scripts/bench.py`, `spikes/04-frame-split/kernels.py ours-static fused`, `scripts/chunks.py`,
`spikes/05-fused-rmsnorm/drift.py`, the GPU tests.

## Results
First run: 5 of 5 rotary tests failed, every difference exactly one bfloat16 step. With
`TRITON_DEFAULT_FP_FUSION=0`: all pass. The kernels now launch with `enable_fp_fusion=False`.

The predictor, one frame:

    before fusion (step 4)   4,665 kernels   11.8 ms of GPU time
    + fused RMSNorm (05)    ~2,460 kernels   ~9.7 ms
    + fused rotary (06)     ~1,640 kernels   ~8.5 ms   (matrix multiplies 7.07 ms: 83%)

          615   7.07 ms  matrix multiply (cuBLAS/cutlass)
          450   0.42 ms  elementwise (add, mul, pow, rsqrt, silu, neg…)
          315   0.29 ms  other: _rms_norm
           75   0.07 ms  other: _rope_and_cache
           45   0.37 ms  sampling (topk, sort, softmax, multinomial…)
           45   0.08 ms  reduction (mean, max, sum…)
           30   0.15 ms  other: _assert_async_cuda_kernel (multinomial's check)
           21   0.02 ms  copy / cast

The engine (bench; then chunks.py with 1, 1, 2, 4):

    predictor     ms/frame  first audio    RTF
    ours-graph        20.3         47 ms   0.29
    ours-fused        17.0         40 ms   0.25

      schedule  first audio   RTF         stalls  margin (worst)      SNR
       1,1,2,4        39 ms  0.24   0 ms (max   0)    60 ms (  59)  27.6 dB   (ours-fused)
       1,1,2,4        46 ms  0.28   0 ms (max   0)    56 ms (  55)  28.5 dB   (ours-graph)

Exactness: the rotary kernel is bit-exact (`torch.equal`). The RMSNorm, with FMA off too, still
differs in the same 51 of 37.5 million elements: its difference is the order of the 1,024-number sum,
not FMA. The near-tie test: 2,790 steps, 6 different picks, all near-ties (unchanged).

## What it showed
- **FMA (fused multiply-add):** GPUs compute `a × b + c` in one instruction with one rounding, and the
  compiler forms one wherever a multiply feeds an add. More accurate, but PyTorch's eager code runs the
  multiply and the add as separate kernels and rounds twice; to match it bit for bit, a kernel must not
  contract. (PyTorch's own `torch.compile` has the same switch for the same reason.)
- **A kernel with no sum can be bit-exact; one with a sum, practically not** (matching the reduction
  order of PyTorch's kernel isn't worth it), hence the near-tie standard for fused paths.
- **The predictor is close to its floor:** 8.5 ms of GPU time against ~5.2 ms of weight reading at the
  card's bandwidth; the matrix multiplies are 83% of it. What's left is small (residual adds, SiLU ×
  multiply: 0.4 ms) or would change the weights (FP8, half the bytes). The talker (7.7 ms a frame,
  plain PyTorch) is now the larger target.
- **chunks.py's SNR** now compares against the engine's own frames, decoded whole: a fused engine says a
  different (equally valid) take of the sentence at near-ties, which isn't a chunking error (the first
  run showed −1.4 dB against Qwen's codes).
