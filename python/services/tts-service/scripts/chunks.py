"""Spike: how small can the first chunk be? Each chunk size on the real model and GPU (CT 108):

    TTS_MODEL=/models/Qwen3-TTS-12Hz-0.6B-CustomVoice python scripts/chunks.py [1 2 4 1,2,4 ...] [--out DIR]
        [--predictor ours|ours-static|ours-graph]

Per chunk size (frames per decode), or schedule (1,2,4: 1, then 2, then every 4), greedy, our predictor, 3 runs of each sentence, medians:
    first audio   from the call to the first audio
    RTF           time to make the sentence ÷ its length
    stalls        a simulated player starting at the first audio: total time it waited for audio
                  (0: smooth; above 0: an audible gap)
    margin        the least audio the player still had queued when a chunk arrived (how close to a gap)
    SNR           streamed vs the whole sentence decoded at once, in dB (above ~20: no audible difference)
With --out, one WAV per chunk size and sentence, to listen to.
"""

import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from app.engine.qwen import QwenEngine  # noqa: E402

SENTENCES = [("ryan", "Hi, how can I help you today?"),
             ("vivian", "好的，我现在帮你把客厅的灯关掉。"),
             ("aiden", "Sure. Tomorrow looks cloudy in the morning, with a little rain in the afternoon.")]


def one(engine, voice, text, sr):
    t, arrivals, chunks = time.perf_counter(), [], []
    for audio in engine.stream(text, voice, sample=False):
        arrivals.append(time.perf_counter() - t)
        chunks.append(audio)
    total = arrivals[-1]
    # The player: starts at the first chunk, plays each one after the last; it waits if one is late.
    clock, stalled, margin = arrivals[0], 0.0, float("inf")
    for i, (at, audio) in enumerate(zip(arrivals, chunks)):
        if i:
            margin = min(margin, clock - at)
        if at > clock:
            stalled += at - clock
            clock = at
        clock += len(audio) / sr
    audio = np.concatenate(chunks)
    return arrivals[0] * 1000, total / (len(audio) / sr), stalled * 1000, margin * 1000, audio


def snr(engine, voice, text, streamed):
    import torch

    codes = engine.official_codes(text, voice)
    with torch.inference_mode():
        whole, _ = engine._codec.decode([{"audio_codes": codes}])
    whole = np.asarray(whole[0], dtype=np.float32)
    n = min(len(whole), len(streamed))
    noise = np.sum((streamed[:n] - whole[:n]) ** 2)
    return 10 * np.log10(np.sum(whole[:n] ** 2) / max(noise, 1e-12))


def main(args):
    out, predictor = None, "ours"
    if "--out" in args:
        i = args.index("--out")
        out, args = args[i + 1], args[:i] + args[i + 2:]
        os.makedirs(out, exist_ok=True)
    if "--predictor" in args:
        i = args.index("--predictor")
        predictor, args = args[i + 1], args[:i] + args[i + 2:]
    sizes = [tuple(int(n) for n in a.split(",")) for a in args] or [(4,), (1, 2, 4), (1, 1, 2, 4), (1, 1, 2, 2, 4)]
    engine = QwenEngine(os.environ["TTS_MODEL"], predictor=predictor)
    print(f"predictor: {predictor}")
    engine.load()
    engine.warm_up()
    sr = engine.sample_rate
    print(f"{'schedule':>10} {'first audio':>12} {'RTF':>5} {'stalls':>14} {'margin (worst)':>15} {'SNR':>8}")
    for size in sizes:
        engine.chunks = size
        one(engine, "ryan", "Hi, how can I help you today?", sr)      # greedy, and it ends
        runs, worst = [], float("inf")
        for voice, text in SENTENCES:
            for i in range(3):
                first, rtf, stalled, margin, audio = one(engine, voice, text, sr)
                runs.append((first, rtf, stalled, margin))
            worst = min(worst, snr(engine, voice, text, audio))
            if out:
                import soundfile as sf
                sf.write(os.path.join(out, f"chunk{'-'.join(map(str, size))}_{voice}.wav"), audio, sr)
        first, rtf, stalled, margin = (statistics.median(r[i] for r in runs) for i in range(4))
        most, least = max(r[2] for r in runs), min(r[3] for r in runs)
        print(f"{','.join(map(str, size)):>10} {first:>9.0f} ms {rtf:>5.2f} {stalled:>3.0f} ms (max {most:>3.0f})"
              f" {margin:>5.0f} ms ({least:>4.0f}) {worst:>5.1f} dB")


if __name__ == "__main__":
    main(sys.argv[1:])
