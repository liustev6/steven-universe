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

## The fix: two streams and events (app/engine/tts.py)
- Generation runs on its own stream (`torch.cuda.stream(self._generation)` in the generation thread);
  the codec's decode on another, `priority=-1` (higher: its work goes first when both wait).
- Each frame crosses with an event: the generation thread records it right after the frame's codes;
  the consumer's stream waits on it **on the GPU** (`wait_event`), then `record_stream` tells the memory
  allocator the frame is in use on that stream too. The CPU never waits for later generation work.
- The end-of-speech check moved into the generation thread (our talker already knows; Qwen's hook
  reads it there, where `generate` syncs every step anyway).

## Results with two streams
    run 0: chunks 1-3 out at 20, 33, 57 ms
    run 1: chunks 1-3 out at 19, 33, 57 ms
    run 2: chunks 1-3 out at 20, 33, 57 ms

    chunks.py 1,1,2,4 --predictor ours-fused:
      ours:          21 ms  0.18   0 ms (max   0)    65 ms (  65)  27.6 dB
      ours-graph:    19 ms  0.16   0 ms (max   0)    66 ms (  66)  25.7 dB

    bench.py:
      qwen:qwen                 34.9         57 ms   0.48
      ours-fused:ours-graph     11.4         20 ms   0.17
    75 passed

- **First audio 42 → 20 ms; chunk 2 75 → 33; chunk 3 104 → 57; margin 47 → 66 ms.** Better than the
  ~28 ms estimated: the first decode now overlaps the second frame instead of queueing behind it.
- The talker's graph now shows its worth too (66 ms margin vs 65; 19 ms first audio vs 21): with the
  CPU no longer blocked behind the GPU, freeing it matters again.
