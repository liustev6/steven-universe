# 12: everything at once, in real time (speech-service 4.5e step 4, 2026-11-17)
**Question:** 2 TTS streams and 2 STT streams at once (T13), each model's batch driven by its own work
thread, the GPU shared through CUDA streams: does every stream keep its deadline? And 4 + 4?

**How:** `run.py`. TTS: a work thread joins sentences and steps the `TTSBatch`, handing each frame over
right after the predictor; a thread per stream decodes chunks (1, 1, 2, 4) on the high-priority
stream into a simulated player. STT: a feeder per stream sends its clip 100 ms at a time in real time,
then its end; the work thread ticks the `STTBatch` every 100 ms. The STT's clips are the TTS's own
speech (made first), so what was said is known. Sampled TTS; 3 runs each after a warm run.

## Results
    clips (s): [4.2, 5.0, 5.1, 4.2]
    2 + 2, staggered (slots=2 each), VRAM peak 4.24 GB:
      TTS 1: first audio   27 ms, worst margin   63 ms, gaps    0 ms
      TTS 2: first audio   31 ms, worst margin   43 ms, gaps    0 ms
      STT 1: final  130 ms after the end (worst  137), heard 1.00 alike
      STT 2: final   63 ms after the end (worst  136), heard 1.00 alike
      STT batched passes per run: [7, 7, 7]
    2 + 2, all at once (slots=2 each), VRAM peak 4.59 GB:
      TTS 1: first audio   47 ms, worst margin   47 ms, gaps    0 ms
      TTS 2: first audio   49 ms, worst margin   37 ms, gaps    0 ms
      STT 1: final   73 ms after the end (worst  111), heard 1.00 alike
      STT 2: final   95 ms after the end (worst  133), heard 1.00 alike
      STT batched passes per run: [7, 7, 7]
    4 + 4, staggered (slots=4 each), VRAM peak 5.03 GB:
      TTS 1: first audio   29 ms, worst margin   62 ms, gaps    0 ms
      TTS 2: first audio   50 ms, worst margin   51 ms, gaps    0 ms
      TTS 3: first audio   44 ms, worst margin   26 ms, gaps    0 ms
      TTS 4: first audio   53 ms, worst margin   20 ms, gaps    0 ms
      STT 1: final   73 ms after the end (worst   80), heard 1.00 alike
      STT 2: final  144 ms after the end (worst  217), heard 1.00 alike
      STT 3: final   77 ms after the end (worst   95), heard 1.00 alike
      STT 4: final  153 ms after the end (worst  155), heard 1.00 alike
      STT batched passes per run: [9, 8, 9]

## What it showed
- **2 + 2 keeps every deadline:** no gaps; the TTS's first audio 27-49 ms, margins ≥ 37 ms; the STT's
  finals 63-130 ms after the speaker stops; every utterance heard right; 7 batched passes where
  separate ones would be ~10.
- **4 + 4 too, with less room:** no gaps, but TTS margins down to 20 ms (the codec's decode, spikes/11)
  and STT finals up to 217 ms.
- **The STT's finals wait for the next tick** (here every 100 ms): up to 100 ms of their latency is
  waiting, not work. The service should tick at once when an utterance ends.
- **One work thread per model**, not one for both: an STT pass (~60 ms) would otherwise hold up the
  TTS's steps (~11 ms each) for its whole length, the TTS's entire margin; on two threads and two
  CUDA streams they overlap (spike 10). T13 updated.
- **The rare ~110-130 ms gap** (spikes/03, 10) didn't appear in these 12 runs. Still open.
