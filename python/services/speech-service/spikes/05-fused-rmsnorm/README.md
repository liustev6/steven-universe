# 05: a fused RMSNorm in Triton (4.5c, fusion A, 2026-11-17)
**Question:** spikes/04 found the predictor launching 4,665 kernels a frame, about half of them small
pieces of RMSNorm (Qwen's runs as 8 kernels: to float32, square, mean, add, rsqrt, multiply, to
bfloat16, × weight). One kernel per row (`app/engine/kernels.py`): how much faster, and how exact?

**How:**
- `tests/test_kernels.py`: the kernel vs Qwen's RMSNorm on random inputs (5 shapes × 200).
- `scripts/bench.py ours-graph ours-fused` and `spikes/04-frame-split/kernels.py ours-static fused`.
- `drift.py` (here): the kernel checked against Qwen's math on every call of a real sentence (88
  frames, greedy); then unfused vs fused on the same predictor inputs, teacher-forced (spikes/02's method).

## Results
Speed (the predictor, one frame; then the whole engine):

| | Kernels a frame | GPU time a frame | Engine ms/frame | First audio | RTF |
|---|---|---|---|---|---|
| graph (step 4) | 4,665 | 11.8 ms | 20.4 | 47 ms | 0.29 |
| **graph + fused RMSNorm** | **~2,460** | **~9.7 ms** | **18.3** | **43 ms** | **0.27** |

Kernels by kind with the fused norm: elementwise 2,625 → 1,050, copies/casts 946 → 316, reductions
360 → 45, plus 315 `_rms_norm` (0.29 ms). Matrix multiplies unchanged: 615, 7.07 ms.

Exactness:

    random inputs: 0 of 2,048,000 elements differ (5 shapes × 200)
    1. the kernel on real activations: 27720 calls, 51 of 37486592 elements differ (11 of 184448 rows); worst 0.98 bfloat16 steps
    2. teacher-forced: steps 1320, argmax flips 3, max |logit diff| 0.3750
       top-2 margins where it flipped: [0.0, 0.125, 0.125]

Greedy end to end, the sentences diverge after the first flip: 46 vs 47 frames, 48 vs 51, 78 vs 88.

## What it showed
- **−2.1 ms a frame (−10%)** from one kernel: half the predictor's kernels gone.
- **Not bit-exact, by the smallest possible amount:** 1 element in 735,000 is one bfloat16 step off (the
  mean adds its numbers in another order than PyTorch's reduction; rarely, that last float32 bit crosses
  a bfloat16 rounding boundary). Then 3 of 1,320 steps (0.2%) pick another best code, every one a
  near-tie (the top two equal or one step apart). For comparison, Hugging Face's fixed cache flipped 44.
- **So greedy whole-sentence equality is the wrong test for fused kernels:** one near-tie flip changes
  every frame after it. Serving engines compare teacher-forced, with a tolerance (vLLM's
  `check_logprobs_close`: where tokens differ, each must be among the other's top few).

## The test standard that followed (agreed 2026-11-17)
Bit-exact paths keep the exact-codes test. Fused paths: `test_fused_predictor_is_close_to_the_exact_one`
(`tests/test_engine_gpu.py`): every frame's predictor inputs from the three test sentences; the fused
predictor fed the exact one's codes (`StaticCodePredictor.teacher_forced`); a different pick only where
the exact one's top two scores are within 2 bfloat16 steps; under 1% of steps; scores within 8 steps
of the best score's size. First run:

    2790 steps, 6 different picks, worst score difference 3.5 bfloat16 steps
