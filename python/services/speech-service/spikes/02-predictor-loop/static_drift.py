"""Teacher-forced: the same predictor inputs, the grown cache vs the fixed one, step by step."""
import os, sys
sys.path.insert(0, "/src")
import torch
from transformers import DynamicCache, StaticCache
from app.tts_engine.engine import QwenTTSEngine

e = QwenTTSEngine(os.environ["TTS_MODEL"]); e.load()
cp = e._talker.code_predictor
inputs = []
orig = cp.generate
def spy(inputs_embeds, **kw):
    inputs.append(inputs_embeds.clone()); return orig(inputs_embeds=inputs_embeds, **kw)
cp.generate = spy
with torch.inference_mode():
    for _ in e.frames("Sure. Tomorrow looks cloudy in the morning, with a little rain in the afternoon.", "ryan", sample=False):
        pass
cp.__dict__.pop("generate", None)
print("frames", len(inputs))

def run(x0, cache, forced=None):
    logits_all, codes, x, pos = [], [], x0, 0
    for k in range(15):
        if k: x = cp.model.codec_embedding[k-1](codes[-1])
        n = x.shape[1]
        h = cp.model(inputs_embeds=x, past_key_values=cache, use_cache=True,
                     cache_position=torch.arange(pos, pos+n, device=x.device)).last_hidden_state
        lg = cp.lm_head[k](h)[:, -1].float(); logits_all.append(lg)
        codes.append(forced[:, k:k+1] if forced is not None else lg.argmax(-1, keepdim=True)); pos += n
    return torch.cat(codes, 1), logits_all

static = StaticCache(config=cp.config, max_cache_len=16)
flips, maxdiff, margins = 0, 0.0, []
with torch.inference_mode():
    for x0 in inputs:
        dc, dl = run(x0, DynamicCache())
        static.reset()
        _, sl = run(x0, static, forced=dc)
        for k in range(15):
            d = (dl[k] - sl[k]).abs().max().item(); maxdiff = max(maxdiff, d)
            if sl[k].argmax() != dl[k].argmax():
                top2 = dl[k].topk(2).values[0]
                flips += 1; margins.append((top2[0]-top2[1]).item())
print(f"steps {len(inputs)*15}, argmax flips {flips}, max |logit diff| {maxdiff:.4f}")
print("top-2 margins where it flipped:", [round(m, 4) for m in margins[:10]])
# which attention kernel each path gets: a mask (static) vs none (grown)
from transformers.masking_utils import create_causal_mask
x = torch.zeros(1, 1, 1024, device="cuda", dtype=torch.bfloat16)
for name, c in [("grown", DynamicCache()), ("fixed", StaticCache(config=cp.config, max_cache_len=16))]:
    m = create_causal_mask(config=cp.config, input_embeds=x, attention_mask=None,
                           cache_position=torch.tensor([3], device="cuda"), past_key_values=c)
    print(name, "mask:", None if m is None else tuple(m.shape))
