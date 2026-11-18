"""How fast each predictor is, on the real model and GPU (CT 108):

    TTS_MODEL=/models/Qwen3-TTS-12Hz-0.6B-CustomVoice python scripts/bench.py [predictor[:talker] ...]

Per predictor, after one warm-up: each sentence 3 times, the medians of
    ms/frame      one frame's 16 codes (the talker's step + the code predictor's 15 passes)
    first audio   from the call to the first audio chunk (4 frames, decoded)
    RTF           time to make the sentence ÷ its length (below 1: faster than real time)
"""

import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.tts_engine.engine import PREDICTORS, QwenTTSEngine  # noqa: E402

SENTENCES = [("ryan", "The kitchen lights are on, and the door is locked."),
             ("vivian", "好的，我现在帮你把客厅的灯关掉。"),
             ("aiden", "Sure. Tomorrow looks cloudy in the morning, with a little rain in the afternoon.")]


def one(engine, voice, text):
    import torch

    t = time.perf_counter()
    first, samples, frames = None, 0, 0
    for _ in engine.frames(text, voice):
        frames += 1
    torch.cuda.synchronize()
    per_frame = (time.perf_counter() - t) / frames
    t = time.perf_counter()
    for audio in engine.stream(text, voice):
        first = first or time.perf_counter() - t
        samples += len(audio)
    rtf = (time.perf_counter() - t) / (samples / engine.sample_rate)
    return per_frame * 1000, first * 1000, rtf


def main(names):
    engine = QwenTTSEngine(os.environ["TTS_MODEL"])
    engine.load()
    engine.warm_up()
    print(f"{'predictor:talker':<20} {'ms/frame':>9} {'first audio':>12} {'RTF':>6}")
    for name in names:
        engine.predictor, _, talker = name.partition(":")
        engine.talker = talker or "qwen"
        one(engine, "ryan", "Hi, how can I help you today?")
        runs = [one(engine, voice, text) for voice, text in SENTENCES for _ in range(3)]
        ms, first, rtf = (statistics.median(r[i] for r in runs) for i in range(3))
        print(f"{engine.predictor + ':' + engine.talker:<20} {ms:>9.1f} {first:>10.0f} ms {rtf:>6.2f}")


if __name__ == "__main__":
    main(sys.argv[1:] or PREDICTORS)
