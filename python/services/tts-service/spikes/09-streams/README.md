# 09: decode and generation on one stream (2026-11-17)
**Question:** frames got 6 ms faster (spikes/08), but the first audio (39 → 42 ms) and the margin (~55-60
ms) didn't follow. Where does the first audio's time go?

**How:** `timeline.py`: the codec's decode alone (synchronized), then when chunks 1-3 come out of
`stream()` (predictor ours-fused, talker ours-graph, greedy, 3 runs).

## Results (before any change)
    codec decode alone,  1 frames:   8.3 ms
    codec decode alone,  2 frames:   8.8 ms
    codec decode alone,  4 frames:   9.1 ms
    codec decode alone, 29 frames:  16.8 ms
    run 0: chunks 1-3 out at 42, 75, 104 ms
    run 1: chunks 1-3 out at 42, 77, 113 ms
    run 2: chunks 1-3 out at 42, 77, 111 ms

Ideal: chunk 1 = prefill (~8) + frame 1 (~11) + decode (~8) ≈ 28 ms; chunk 2 = frame 2 made while chunk
1 decodes ≈ 55 ms. Measured: 42 and 75-77: ~14-20 ms lost a chunk.

## Why
Both threads use PyTorch's default CUDA stream, one queue: the codec's decode (the stream's thread)
waits behind generation work already queued (the generation thread), and generation waits behind the
decode. Worse, the stream's thread checks each frame for the end code with `int(item[0])`, a copy to
the CPU that waits for **everything** queued on that stream before it, including the next frame's
generation.
