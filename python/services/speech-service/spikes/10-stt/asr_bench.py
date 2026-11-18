"""Speech-to-text on the RTX 5060: accuracy, latency, speed, VRAM (speech-service 4.5d, spike 10).

Modes (each over the whole test set, prep_data.py's manifest):
  qwen-utt       Qwen3-ASR-0.6B, the whole utterance decoded after speech ends (transformers backend)
  qwen-stream-N  Qwen3-ASR-0.6B streaming, Qwen's own method on the transformers backend: every N s of
                 audio, re-feed ALL the audio so far, with the text so far (less its last 5 tokens; none
                 for the first 2 chunks) as the prompt's prefix; at the end, the leftover audio once more
  whisper-utt    Whisper large-v3-turbo, the whole utterance (the reference point)

Per clip: the text, and the "final ms": the work left after the speaker stops (utterance: the whole
decode; streaming: the last pass). Streaming also: each chunk's compute (must stay under N s to keep
up) and the first partial's time. Greedy decoding throughout (Qwen's streaming uses temperature 0).

    docker run ... speech-spike python spikes/10-stt/asr_bench.py qwen-utt qwen-stream-1 qwen-stream-2 whisper-utt
"""
import json, os, statistics, sys, time
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np, soundfile as sf, torch
from scoring import error_rate

DATA, MODELS, OUT = os.environ.get("DATA", "/data"), os.environ.get("MODELS", "/models"), os.environ.get("OUT", "/out")
SR = 16000


def sync_ms(t):
    torch.cuda.synchronize(); return (time.perf_counter() - t) * 1000


class Qwen:
    def __init__(self):
        from qwen_asr import Qwen3ASRModel
        from qwen_asr.inference.utils import parse_asr_output
        self.m = Qwen3ASRModel.from_pretrained(f"{MODELS}/Qwen3-ASR-0.6B", dtype=torch.bfloat16, device_map="cuda:0",
                                               attn_implementation="sdpa", max_new_tokens=256)
        self.parse, self.tok = parse_asr_output, self.m.processor.tokenizer
        self.prompt = self.m._build_text_prompt(context="", force_language=None)

    def decode(self, audio, prefix=""):
        """One pass: the audio so far, the prompt + prefix → the raw text (prefix + what it generated)."""
        p, m = self.m.processor, self.m.model
        inputs = p(text=[self.prompt + prefix], audio=[audio], return_tensors="pt", padding=True).to(m.device).to(m.dtype)
        with torch.inference_mode():
            ids = m.generate(**inputs, max_new_tokens=256)
        return prefix + p.batch_decode(ids.sequences[:, inputs["input_ids"].shape[1]:], skip_special_tokens=True,
                                       clean_up_tokenization_spaces=False)[0]

    def utterance(self, audio):
        t = time.perf_counter(); raw = self.decode(audio); ms = sync_ms(t)
        return {"text": self.parse(raw)[1], "final_ms": ms, "compute_ms": ms}

    def prefix(self, raw, chunk_id, k=5, unfixed_chunks=2):
        if chunk_id < unfixed_chunks:
            return ""
        ids = self.tok.encode(raw)
        while True:                                   # Qwen's rollback: drop the last k tokens (more if a
            end = max(0, len(ids) - k)                # cut character would be left broken)
            pre = self.tok.decode(ids[:end]) if end > 0 else ""
            if "�" not in pre or end == 0:
                return pre
            k += 1

    def stream(self, audio, chunk_s):
        n, raw, chunk_ms, first, done = int(chunk_s * SR), "", [], None, 0
        for chunk_id, end in enumerate(range(n, len(audio) + 1, n)):    # a full chunk has arrived
            t = time.perf_counter(); raw = self.decode(audio[:end], self.prefix(raw, chunk_id)); chunk_ms.append(sync_ms(t))
            if first is None and self.parse(raw)[1].strip():
                first = end / SR
            done = end
        t = time.perf_counter()                                           # the speaker stopped: the leftover
        if done < len(audio) or not chunk_ms:
            raw = self.decode(audio, self.prefix(raw, len(chunk_ms)) if chunk_ms else "")
        final = sync_ms(t)
        return {"text": self.parse(raw)[1], "final_ms": final, "compute_ms": sum(chunk_ms) + final,
                "chunk_ms_max": max(chunk_ms) if chunk_ms else 0.0, "chunks": len(chunk_ms), "first_partial_s": first}


class Whisper:
    def __init__(self):
        from transformers import AutoProcessor, WhisperForConditionalGeneration
        path = f"{MODELS}/whisper-large-v3-turbo"
        self.p = AutoProcessor.from_pretrained(path)
        self.m = WhisperForConditionalGeneration.from_pretrained(path, dtype=torch.bfloat16, attn_implementation="sdpa").to("cuda:0")

    def utterance(self, audio):
        t = time.perf_counter()
        feats = self.p(audio, sampling_rate=SR, return_tensors="pt").input_features.to("cuda:0", torch.bfloat16)
        with torch.inference_mode():
            ids = self.m.generate(feats, task="transcribe", num_beams=1)
        text = self.p.batch_decode(ids, skip_special_tokens=True)[0]
        ms = sync_ms(t)
        return {"text": text, "final_ms": ms, "compute_ms": ms}


def main(modes):
    rows = [json.loads(l) for l in open(f"{DATA}/manifest.jsonl")]
    clips = [(r, sf.read(f"{DATA}/{r['path']}", dtype="float32")[0]) for r in rows]
    models = {}
    print(f"{'mode':<15}{'en':>6}{'zh':>6}{'mix':>6} | {'en*':>6}{'zh*':>6} | {'RTF':>5} {'final p50/p90 ms':>17} "
          f"{'chunk max ms':>13} {'1st partial s':>14} {'VRAM GB':>8}", flush=True)
    for mode in modes:
        kind = mode.split("-")[0]
        if kind not in models:
            models[kind] = Qwen() if kind == "qwen" else Whisper()
        model = models[kind]
        run = model.utterance if mode.endswith("utt") else (lambda a: model.stream(a, float(mode.rsplit("-", 1)[1])))
        run(clips[0][1]); torch.cuda.reset_peak_memory_stats()                     # warm up
        results = []
        with open(f"{OUT}/{mode}.jsonl", "w") as f:
            for r, audio in clips:
                res = run(audio); res.update(id=r["id"], lang=r["lang"], ref=r["text"], dur=r["dur"])
                results.append(res); f.write(json.dumps(res, ensure_ascii=False) + "\n")
        import re
        by = {l: [x for x in results if x["lang"] == l] for l in ("en", "zh", "mix")}
        nodig = {l: [x for x in xs if not re.search(r"\d", x["ref"])] for l, xs in by.items()}
        pct = lambda x: "  -  " if x is None else f"{100 * x:5.1f}"
        finals = [x["final_ms"] for x in results]
        rtf = sum(x["compute_ms"] for x in results) / 1000 / sum(x["dur"] for x in results)
        cmax = f"{max(x.get('chunk_ms_max', 0) for x in results):.0f}" if "stream" in mode else "-"
        fp = [x["first_partial_s"] for x in results if x.get("first_partial_s") is not None]
        print(f"{mode:<15}{pct(error_rate(by['en'])):>6}{pct(error_rate(by['zh'])):>6}{pct(error_rate(by['mix'])):>6} | "
              f"{pct(error_rate(nodig['en'])):>6}{pct(error_rate(nodig['zh'])):>6} | {rtf:>5.3f} "
              f"{statistics.median(finals):>8.0f}/{np.percentile(finals, 90):<8.0f} {cmax:>13} "
              f"{(f'{statistics.median(fp):.1f}' if fp else '-'):>14} {torch.cuda.max_memory_allocated() / 2**30:>8.2f}", flush=True)
    print("\nen/zh/mix: error % (WER / CER / MER), the Pi spike's scoring. en*/zh*: clips whose reference has no digits.")


if __name__ == "__main__":
    main(sys.argv[1:])
