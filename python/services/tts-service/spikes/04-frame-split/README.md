# 04: where a frame's time goes (2026-11-17, after the predictor's graph)
**Question:** with the code predictor as a CUDA graph (20 ms a frame), how much is still the talker,
and is the rest the GPU working or the GPU waiting for the CPU? That decides what stage 2 can win.

**How:**
- `split.py`: CUDA events around the talker's forward and the predictor's replay (no syncs added);
  wall clock for the CPU side. Sampled, three sentences × 3 runs, 455 frames.
- `busy.py`: PyTorch's profiler: the kernels' own run time per frame vs the frame (one long sentence).
  The profiler slows the CPU side, so its frame times run long; the GPU's busy time is the number.

## Results
    frames measured: 455 (medians, ms)
      a frame, end to end (GPU clock)        19.9
      ├ the predictor's graph replay (GPU)   11.5
      ├ the talker's own work (GPU span)      7.7   its forward call on the CPU: 19.2 (incl. the replay)
      └ between calls: generate's loop        0.7   on the CPU: 0.7
      the prompt (prefill): GPU 8.0, CPU 8.0

    ours-static  frames 56:  41.4 ms a frame, GPU busy  16.4 ms (40%), 6657 kernels a frame
    ours-graph   frames 61:  27.5 ms a frame, GPU busy  16.1 ms (59%), 6658 kernels a frame

## What it showed
- **The GPU is busy ~16 ms of each 20 ms frame.** The graph didn't change the GPU's work (16.4 → 16.1);
  it removed the waiting. What's left to remove by graphing the talker: its idle gaps, ~3 ms (7.7 ms
  span, ~4.6 busy: 16.1 busy minus the predictor's 11.5).
- **6,658 kernels a frame**, far more than the ~2,000 estimated: each RMSNorm (4 a layer: input, post,
  and Qwen3's per-head q and k norms) is ~8 small kernels (to float32, square, mean, add, rsqrt, multiply,
  back to bfloat16, scale); rotary ~10; and so on. 15 passes × 5 layers, plus 28 talker layers.
- **The bigger lever now is the number of kernels, not the CPU.** Even inside a graph each kernel takes a
  few microseconds of GPU time to start and finish, however little it does: 6,658 × ~2 µs ≈ 13 ms. The
  data itself is small: every layer's weights read once per pass is ~3.2 GB a frame (predictor 2.3,
  talker 0.86), ~7 ms at the RTX 5060's 448 GB/s. So **fusing** kernels (one kernel for a whole RMSNorm,
  for the rotary, …) could take the 16 ms of GPU work toward ~7-8.
