"""What a batch of 2 costs, piece by piece, before building it (speech-service 4.5e; T13).

Each piece at batch 1, 2 and 4 (synchronized GPU time, medians of 30):
  (a) the TTS's code predictor: our fused graph (GraphCodePredictor takes any batch: a graph each)
  (b) the TTS's talker step: 28 fused layers + codec_head on a cache filled to 300 positions, as a graph
      (every row at the same position: enough to time it; per-row positions come with the build)
  (c) the codec's decode: 29 frames each (25 of context + 4 new), one batched call vs separate calls
  (d) the STT's pass: utterances of 3 and 8 s, one padded batch vs one after the other

    docker run ... speech-spike python spikes/11-batching/batch_bench.py
"""
import os, statistics, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import numpy as np, torch
from app.engine.layers import decoder_layers, new_cache, rotary_table
from app.engine.predictor import GraphCodePredictor
from app.engine.stt import QwenSTTEngine
from app.engine.tts import QwenTTSEngine

DEV, BF = "cuda:0", torch.bfloat16
SAMPLING = dict(do_sample=True, top_k=50, top_p=1.0, temperature=0.9)


def timed(fn, n=30):
    fn(); fn(); torch.cuda.synchronize()
    out = []
    for _ in range(n):
        t = time.perf_counter(); fn(); torch.cuda.synchronize(); out.append((time.perf_counter() - t) * 1000)
    return statistics.median(out)


def row(name, ms):
    base = ms[1]
    print(f"{name:<34}" + "".join(f"  B={b}: {ms[b]:6.2f} ms (×{ms[b] / base:.2f})" for b in sorted(ms)), flush=True)


tts = QwenTTSEngine(os.environ["TTS_MODEL"], predictor="ours-fused", talker="ours-graph"); tts.load(); tts.warm_up()
talker, cp = tts._talker, tts._talker.code_predictor

with torch.inference_mode():
    # (a) the predictor
    pred = GraphCodePredictor(cp, fused=True)
    row("(a) predictor, 15 passes (graph)", {b: timed(lambda b=b: pred(torch.randn(b, 2, 1024, device=DEV, dtype=BF), **SAMPLING)) for b in (1, 2, 4)})

    # (b) the talker step at position 300
    model, cfg = talker.model, talker.config
    talker_ms = {}
    for b in (1, 2, 4):
        keys, values = new_cache(len(model.layers), b, cfg.num_key_value_heads, 1024, model.layers[0].self_attn.head_dim, dtype=BF, device=DEV)
        for k, v in zip(keys, values):
            k.normal_(); v.normal_()
        cos, sin = rotary_table(model.rotary_emb, torch.zeros(1, 1, 1024, device=DEV, dtype=BF), 1024, streams=3)
        position, x = torch.tensor([300], dtype=torch.int32, device=DEV), torch.randn(b, 1, 1024, device=DEV, dtype=BF)
        run = lambda: talker.codec_head(decoder_layers(model.layers, model.norm, x, 0, 1, keys, values, cos, sin, fused=True, position=position))
        side = torch.cuda.Stream(); side.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(side):
            for _ in range(3): run()
        torch.cuda.current_stream().wait_stream(side)
        g = torch.cuda.CUDAGraph()
        with torch.cuda.graph(g):
            run()
        talker_ms[b] = timed(g.replay)
    row("(b) talker step, 28 layers (graph)", talker_ms)

    # (c) the codec's decode
    codes = [torch.randint(0, 2048, (29, 16), device=DEV) for _ in range(4)]
    codec = tts._codec
    row("(c) codec decode, one batched call", {b: timed(lambda b=b: codec.decode([{"audio_codes": c} for c in codes[:b]]), 15) for b in (1, 2, 4)})
    row("    codec decode, separate calls", {b: timed(lambda b=b: [codec.decode([{"audio_codes": c}]) for c in codes[:b]], 15) for b in (1, 2, 4)})

    # (d) the STT pass
    stt = QwenSTTEngine(os.environ["STT_MODEL"]); stt.load(); stt.warm_up()
    rng = np.random.default_rng(0)
    clips = [rng.normal(0, 0.05, int(s * 16000)).astype(np.float32) for s in (3, 8, 3, 8)]
    p, m, prompt = stt._processor, stt._model, stt._prompt(None)

    def batched(n):
        inputs = p(text=[prompt] * n, audio=clips[:n], return_tensors="pt", padding=True).to(m.device).to(m.dtype)
        m.generate(**inputs, max_new_tokens=32)

    def separate(n):
        for a in clips[:n]:
            inputs = p(text=[prompt], audio=[a], return_tensors="pt", padding=True).to(m.device).to(m.dtype)
            m.generate(**inputs, max_new_tokens=32)

    row("(d) STT pass (3 s, 8 s), batched", {b: timed(lambda b=b: batched(b), 10) for b in (1, 2, 4)})
    row("    STT pass, one after another", {b: timed(lambda b=b: separate(b), 10) for b in (1, 2, 4)})
print(f"VRAM peak {torch.cuda.max_memory_allocated() / 2**30:.2f} GB")
