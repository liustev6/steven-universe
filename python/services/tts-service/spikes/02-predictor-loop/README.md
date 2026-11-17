# 02: our own code-predictor loop (4.5c stage 1, step 2, 2026-11-17)
**Question:** the code predictor makes 15 of each frame's 16 codes, through Hugging Face's `generate`.
Does our own loop over the same modules (`app/engine/predictor.py`) give Qwen's exact codes, and what
does skipping `generate` save? And can it use a fixed 16-slot cache (what a CUDA graph needs)?

**How:**
- Codes: `tests/test_engine_gpu.py::test_streamed_codes_are_qwens_own`, greedy, three sentences
  (English, Chinese, a long one), per predictor.
- Speed: `scripts/bench.py`, sampled, three sentences × 3 runs, medians.
- The drift: `static_drift.py` (here). It records the predictor's inputs for every frame of one
  sentence (88 frames), then runs both caches on the same inputs, teacher-forced (the fixed cache is
  fed the growing cache's codes), comparing each step's scores. Run it with `/opt/tts-dev` mounted at `/src`.

## Results
| Predictor | Codes vs Qwen's | ms/frame | First audio | RTF |
|---|---|---|---|---|
| `qwen`: Qwen's `generate` | the reference | 35.2 | 165 ms | 0.49 |
| `ours`: our loop, Hugging Face's growing cache | **identical** (3 sentences) | **31.1** | **149 ms** | **0.43** |
| `ours-static`: the same, Hugging Face's fixed 16-slot cache | differ | 37.6 | 174 ms | 0.51 |

The drift, teacher-forced over 1,320 steps (88 frames × 15):

    steps 1320, argmax flips 44, max |logit diff| 0.5000
    top-2 margins where it flipped: [0.0, 0.0, 0.125, 0.125, 0.0, 0.0, 0.0, 0.0, 0.125, 0.0]
    grown mask: None
    fixed mask: (1, 1, 1, 16)

## What it showed
- **Skipping `generate`** (its per-step sampling setup, stop checks, cache bookkeeping): −4 ms a frame, −12%.
- **The fixed cache drifts**, and it isn't a bug: every flip was a near-tie (the top two scores equal,
  or one bfloat16 step, 0.125, apart). The growing cache needs no mask, so PyTorch's attention runs its
  flash kernel; the fixed cache attends over all 16 slots with a mask, which sends it to another kernel,
  which adds in another order and rounds differently. Slower too: the mask is built every pass.
- **For the graph (step 3):** the loop is unrolled and always 15 passes, so pass k always attends over
  exactly k+2 positions: our own attention reads that fixed slice of a 16-slot cache, with no mask.
- **Found on the way:** greedy, some short sentences never produce the end code ("Warming up." ran to
  Qwen's 8,192-frame limit, 11 minutes of audio, which made every test run take 11 minutes). Now each
  sentence is capped at 50 + 8 frames a character (`max_frames`), and the warm-up samples.

## Step 3: our own fixed cache, read in fixed slices (2026-11-17)
`StaticCodePredictor`: the 5 layers' forward written out over Qwen's modules, our own 16-position cache
per layer, pass k attending over the slice 0…k+1 with no mask (the same attention call as the growing
cache: `enable_gqa=True`, causal only for the 2-position first pass).

| Predictor | Codes vs Qwen's | ms/frame | First audio | RTF |
|---|---|---|---|---|
| `qwen` | the reference | 35.0 | 57 ms | 0.49 |
| `ours` (step 2) | identical | 30.5 | 55 ms | 0.43 |
| **`ours-static` (step 3)** | **identical** (3 sentences) | **27.0** | **50 ms** | **0.38** |

(First audio is now with the 1, 1, 2, 4 schedule, spikes/03.)

- **Exact again:** reading only the written positions, without a mask, gives PyTorch's attention the
  same choice of kernel as the growing cache, so the same rounding, so Qwen's codes.
- **Faster without any graph:** −3.5 ms a frame from step 2. Hugging Face's forward per pass built a
  mask, computed the rotary angles, and grew the cache with `torch.cat` (new memory, a copy of all
  earlier positions); ours does none of that: the angles are computed once, the cache written in place.
