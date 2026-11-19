# speech-service spikes: the metrics
The measurements that shaped speech-service ([speech-service.md](../speech-service.md), its decisions and
an appendix summarizing each). Each folder keeps its write-up (`README.md`: the question, how it ran, the
results, what was decided) and, from spike 13 on, its raw output (`.txt`, `.log`). **The spikes' code was
removed once each was decided** (2026-11-18), so the service's dependencies can move on; it's in git:
`git log --diff-filter=D -- python/services/speech-service/spikes`. So were `scripts/bench.py` and
`scripts/chunks.py` (spikes 02-09; 2026-11-19): `git log --diff-filter=D -- python/services/speech-service/scripts`.
The measuring tools still in use are in `experiments/python/speech-service/`: `evenness.py` (from 14),
`roundtrip.py` (13), `trace_reply.py` (16); the service keeps `scripts/gpu_window.sh` (its GPU tests).

All ran on CT 108's RTX 5060 (8 GB), PyTorch 2.15.0 (CUDA 13.2), `qwen-tts` 0.1.1 (transformers 4.57.3).
The TTS model: Qwen3-TTS-12Hz-0.6B-CustomVoice (`85e237c`) through spike 14; spike 15 compared the 1.7B
(`0c0e305`), live since 2026-11-18; spike 16 on the 1.7B.

| # | Date | Question | Answer |
|---|---|---|---|
| [01](01-engine-options/) | 2026-11-16 | Official package, our streaming, batching, or CUDA graphs? | Our streaming: first audio 169 ms vs 1,735; graphs (a community package): 112 ms, RTF 0.33. Decided T11: our own engine, our own graphs |
| [02](02-predictor-loop/) | 2026-11-17 | Does our own code-predictor loop give Qwen's exact codes, and how fast? | Yes, −12% per frame. A fixed cache via Hugging Face's mask drifts (3% of steps flip at near-ties): another attention kernel. Our own fixed cache, read in slices without a mask: exact, 27.0 ms a frame. As a CUDA graph: exact, 20.0 ms a frame (−42%), RTF 0.29 |
| [03](03-chunk-schedule/) | 2026-11-17 | How small can the first chunk be without stalls or worse sound? | Decode after 1, 1, 2, then every 4 frames: first audio 52 ms (from 143), same cost, 43 ms margin; with the graph: 45 ms, margin 56 |
| [04](04-frame-split/) | 2026-11-17 | With the predictor graphed, where does a frame's 20 ms go? | Predictor 11.5, talker 7.7, loop 0.7. GPU busy 16 ms: 6,658 tiny kernels a frame. Graphing the talker wins ~3 ms; fusing kernels could win ~8 |
| [05](05-fused-rmsnorm/) | 2026-11-17 | A fused RMSNorm (Triton): how much faster, how exact? | 8 kernels → 1: 18.3 ms a frame (−10%), RTF 0.27. 1 element in 735,000 one bfloat16 step off; 0.2% of steps flip, all near-ties |
| [06](06-fused-rope/) | 2026-11-17 | The rotary + cache write as one kernel: faster, bit-exact? | 14 kernels → 1, bit-exact once FMA contraction is off: 17.0 ms a frame, RTF 0.25, first audio 39 ms, margin 60 ms |
| [07](07-talker-loop/) | 2026-11-17 | Our own talker loop instead of Hugging Face's `generate`: exact, and faster? | Qwen's exact codes; 12.9 ms a frame (from 17.0), RTF 0.21, no graph yet |
| [08](08-talker-graph/) | 2026-11-17 | A decode-attention kernel (length from GPU memory) and the talker's graph | Kernel right (bfloat16 rounding); fused talker 11.5 ms a frame (RTF 0.18); the graph ~nothing more: GPU-bound (busy 90%) |
| [09](09-streams/) | 2026-11-17 | Why don't first audio and margin follow the faster frames? | Decode and generation shared one CUDA stream, and the end check's `int()` waited for all of it. Two streams + events: first audio 42 → 20 ms, margin 47 → 66 ms |
| [10](10-stt/) | 2026-11-17 | Speech-to-text on the GPU beside the TTS: does Qwen3-ASR-0.6B load, how good, does either slow the other? | Loads (transformers 4.57.6; the TTS's codes unchanged). Error 3.9/4.2/12.5% en/zh/mix (Whisper turbo: 24.7% mixed); streaming 58 ms after the speaker stops. Both: 4.7 of 8 GB; realistic barge-in costs neither anything |
| [11](11-batching/) | 2026-11-17 | What does a batch of 2 cost, piece by piece? | TTS generation ×1.07-1.08 (batch it); the codec's decode ×1.89 (compute-bound, 86% re-decoding context: a stateful decode instead, later); STT passes ×1.64 but unaligned (in turn). 2 + 2 ≈ 32-41% of the GPU |
| [12](12-two-plus-two/) | 2026-11-17 | 2 TTS + 2 STT at once, in real time? | Yes, with one work thread per model: no gaps, TTS first audio 27-49 ms, margins ≥ 37 ms, STT finals 63-130 ms, every utterance heard right; 4 + 4 too (margins down to 20 ms). One thread for both would let an STT pass stall the TTS |
| [13](13-text-normalization/) | 2026-11-17 | Numbers and symbols read wrong ("5,959", phone numbers, mixed zh/en): does text normalization fix it? | Yes: 27 → 43 of 48 runs heard right with wetext (`auto` per sentence, ~1-2 ms); code-switching ("London" in Chinese) was never the problem. What's left is the sampling's babble |
| [14](14-speaking-rate/) | 2026-11-17 | Why do some short sentences take so long? | Mostly the voice: ryan (Hey Pi's) stretched, paused up to 1.5 s and babbled on short sentences (worst 3.6x its median, 6 of 66 babbled); serena never past 1.16x. Every sentence carries ~0.5 s of silence at each end. The codec's chunks don't change timing |
| [15](15-model-and-sampling/) | 2026-11-18 | Does lower temperature, or the 1.7B model, fix it? Does the 1.7B fit with 2 + 2? Is it our engine? | The 1.7B with a steady-pace instruction: short sentences at natural length, no runaways, 0 of 88 babbled; fits (6.96 of 8.15 GB under 2 + 2), first audio 33 ms, RTF 0.25. Lower temperature doesn't fix it; Qwen's own path stretches as often (the model, not our engine). Decided: the 1.7B |
| [16](16-streaming-text/) | 2026-11-18 | One continuous generation per reply, the text fed as it comes (Qwen's streaming mode)? | Yes: today's sentence joins are 0.08-0.18 s whatever the punctuation (what sounds wrong); in one generation the model places its own pauses, speaks the reply as one passage, and read it most faithfully (0.99-1.00). Next: the engine, a row per reply |

**WAVs** weren't kept in git (listened to at the time).
