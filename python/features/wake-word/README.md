# wake-word: training "Hey Pi"

Hey Pi's own wake word (docs/specs/voice-path.md, V9 and 4.12b), a microWakeWord model like the
built-in `hey_jarvis` that pi-server runs today (`app/voice/wake.py`, `PI_SERVER_WAKE_WORD`).

| File | What |
|---|---|
| `train_hey_pi.ipynb` | the training notebook, for **Google Colab with a T4 GPU**: microWakeWord's own (kahrendt/microWakeWord, Apache-2.0) with our data added (its first cell says what changed) |
| `segment.py` | a recording session on the Pi → one clip per "Hey Pi" |
| `tts_samples.py` | positives from speech-service's 9 voices, and near-miss negatives, through the live service |
| `data/` (not in git) | the clips: `positives/me`, `positives/tts`, `negatives/tts`; voice recordings are personal and stay local |

## Steps
1. **Your voice, on the Pi's own mic** (pi-server is stopped meanwhile: it holds the mic). Between a
   beep and two beeps, say "Hey Pi" about every 2 s: close, ~1 m, across the room; quiet, normal, loud;
   casual and calling out; "嘿 Pi" too. Then split it:

       scp pi:wake-word/hey-pi-session-1.wav data/
       python segment.py data/hey-pi-session-1.wav data/positives/me

2. **speech-service's voices and the near misses** (the test client running):

       ../../services/speech-service/.venv/bin/python tts_samples.py data/positives/tts data/negatives/tts --runs 5

3. **Zip and train:** `cd data && zip -qr ../data.zip positives negatives`, open `train_hey_pi.ipynb` in
   Colab (T4), run the cells in order, upload `data.zip` when asked. It ends by downloading
   `hey_pi.tflite` and `hey_pi.json`.
4. **On the Pi:** both files into `python/services/pi-server/models/`, `PI_SERVER_WAKE_WORD` pointed at the
   `.json`; then say "Hey Pi" ~30 times at different distances (it should wake), and leave it through an
   evening of TV and talk (it shouldn't); tune `probability_cutoff` in the `.json` (higher: fewer false
   wakes, more misses).

**Notes.** "Hey Pi" is two syllables: short wake words wake by accident more easily than the built-in
three- and four-syllable ones, hence the near misses and the evening test. The training data's licenses
are mixed: the model is for non-commercial personal use.
