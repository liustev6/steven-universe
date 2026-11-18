"""The first audio, piece by piece: how long the codec's decode takes alone, and how long the stream's
thread waits for each frame (the end check's `int()` copies to the CPU: it waits for everything queued
on the shared stream before it).

    docker run ... -e TTS_MODEL=... tts-spike-official python spikes/08-streams/timeline.py
"""
import os, statistics, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import torch
from app.tts_engine.engine import QwenTTSEngine

e = QwenTTSEngine(os.environ["TTS_MODEL"], predictor="ours-fused", talker="ours-graph"); e.load(); e.warm_up()
text, voice = "Sure. Tomorrow looks cloudy in the morning, with a little rain in the afternoon.", "aiden"
frames = list(e.frames(text, voice, sample=False))
with torch.inference_mode():
    for n in (1, 2, 4, 29):                                       # 29 = 25 of context + 4 new
        e._codec.decode([{"audio_codes": torch.stack(frames[:n])}]); torch.cuda.synchronize()
        times = []
        for _ in range(10):
            t = time.perf_counter(); e._codec.decode([{"audio_codes": torch.stack(frames[:n])}])
            torch.cuda.synchronize(); times.append((time.perf_counter() - t) * 1000)
        print(f"codec decode alone, {n:>2} frames: {statistics.median(times):5.1f} ms")
# When does each frame reach the stream's thread, and when is its first chunk decoded?
for run in range(3):
    t0 = time.perf_counter(); marks = []
    for audio in e.stream(text, voice, sample=False):
        marks.append((time.perf_counter() - t0) * 1000)
        if len(marks) == 3:
            break
    print(f"run {run}: chunks 1-3 out at " + ", ".join(f"{m:.0f}" for m in marks) + " ms")
