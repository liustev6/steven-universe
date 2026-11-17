# tts-service spikes
Measurements that shaped the engine, kept with their code and raw output so they can be rerun and
compared. Each folder has a `README.md`: the question, how it ran, the results, what was decided.
All ran on CT 108's RTX 5060 (8 GB), PyTorch 2.15.0 (CUDA 13.2), `qwen-tts` 0.1.1 (transformers 4.57.3),
the model Qwen3-TTS-12Hz-0.6B-CustomVoice at revision `85e237c`.

| # | Date | Question | Answer |
|---|---|---|---|
| [01](01-engine-options/) | 2026-11-16 | Official package, our streaming, batching, or CUDA graphs? | Our streaming: first audio 169 ms vs 1,735; graphs (a community package): 112 ms, RTF 0.33. Decided T11: our own engine, our own graphs |
| [02](02-predictor-loop/) | 2026-11-17 | Does our own code-predictor loop give Qwen's exact codes, and how fast? | Yes, −12% per frame. A fixed cache via Hugging Face's mask drifts (3% of steps flip at near-ties): another attention kernel. Our own fixed cache, read in slices without a mask: exact, 27.0 ms a frame. As a CUDA graph: exact, 20.0 ms a frame (−42%), RTF 0.29 |
| [03](03-chunk-schedule/) | 2026-11-17 | How small can the first chunk be without stalls or worse sound? | Decode after 1, 1, 2, then every 4 frames: first audio 52 ms (from 143), same cost, 43 ms margin; with the graph: 45 ms, margin 56 |
| [04](04-frame-split/) | 2026-11-17 | With the predictor graphed, where does a frame's 20 ms go? | Predictor 11.5, talker 7.7, loop 0.7. GPU busy 16 ms: 6,658 tiny kernels a frame. Graphing the talker wins ~3 ms; fusing kernels could win ~8 |
| [05](05-fused-rmsnorm/) | 2026-11-17 | A fused RMSNorm (Triton): how much faster, how exact? | 8 kernels → 1: 18.3 ms a frame (−10%), RTF 0.27. 1 element in 735,000 one bfloat16 step off; 0.2% of steps flip, all near-ties |

**The tools that stay** (in `scripts/`, reused at each step): `bench.py` (each predictor: ms per frame,
first audio, RTF) and `chunks.py` (each chunk schedule: first audio, RTF, stalls, margin, SNR, WAVs).
GPU tests and spikes run in the spike's image on CT 108:

    docker run --rm --gpus all -v /opt/tts-dev:/src -w /src -v /var/lib/tts-spike/models:/models:ro \
      -e TTS_MODEL=/models/Qwen3-TTS-12Hz-0.6B-CustomVoice tts-spike-official python scripts/chunks.py

**WAVs** stay on CT 108, not in git: `/opt/tts-spike/out` (01) and `/opt/tts-dev/out/chunks` (03).
