# 07: our own talker loop (4.5c stage 2, step 2, 2026-11-17)
**Question:** the talker (28 layers, code 1 of each frame) still ran inside Hugging Face's `generate`, on
its growing cache. Our own loop (`app/tts_engine/talker.py`) over Qwen's modules, on our fixed cache
(`app/tts_engine/layers.py`, shared with the predictor), with Qwen's generation rules as GPU tensor operations:
does it give Qwen's exact codes, and what does it save?

**What it does:** Qwen's own code still builds the prompt; at its `self.talker.generate(...)` the engine
puts `TalkerLoop.generate`. Prefill the prompt into the cache; then each frame: the predictor (codes
2-16), the frame out to the stream, the 16 code embeddings summed + the next text hidden state, 28
layers at the next position, `codec_head`, then code 1 by Qwen's rules: repetition penalty 1.05 (a
"seen" mask on the GPU), control codes 2048-3071 suppressed except end-of-speech, no end-of-speech in
the first 2 frames, then sampling (or the best). One sync a frame: the end check.

**How:** `tests/test_engine_gpu.py::test_our_talker_gives_qwens_codes` (greedy, 3 sentences, with Qwen's
predictor and with our graph), `test_our_talker_samples_and_stops`; `scripts/bench.py predictor:talker`.

## Results
    64 passed (all GPU tests; the talker's: exact codes × 3 sentences × 2 predictors, sampling ends, the limit holds)

    predictor:talker      ms/frame  first audio    RTF
    ours-fused:qwen           17.0         40 ms   0.25
    ours-fused:ours           12.9         39 ms   0.21
    ours-graph:ours           16.0         45 ms   0.25

## What it showed
- **Qwen's exact codes:** the multimodal rotary positions are the ordinary ones for speech (all three
  position streams equal), and the generation rules reproduce `generate`'s bit for bit (the same float32
  operations, in disjoint places, so their order doesn't matter).
- **−4.1 ms a frame (−24%) without any graph:** what's gone is Hugging Face's per-step work around the
  talker: the growing cache (`torch.cat` of every earlier position, 28 layers, every frame: new memory
  and a copy that grows with the sentence), building a mask, recomputing the rotary angles, the logits
  processors' setup, the stop checks.
- **First audio barely moves** (40 → 39 ms): it's mostly the prompt's prefill (~8 ms) and the first
  frame, both the same work as before.
- **Next:** the talker step is still ~500 kernel launches from Python, with attention over a slice whose
  length grows every frame. Step 3: a decode-attention kernel that reads the length from GPU memory, so
  one graph serves every frame; step 4: capture it.
