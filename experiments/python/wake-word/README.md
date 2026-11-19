# wake-word: training the Pi's wake word

The Pi's own wake word ([docs/specs/wake-word.md](../../../docs/specs/wake-word.md)): microWakeWord models that pi-server
runs (`app/voice/wake.py`, `PI_SERVER_WAKE_WORD`). "Hey Pi" is live (spike 00); 小派小派 replaces it (spikes 01-04).
Training runs on the playground (CT 110, docs/playground.md), its voices generated there on the GPU.

| File | What |
|---|---|
| `train.py` | **the trainer**: microWakeWord's notebook as one resumable script; reads only what a config lists |
| `config-hey-pi.yaml`, `config-xiao-pai*.yaml` | each spike's settings and **exactly the sets it trains on** (01, 02, 03, 04, 04b–04f) |
| `train-run.sh` | a run on the playground, then the common test; logs at :8080 (`GPU=1` for the card) |
| `testset.py` | **the test set** (v1): fixed, rendered once from test-only sound, each clip labelled for every word |
| `evaluate.py` | **the common test**: every model on the test set, one table each (`results/<spike>/test-<model>.txt`) |
| `recording/` | the recording pages, the Mac's mic (`/`) and the Pi's (`/pi`); `label.py` splits and transcribes every take |
| `segment.py`, `tts_samples.py`, `phrases/` | splitting a take into clips; speech-service's 9 voices saying a phrase set; the phrases |
| `heypi_testset.py` | "Hey Pi"'s test clips from the 51 held-out voices, checked by speech-to-text |
| `spikes/NN-*/` | each spike's own scripts (generating voices, cutting datasets, sorting recordings) |
| `results/NN-*/` | each spike's write-up and its model's table in the common test |

## The data (`data/`, not in git; the same on the Mac and the playground)
Voice recordings are personal and generated audio is GBs: both stay out of git, on the Mac and in
`/srv/experiments/wake-word/data` on the playground, kept in step.

```
data/
├── datasets/
│   ├── full/                 as downloaded, recorded or generated; never edited
│   │   ├── alimeeting/       Mandarin meetings, far-field channel 0: train / test / eval
│   │   ├── demand/  musan/noise/  aishell3-refs/
│   │   ├── recordings/       every take of yours, whole: hey-pi-session-*, mac-*, pi-* (wake, near, room)
│   │   └── generated/        every TTS run: heypi-tts (9 voices), spike-01, spike-03 (255 voices), spike-04, heypi-test
│   └── processed/            broken down from full/
│       ├── recordings/       your takes split into clips (clips/<take>/) and labelled (transcripts.jsonl)
│       ├── train/            THE ONLY DATA TRAINING READS (train.py refuses anything else)
│       └── test/             test-only: held-out voices, your held-out clips, room and videos, AliMeeting test + eval
├── testset/v1/               the test set: audio/ and manifest.jsonl, rendered from processed/test/ only
└── 00/ 01/ 03/ 04/           a spike's own extra processing: set-asides (rejected/), 03's held-out voices,
                              04's video background / negative split
```

Set names say what they are: `xiaopai-` and `heypi-` for a word's own clips, `speech-` for everyday speech, the
source after it (`tts-01`: spike 01's voices; `tts-03`: spike 03's 255; `me-pi`, `me-mac`: you, on that mic), and
`-near` for near misses, `-3s` for audio cut into 3-second clips.

## Steps
1. **Record** (the Pi's mic; the page stops pi-server and starts it again): `.venv/bin/python recording/server.py`,
   then http://localhost:8770/pi. Takes land in `data/datasets/full/recordings/`. Then `recording/label.py`
   (speech-service's venv, its test client running) and `spikes/04-pi-mic/sort_recordings.py` (every 4th clip,
   and the last quarter of the room, to `processed/test/`).
2. **Voices** (a GPU window: speech-service stopped by hand, docs/playground.md): `spikes/03-many-voices/gen_voices.py`
   in speech-service's base image on CT 110 (`--lines`, `--out`); then the spike's sorter into train/ and test/.
3. **Train and test** (CT 110, in tmux): `./train-run.sh <run> config-<…>.yaml`. The model lands in
   `/srv/experiments/wake-word/out/models/`; copy it to the Mac's `out/`.
4. **The common test** for any set of models: `python evaluate.py <data root> out/<model>.json … --out results`.
   A new test-set version (more test-only data) is a superset: `testset.py` renders v1's clips identically.
5. **On the Pi:** the `.tflite` and `.json` into `python/services/pi-server/models/`, `PI_SERVER_WAKE_WORD`, a deploy.

**Setting up the trainer** (once per machine; ~7 GB of downloads into `work/`):

    uv venv -p 3.10 .venv
    uv pip install -p .venv 'pymicro-features==2.0.2' 'piper-tts==1.8.0' 'datasets==3.6.0' librosa scipy tqdm pyyaml soundfile \
      'git+https://github.com/whatsnowplaying/audio-metadata@d4ebb238e6a401bb1a5aaaac60c9e2b3cb30929f'
    git clone https://github.com/kahrendt/microWakeWord work/microWakeWord && git -C work/microWakeWord checkout 4665173
    uv pip install -p .venv -e work/microWakeWord        # editable: its `audio` package has no __init__.py
    mkdir -p work/piper-onnx && for f in en_US-libritts_r-medium.onnx en_US-libritts_r-medium.onnx.json; do
      curl -sSLfo work/piper-onnx/$f https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/libritts_r/medium/$f; done

Where it differs from microWakeWord's notebook, and why: Piper's samples come from its **ONNX** voice through
`piper-tts` (GPL-3.0, run locally; the notebook's `.pt` model is a pickle that newer PyTorch rightly won't load),
`pymicro-features` 2.0.2 from PyPI (the notebook's macOS fork no longer compiles), `datasets` 3.6 (5.x needs
torchcodec), and AudioSet read from its Parquet shards with pyarrow (the `.tar` parts the notebook links to are gone).
The training data's licenses are mixed: the models are for non-commercial personal use.
