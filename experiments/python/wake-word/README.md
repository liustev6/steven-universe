# wake-word: training "Hey Pi"

Hey Pi's own wake word (docs/specs/voice-path.md, V9 and 4.12b), a microWakeWord model like the
built-in `hey_jarvis` that pi-server runs today (`app/voice/wake.py`, `PI_SERVER_WAKE_WORD`).

| File | What |
|---|---|
| `train.py`, `config.yaml` | **the trainer, on our own hardware** (this Mac first): microWakeWord's notebook as one resumable script, its settings in git |
| `train_hey_pi.ipynb` | the same as a Colab notebook (T4); a fallback: it hung at its installs on 2026-11-18 |
| `segment.py` | a recording session on the Pi → one clip per "Hey Pi" |
| `tts_samples.py`, `phrases/` | samples from speech-service's 9 voices, through the live service: a phrase set's positives, near misses or everyday speech |
| `config-xiao-pai.yaml`, `spikes/` | 小派小派, a Chinese wake word: spike 01 (our TTS alone) and its results |
| `data/` (not in git) | the clips: `positives/me`, `positives/tts`, `negatives/tts`; voice recordings are personal and stay local |

## Steps
1. **Your voice, on the Pi's own mic** (pi-server is stopped meanwhile: it holds the mic). Between a
   beep and two beeps, say "Hey Pi" about every 2 s: close, ~1 m, across the room; quiet, normal, loud;
   casual and calling out; "嘿 Pi" too. Then split it:

       scp pi:wake-word/hey-pi-session-1.wav data/
       python segment.py data/hey-pi-session-1.wav data/positives/me

2. **speech-service's voices and the near misses** (the test client running):

       PY=../../../python/services/speech-service/.venv/bin/python
       $PY tts_samples.py phrases/hey_pi.yaml positive data/positives/tts --runs 5
       $PY tts_samples.py phrases/hey_pi.yaml near_miss data/negatives/tts --runs 5

3. **Train** (on a Mac, Apple silicon; ~7 GB of downloads into `work/`, fetched once):

       uv venv -p 3.10 .venv
       uv pip install -p .venv 'pymicro-features==2.0.2' 'piper-tts==1.8.0' 'datasets==3.6.0' librosa scipy tqdm pyyaml soundfile \
         'git+https://github.com/whatsnowplaying/audio-metadata@d4ebb238e6a401bb1a5aaaac60c9e2b3cb30929f'
       git clone https://github.com/kahrendt/microWakeWord work/microWakeWord && git -C work/microWakeWord checkout 4665173
       uv pip install -p .venv -e work/microWakeWord        # editable: its `audio` package has no __init__.py
       mkdir -p work/piper-onnx && for f in en_US-libritts_r-medium.onnx en_US-libritts_r-medium.onnx.json; do
         curl -sSLfo work/piper-onnx/$f https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/libritts_r/medium/$f; done
       WORK=$PWD/work DATA=$PWD/data OUT=$PWD/out .venv/bin/python train.py config.yaml

   It ends with `out/hey_pi.tflite` and `out/hey_pi.json`. Where it differs from the notebook, and why:
   Piper's samples come from its **ONNX** voice through `piper-tts` (GPL-3.0, run locally; the notebook's
   `.pt` model is a pickle that newer PyTorch rightly won't load), `pymicro-features` 2.0.2 from PyPI
   (the notebook's macOS fork no longer compiles), `datasets` 3.6 (5.x needs torchcodec), and AudioSet
   read from its Parquet shards with pyarrow (the `.tar` parts the notebook links to are gone).
   The training itself takes ~8 min on an M-series Mac; the log, `work/train.log`, has each validation
   round, and the quantized model's test at several cutoffs at its end.
4. **On the Pi:** both files into `python/services/pi-server/models/` (pi-server's default wake word;
   `infra/deployment/pi-server.sh` ships them), its `probability_cutoff` set from the test; then say
   "Hey Pi" ~30 times at different distances (it should wake), and leave it through an evening of TV and
   talk (it shouldn't); tune `probability_cutoff` in the `.json` (higher: fewer false wakes, more misses).
   The first model's numbers: docs/specs/voice-path.md, 4.12b.

**Notes.** "Hey Pi" is two syllables: short wake words wake by accident more easily than the built-in
three- and four-syllable ones, hence the near misses and the evening test. The training data's licenses
are mixed: the model is for non-commercial personal use.
