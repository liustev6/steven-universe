"""How far the fused RMSNorm moves the predictor (teacher-forced, like spikes/02's static_drift.py).

1. On real activations: how often does our kernel's output differ from Qwen's RMSNorm, and by how much?
2. The same predictor inputs (every frame of one sentence), unfused vs fused, the fused one fed the
   unfused one's codes: how often does the best code differ, and how close were the top two there?

    docker run ... -e TTS_MODEL=... tts-spike-official python spikes/05-fused-rmsnorm/drift.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import torch
from app.engine import kernels
from app.engine.predictor import StaticCodePredictor
from app.engine.qwen import QwenEngine

e = QwenEngine(os.environ["TTS_MODEL"], predictor="ours-static"); e.load()
cp = e._talker.code_predictor
inputs, orig = [], e._predictors["ours-static"]
e._predictors["ours-static"] = lambda inputs_embeds, **kw: (inputs.append(inputs_embeds.clone()), orig(inputs_embeds, **kw))[1]
text = "Sure. Tomorrow looks cloudy in the morning, with a little rain in the afternoon."
with torch.inference_mode():
    for _ in e.frames(text, "ryan", sample=False):
        pass
print("frames", len(inputs))

# 1. the kernel on real activations: compare every call with Qwen's module
stats = {"calls": 0, "elements": 0, "differ": 0, "worst_steps": 0.0, "rows_with_diff": 0, "rows": 0}
real = kernels.rms_norm
def checked(x, w, eps):
    got = real(x, w, eps)
    xf = x.float(); want = w * (xf * torch.rsqrt(xf.pow(2).mean(-1, keepdim=True) + eps)).to(x.dtype)
    d = got != want
    stats["calls"] += 1; stats["elements"] += d.numel(); stats["differ"] += int(d.sum())
    stats["rows"] += d.reshape(-1, d.shape[-1]).shape[0]; stats["rows_with_diff"] += int(d.reshape(-1, d.shape[-1]).any(-1).sum())
    if d.any():
        step = torch.finfo(torch.bfloat16).eps * want.float().abs().clamp(min=1e-3)
        stats["worst_steps"] = max(stats["worst_steps"], ((got.float() - want.float()).abs() / step).max().item())
    return got
kernels.rms_norm = checked

plain, fused = StaticCodePredictor(cp), StaticCodePredictor(cp, fused=True)

def run(sp, x0, forced=None):
    b = sp._setup(x0); x, codes, logits = x0, [], []
    for k in range(15):
        if k: x = cp.model.codec_embedding[k - 1](codes[-1])
        start, n = (0, 2) if k == 0 else (k + 1, 1)
        lg = cp.lm_head[k](sp._layers(x, start, n, b))[:, -1].float(); logits.append(lg)
        codes.append(forced[:, k:k + 1] if forced is not None else lg.argmax(-1, keepdim=True))
    return torch.cat(codes, 1), logits

flips, margins, maxdiff = 0, [], 0.0
with torch.inference_mode():
    for x0 in inputs:
        codes, want = run(plain, x0)
        _, got = run(fused, x0, forced=codes)
        for k in range(15):
            maxdiff = max(maxdiff, (want[k] - got[k]).abs().max().item())
            if want[k].argmax() != got[k].argmax():
                top2 = want[k].topk(2).values[0]; flips += 1; margins.append(round((top2[0] - top2[1]).item(), 4))
print(f"1. the kernel on real activations: {stats['calls']} calls, {stats['differ']} of {stats['elements']} elements differ "
      f"({stats['rows_with_diff']} of {stats['rows']} rows); worst {stats['worst_steps']:.2f} bfloat16 steps")
print(f"2. teacher-forced: steps {len(inputs) * 15}, argmax flips {flips}, max |logit diff| {maxdiff:.4f}")
print("   top-2 margins where it flipped:", margins[:12])
