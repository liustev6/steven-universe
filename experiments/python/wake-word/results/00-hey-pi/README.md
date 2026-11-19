# Spike 00: "Hey Pi", the first wake word of our own (2026-11-18)

**Question:** can microWakeWord's training recipe, run on our own hardware, give Hey Pi a wake word of its own
in place of the built-in `hey_jarvis`?

**Answer:** yes; it has been the Pi's only wake word since 2026-11-18, at cutoff 0.95. On its own held-out test
it misses 24% at 0.4 false wakes an hour; it wakes for 99% of the owner's clips (the ones it trained on). In real
use it wakes too easily: measured on 2026-11-19 against the same audio as 小派小派 (spike 04), **6.7 false wakes an
hour in the owner's room and 35.5 in everyday Chinese speech** at 0.95. That's why the word is moving to 小派小派.

**The common test:** `test-hey_pi.txt` here is this spike's model in evaluate.py, the same test every model takes (the spec's numbers). The tables below are from the spike's own test at the time.

## Setup
- **Positives:** 171 of the owner's "Hey Pi"s on the Pi's mic (`segment.py`; ×5), 2,000 Piper clips (its LibriTTS
  model: 904 speakers), 510 from speech-service's 9 voices.
- **Negatives:** microWakeWord's sets (English read speech, dinner parties, music, sounds) and 1,530 near misses
  (`phrases/hey_pi.yaml`: "Hey pal", "Apple pie", "Hey Bai" …).
- **Training:** `train.py config.yaml`, MixedNet, 10,000 steps in 7.7 min on an M-series Mac; best at step 8,000.

## Results
| | Value |
|---|---|
| held-out test, quantized streaming model, cutoff 0.90 | misses 21% at 1.1 false wakes/h |
| … cutoff 0.95 (deployed) | misses 24% at 0.4/h |
| through pi-server's code at 0.95: the owner's clips (trained on) | 99% |
| … Piper and TTS voices, by voice | 73-97% ("嘿，Pi" 42%) |
| … music, 105 min | 0 wakes |
| … AudioSet | 1.2% of clips |
| near misses still waking it | "Hey pal", "Hey, hi" ~50%; "Apple pie", "Hey Bai" 13-14% |

**2026-11-19, the same streams as spike 04** (false wakes per hour, cutoffs 0.90 / 0.95 / 0.97):

| | 0.90 | 0.95 | 0.97 |
|---|---|---|---|
| the owner's room, the Pi's mic (45 min) | 8.0 | 6.7 | 6.7 |
| the owner's short videos (15 min) | 0 | 0 | 0 |
| Mandarin meetings (2 h) | 3.0 | 2.0 | 1.0 |
| everyday Chinese, unheard voices (30 min) | 49.3 | 35.5 | 25.7 |
| AudioSet sounds (35 min) | 1.7 | 1.7 | 1.7 |

## What it says
- The recipe works on our own hardware, and the owner's own recordings make it reliable for the owner.
- A two-syllable English word in a Chinese-speaking home wakes to everyday speech ("Hey" + any *pai*-like
  syllable), and its negatives were English: it never learned what Mandarin conversation sounds like. Spikes
  01-04 move to a longer Chinese word, with Mandarin negatives.

Files: the code in [experiments/python/wake-word/](../../) (`train.py`,
`config.yaml`, `segment.py`, `tts_samples.py`, `phrases/hey_pi.yaml`); the model in pi-server
(`python/services/pi-server/models/hey_pi.{tflite,json}`).
