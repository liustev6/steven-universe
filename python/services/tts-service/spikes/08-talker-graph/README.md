# 08: a decode-attention kernel and the talker's graph (4.5c stage 2, steps 3-4, 2026-11-17)
**Question:** the talker's cache grows every frame, but a graph records fixed shapes. Option c (agreed):
our own attention kernel for one query position that reads the length from GPU memory, so one graph
serves every frame. Is it right, and what does the talker's graph then buy?

**What:**
- `kernels._decode_attention`: one program per (batch, query head), over cache positions 0…position in
  blocks of 64, with an online softmax (FlashAttention's trick: running max, running sum of exponentials,
  running weighted sum of values, rescaled by exp(old max − new max) when a block raises the max). The
  position comes from a GPU int32 tensor; so does the rotary/cache kernel's write position
  (`START_FROM_PTR`).
- `TalkerLoop(fused=True)`: our norms, rotary/cache and decode attention ("ours-fused");
  `graph=True`: each frame's step (28 layers + codec_head, ~550 kernels) captured once, replayed at every
  position: write the position, copy the input, replay ("ours-graph").
- Tests: `test_decode_attention_matches_pytorch` (lengths 1 … 2,048; the cache past the length filled
  with NaN, never read); `test_fused_talker_is_close_to_the_exact_one` (Qwen's prompts for the test
  sentences, the exact talker's frames fed to the fused one, code 1's scores step by step, near-tie
  standard; eager and graph); `test_our_talker_samples_and_stops`.

## Results
    decode attention vs PyTorch: max |difference| 0 (length 1) … 1.6e-2 (2) … 4.9e-4 (2,048): bfloat16 rounding
    talker (graph: False): 189 steps, 1 different code 1s, worst score difference 3.5 bfloat16 steps
    talker (graph: True):  189 steps, 1 different code 1s, worst score difference 3.5 bfloat16 steps
    75 passed

    predictor:talker      ms/frame  first audio    RTF
    qwen:qwen                 34.8         57 ms   0.49
    ours-fused:ours           12.9         39 ms   0.21
    ours-fused:ours-fused     11.5         42 ms   0.18
    ours-fused:ours-graph     11.4         43 ms   0.18

    busy.py:
    ours-fused:ours-fused frames 67:  12.5 ms a frame, GPU busy  11.3 ms (90%), 2186 kernels a frame
    ours-fused:ours-graph frames 66:  12.3 ms a frame, GPU busy  11.3 ms (92%), 2189 kernels a frame

## What it showed
- **The fused talker: −1.4 ms a frame** (12.9 → 11.5), RTF 0.18: 34.8 → 11.4 ms a frame from Qwen's code.
- **The talker's graph bought ~nothing (11.5 → 11.4): the frame is GPU-bound now.** The GPU is busy 90%
  of each frame without it; the CPU already launched the talker's kernels while the GPU was still on the
  predictor's graph. Graphs pay while the GPU waits on the CPU; here it no longer does. Kept as the
  default anyway: it frees the CPU (the HTTP server, other requests, Python's garbage collector) and
  makes a frame's timing steadier.
- **A concurrency bug found by the tests:** capturing a graph while another thread uses the GPU (the
  stream's thread decoding audio) fails in CUDA's default capture mode, which checks every thread.
  `capture_error_mode="thread_local"` (both graphs) checks only the capturing thread.
- **Next lever is not a kernel:** the first audio (42 ms) and the margin did not improve with faster
  frames; spikes/09 found why.
