# 10: speech-to-text on the GPU, beside the TTS (speech-service 4.5d parts 1-2, 2026-11-17)
**Questions:** does Qwen3-ASR-0.6B load in the TTS's environment (T12)? How accurate and how fast is it
on the RTX 5060, against Whisper large-v3-turbo and the Pi? Do both models fit, and does either slow
the other?

**Setup:** image `speech-spike` (`Dockerfile` here): the TTS spike image (PyTorch 2.15.0, CUDA 13.2) +
transformers 4.57.6, `qwen-asr` 0.0.6 without its pins, the packages it imports. Models pinned:
Qwen/Qwen3-ASR-0.6B `5eb1441` (Apache-2.0, 0.94B parameters, 1.79 GB), openai/whisper-large-v3-turbo
`41f01f3` (MIT). The test set: pi-server's spike (`prep_data.py`, copied): FLEURS English and Chinese,
ASCEND mixed Chinese-English, 40 clips each (6.1, 6.2, 2.6 min); its scoring (`scoring.py`, copied).

**Files:** `tts_same.py` (the TTS's codes on both transformers versions), `asr_bench.py` (accuracy,
latency), `summary.py` (the table from its per-clip results), `together.py` (both in one process).

## 1. Dependencies: resolved
`qwen-tts` pins `transformers==4.57.3`, `qwen-asr` pins `==4.57.6` (patch releases of 4.57). With 4.57.6:

    transformers 4.57.6
    same  qwen:qwen              (47, 16) vs (47, 16)  Hi, how can I help you today?
    … all 10 the same: 5 sentences × Qwen's path and ours (ours-fused:ours-graph)

`pip check` names only the two deliberate deviations (qwen-tts's pin; qwen-asr's demo-only flask).
`nagisa` is needed though unused: the package imports its forced aligner at load.

## 2. Accuracy and latency (greedy; 120 clips)
    mode               en    zh   mix |    en*   zh* |    RTF  final p50/p90 ms  chunk max  1st partial s
    qwen-utt          3.9   4.2  12.5 |    3.5   2.8 |  0.027      177/289               -              -
    qwen-stream-1     5.0   5.1  13.3 |    4.2   3.1 |  0.075       58/67              179            1.0
    qwen-stream-2     4.5   5.0  12.9 |    3.8   3.1 |  0.051       60/103             226            2.0
    whisper-utt       3.6   4.8  24.7 |    3.2   4.4 |  0.026      189/219               -              -

en/zh/mix: error % (WER / CER / MER); en*/zh*: clips whose reference has no digits. "final": the work
left after the speaker stops. The Pi (pi-server's spike, x-asr streaming, 2 clips a language, throttled):
~20% English, ~2% Chinese, ~31% mixed, RTF 0.32.

- **Qwen3-ASR halves Whisper's error on mixed speech** (12.5% vs 24.7%), close to it on English and
  Chinese, far ahead of the Pi.
- **How Qwen3-ASR streams** (its code, `streaming_transcribe`): not incrementally. Every chunk it
  **re-feeds all the audio so far**, with its text so far less the last 5 tokens as the prompt's prefix
  (none for the first 2 chunks), so settled text stays and only the tail is re-decided. vLLM only makes
  that faster; on the transformers backend the same loop works (ours, `asr_bench.Qwen.stream`).
- **Streaming: 58 ms left after the speaker stops** (vs 177 for the whole utterance), partial text every
  second; the cost: re-feeding grows with the utterance (a 1 s chunk took up to 179 ms by ~15 s of
  audio, still far under its 1 s), RTF 0.075. A slight accuracy cost (5.0 vs 3.9% English).

## 3. Both models in one process
    VRAM: TTS peak 2.30 GB; both, PyTorch peak 3.98 GB; the GPU's used 4.70 of 7.96 GB (with the CUDA context)

TTS: fused predictor, graphed talker, two streams; three sentences × 3 runs. ASR on its own stream.

    alone:                            first audio 21 ms, margin 66 ms (worst 62), RTF 0.172
    ASR whole utterances nonstop, priority -1 (first run):
                                      first audio 31 ms, margin 62 ms (worst -131), RTF 0.242
      (rerun)                         first audio 29 ms, margin 63 ms (worst 60),   RTF 0.241   ASR final 293 ms (alone 175)
    the same, ASR priority 0:         first audio 30 ms, margin 63 ms (worst 60),   RTF 0.225   ASR final 350 ms (alone 177)
    ASR streaming 1 s chunks in real time (barge-in as it would be), priority -1:
                                      first audio 20 ms, margin 66 ms (worst 65),   RTF 0.178   ASR final 59 ms (alone 58)
    the same, priority 0:             first audio 21 ms, margin 66 ms (worst 59),   RTF 0.175   ASR final 61 ms (alone 59)

- **Both fit: 4.7 of 8 GB**, 3.3 GB to spare (batches of 2 in 4.5e need ~0.5 GB more).
- **Barge-in as it would be costs the TTS nothing** (first audio 20-21 ms, margin 66 ms) and the
  speech-to-text nothing (59-61 ms final).
- **The worst case** (whole utterances nonstop) slows the TTS (RTF 0.17 → 0.24, first audio +10 ms) but
  it keeps its margin; the ASR's final ~1.7× slower.
- **One 131 ms gap**, in the first worst-case run only; not in its rerun or the other three: like the
  rare ~110-120 ms hiccups of spikes/03 (twice in ~40 runs), not a priority effect. **Open**: find it
  (candidates: Python's garbage collector, the allocator) in 4.5e/f.
- **Priorities:** the TTS has the hard deadline (playback must not stop), speech-to-text a soft one: the
  ASR's stream level with the TTS's generation (0), the TTS's decode above both (-1). Priority 0 vs -1
  made no difference to the TTS here; 0 keeps the deadline that matters first.

## Decided (4.5d part 3, the engine)
Qwen3-ASR-0.6B, streaming in 1 s chunks (partial text every second, ~60 ms after the speaker stops),
Qwen's re-feed method on our own loop over the transformers backend (no vLLM), on its own CUDA stream at
priority 0, in the same process as the TTS.
