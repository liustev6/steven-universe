# Spike 04: 小派小派 on the Pi's own mic, the doubled near misses, and how much background to mix (2026-11-19)

**Question:** spike 03 woke for 90-99% of unheard voices, but its weak spots were near misses with a doubled
syllable close to 派 (小怕小怕, 小潘小潘) or a lone 小派, and the owner's voice was measured on 7 clips from the Mac's
mic. With the owner recorded on the Pi's own mic (wake words, near misses, 45 min of the room) and every
synthetic voice saying 25 more doubled and lone-小派 lines, how much better does it get?

**Answer (04, 04b, 04c; 04d and 04e running):** the doubled lines and the Pi-mic recordings help, but the run
that added them (04) was **worse**: it woke 18 times an hour in the owner's room, every time to a short video
playing quietly. The cause was one change: the owner's short videos mixed *under* the wake word as background,
the same clips that were also negatives. Without it (**04b**): 0 false wakes in the held-out room, everyday
Chinese and meetings; near misses halved (doubled 25% → 11%, core 9% → 4% at 0.97); the owner's held-out Pi
wake words 92% (quiet) and 100% (a video playing) at **cutoff 0.90**, where it wakes for unheard voices about as
often as spike 03 did at 0.97. Its cost: unheard voices in loud background (5 dB) 50-56% at 0.90, against 60-83%
for spike 03 at 0.97. Mixing background under only 5% of clips (04c) cuts that further (30-58%): the synthetic
voices learn 小派小派 over noise from the mixing.

**The common test:** `test-xiao_pai_04*.txt` here is this spike's model in evaluate.py, the same test every model takes (the spec's numbers). The tables below are from the spike's own test at the time.

## Setup
- **Recordings on the Pi's mic** (`recording/pi.html`: the recording server drives the Pi over SSH, pi-server
  stopped meanwhile; `recording/label.py` splits and transcribes; `sort_recordings.py` sorts):

  | Take | Clips | Training | Held out |
  |---|---|---|---|
  | 1. 小派小派, a quiet room: 1 m, close, across the room, calling, from the side, fast and slow, moods, while doing something | 100 | 75 | 25 (every 4th) |
  | 2. 小派小派, a short video playing | 94; 8 left out (heard as "还有", "I would have": the video through the mic's gate) | 65 | 21 |
  | 3. The 30 core near misses, each twice (labelled by reading order: speech-to-text hears 派派 as 太太) | 62 | 47 | 15 |
  | 4. The room: talk, TV, short videos (no wake word) | 45 min, 9 files | 35 min, cut to 700 × 3 s | the last 10 min |

  The Pi's speakerphone gates its mic: between words the audio is digital silence even with a video playing,
  so each word splits cleanly at -50 dBFS. Speech-to-text heard the owner's 小派小派 as 小派 63 times in 100, the rest
  as 小胖, 小潘, 小拍 (a tone apart), mostly from across the room: all kept as positives.
- **Doubled near misses** (the bank's `near_miss_doubled`, 25 lines: 小拜小拜, 小盼小盼, 小快小快, 小开小开, 小佩小佩 …,
  小派小心, 我们家小派, 喂小派, 小派在吗 …, "Hey Pi Hey Pi"): every spike 03 voice said each once
  (`gen_voices.py --lines doubled`, Qwen3-TTS 1.7B VoiceDesign and Base on the playground's GPU, speech-service
  stopped by hand): 6,375 clips in 35 min (150 designed voices 21 min, 105 cloned 14 min); 5,100 to training,
  the 51 held-out voices' 1,275 to test. Tone-only variants (小拍小拍, 小牌小牌) left out on purpose: that is how
  the owner's own 小派小派 is often heard.
- **Training** (`train-run.sh` on CT 110; `config-xiao-pai-04*.yaml`): spike 03's data plus the above; the owner's
  positives and near misses ×5 (`own_voice_copies`, `own_voice_negative_copies`), near-miss weight 8 (was 5),
  the room as an extra negative (weight 8). 12 min a run. **On the GPU** (04, `GPU=1`: TensorFlow 2.21 with
  `tensorflow[and-cuda]`, its libraries on `LD_LIBRARY_PATH`; the RTX 5060's sm_120 runs its compute_90 PTX,
  JIT-compiled) the 10,000 steps took 5.8 min, as on the CPU: the model is tiny and the features (CPU) are
  half the run. So the rest ran on the CPU, speech-service up.
- **The runs:** one change each.

  | Run | Background mixed under a clip | Background loudness vs the clip (nominal) | Videos as background |
  |---|---|---|---|
  | 04 | 75% | -5 to +10 dB | **yes** |
  | 04b | 75% | -5 to +10 dB | no |
  | 04c | 5% | -5 to +10 dB | no |
  | 04d | 75% | 0 to +15 dB | no |
  | 04e | 30% | 0 to +15 dB | no |

  "Nominal": microWakeWord pads each clip to 3.2 s before mixing, and audiomentations scales the background to
  the padded clip's loudness, so during a ~1 s word the voice is ~5 dB louder than the setting.
- **Tests:** spike 03's `evaluate.py`, and `test_pi.py`: the owner's held-out Pi clips, the 51 held-out voices'
  doubled near misses, the room's held-out 10 min as one stream.

## Results

**Before training: spike 03's model on every Pi take** (none of it trained on), share woken / waking:

| | 0.90 | 0.95 | 0.97 | 0.99 |
|---|---|---|---|---|
| the owner's wake word, quiet (100) | 91% | 86% | 83% | 66% |
| … a video playing (86) | 99% | 98% | 95% | 88% |
| the owner's near misses (62) | 15% | 13% | 8% | 5% |
| the room, 45 min (false wakes per hour) | 4.0 | 1.3 | 1.3 | 0 |

Its misses by section (0.97): calling out 100%, 1 m 93%, the side 90%, across the room 87%, close and quiet
80%, fast/slow 80%, while doing something 70%, moods 60%. Near misses that woke it: 小怕小怕 (1.00), 小小派,
小胖小胖, 小爱小爱, 小派派, 叫小派. The room's one wake at 0.97: the owner saying "我叫他小派" (a lone 小派).

**Each model at 0.90 and 0.97**, on the same held-out sets (share woken; false wakes per hour):

| | 03 | 04 | **04b** | 04c |
|---|---|---|---|---|
| **The owner, the Pi's mic** | | | | |
| wake word, quiet (25) | 92 / 84% | 96 / 92% | **92 / 80%** | 88 / 72% |
| wake word, a video playing (21) | 100 / 95% | 100 / 100% | **100 / 95%** | 95 / 95% |
| near misses (15) | 7 / 7% | 33 / 27% | **13 / 13%** | 0 / 0% |
| the room, 10 min held out | 0 / 0 | 49.1 / 18.4 | **0 / 0** | 0 / 0 |
| **Unheard voices** | | | | |
| spike 01's two, clean | 94 / 85% | 89 / 77% | **84 / 56%** | 84 / 65% |
| … background 5 dB | 73 / 60% | 70 / 48% | **56 / 30%** | 41 / 29% |
| 51 designed, clean | 92 / 87% | 94 / 85% | **79 / 55%** | 80 / 62% |
| … background 5 dB | 76 / 59% | 75 / 54% | **50 / 28%** | 30 / 14% |
| 51 cloned, clean | 99 / 97% | 95 / 85% | **96 / 87%** | 95 / 89% |
| … background 5 dB | 92 / 83% | 85 / 68% | **81 / 58%** | 58 / 34% |
| **Near misses, unheard voices** | | | | |
| core (1,530) | 15 / 9% | 24 / 11% | **10 / 4%** | 7 / 3% |
| doubled (1,275) | 38 / 25% | 36 / 23% | **20 / 11%** | 15 / 7% |
| **False wakes per hour** | | | | |
| everyday Chinese, unheard voices (30 min) | 17.8 / 5.9 | 43.4 / 19.7 | **3.9 / 0** | 2.0 / 0 |
| Mandarin meetings (2 h) | 1.5 / 1.0 | 7.0 / 0.5 | **0 / 0** | 0 / 0 |
| the owner's short videos, held out (15 min) | 3.9 / 0 | 3.9 / 0 | **0 / 0** | 0 / 0 |
| AudioSet sounds (35 min) | 1.7 / 1.7 | 6.9 / 0 | **0 / 0** | 0 / 0 |
| the owner's "Hey Pi"s back to back (4 min) | 241.7 / 112.8 | 161.2 / 16.1 | **0 / 0** | 32.2 / 0 |

microWakeWord's own validation (recall with no false wakes on its ambient set): 03 81%, 04 72%, 04b 78%, 04c
94% (04c's validation clips are mixed as rarely as its training: an easier test, not comparable).

**Spike 04's room wakes** (18.4 an hour at 0.97), each transcribed: 7 of 8 were short videos playing quietly
(-38 to -43 dBFS: "最厉害的是什么？小鱼儿的边线", "十四分钟对位落后一个点" …), the 8th a quiet "嗯".

**Against "Hey Pi"** (production, deployed at 0.95), the same streams, false wakes per hour:

| | Hey Pi at 0.95 | **小派小派 04b at 0.90** | 小派小派 03 at 0.97 |
|---|---|---|---|
| the owner's room, the Pi's mic (45 min) | **6.7** (5 in 45 min) | 1.3 (35 of the 45 min trained on; 0 in the 10 held out) | 1.3 |
| the owner's short videos (15 min) | 0 | 0 | 0 |
| Mandarin meetings (2 h) | 2.0 | 0 | 1.0 |
| everyday Chinese, unheard voices (30 min) | **35.5** | 3.9 | 5.9 |
| AudioSet sounds (35 min) | 1.7 | 0 | 1.7 |

## What it says
- **Never mix a source under the positives that is also a negative**, and never with the background as loud as
  the voice: the owner's videos under the wake word taught spike 04 that video speech can carry it, the
  opposite of what the same videos taught as negatives. The owner's own wake words over a real video (take 2)
  are the honest version of that data.
- **The doubled and lone-小派 lines, and the owner's near misses, work:** with the videos out, every near-miss
  and false-wake measure is the best so far, and back-to-back "Hey Pi"s no longer wake it.
- **A model is judged at its own cutoff.** 04b is more cautious than 03 at the same number: at 0.90 it wakes for
  unheard voices about as often as 03 at 0.97, for fewer false wakes. Comparisons use equal false-wake levels.
- **Background mixing is what lets the synthetic voices (87% of positives) say it over noise:** at 5% (04c), clean
  recall is unchanged but recall in loud background falls 15-25 points; the owner's real recordings only cover
  the owner. 04d (the voice never quieter than the background) and 04e (30%) look for the balance.
- **The GPU doesn't speed up this training**; only generating voices needs it (and speech-service stopped).

**Next:** 04d and 04e's results; the chosen model on the Pi beside "Hey Pi" at its cutoff (~0.90 for 04b) for a
day of real use.

Files: `test-xiao_pai_04*.txt` here (each run in the common test); the code in
[spikes/04-pi-mic/](../../spikes/04-pi-mic/)
(`sort_recordings.py`, `sort_voices.py`, `test_pi.py`), `recording/pi.html`, `recording/label.py`; the configs
`config-xiao-pai-04*.yaml`; the models on the playground and the Mac (`out/xiao_pai_04*.{tflite,json}`); the data
in `data/xiao_pai/` and `data/xiao_pai_04/` (not in git).
