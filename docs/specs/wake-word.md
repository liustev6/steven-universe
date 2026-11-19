# Spec: the wake word (小派小派, a microWakeWord model of our own, listening on the Pi)

**Status: live.** **小派小派** has been the Pi's wake word since 2026-11-19, replacing "Hey Pi" (2026-11-18): model 04b at
0.91 first, then **04f at 0.96** the same day, after 04b woke on the owner's casual conversation. The earlier models stay
in pi-server for a switch back. Part of the voice path ([voice-path.md](voice-path.md):
V9, 4.12, 4.12b), which owns what happens after the word (the session, the turn). Related: [playground.md](../playground.md),
[speech-service.md](speech-service.md) (the voices it trains on). Code: the detector in pi-server
([app/voice/wake.py](../../python/services/pi-server/app/voice/wake.py)); training and testing in
[experiments/python/wake-word/](../../experiments/python/wake-word/). Every measurement behind a decision is a spike:
summarized in the appendix, its write-up and its model's table in the common test in
[experiments/python/wake-word/results/](../../experiments/python/wake-word/results/).

## What it is
A small neural network on the Pi that listens to the mic all the time and says, every 10 ms, how sure it is the word
was just said. Past a cutoff, pi-server starts a voice session (voice-path V9). Audio never leaves the Pi for it. The
model is trained on the playground from synthetic voices, the owner's recordings and hours of other audio, tested on a
fixed test set no model trains on, and copied to pi-server as two files.

```
the Pi:  speakerphone ─ arecord 16 kHz ─▶ pi-server ─ wake.py: pymicro-wakeword ─ score ≥ 0.96 ─▶ a voice session (V9)
                                                  └ models/xiao_pai_04f.tflite + .json (the cutoff)

the playground (CT 110), data/:
  datasets/full/        as downloaded, recorded, generated ──▶ datasets/processed/recordings (your takes as labelled clips)
  datasets/processed/train/  ──▶ train.py (only what a config lists) ──▶ <model>.tflite
  datasets/processed/test/   ──▶ testset.py ──▶ testset/v1 (fixed) ──▶ evaluate.py ──▶ results/<spike>/test-<model>.txt
```

**As it stands** (2026-11-19, the common test on test set v1):

| | "Hey Pi" (retired, 0.995) | 04b at 0.91 (the first day) | **04f at 0.96 (live)** |
|---|---|---|---|
| false wakes per hour, 2.9 h of test-only audio | 1.71 | 0.34 | **0** |
| the owner's other lines (55) that wake it | – | 9% | **0%** |
| the owner on the Pi's mic: quiet / a video playing | (no held-out clips) | 92 / 100% | 68 / 95% |
| unheard voices: quiet / room noise / a video, TV or meeting / very noisy | 54 / 49 / 22 / 14% | 84 / 82 / 62 / 31% | 74 / 65 / 42 / 15% |
| near misses: core / doubled | 3% (its own) | 8 / 17% | 4 / 9% |
| the other wake word said back to back | 5.2 an hour | 0 | 0 |

04b scored a tenth of the owner's ordinary lines above 0.67 (one at 0.995): it woke on the owner's casual talk. 04f keeps
nine-tenths of them under 0.08 (none above 0.876) and the owner's wake words at 0.99+ three times in four; at 0.96 it
hears 81% of the owner's 53 held-out takes and never wakes on the test's ordinary speech. Its weak spot: the owner
speaking quietly on the Pi (68%), and other voices over loud media.

## Decisions
| ID | Decision | Why |
|---|---|---|
| W1 | **microWakeWord**, run by `pymicro-wakeword` 2.5.0 in pi-server (`app/voice/wake.py`): a streaming MixedNet, quantized to int8 TFLite (62 KB); every 10 ms a score, averaged over the last 5; a wake when the average passes the manifest's `probability_cutoff`. | Home Assistant's satellites' engine (Apache-2.0), made for an ESP32: 3.7% of one Pi core. Maintained (2026); openWakeWord's last release is 2024 |
| W2 | **The word is 小派小派** (replacing "Hey Pi"; chosen over 蛋黄派). | Four syllables, a word repeated: distinctive in a Chinese-speaking home. "Hey Pi" never reaches 0.5 false wakes an hour on the same audio and wakes for 22% of voices over a video (spike 00) |
| W3 | **Positives: synthetic voices plus the owner, on the Pi's own mic.** Qwen3-TTS 1.7B VoiceDesign (150 voices from descriptions) and Base (105 cloned from AISHELL-3 speakers), each saying it in 7 written forms ×2, speech-to-text-checked (8-9% set aside); the owner's takes never filtered, ×5. | Speaker variety: 5 voices woke for 59% of unheard voices in quiet, 204 voices 83-84% (spikes 01, 03). The owner on the Pi's mic: 68% → 92% in quiet (03 → 04b) |
| W4 | **Negatives: Mandarin and the home.** microWakeWord's sets, AliMeeting's train meetings (36 h, weight 10), DEMAND and MUSAN noise, the owner's short videos (39 min) and room (35 min, the Pi's mic). | English-only negatives left it deaf to unfamiliar voices instead of telling words apart (spike 01 → 02) |
| W5 | **Near misses from every voice**, weight 8: the bank's `near_miss_core` (30) and `near_miss_doubled` (25: 小快小快, 小派小心, "Hey Pi Hey Pi" …); the owner's on the Pi ×5. **No tone-only variants** (小拍小拍). | What still woke spike 03; the owner's 小派小派 is often heard as 小拍, a tone apart: training that as "not it" would fight recall |
| W6 | **Background under training clips: 75% of clips, -5 to +10 dB** (nominal: measured over the clip padded to 3.2 s, so ~0 to +15 dB over the word), from AudioSet, FMA music, DEMAND, MUSAN noise. **Never a source that is also a negative** (the owner's videos). | Spike 04 (the owner's videos under the word, also negatives) woke to videos 18 times an hour. Less mixing (5%: 04c; 30%: 04e; 50%: 04f) or a louder voice (0 to +15 dB: 04d) each lost recall over noise or on the owner's quiet takes, or woke for the other word back to back |
| W7 | **The test set is fixed** (`testset.py`, v1, 5,257 clips): rendered once, only from `datasets/processed/test/` (test-only by where it lives). Each synthetic clip in one condition by its name, as a home has them: **40% quiet, 25% room noise** (the owner's held-out room, +15 to +20 dB), **25% a video, TV or meeting** (the owner's held-out videos or AliMeeting's eval meetings, +5 to +10 dB), **10% very noisy** (the same, 0 to +5 dB); the owner's held-out clips as recorded; near misses in the same mix. **Each clip is labelled for every word** (one word's clips are the other's "other wake word"). More test data makes v2, a superset: v1's clips render identically. | Mixing at test time with sound models had trained with flattered every model; a fixed, realistic mix of unheard sound makes models of any date comparable, and the other word's clips test each model for free |
| W8 | **Models are compared at their own cutoff**, with limits first: at most 0.5 false wakes an hour over the test-only streams (2.9 h: the owner's room and videos, AliMeeting test meetings, everyday speech of unheard voices) and **at most 2% of the owner's own ordinary lines**; then **the owner's voice first**, other voices second; near misses of the 小X小X kind are tolerated (an accent can make them the word). **Certainty** beside recall: the peak score each set gets (median, 10th percentile), and how high the owner's ordinary speech scores. | Industry practice (recall at a fixed false-alarm rate); the cutoff is a dial. The owner's own speech is what a panel at home hears most: 04b, chosen on the streams alone, woke on it (2026-11-19) |
| W9 | **Training on the playground's CPU** (`train-run.sh`, ~12 min); only generating voices takes the GPU, speech-service stopped by hand (playground.md). | The model is tiny: 10,000 steps took 5.8 min on the GPU and about as long on the CPU |
| W10 | **The data: `datasets/full/`** (as downloaded, recorded, generated; never edited), **`datasets/processed/`** (`recordings/`: the takes as labelled clips; `train/`: the only data training reads, `train.py` refuses anything else; `test/`: test-only), **`testset/`**, and a folder per spike for its own extra processing. A config lists exactly the sets it trains on. Out of git, the same on the Mac and the playground. | No test clip can reach training; any run can be repeated; voice recordings are personal |
| W11 | **Recordings on the Pi's mic through a page** (`recording/pi.html`): it stops pi-server, records over SSH between beeps, copies each take to the Mac; `recording/label.py` splits, transcribes and labels every clip. | The same mic and processing as in use; every clip labelled, also for a TTS of the owner's voice later |

## The model in pi-server
`PI_SERVER_WAKE_WORD` names a manifest: `models/xiao_pai_04f.json` (the default), whose `probability_cutoff` is 0.96;
`models/xiao_pai_04b.json` (0.91) and `models/hey_pi.json` switch back. A new model: its `.tflite` and `.json` into `models/`, the cutoff from its table's
last column, the setting, `infra/deployment/pi-server.sh`.

## Operating it
- **Record** (pi-server stopped by the page, restarted after): `recording/server.py` on the Mac, then
  `http://localhost:8770/pi`. Then `recording/label.py` and `spikes/04-pi-mic/sort_recordings.py` (every 4th clip, the
  room's last quarter, to `processed/test/`); keep the playground's `data/` in step.
- **Generate voices** (a GPU window: `ssh speech-host docker stop speech-service-speech-service-1`, then `docker start`
  and the health check): `spikes/03-many-voices/gen_voices.py` in speech-service's base image on CT 110.
- **Train:** `./train-run.sh <run> config-<…>.yaml` on CT 110, in tmux (logs at `http://192.168.50.119:8080`); it ends
  with the common test. Models in `/srv/experiments/wake-word/out/models/`, copied to the Mac's `out/`.
- **Test any models:** `python evaluate.py <data root> out/<model>.json … --out results`.

## Not chosen
| Option | Why not |
|---|---|
| Keeping "Hey Pi" | never under 0.5 false wakes an hour; 22% of voices over a video (spike 00) |
| 蛋黄派 | a real food word: said at home, and in videos |
| Piper's voices for 小派小派 | English voices; they can't say Chinese |
| Only our TTS's 9 preset voices | 5 usable: 59% of unheard voices in quiet, 8% of the owner (spike 01) |
| The owner's videos as background under the word | they were negatives too: it woke to videos (spike 04) |
| Background under 5%, 30% or 50% of clips; the voice never quieter than it | recall over noise or on the owner's quiet takes fell, or the other word back to back woke it (04c-04f) |
| Testing with background mixed at test time | the sound was in training's background pool: flattering; replaced by the fixed test set (W7) |
| Training on the GPU by default | no faster for this model; it would stop speech-service for nothing |
| Colab | its notebook hung at the installs (2026-11-18); our own hardware is as fast |

## Appendix: the spikes
Every model in the common test (`evaluate.py` on test set v1): its table is
`experiments/python/wake-word/results/<spike>/test-<model>.txt`, the spike's write-up beside it. Each model below at
its cutoff for at most 0.5 false wakes an hour over the 2.9 h of test-only streams ("never": it doesn't get there;
0.995 shown). Recall is the share of its word's clips that wake it; "media" is a video, TV or meeting; near misses are
the share of near-miss clips that wake it; certainty is the peak score of unheard voices in quiet (median / 10th
percentile); the margin is unheard voices' 10th percentile minus near misses' 90th (above 0: no overlap).

| Spike | Cutoff | False wakes/h | The owner, Pi: quiet / video | Unheard: quiet / room / media / very noisy | Near: core / doubled / the owner's | Other word back to back /h | Certainty | Margin |
|---|---|---|---|---|---|---|---|---|
| 00 "Hey Pi" | never (0.995) | 1.71 | – | 54 / 49 / 22 / 14% | 3% (its own 18) | 5.2 | 0.996 / 0.817 | −0.59 |
| 01 | 0.910 | 0.34 | 8 / 24% | 59 / 64 / 42 / 20% | 4 / 10 / 0% | 0 | 0.979 / 0.009 | −0.63 |
| 02 | 0.995 | 0.34 | 52 / 100% | 73 / 68 / 46 / 22% | 8 / 19 / 0% | 0 | 0.996 / 0.797 | −0.77 |
| 03 | 0.985 | 0.34 | 68 / 86% | 83 / 81 / 57 / 20% | 5 / 16 / 0% | 13 | 0.996 / 0.971 | −0.12 |
| 04 | 0.995 | 0 | 84 / 100% | 47 / 44 / 26 / 8% | 1 / 5 / 7% | 0 | 0.995 / 0.940 | −0.13 |
| 04b (first deployed) | 0.910 | 0.34 | **92 / 100%** | **84 / 81 / 59 / 31%** | 7 / 16 / 13% | **0** | 0.985 / 0.789 | −0.33 |
| 04c | 0.800 | 0.34 | 92 / 100% | 91 / 81 / 51 / 26% | 7 / 14 / 7% | 95 | 0.990 / 0.815 | −0.58 |
| 04d | 0.925 | 0 | 68 / 100% | 83 / 76 / 46 / 18% | 4 / 11 / 0% | 13 | 0.984 / 0.848 | −0.41 |
| 04e | 0.945 | 0 | 84 / 90% | 66 / 50 / 28 / 5% | 2 / 5 / 0% | 0 | 0.967 / 0.668 | −0.58 |
| **04f (live at 0.96)** | 0.870 | 0.34 | 84 / 100% | 87 / 79 / 58 / 31% | 6 / 14 / 0% | 8.7 | 0.991 / 0.760 | −0.50 |

No 小派小派 model wakes for any of "Hey Pi"'s 1,421 test clips; "Hey Pi" wakes for 0-5% of 小派小派's (at 0.995).

### 00: "Hey Pi" (2026-11-18)
The owner's 171 takes on the Pi's mic (×5), 2,000 Piper clips (904 speakers), 510 from our TTS's 9 voices, 1,530 near
misses; English negatives only. Live 2026-11-18 at 0.95. In the common test it never gets under 0.5 false wakes an
hour (1.71 at 0.995; 7.5 at its deployed 0.95, mostly everyday Chinese speech), and over a video it hears 22% of unheard
voices. Decided: a longer Chinese word (W2).

### 01: 小派小派 from our TTS alone (2026-11-18)
7 of speech-service's 9 voices (5 usable: the Japanese and Korean voices can't say it), 1,345 clips; microWakeWord's
English negatives. Few false wakes, but 59% of unheard voices in quiet and **8% of the owner**: too few speakers.

### 02: + Mandarin meetings and household noise (2026-11-18)
AliMeeting's train meetings (36 h) as negatives; DEMAND and MUSAN as negatives and background. Unheard voices in
quiet 59% → 73%, the owner 8% → 52% (at its 0.995). Decided: Mandarin negatives (W4).

### 03: many voices, the owner's voice, short videos (2026-11-19)
255 voices (150 designed, 105 cloned; 51 held out), each saying the word, the 30 core near misses and 12 everyday
lines; the owner's 31 wake words and 230 other lines (Mac mic); 39 min of short videos as negatives. The surest of
its word (10th percentile 0.971, margin −0.12), but it needs 0.985 to stay under 0.5 false wakes an hour, where it
hears the owner on the Pi 68% and wakes 13 times an hour to "Hey Pi" back to back. Decided: synthetic voices (W3).

### 04: the Pi's mic, doubled near misses, how much background (2026-11-19)
The owner on the Pi's mic (100 wake words quiet, 94 over a video, 62 near misses, 45 min of the room; a quarter
held out); every voice saying 25 doubled and lone-小派 lines (6,375 clips). Six runs, one change each from 04b:
04 (+ the owner's videos as background, also negatives: woke to videos, needs 0.995), **04b**, 04c (background under
5% of clips), 04d (the voice never quieter: 0 to +15 dB), 04e (30%, the voice louder), 04f (50%, -5 to +15 dB, the
owner's videos at 5% of picks, kept off the negatives). **04b** hears the owner best and the most unheard voices over
noise at 0.91, and was deployed first; but it scores the owner's ordinary speech high (a tenth above 0.67) and woke on
casual conversation, so the same day **04f at 0.96** replaced it: none of the owner's ordinary lines, no stream false
wakes, 81% of the owner's takes. Decided: W5, W6, W8.
