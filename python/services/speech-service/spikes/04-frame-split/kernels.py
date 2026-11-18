"""Which kernels a frame launches, by kind (the graph-ready predictor, unfused; one sentence).

    docker run ... -e TTS_MODEL=... tts-spike-official python spikes/04-frame-split/kernels.py
"""
import collections, os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import torch
from torch.profiler import ProfilerActivity, profile
from app.tts_engine.engine import QwenTTSEngine

text, voice = "Sure. Tomorrow looks cloudy in the morning, with a little rain in the afternoon.", "aiden"
e = QwenTTSEngine(os.environ["TTS_MODEL"]); e.load(); e.warm_up()
cp = e._talker.code_predictor
# Count only the predictor's kernels: profile a direct call of the predictor, 20 times
x = torch.randn(1, 2, 1024, device="cuda", dtype=torch.bfloat16)
pred = e._predictors[sys.argv[1] if len(sys.argv) > 1 else "ours-static"].__class__(cp, fused=len(sys.argv) > 2)
with torch.inference_mode():
    pred(x, do_sample=True, top_k=50, top_p=1.0, temperature=0.9)
    with profile(activities=[ProfilerActivity.CUDA]) as prof:
        for _ in range(20):
            pred(x, do_sample=True, top_k=50, top_p=1.0, temperature=0.9)
        torch.cuda.synchronize()
kinds, time_ = collections.Counter(), collections.Counter()
for ev in prof.events():
    if ev.device_type != torch.autograd.DeviceType.CUDA:
        continue
    name = ev.name
    k = ("matrix multiply (cuBLAS/cutlass)" if re.search(r"gemm|gemv|cutlass|sm\d+_xmma|cublas|Kernel2", name, re.I) else
         "attention" if re.search(r"flash|fmha|attention|efficient", name, re.I) else
         "reduction (mean, max, sum…)" if re.search(r"reduce", name, re.I) else
         "copy / cast (to, contiguous, cat)" if re.search(r"copy|cat|CatArray|direct_copy", name, re.I) else
         "elementwise (add, mul, pow, rsqrt, silu, neg…)" if re.search(r"elementwise|vectorized|unrolled", name, re.I) else
         "sampling (topk, sort, softmax, multinomial…)" if re.search(r"topk|sort|softmax|multinomial|radix|gather|scatter|sampleMultinomial|distribution", name, re.I) else
         "other: " + name[:60])
    kinds[k] += 1; time_[k] += ev.device_time
total = sum(kinds.values()) / 20
print(f"the predictor, one frame (15 passes): {total:.0f} kernels, {sum(time_.values()) / 20 / 1000:.2f} ms of GPU time")
for k, n in kinds.most_common():
    print(f"  {n / 20:6.0f}  {time_[k] / 20 / 1000:5.2f} ms  {k}")
