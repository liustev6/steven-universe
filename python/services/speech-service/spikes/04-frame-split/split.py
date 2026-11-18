"""Where a frame's time goes, with the code predictor as a CUDA graph (4.5c, before stage 2).

Each call of the talker's forward is one frame: Qwen's talker runs our predictor (codes 2-16 of the
previous frame), then its own 28 layers (code 1 of the next). Between two calls, Hugging Face's
`generate` loop picks code 1 (repetition penalty, sampling) and checks for the end.

Measured per frame, sampled, after the warm-up:
    GPU time (CUDA events, no syncs added): the predictor's replay; the talker's own work (its forward
        minus the predictor); the generate loop's work between calls
    CPU time (wall clock): the talker's forward call; the gap between calls
If the CPU takes longer than the GPU to get through a part, the GPU waits on the CPU there
(launch-bound): what a graph fixes.

    docker run ... -e TTS_MODEL=... tts-spike-official python spikes/04-frame-split/split.py
"""
import os, statistics, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import torch
from app.engine.tts import QwenTTSEngine

e = QwenTTSEngine(os.environ["TTS_MODEL"], predictor="ours-graph"); e.load(); e.warm_up()
talker, cp = e._talker, e._talker.code_predictor
graph = e._predictors["ours-graph"]
calls = []                                            # one dict per talker forward call


def event():
    ev = torch.cuda.Event(enable_timing=True); ev.record(); return ev


def pre(module, args, kwargs):
    calls.append({"start": event(), "t0": time.perf_counter(), "prefill": kwargs.get("inputs_embeds") is not None
                  and kwargs["inputs_embeds"].shape[1] > 1})


def post(module, args, out):
    calls[-1]["end"], calls[-1]["t1"] = event(), time.perf_counter()


def predictor(*a, **kw):
    calls[-1]["p0"] = event()
    r = graph(*a, **kw)
    calls[-1]["p1"] = event()
    return r


h1 = talker.register_forward_pre_hook(pre, with_kwargs=True)
h2 = talker.register_forward_hook(post)
e._predictors["ours-graph"] = predictor
rows, prefill = [], []
for voice, text in [("ryan", "Hi, how can I help you today?"), ("vivian", "好的，我现在帮你把客厅的灯关掉。"),
                    ("aiden", "Sure. Tomorrow looks cloudy in the morning, with a little rain in the afternoon.")]:
    for _ in range(3):
        calls.clear()
        for _ in e.frames(text, voice):
            pass
        torch.cuda.synchronize()
        prefill.append((calls[0]["start"].elapsed_time(calls[0]["end"]), (calls[0]["t1"] - calls[0]["t0"]) * 1000))
        for prev, c in zip(calls, calls[1:]):
            if "p0" not in c:
                continue
            gpu_call = c["start"].elapsed_time(c["end"])
            pred = c["p0"].elapsed_time(c["p1"])
            rows.append({"predictor": pred, "talker": gpu_call - pred,
                         "between_gpu": prev["end"].elapsed_time(c["start"]),
                         "call_cpu": (c["t1"] - c["t0"]) * 1000, "between_cpu": (c["t0"] - prev["t1"]) * 1000,
                         "frame": prev["end"].elapsed_time(c["end"])})
h1.remove(); h2.remove()
med = {k: statistics.median(r[k] for r in rows) for k in rows[0]}
print(f"frames measured: {len(rows)} (medians, ms)")
print(f"  a frame, end to end (GPU clock)      {med['frame']:6.1f}")
print(f"  ├ the predictor's graph replay (GPU) {med['predictor']:6.1f}")
print(f"  ├ the talker's own work (GPU span)   {med['talker']:6.1f}   its forward call on the CPU: {med['call_cpu']:.1f} (incl. the replay)")
print(f"  └ between calls: generate's loop     {med['between_gpu']:6.1f}   on the CPU: {med['between_cpu']:.1f}")
print(f"  the prompt (prefill): GPU {statistics.median(p[0] for p in prefill):.1f}, CPU {statistics.median(p[1] for p in prefill):.1f}")
