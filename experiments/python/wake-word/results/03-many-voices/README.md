# Spike 03: 小派小派 from many voices, the owner's, and short videos (2026-11-19)

**Question:** spike 02 woke for 83% of unheard voices (two TTS voices). With hundreds of synthetic voices
(designed and cloned), the owner's own recordings, and the short videos that set off "Hey Pi", how close is
小派小派 to something to try on the Pi?

**Answer:** much closer. Unheard voices: 90-99% at cutoff 0.95 (spike 01's two: 92%, from 83% in spike 02); no
false wakes in 15 held-out minutes of short videos at 0.95 and up, 1 an hour in Mandarin meetings. The weak spot
is near misses that double a close syllable (小怕小怕, 小潘小潘: about half wake it at 0.95) or contain a lone
小派 (嘿小派, 叫小派: ~20%). The owner's 7 held-out wake words: 5 of 7 at 0.95 (too few to judge).

**The common test:** `test-xiao_pai_03.txt` here is this spike's model in evaluate.py, the same test every model takes (the spec's numbers). The tables below are from the spike's own test at the time.

## Setup
- **Voices** (`gen_voices.py`, on the playground's GPU, speech-service stopped for 1.5 h): Qwen3-TTS 1.7B
  **VoiceDesign**, 150 voices from descriptions spread over gender, five ages (children to elderly), timbre, eight
  accents and manner; **Base**, 105 voices cloned from AISHELL-3 speakers' reference clips (`fetch_refs.py`; the
  Hugging Face copy had files for 105 of 218). Each said 小派小派 in 7 written forms ×2, **all 30 core near misses**
  (phrases/xiao_pai_bank.yaml), and 12 everyday lines: 14,280 clips, 3.5 h. Listen: `voices_page.py`.
- **Held out:** every 5th voice of each source (51: 30 designed, 21 cloned), never trained on.
- **Cleanup** (`check_clips.py`): positives through speech-to-text; 8-9% set aside (mostly heard as 小胖), against
  31% in spike 01 (no Japanese or Korean voices now).
- **The owner:** 31 wake-word takes (×5) and 230 other lines (Chinese, English) on the Mac's mic; 7 and 55 held
  out. 39 min of short videos playing near the mic as a negative set; 15 min held out.
- **Training** (`config-xiao-pai-03.yaml`, `train-run.sh` on the playground): spike 02's settings and negatives
  (AliMeeting, DEMAND, MUSAN) plus the above: 4,124 positive clips, ~10,000 near-miss clips. 12 min on CT 110
  (features 5.4 min, 10,000 steps ~6 min; the Mac took 9 + 9.4 for spike 02's smaller set). Best at step 9,500.
- **Tests** (`evaluate.py`): as pi-server runs the model; spike 01's tests plus the new held-out sets.

## Results

**Recall, unheard voices** (share woken):

| | 0.90 | 0.95 | 0.97 | 0.99 |
|---|---|---|---|---|
| serena, uncle_fu: spike 01 / 02 / **03** | 51 / 86 / **94%** | 46 / 83 / **92%** | 45 / 78 / **85%** | 41 / 75 / **70%** |
| … + background 5 dB: 01 / 02 / **03** | 41 / 64 / **73%** | 37 / 58 / **65%** | 33 / 54 / **60%** | 29 / 50 / **41%** |
| 51 new voices, designed (355 takes) | 92% | 90% | 87% | 74% |
| 51 new voices, cloned (293 takes) | 99% | 99% | 97% | 88% |
| … designed / cloned + background 5 dB | 76 / 92% | 66 / 88% | 59 / 83% | 43 / 71% |
| the owner, 7 held-out takes | 100% | 71% | 71% | 43% |

**Near misses and other speech** (share that wakes it):

| | 0.90 | 0.95 | 0.97 | 0.99 |
|---|---|---|---|---|
| the 51 voices' 30 core near misses (1,530) | 15% | 11% | 9% | 5% |
| … 小怕小怕 / 小潘小潘 | 69 / 65% | 61 / 57% | 53 / 45% | 39 / 27% |
| … 小爱小爱 / 小白小白 | 45 / 37% | 27 / 27% | 27 / 24% | 10 / 14% |
| … 小派派 / 叫小派 / 嘿小派 / 小小派 | 35 / 31 / 29 / 25% | 24 / 22 / 18 / 16% | 20 / 14 / 16 / 12% | 12 / 10 / 12 / 6% |
| spike 01's near misses: 01 / 02 / **03** | 2 / 5 / **5%** | 2 / 5 / **3%** | 2 / 3 / **3%** | 1 / 2 / **2%** |
| the owner's own other lines (55) | 7% | 4% | 0% | 0% |

**False wakes per hour**, each set as one stream:

| | 0.90 | 0.95 | 0.97 | 0.99 |
|---|---|---|---|---|
| **the owner's short videos, held out (15 min)** | 3.9 | **0** | **0** | **0** |
| Mandarin meetings, 2 h: spike 02 / **03** | 2.0 / **1.5** | 2.0 / **1.0** | 1.0 / **1.0** | 0.5 / **0.5** |
| everyday Chinese, unheard voices (30 min) | 17.8 | 13.8 | 5.9 | 0 |
| AudioSet sounds (35 min) | 1.7 | 1.7 | 1.7 | 0 |

The owner's "Hey Pi" recordings (171, the Pi's mic): **none wakes it on its own** at any cutoff; played back to back
(0.5 s apart) they did (177 an hour at 0.95): "…Pi. Hey Pi…" doubles a *pai*, as 小派小派 does. The everyday-Chinese
stream's wakes come the same way, where one sentence runs into the next.

## What it says
- **Speaker variety was the bottleneck spike 01 found, and it's largely fixed:** 204 training voices take unheard
  voices from 46% (spike 01) and 83% (spike 02) to 90-99%. Cloned voices (real speakers' timbres) score higher than
  designed ones (children and the elderly are the hardest); loud background still costs 20-30 points.
- **The short videos that set off "Hey Pi" don't set off 小派小派** at 0.95 and up (15 unseen minutes).
- **The doubled pattern is the weakness:** what wakes it is a repeated syllable close to *pai* (小怕小怕, 小潘小潘,
  back-to-back "Hey Pi"s) or a lone 小派 with a syllable around it. These are in the training negatives (every voice
  said them), yet still close: they need more weight, and more voices saying them.
- **The owner's voice is under-measured:** 7 held-out takes (5 woke it at 0.95), recorded on the Mac's mic. The Pi's
  mic, at distance, is the test that matters.
- **Cutoff 0.97** looks like the balance: 85-97% of unheard voices, no video wakes, 1 meeting wake an hour.

**Next:** near misses weighted higher and said by every voice in more forms (doubled 怕/潘/拍/白, lone 小派 in
phrases); the owner's wake word recorded on the Pi; then a trial on the Pi beside "Hey Pi" at 0.97.

Files: `test-xiao_pai_03.txt` here (the common test); the code in
[spikes/03-many-voices/](../../spikes/03-many-voices/) (`gen_voices.py`, `fetch_refs.py`, `voices_page.py`, `evaluate.py`); the model on the playground (`/srv/experiments/wake-word/out/models/`).
