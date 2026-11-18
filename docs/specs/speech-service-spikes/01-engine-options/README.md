# 01: engine options (4.5a, 2026-11-16)
**Question:** which way to run Qwen3-TTS on the RTX 5060: the official package as it is, our own
streaming around it, a batch of sentences, or CUDA graphs? And does it fit the 8 GB?

**How:** six sentences (four short voice replies, two of 8–9 s; English and Chinese, `common.py`),
sampling on (the defaults), seed 0. "First audio": from the call to the first audio. "RTF" (real-time
factor): generation time ÷ audio length; below 1 is faster than speech.
- `spike_official.py` (image `Dockerfile.official`): A, B, C on the official package.
- `spike_faster.py` (image `Dockerfile.faster`): D, the community package `faster-qwen3-tts` 0.5.4, which
  captures CUDA graphs. Only as a reference measurement: it needed transformers pinned to 5.15.1 (5.23
  broke its copy of the model code).
- Raw output: `run-official.log`, `run-faster.log`.

## Results
| Run | RTF (median) | First audio (median / worst) | Peak VRAM |
|---|---|---|---|
| A: the official package, whole sentences | 0.47 | 1,735 / 4,177 ms | 2.5 GB |
| **B: our streaming loop, plain PyTorch, 4-frame chunks** | **0.51** | **169 / 173 ms** | 2.5 GB |
| B with 12-frame chunks (0.96 s) | 0.47 | 457 ms | |
| C: a batch of 2 vs one at a time | throughput ×1.75–1.85 (similar lengths), ×1.03 (different lengths) | | |
| **D: CUDA graphs (`faster-qwen3-tts`), 4-frame chunks** | **0.33** | **112 / 179 ms** | 2.3 GB |
| D with 12-frame chunks | 0.31 | 301 ms | |

Loading 1.7 s; the first generation ~10 s (CUDA starting up), so the service warms up before healthy.

## What it decided
- **Streaming** makes the first audio ~10× earlier at the same speed, nearly independent of the
  sentence's length. 4-frame chunks sound as good as whole sentences (listened).
- **CUDA graphs:** ~27 ms of work per 80 ms frame instead of ~40: ×1.5, and ~55 ms earlier first audio.
  The model is small but not purely launch-bound, so not more. Built ourselves (T11), as the learning goal.
- **Batching** pays off for two callers at once, not for one conversation: one worker already outruns
  playback.
