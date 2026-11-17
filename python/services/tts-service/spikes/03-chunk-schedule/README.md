# 03: the chunk schedule (2026-11-17)
**Question:** the first audio waits for the first chunk of frames (each frame: ~30 ms to make, 80 ms of
audio). How small can the first chunk be, without stalls or worse sound?

**How:** `scripts/chunks.py`, our predictor, greedy, three sentences × 3 runs, medians. A simulated
player starts at the first audio and plays chunks back to back:
- **stalls:** time it waited with nothing to play (an audible gap); 0 is smooth.
- **margin:** the least audio still queued when a chunk arrived: how close it came to a gap.
  Negative means it ran dry.
- **SNR:** the streamed audio vs the whole sentence decoded at once (worst sentence); above ~20 dB no
  audible difference.

## Results
Single sizes (first run):

| Frames per decode | First audio | RTF | Stalls | SNR |
|---|---|---|---|---|
| 1 | 52 ms | 0.51 | none | 27.0 dB |
| 2 | 84 ms | 0.45 | none | 27.2 dB |
| 4 | 141 ms | 0.41 | none | 28.5 dB |

Schedules (the last size repeats), with the margin (last run):

    schedule  first audio   RTF         stalls  margin (worst)      SNR
           4       143 ms  0.41   0 ms (max   0)   193 ms ( 188)  28.5 dB
       1,2,4        52 ms  0.41   0 ms (max   0)    13 ms (  12)  28.2 dB
     1,1,2,4        52 ms  0.41   0 ms (max   0)    43 ms (  43)  28.5 dB
   1,1,2,2,4        52 ms  0.41   0 ms (max 108)    43 ms (-108)  27.5 dB

An earlier run of `1,2,4` stalled once, 123 ms.

## What it decided: 1, 1, 2, then every 4 (the default)
- **Each frame not waited for saves ~30 ms of first audio.** Size 1 throughout costs decode work (each
  decode re-reads up to 25 frames of context: RTF 0.51); a schedule gets the small first chunk at size
  4's cost.
- **The second chunk is the tightest moment:** the player holds only frame 1's 80 ms, so the next chunk
  must be small. 2 frames take ~68 ms (12 ms to spare); 1 frame leaves 43. After that the queue grows
  with every chunk.
- **The sound:** the codec's decoder is causal (causal convolutions, a causal transformer with a
  72-frame window): cutting right after the newest frame loses nothing. The small difference from the
  whole decode is the 25 frames of history (Qwen's own `chunked_decode` uses 25 too).
- **Open:** a rare hiccup of ~110–120 ms (twice in ~40 runs, not tied to a schedule) is more than the
  43 ms margin. Candidates: Python's garbage collector, the growing caches' allocations, a decode at a
  new length. Rerun this spike after the CUDA graphs (faster frames widen the margin).
