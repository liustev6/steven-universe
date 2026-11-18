"""Spike 15, part 1: Qwen's own generation (its `generate_custom_voice`, sampling as it ships) on spike
14's sentences, to compare with ours: if ours stretches or babbles more often, it's our engine, not the
model. Runs in the service image with the GPU (the service stopped), the dev code at /src:

    docker run --rm --gpus all -v /opt/speech-dev:/src -w /src -v /var/lib/speech-service/models:/models:ro \
      <image> python spikes/15-model-and-sampling/reference.py /models/Qwen3-TTS-12Hz-0.6B-CustomVoice 6
"""

import importlib.util
import statistics
import sys
import time

import numpy as np
import torch
from qwen_tts import Qwen3TTSModel

spec = importlib.util.spec_from_file_location("rate", "spikes/14-speaking-rate/rate.py")
SENTENCES = [l.strip().strip('",') for l in open("spikes/14-speaking-rate/rate.py").read().split("SENTENCES = [")[1].split("]")[0].splitlines() if l.strip()]
NATIVE = {"ryan": "English", "serena": "Chinese"}          # as our engine picks it with no language given
WIN = 480


def measure(x: np.ndarray) -> dict:
    n = len(x) // WIN
    db = 20 * np.log10(np.sqrt(np.mean(x[: n * WIN].reshape(n, WIN) ** 2, axis=1)) + 1e-9)
    idx = np.flatnonzero(db > -45)
    if len(idx) == 0:
        return {"duration": len(x) / 24000, "longest": 0.0}
    inner, pauses, run = (db > -45)[idx[0]:idx[-1] + 1], [], 0
    for v in inner:
        if not v:
            run += 1
        elif run:
            pauses.append(run * 0.02)
            run = 0
    return {"duration": len(x) / 24000, "longest": max([p for p in pauses if p >= 0.15], default=0.0)}


model_path, runs = sys.argv[1], int(sys.argv[2])
tts = Qwen3TTSModel.from_pretrained(model_path, device_map="cuda:0", dtype=torch.bfloat16)
print("Qwen's reference,", model_path, "sampling:", {k: v for k, v in tts._merge_generate_kwargs().items() if k != "max_new_tokens"})
for n, text in enumerate(SENTENCES, 1):
    for voice in ("ryan", "serena"):
        d, longest = [], []
        for _ in range(runs):
            t = time.perf_counter()
            wavs, sr = tts.generate_custom_voice(text=text, speaker=voice, language=NATIVE[voice])
            m = measure(np.asarray(wavs[0], dtype=np.float32))
            d.append(m["duration"]); longest.append(m["longest"])
        med = statistics.median(d)
        print(f"  [{n:2}] {voice:7} median {med:5.2f} s  worst {max(d):5.2f} s (x{max(d) / med:4.2f})  longest pause {max(longest):4.2f} s"
              f"   runs {' '.join(f'{x:.1f}' for x in d)}", flush=True)
