"""How busy the GPU is during a frame: the kernels' own run time (PyTorch's profiler, CUPTI) vs the
frame's span. Busy ≈ span: GPU-bound (only less or faster work helps). Busy ≪ span: the GPU waits for
the CPU to launch work (launch-bound): a graph helps.

    docker run ... -e TTS_MODEL=... tts-spike-official python spikes/04-frame-split/busy.py
"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import torch
from torch.profiler import ProfilerActivity, profile
from app.tts_engine.engine import QwenTTSEngine

text, voice = "Sure. Tomorrow looks cloudy in the morning, with a little rain in the afternoon.", "aiden"
e = QwenTTSEngine(os.environ["TTS_MODEL"]); e.load(); e.warm_up()
for name in sys.argv[1:] or ("ours-static", "ours-graph"):        # predictor[:talker]
    e.predictor, _, talker = name.partition(":")
    e.talker = talker or "qwen"
    for _ in e.frames(text, voice):                       # warm (and capture) first
        pass
    with profile(activities=[ProfilerActivity.CUDA]) as prof:
        t = time.perf_counter()
        n = sum(1 for _ in e.frames(text, voice))
        torch.cuda.synchronize()
        wall = (time.perf_counter() - t) * 1000
    kernels = [ev for ev in prof.events() if ev.device_type == torch.autograd.DeviceType.CUDA]
    busy = sum(ev.device_time for ev in kernels) / 1000      # µs → ms
    print(f"{name:<12} frames {n}: {wall / n:5.1f} ms a frame, GPU busy {busy / n:5.1f} ms "
          f"({100 * busy / wall:.0f}%), {len(kernels) / n:.0f} kernels a frame", flush=True)
