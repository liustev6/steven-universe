# Spec: the wake word (小派小派, a microWakeWord model of our own, listening on the Pi)

**Status:** "Hey Pi" **live** on the Pi since 2026-11-18 (cutoff 0.95), the only wake word. **小派小派** in its
spikes: the best so far is spike 04b (2026-11-19), better than "Hey Pi" offline on every false-wake measure;
next, a trial on the Pi beside "Hey Pi". Part of the voice path ([voice-path.md](voice-path.md): V9, 4.12, 4.12b),
which owns what happens after the word (the session, the turn). Related: [playground.md](../playground.md),
[speech-service.md](speech-service.md) (the voices it trains on). Code: the detector in pi-server
([app/voice/wake.py](../../python/services/pi-server/app/voice/wake.py)); training in
[experiments/python/wake-word/](../../experiments/python/wake-word/). Every measurement behind a decision is a
spike: summarized in the appendix, its write-up and raw results in
[experiments/python/wake-word/results/](../../experiments/python/wake-word/results/).

## What it is
A small neural network on the Pi that listens to the mic all the time and says, every 10 ms, how sure it is the
word was just said. Past a cutoff, pi-server starts a voice session (voice-path V9). Audio never leaves the Pi
for it. The model is trained offline from synthetic voices, the owner's recordings and hours of other audio, and
copied to pi-server as two files.

```
the Pi:  speakerphone ─ arecord 16 kHz ─▶ pi-server ─ wake.py: pymicro-wakeword ─ score ≥ cutoff ─▶ a voice session (V9)
                                                  └ models/<word>.tflite + .json (the cutoff)

training (the playground, CT 110; never in production):
  positives  the owner on the Pi's mic (×5) ─┐                       ┌─ microWakeWord (MixedNet), 10,000 steps
             Qwen3-TTS: 255 voices ──────────┤─ augmented (room      │   ~12 min a run, on the CPU
  negatives  near misses (bank, every voice) ┤  echo, EQ, background)┤─▶ <word>.tflite (quantized, streaming)
             the owner's near misses, room   │─ spectrograms ────────┘   + tests on held-out audio
             AliMeeting, DEMAND, MUSAN,  ────┘
             microWakeWord's sets
```

**As it stands** (2026-11-19, the same held-out audio, each model at its own cutoff):

| | "Hey Pi" (live, 0.95) | 小派小派 04b (0.90) |
|---|---|---|
| the owner, the Pi's mic | 99% (clips it trained on) | 92% quiet, 100% with a video playing (clips it never heard) |
| unheard voices, clean / background 5 dB | 73-97% by voice / not measured | 79-96% / 50-81% |
| false wakes: the owner's room (Pi mic) | 6.7 an hour | 0 (in the 10 held-out minutes) |
| … everyday Chinese speech | 35.5 an hour | 3.9 an hour |
| … Mandarin meetings / sounds | 2.0 / 1.7 an hour | 0 / 0 |

## Decisions
| ID | Decision | Why |
|---|---|---|
| W1 | **microWakeWord**, run by `pymicro-wakeword` 2.5.0 in pi-server (`app/voice/wake.py`): a streaming MixedNet, quantized to int8 TFLite (~60 KB); every 10 ms a score, averaged over the last 5; a wake when the average passes the manifest's `probability_cutoff`. | Home Assistant's satellites' engine (Apache-2.0), made for an ESP32: 3.7% of one Pi core. Maintained (2026); openWakeWord's last release is 2024 |
| W2 | **The word is 小派小派** (replacing "Hey Pi"; chosen over 蛋黄派). | Four syllables, a word repeated: distinctive in a Chinese-speaking home. "Hey Pi" is two syllables, English, and wakes to everyday Chinese (35/h) and to "Hey" + any *pai*-like sound (spike 00) |
| W3 | **Positives: synthetic voices plus the owner, on the Pi's own mic.** Qwen3-TTS 1.7B VoiceDesign (150 voices from descriptions: gender, five ages, timbre, eight accents, manner) and Base (105 voices cloned from AISHELL-3 speakers), each saying it in 7 written forms ×2; synthetic takes that speech-to-text doesn't hear as xiao-pai twice are set aside (8-9%). The owner's takes are never filtered and count ×5. | Speaker variety is what lets it wake for people it never heard: 5 voices gave 46%, 204 gave 90-99% (spikes 01, 03). The Pi's speakerphone processes its audio (gating, suppression): the owner recorded on it is what the model hears in use (spike 04: 84% → 92%) |
| W4 | **Negatives: Mandarin and the home.** microWakeWord's sets (English speech, dinner parties, music, sounds), AliMeeting's far-field meetings (36 h, weight 10), DEMAND and MUSAN noise, the owner's short videos (39 min) and room (35 min, the Pi's mic). | English-only negatives left it deaf to unfamiliar voices instead of telling words apart; Mandarin meetings lifted unheard voices from 46% to 83% (spike 02) |
| W5 | **Near misses from every voice**, weight 8 (`phrases/xiao_pai_bank.yaml`): `near_miss_core` (30: 小派, 小胖小胖, 小怕小怕, 小爱小爱, 小度小度, 嘿小派, 蛋黄派 …) and `near_miss_doubled` (25: a doubled syllable near 派, 小快小快, 小盼小盼; a lone 小派 in a phrase, 小派小心, 我们家小派; "Hey Pi Hey Pi"); the owner's read on the Pi ×5. **No tone-only variants** (小拍小拍, 小牌小牌). | What still woke it were these shapes (spike 03); from every voice they halved near-miss wakes (spike 04b). The owner's 小派小派 is often heard as 小拍, a tone apart: training that as "not it" would fight recall |
| W6 | **Background mixed under training clips: never a source that is also a negative, and never louder than the voice.** The pool: AudioSet, FMA music, DEMAND, MUSAN noise; the owner's videos are negatives only. How often (75% or 30%) and the loudness range (0 to +15 dB) are being decided (spikes 04d, 04e). | The owner's videos under the wake word taught spike 04 that video speech carries the word: 18 false wakes an hour in the room, all videos; without them, 0 (04b). Mixing is still needed: at 5% (04c) unheard voices in loud background fell 15-25 points |
| W7 | **Tested on what it never trained on**, as pi-server runs it: every 5th synthetic voice held out (51), every 4th of the owner's Pi clips, the last quarter of the room; recall at cutoffs 0.80-0.99, clean and with background at 10 and 5 dB; false wakes per hour with each set played as one stream (meetings 2 h, everyday Chinese, the room, short videos, sounds). | A model's own validation set is augmented like its training (spike 04c's looked best, 94%, and wasn't); only unheard speakers and real streams say how it does in a home |
| W8 | **Models are compared at their own cutoff**, at equal false-wake levels, and deployed at that cutoff (in its `.json`). | The cutoff is a dial: 04b at 0.90 wakes as often as 03 at 0.97, with fewer false wakes. Same-number comparisons misjudge |
| W9 | **Training runs on the playground (CT 110), on its CPU** (`train-run.sh`); only generating voices takes the GPU, with speech-service stopped by hand (playground.md). `GPU=1` trains on the card (TensorFlow with `tensorflow[and-cuda]`; the RTX 5060's sm_120 runs JIT-compiled PTX). | The model is tiny: 10,000 steps took 5.8 min on the GPU and about as long on the CPU; half a run is the features (CPU). So production stays up while it trains |
| W10 | **Recordings on the Pi's mic through a page** (`recording/pi.html`, on the Mac): it stops pi-server (which holds the mic), records over SSH between a beep and two beeps, copies each take to the Mac; `recording/label.py` splits, transcribes and labels every clip (near misses by reading order). | The same mic and processing as in use; every clip labelled, also for a TTS of the owner's voice later |
| W11 | **Data stays out of git**, on the Mac and the playground (`/srv/experiments/wake-word`), both kept in step; the code, configs and results are in git. | Voice recordings are personal; generated audio is GBs |

## The model in pi-server
`PI_SERVER_WAKE_WORD` names a manifest (`models/hey_pi.json`); the manifest names the `.tflite` and the cutoff.
Changing the word is two files and the setting; a deploy restarts pi-server. Two models can't run at once
yet: a trial of 小派小派 beside "Hey Pi" needs `wake.py` to take a list.

## Operating it
- **Record** (pi-server stopped by the page, restarted after): `recording/server.py` on the Mac, then
  `http://localhost:8770/pi`: the wake word (quiet; over a video), the near misses, the room, someone else.
  Then `recording/label.py` (speech-service's venv, its test client running) and
  `spikes/04-pi-mic/sort_recordings.py`; copy the sets to the playground.
- **Generate voices** (a GPU window: `ssh speech-host docker stop speech-service-speech-service-1`, then
  `docker start` and the health check after): `spikes/03-many-voices/gen_voices.py --source design|clone`
  in speech-service's base image on CT 110.
- **Train and test:** on CT 110, `train-run.sh <run> config-xiao-pai-<run>.yaml spikes/03-many-voices/evaluate.py
  spikes/04-pi-mic/test_pi.py` in tmux; the logs live at `http://192.168.50.119:8080`; the model in
  `/srv/experiments/wake-word/out/models/`, copied to the Mac's `out/`.
- **Deploy:** the `.tflite` and `.json` into `python/services/pi-server/models/`, the setting, push.

## Not chosen
| Option | Why not |
|---|---|
| Keeping "Hey Pi" | 6.7 false wakes an hour in the owner's room, 35 in everyday Chinese (spike 00) |
| 蛋黄派 | a real food word: said at home, and in videos |
| Piper's voices for 小派小派 | English voices; "Hey Pi"'s 904 speakers can't say Chinese |
| Only our TTS's 9 preset voices | 5 usable (the Japanese and Korean voices can't say it): 46% of unheard voices (spike 01) |
| The owner's videos as background under the wake word | taught it that video speech carries the word (spike 04: 18 false wakes/h) |
| Background under only 5% of clips | unheard voices in loud background 15-25 points lower (spike 04c) |
| Training on the GPU by default | no faster for this model; it would stop speech-service for nothing |
| Colab | its notebook hung at the installs (2026-11-18); our own hardware is as fast |

## Appendix: the spikes
Every measurement behind a decision. Write-ups and raw results:
[experiments/python/wake-word/results/](../../experiments/python/wake-word/results/). "Unheard voices" are voices
the model never trained on; a cutoff's column is the share woken (recall) or that wakes it (near misses), or
false wakes per hour of a stream. Spikes 00-02 trained on the Mac, 03 on and after on the playground.

### 00: "Hey Pi" (2026-11-18)
171 of the owner's clips on the Pi's mic (×5), 2,000 Piper clips (904 speakers), 510 from our TTS, 1,530 near
misses; 7.7 min on the Mac. Its own held-out test: misses 21% at 1.1 false wakes/h (0.90), **24% at 0.4/h (0.95,
deployed)**; the owner's clips 99%; Piper and TTS voices 73-97%; "Hey pal", "Hey, hi" still wake it ~50%.
Measured on 2026-11-19 against spike 04's streams:

| False wakes per hour | 0.90 | 0.95 | 0.97 |
|---|---|---|---|
| the owner's room, the Pi's mic (45 min) | 8.0 | 6.7 | 6.7 |
| everyday Chinese, unheard voices (30 min) | 49.3 | 35.5 | 25.7 |
| Mandarin meetings (2 h) | 3.0 | 2.0 | 1.0 |
| AudioSet sounds (35 min) / short videos (15 min) | 1.7 / 0 | 1.7 / 0 | 1.7 / 0 |

### 01: 小派小派 from our TTS alone (2026-11-18)
7 of speech-service's 9 voices, 40 takes each of 7 written forms; 31% set aside (the Japanese and Korean voices
can't say it): 1,345 from 5 voices; two voices held out. Negatives: microWakeWord's (English) and 1,190 of ours.

| Unheard voices (128) | 0.80 | 0.90 | 0.95 | 0.97 | 0.99 |
|---|---|---|---|---|---|
| clean | 56% | 51% | 46% | 45% | 41% |
| background 5 dB | 48% | 41% | 37% | 33% | 29% |

Near misses 2%. No false wakes in 7 min of Chinese or the owner's "Hey Pi"s. Decided: the word works; speakers
are the bottleneck.

### 02: + Mandarin meetings and household noise (2026-11-18)
AliMeeting (36 h of far-field meetings, weight 10), DEMAND and MUSAN as negatives and background; 2 h of
AliMeeting's test meetings as a stream.

| | 0.80 | 0.90 | 0.95 | 0.97 | 0.99 |
|---|---|---|---|---|---|
| unheard voices, clean: 01 / **02** | 56 / **90%** | 51 / **86%** | 46 / **83%** | 45 / **78%** | 41 / **75%** |
| … background 5 dB: 01 / **02** | 48 / **68%** | 41 / **64%** | 37 / **58%** | 33 / **54%** | 29 / **50%** |
| false wakes/h, Mandarin meetings (2 h) | 2.5 | 2.0 | 2.0 | 1.0 | 0.5 |
| near misses: all / 小爱小爱 / 小胖小胖 | 9 / 50 / 67% | 5 / 50 / 33% | 5 / 50 / 33% | 3 / 33 / 17% | 2 / 17 / 0% |

Decided: Mandarin negatives (W4).

### 03: many voices, the owner's voice, short videos (2026-11-19)
255 voices (150 designed, 105 cloned), each saying the word, the 30 core near misses and 12 everyday lines
(14,280 clips, 3.5 h, 1.5 h of GPU); 51 held out. The owner's 31 wake words and 230 other lines (Mac mic), 39
min of short videos. 4,124 positives, ~10,000 near misses; 12 min on CT 110.

| | 0.90 | 0.95 | 0.97 | 0.99 |
|---|---|---|---|---|
| unheard voices: spike 01's two / designed / cloned | 94 / 92 / 99% | 92 / 90 / 99% | 85 / 87 / 97% | 70 / 74 / 88% |
| … background 5 dB | 73 / 76 / 92% | 65 / 66 / 88% | 60 / 59 / 83% | 41 / 43 / 71% |
| near misses (1,530) / 小怕小怕 / 小潘小潘 | 15 / 69 / 65% | 11 / 61 / 57% | 9 / 53 / 45% | 5 / 39 / 27% |
| false wakes/h: short videos (held out) / meetings | 3.9 / 1.5 | 0 / 1.0 | 0 / 1.0 | 0 / 0.5 |
| … everyday Chinese / the owner's "Hey Pi"s back to back | 17.8 / 241.7 | 13.8 / 177.3 | 5.9 / 112.8 | 0 / 32.2 |

Decided: synthetic voices (W3). Weak: doubled syllables near 派, a lone 小派.

### 04: the Pi's mic, doubled near misses, how much background (2026-11-19)
The owner on the Pi's mic: 100 wake words (quiet), 86 over a video, 62 near misses, 45 min of the room (a
quarter of each held out). Every voice said 25 doubled and lone-小派 lines (6,375 clips, 35 min of GPU). Runs, one
change each: 04 (+ the owner's videos as background), 04b (without), 04c (background under 5% of clips, not
75%), 04d (the voice never quieter than the background), 04e (30%, the voice louder).

Spike 03's model on every Pi take first: the owner 83% (quiet) and 95% (video) at 0.97; weakest when speaking
in a mood (60%) or while doing something (70%); 1.3 false wakes/h in the room (the owner saying "我叫他小派").

| Each at 0.90 / 0.97 | 03 | 04 | **04b** | 04c |
|---|---|---|---|---|
| the owner (Pi), quiet / video | 92, 100 / 84, 95% | 96, 100 / 92, 100% | **92, 100 / 80, 95%** | 88, 95 / 72, 95% |
| the owner's near misses (Pi) | 7 / 7% | 33 / 27% | **13 / 13%** | 0 / 0% |
| the room, held-out 10 min (per hour) | 0 / 0 | 49.1 / 18.4 | **0 / 0** | 0 / 0 |
| unheard voices: spike 01's two / designed / cloned | 94, 92, 99 / 85, 87, 97% | 89, 94, 95 / 77, 85, 85% | **84, 79, 96 / 56, 55, 87%** | 84, 80, 95 / 65, 62, 89% |
| … background 5 dB | 73, 76, 92 / 60, 59, 83% | 70, 75, 85 / 48, 54, 68% | **56, 50, 81 / 30, 28, 58%** | 41, 30, 58 / 29, 14, 34% |
| near misses: core / doubled | 15, 38 / 9, 25% | 24, 36 / 11, 23% | **10, 20 / 4, 11%** | 7, 15 / 3, 7% |
| false wakes/h: everyday Chinese / meetings | 17.8, 1.5 / 5.9, 1.0 | 43.4, 7.0 / 19.7, 0.5 | **3.9, 0 / 0, 0** | 2.0, 0 / 0, 0 |
| … the owner's "Hey Pi"s back to back | 241.7 / 112.8 | 161.2 / 16.1 | **0 / 0** | 32.2 / 0 |

Spike 04's room wakes, transcribed: 7 of 8 short videos playing quietly, 1 a quiet "嗯". Decided: W5, W6 (the
videos), W8, W9 (the GPU, 5.8 min for the steps either way). 04d and 04e: running.
