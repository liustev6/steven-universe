# 11: what a batch of 2 costs, piece by piece (speech-service 4.5e step 1, 2026-11-17)
**Question:** before building continuous batching (T13), which pieces get cheaper per stream when two
streams share a pass, and which don't?

**How:** `batch_bench.py`: each piece at batch 1, 2 and 4, synchronized GPU time, medians. The TTS's
pieces as graphs (our fused predictor; 28 fused talker layers + codec_head on a cache filled to 300
positions, every row at the same position); the codec's decode on 29 frames (25 of context + 4 new),
one batched call (Qwen's `decode` pads a list into one batch) vs separate calls; the STT's pass on
utterances of 3 and 8 s (synthetic), one padded batch vs one after the other.

## Results
    (a) predictor, 15 passes (graph)    B=1:   8.41 ms (×1.00)  B=2:   9.09 ms (×1.08)  B=4:   9.52 ms (×1.13)
    (b) talker step, 28 layers (graph)  B=1:   2.89 ms (×1.00)  B=2:   3.09 ms (×1.07)  B=4:   3.24 ms (×1.12)
    (c) codec decode, one batched call  B=1:  16.98 ms (×1.00)  B=2:  32.11 ms (×1.89)  B=4:  60.68 ms (×3.57)
        codec decode, separate calls    B=1:  16.96 ms (×1.00)  B=2:  33.89 ms (×2.00)  B=4:  67.76 ms (×4.00)
    (d) STT pass (3 s, 8 s), batched    B=1:  35.89 ms (×1.00)  B=2:  58.95 ms (×1.64)  B=4:  80.51 ms (×2.24)
        STT pass, one after another     B=1:  36.02 ms (×1.00)  B=2:  76.07 ms (×2.11)  B=4: 152.24 ms (×4.23)
    VRAM peak 4.44 GB (both models, all of the above)

## What it showed
- **The TTS's generation batches almost for free: 2 streams cost ×1.07-1.08 one.** Each step reads all
  the weights once, whatever the batch (bandwidth-bound, spike 06), so a second row adds only its own
  attention and sampling. Continuous batching for the TTS: worth building.
- **The codec's decode doesn't (×1.89): it's compute-bound**, and now the largest cost a stream has:
  17 ms per 4 new frames = 4.2 ms a frame, more than the generation's ~11. And **86% of it is re-decoding
  context** (25 of the 29 frames, to give the 4 new ones their history). The codec is causal and Mimi-
  based (Qwen's tokenizer subclasses `MimiModel`): a **stateful streaming decode** (keep the convolutions'
  and the transformer's state between chunks, decode only the new frames) could cut a stream's decode
  several-fold, and the first chunk's ~8 ms with it. A spike of its own, after batching.
- **The STT's passes batch somewhat (×1.64)**, but two streams' passes fall at unrelated moments (each
  every 1-4 s of its own audio); batching them means making one wait for the other's tick. Two STT
  streams cost ~60-80 ms a second of GPU in turn (~6-8%): **in turn, no batching**, for now.

## The budget for 2 + 2, from these numbers
    TTS, 2 streams batched:   generation 12.2 ms + decode 2 × 4.2 ms = ~20.6 ms per 80 ms of audio   ~26%
    STT, 2 streams in turn:   ~60-150 ms per second (grows with the utterances; the schedule caps it)  ~6-15%
                                                                                          total   ~32-41%
