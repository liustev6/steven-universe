# Spike 02: 小派小派 with Mandarin conversation and household noise (2026-11-18)

**Question:** spike 01 (our TTS alone, English-only negatives) woke for 46% of unheard voices. Does adding
Mandarin conversation and household noise, as negatives and as background, change that, and what does a
Mandarin home's false-wake rate look like?

**Answer:** much better. At the same false-wake level as spike 01 it wakes for 75% of unheard voices instead of
46% (cutoff 0.99 here, against spike 01 at 0.80), and in 2 h of Mandarin meetings it woke 0.5-1 times an hour
at cutoffs 0.97-0.99. Near misses are its weak spot: 小爱小爱 and 小胖小胖.

## What changed from spike 01
Same positives (1,345 cleaned TTS takes, 5 effective voices) and near misses, same training settings, plus:

| Added | As | Size | Weight |
|---|---|---|---|
| [AliMeeting](https://openslr.org/119/) train, one channel of the far-field array (`extract_alimeeting.py`) | negatives, 3 s clips (`cut_clips.py`, every 3rd) | 36 h of 111 | 10 (as the dinner-party set) |
| [DEMAND](https://zenodo.org/records/1227121), 17 places (kitchen, living room, washer, café, bus, …), 3 of 16 mics | negatives, and background noise | 3.2 h | 3 |
| [MUSAN](https://openslr.org/17) noise | negatives, and background noise | 5.7 h | 3 |

Training took 18 min (9 of them building the new sets' features). New test: **2 h of AliMeeting's test meetings**
(far-field, never trained on), played as one stream.

## Results

**Recall, the held-out voices** (128 takes), share woken:

| | 0.80 | 0.90 | 0.95 | 0.97 | 0.99 |
|---|---|---|---|---|---|
| spike 01, clean | 56% | 51% | 46% | 45% | 41% |
| **spike 02, clean** | **90%** | **86%** | **83%** | **78%** | **75%** |
| spike 01, background at 5 dB | 48% | 41% | 37% | 33% | 29% |
| **spike 02, background at 5 dB** | **68%** | **64%** | **58%** | **54%** | **50%** |
| spike 02, serena / uncle_fu (clean) | 97 / 82% | 95 / 76% | 91 / 74% | 86 / 69% | 83 / 66% |

**False wakes per hour**, each set as one stream:

| | 0.80 | 0.90 | 0.95 | 0.97 | 0.99 |
|---|---|---|---|---|---|
| spike 01, Mandarin meetings (2 h) | 0.5 | 0.5 | 0 | 0 | 0 |
| **spike 02, Mandarin meetings (2 h)** | 2.5 | 2.0 | 2.0 | **1.0** | **0.5** |
| spike 01, everyday Chinese, TTS (7 min) | 0 | 0 | 0 | 0 | 0 |
| spike 02, everyday Chinese, TTS (7 min) | 49 | 24 | 16 | 16 | **0** |
| both, your "Hey Pi" recordings (4 min) | 0 | 0 | 0 | 0 | 0 |
| spike 02, AudioSet sounds (35 min) | 1.7 | 0 | 0 | 0 | 0 |

The everyday-Chinese set is 7 minutes: 16/h there is 2 wakes, where one sentence runs into the next (no single
sentence reached 0.95; the closest: 小作文 0.94, 爸爸 0.94, 换个台 0.92, 小笼包 0.90).

**Near misses, the held-out voices** (132 takes), share that wakes it:

| | 0.80 | 0.90 | 0.95 | 0.97 | 0.99 |
|---|---|---|---|---|---|
| spike 01, all | 2% | 2% | 2% | 2% | 1% |
| spike 02, all | 9% | 5% | 5% | 3% | 2% |
| spike 02, 小爱小爱 | 50% | 50% | 50% | 33% | 17% |
| spike 02, 小胖小胖 | 67% | 33% | 33% | 17% | 0% |
| spike 02, 小派是谁？ | 33% | 17% | 17% | 17% | 17% |

## What it says
- **Real negatives let the model learn the word.** With English-only negatives, spike 01 could only be cautious by
  being deaf to unfamiliar voices. With Mandarin conversation and noise, spike 02 tells the word from everyday
  speech by its sound, so it can wake for many more voices at the same false-wake level: compare spike 02 at
  0.99 (75% clean, 50% in loud background, 0.5 false wakes/h in meetings) with spike 01 at 0.80 (56%, 48%, 0.5).
- **A cutoff of 0.97-0.99** for spike 02: 0.5-1 false wakes an hour in Mandarin conversation, 75-78% recall on
  unheard voices (more for yours, once recorded).
- **Weak spots:** other assistants' four-syllable names (小爱小爱: 17% even at 0.99), 小胖小胖, and noise
  (50% at 5 dB). Next: more near misses from more voices, speaker variety (spike 03, cloned voices), your
  recordings, and your room's own audio.

Files: `results.txt` here (both models' evaluation output); the code in [spikes/02-mandarin-noise/](../../spikes/02-mandarin-noise/)
(`extract_alimeeting.py`, `cut_clips.py`); the config
`config-xiao-pai-02.yaml`; the model `out/xiao_pai_02.{tflite,json}`; the data in `data/` (not in git).
