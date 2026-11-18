"""Both models in one process (T12): VRAM, and does either slow the other? (speech-service 4.5d, spike 10)

1. Load the TTS engine (fused predictor, graphed talker, two streams) and Qwen3-ASR-0.6B; VRAM.
2. Alone: the TTS (first audio, margin, RTF over 3 sentences × 3 runs); the ASR (final ms per clip,
   whole utterances, on its own CUDA stream).
3. Together: a thread keeps the ASR busy on its own stream while the TTS speaks the same sentences;
   then the same numbers.

    ASR_LOAD=utt      the worst case: whole utterances back to back, nonstop
    ASR_LOAD=stream   barge-in as it would be: a clip streamed in 1 s chunks, paced in real time
                      (each chunk decoded when its audio would have arrived), then the final pass
    ASR_PRIORITY=-1   the ASR's stream above the TTS's generation (and level with its decode)
    ASR_PRIORITY=0    the ASR level with the TTS's generation, below its decode

    docker run ... -e ASR_LOAD=stream -e ASR_PRIORITY=0 speech-spike python spikes/10-stt/together.py
"""
import json, os, statistics, subprocess, sys, threading, time
here = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(here, "..", "..")); sys.path.insert(0, here)
import numpy as np, soundfile as sf, torch
from app.tts_engine.engine import QwenTTSEngine
from asr_bench import Qwen

SENTENCES = [("ryan", "Hi, how can I help you today?"), ("vivian", "好的，我现在帮你把客厅的灯关掉。"),
             ("aiden", "Sure. Tomorrow looks cloudy in the morning, with a little rain in the afternoon.")]


def gpu_used_gb():
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True).stdout.split(",")
    return float(out[0]) / 1024, float(out[1]) / 1024


def tts_run(e, voice, text):
    t, arrivals, chunks = time.perf_counter(), [], []
    for audio in e.stream(text, voice):
        arrivals.append(time.perf_counter() - t); chunks.append(audio)
    clock, margin = arrivals[0], float("inf")
    for i, (at, audio) in enumerate(zip(arrivals, chunks)):
        if i:
            margin = min(margin, clock - at)
        clock = max(clock, at) + len(audio) / 24000
    seconds = sum(len(a) for a in chunks) / 24000
    return arrivals[0] * 1000, margin * 1000, arrivals[-1] / seconds


def tts_numbers(e):
    runs = [tts_run(e, v, s) for v, s in SENTENCES for _ in range(3)]
    return {k: statistics.median(r[i] for r in runs) for i, k in enumerate(("first_ms", "margin_ms", "rtf"))} | \
           {"worst_margin_ms": min(r[1] for r in runs)}


def main():
    data = os.environ.get("DATA", "/data")
    clips = [sf.read(f"{data}/{json.loads(l)['path']}", dtype="float32")[0] for l in open(f"{data}/manifest.jsonl")][::6]
    torch.cuda.reset_peak_memory_stats()
    e = QwenTTSEngine(os.environ["TTS_MODEL"], predictor="ours-fused", talker="ours-graph"); e.load(); e.warm_up()
    after_tts = torch.cuda.max_memory_allocated() / 2**30
    load, priority = os.environ.get("ASR_LOAD", "utt"), int(os.environ.get("ASR_PRIORITY", "-1"))
    asr = Qwen(); asr_stream = torch.cuda.Stream(priority=priority)
    print(f"ASR load: {load}, its stream's priority: {priority}", flush=True)
    with torch.cuda.stream(asr_stream):
        asr.utterance(clips[0])
    used, total = gpu_used_gb()
    print(f"VRAM: TTS peak {after_tts:.2f} GB; both, PyTorch peak {torch.cuda.max_memory_allocated() / 2**30:.2f} GB; "
          f"the GPU's used {used:.2f} of {total:.2f} GB (with the CUDA context)", flush=True)

    def paced_stream(audio, chunk_s=1.0):
        """A clip as the Pi would send it: each 1 s chunk decoded when its audio has arrived."""
        n, raw, start = int(chunk_s * 16000), "", time.perf_counter()
        for chunk_id, end in enumerate(range(n, len(audio) + 1, n)):
            time.sleep(max(0.0, start + end / 16000 - time.perf_counter()))
            raw = asr.decode(audio[:end], asr.prefix(raw, chunk_id))
        time.sleep(max(0.0, start + len(audio) / 16000 - time.perf_counter()))
        t = time.perf_counter()
        asr.decode(audio, asr.prefix(raw, len(audio) // n))
        torch.cuda.synchronize()
        return (time.perf_counter() - t) * 1000

    def asr_final_ms(n=None):
        out = []
        with torch.cuda.stream(asr_stream):
            for audio in clips[:n]:
                out.append(paced_stream(audio) if load == "stream" else asr.utterance(audio)["final_ms"])
        return out

    tts_alone = tts_numbers(e)
    asr_alone = asr_final_ms(None if load == "utt" else 6)
    stop, during = threading.Event(), []

    def keep_asr_busy():
        while not stop.is_set():
            during.extend(asr_final_ms(4 if load == "utt" else 1))

    worker = threading.Thread(target=keep_asr_busy); worker.start()
    time.sleep(0.2)
    tts_together = tts_numbers(e)
    stop.set(); worker.join()
    fmt = lambda d: f"first audio {d['first_ms']:.0f} ms, margin {d['margin_ms']:.0f} ms (worst {d['worst_margin_ms']:.0f}), RTF {d['rtf']:.3f}"
    print(f"TTS alone:        {fmt(tts_alone)}")
    print(f"TTS with ASR:     {fmt(tts_together)}")
    print(f"ASR alone:        final {statistics.median(asr_alone):.0f} ms (p90 {np.percentile(asr_alone, 90):.0f}) over {len(asr_alone)} clips")
    print(f"ASR during TTS:   final {statistics.median(during):.0f} ms (p90 {np.percentile(during, 90):.0f}) over {len(during)} clips")


if __name__ == "__main__":
    main()
