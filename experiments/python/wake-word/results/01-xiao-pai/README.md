# Spike 01: 小派小派 from our TTS alone (2026-11-18)

**Question:** can a 小派小派 wake word be trained from speech-service's own voices (no Piper: its voices are
English; no recordings yet), with microWakeWord's existing negative sets, and how well does it do?

**Answer:** it almost never wakes by mistake, but it wakes for only about half of voices it has never heard
(46% at cutoff 0.95; 68% and 23% for the two held-out voices). Five usable TTS voices aren't enough speakers
to generalise. The word itself looks like a good choice; the next step is speaker variety (your own
recordings, and many more synthetic speakers), then Mandarin and household negatives (spike 02).

## Setup
- **Positives:** 小派小派 in 7 written forms (小派小派, 小派，小派。, with ！？。), from 7 of the 9 voices, 40 sampled
  takes each: 1,960. **Two voices held out** (serena, uncle_fu): never in training, used only to test.
- **Cleanup** (`check_clips.py`): each take through speech-to-text; one that doesn't say xiao-pai twice (any tones)
  set aside. **615 of 1,960 (31%) rejected**: the Japanese (ono_anna) and Korean (sohee) voices can't say it
  (97-98% heard as 小巴, "shoppe"); the others 0-11% (mostly 小胖, 小潘: the nasal ending slurred). **1,345 kept**,
  in effect from 5 voices: ryan, aiden, dylan, eric, vivian.
- **Negatives:** microWakeWord's sets (~550 h unique: English read speech, English dinner parties, music, sounds,
  noise), plus ours from the same 7 voices: 22 near misses ×5 (770: 小派, 小白小白, 小度小度, 小爱小爱, 小胖小胖,
  蛋黄派, 苹果派, 派对, …) and 60 everyday Chinese sentences (420).
- **Training:** `train.py config-xiao-pai.yaml` (MixedNet, 10,000 steps, as "Hey Pi"): 8 min on the Mac.
- **Tests** (`evaluate.py`): the model as pi-server runs it (pymicro-wakeword, streaming, 5-frame average), on
  audio it never trained on.

## Results

microWakeWord's own test (unheard takes of the training voices, augmented; 5.3 h of English dinner parties):

| Cutoff | Misses | False wakes per hour |
|---|---|---|
| 0.91 | 36% | 0 |
| 0.76 | 27% | 0.4 |
| 0.36 | 17% | 0.6 |

("Hey Pi", trained with Piper's 904 speakers: 21% missed at 1.1 false wakes/h at 0.90.)

**Recall, the held-out voices** (128 takes), share woken by cutoff:

| | 0.80 | 0.90 | 0.95 | 0.97 | 0.99 |
|---|---|---|---|---|---|
| clean | 56% | 51% | 46% | 45% | 41% |
| + background at 10 dB | 54% | 48% | 43% | 41% | 30% |
| + background at 5 dB | 48% | 41% | 37% | 33% | 29% |
| clean, serena | 74% | 73% | 68% | 67% | 65% |
| clean, uncle_fu | 37% | 27% | 23% | 21% | 16% |

**Near misses, the held-out voices** (132 takes): 2% wake it at 0.80-0.97, 1% at 0.99. The worst: 小爱小爱
(2 of 6 at 0.95, 0 at 0.99) and a single 小派 (1 of 6 at every cutoff).

**False wakes per hour**, each set played as one stream:

| | 0.80 | 0.90 | 0.95 | 0.97 | 0.99 |
|---|---|---|---|---|---|
| everyday Chinese, held-out voices (7 min) | 0 | 0 | 0 | 0 | 0 |
| your "Hey Pi" recordings, the Pi's mic (4 min) | 0 | 0 | 0 | 0 | 0 |
| AudioSet sounds (35 min) | 1.7 | 1.7 | 0 | 0 | 0 |

The false-wake sets are short (minutes, not hours): "0" means none seen, not a rate.

## What it says
- **The word works against false wakes:** near misses 2%, no Chinese speech or your voice saying something else
  woke it. A four-syllable, repeated word is as distinctive as expected.
- **Speakers are the bottleneck:** 5 voices don't generalise (uncle_fu, an older male voice, 23%). Your own
  voice will do much better once recorded ("Hey Pi" woke for 99% of yours), but guests need variety: a TTS that
  clones voices from short samples (Qwen3-TTS's Base models, CosyVoice) can say it in hundreds of speakers' voices
  (AISHELL-3: 218 Mandarin speakers).
- **Next:** spike 02 adds Mandarin conversation (AliMeeting) and household noise (DEMAND, MUSAN), with a
  Mandarin false-wake test of hours, not minutes; then speaker variety and your recordings.

Files: `results.txt` here (the evaluation's output); the code in [spikes/01-xiao-pai/](../../spikes/01-xiao-pai/)
(`check_clips.py`, `evaluate.py`); the data in
`data/xiao_pai/` (not in git), the model `out/xiao_pai.{tflite,json}`.
