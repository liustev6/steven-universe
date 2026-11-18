# 14: how evenly does the TTS speak? (2026-11-17)

**Question.** Some short sentences take far too long: a word stretched, long pauses, babble
("哈哈哈哈", "嗯嗯嗯"). How often, for which voices, and where does the time go?

**How.** `rate.py`: 11 short replies (Chinese, English, mixed; 1 to 21 spoken units), each spoken
by a voice N times through the live service (sampling as Qwen ships it: temperature 0.9, top-k 50,
repetition penalty 1.05, both stages), each clip measured from its audio (sound above -45 dBFS in
20 ms windows: speech time, pauses of 150 ms or more, the silence before and after) and heard back
by speech-to-text. ryan and serena 6 runs each (`baseline.txt`); aiden, vivian, eric, dylan,
uncle_fu, serena 4 runs each (`voices.txt`).

## Results
| voice | worst / median duration | clips > 1.4x their median | longest pause | silence before / after | babble heard |
|---|---|---|---|---|---|
| **serena** | **1.16x** | **0 / 110** | 0.66 s | **0.22 / 0.30 s** | 0 |
| vivian | 1.28x | 0 / 44 | 1.22 s | 0.48 / 0.48 s | 0 |
| dylan | 1.41x | 1 / 44 | 1.58 s | 0.48 / 0.52 s | 0 |
| eric | 2.10x | 4 / 44 | 0.60 s | 0.09 / 0.18 s | 0 |
| uncle_fu | 2.55x | 3 / 44 | 1.96 s | 0.46 / 0.55 s | 0 |
| aiden | 4.09x | 1 / 44 | 1.64 s | 0.22 / 0.26 s | 0 |
| **ryan** (Hey Pi's) | **3.56x** | **9 / 66** | 1.52 s | 0.45 / 0.55 s | **4** ("哈哈哈哈哈哈！好的", "嗯嗯嗯嗯嗯", "嗯，是", one heard as nothing) |

- **It's the voice, mostly.** ryan stretches short sentences ("好的。" 1.5–5.8 s; "Sure." 1.6–7.1 s),
  pauses up to 1.5 s mid-sentence, and babbles; one 10-unit sentence took 15.5 s, 8.5 of it pauses.
  serena, on the same sentences, never went past 1.16x her median and never babbled.
- **Every sentence carries silence at both ends:** ~0.45 s before and ~0.5 s after for ryan,
  vivian, dylan, uncle_fu (~0.25 / 0.3 s for serena, aiden). In a reply of several sentences that's
  ~1 s of dead air between each, and the lead-in adds to the first audio.
- **Speech rate when it goes right:** Chinese 4.7 characters a second (p10 2.6, p90 5.4); English
  3.1 syllables a second (p10 1.4, p90 4.5).
- **Not the codec's chunks:** a clip's length is its frame count (80 ms each), decided by the
  talker's codes; the chunked decode (1, 1, 2, 4 frames: spikes/03) turns the same frames into
  audio, it can't add or remove time.

## Next
- Hey Pi on a steadier voice (serena, or another after the 1.7B spike).
- Trim the silence at a sentence's ends and cap pauses inside it (speech-service, every voice).
- A tighter length cap per sentence (now 50 + 8 frames a character: 4 s + 0.64 s a character).
- Spike 15: the 1.7B model (fit with 2 + 2, speed, this spike's evenness), and the sampling
  settings (temperature, top-k), in a maintenance window: the GPU can't hold a second engine
  beside the live service.
