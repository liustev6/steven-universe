"""Several TTS streams at once, through the batch, as the service will run them (4.5e step 2):

    a generation thread   joins sentences when they arrive, steps the batch, hands each frame to its
                          stream with a CUDA event (on the generation stream)
    a thread per stream   decodes its chunks (1, 1, 2, then 4 frames) on the high-priority decode stream,
                          and a simulated player plays them: first audio, the margin, gaps

Scenarios (sampled, as requests run): 1 stream; 2 at once; 2 with the second arriving 1 s into the
first; 4 arriving 0.5 s apart (slots=4). Each 3 times.

    docker run ... speech-spike python spikes/11-batching/streams.py
"""
import os, queue, statistics, sys, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import numpy as np, torch
from app.tts_engine.chunker import ChunkDecoder
from app.tts_engine.engine import QwenTTSEngine

SENTENCES = [("aiden", "Sure. Tomorrow looks cloudy in the morning, with a little rain in the afternoon."),
             ("vivian", "好的，我现在帮你把客厅的灯关掉，然后把卧室的空调调到二十四度。"),
             ("ryan", "The kitchen lights are on, the door is locked, and the alarm is set for seven."),
             ("serena", "今天西雅图多云，最高气温十二度，晚上可能会下小雨。")]
DONE = object()


def scenario(e, batch, arrivals):
    """`arrivals`: seconds after the start each stream's sentence arrives. Per stream: first audio after
    its arrival (ms), the worst margin (ms), total gaps (ms)."""
    gen, dec = e._generation, e._decoding
    start, outs = time.perf_counter(), [queue.Queue() for _ in arrivals]
    prompts = [e.prompt(t, v) for v, t in SENTENCES[:len(arrivals)]]
    results = [None] * len(arrivals)

    def generate():
        pending, slot_of = list(range(len(arrivals))), {}
        with torch.inference_mode(), torch.cuda.stream(gen):
            while pending or slot_of:
                now = time.perf_counter() - start
                for i in [i for i in pending if arrivals[i] <= now]:
                    slot_of[batch.join(prompts[i])] = i
                    pending.remove(i)
                if not slot_of:
                    time.sleep(0.001); continue
                def hand_over(frames):                  # right after the predictor (batch.step)
                    ready = torch.cuda.Event(); ready.record()
                    for slot, frame in frames.items():
                        outs[slot_of[slot]].put((frame, ready))
                frames, ended = batch.step(on_frames=hand_over)
                for slot in ended:
                    outs[slot_of.pop(slot)].put(DONE)

    def play(i):
        def decode(frames):
            with torch.inference_mode():
                return e._codec.decode([{"audio_codes": torch.stack(list(frames))}])[0][0]
        chunker, arrived = ChunkDecoder(decode, chunks=(1, 1, 2, 4)), []
        with torch.cuda.stream(dec):
            while (item := outs[i].get()) is not DONE:
                frame, ready = item
                torch.cuda.current_stream().wait_event(ready)
                if (audio := chunker.add(frame)) is not None:
                    arrived.append((time.perf_counter() - start, len(audio)))
            if (audio := chunker.flush()) is not None:
                arrived.append((time.perf_counter() - start, len(audio)))
        clock, margin, gaps = arrived[0][0], float("inf"), 0.0
        for k, (at, n) in enumerate(arrived):
            if k:
                margin = min(margin, clock - at)
            if at > clock:
                gaps += at - clock; clock = at
            clock += n / 24000
        results[i] = ((arrived[0][0] - arrivals[i]) * 1000, margin * 1000, gaps * 1000)

    threads = [threading.Thread(target=generate)] + [threading.Thread(target=play, args=(i,)) for i in range(len(arrivals))]
    [t.start() for t in threads]; [t.join() for t in threads]
    return results


e = QwenTTSEngine(os.environ["TTS_MODEL"], predictor="ours-fused", talker="ours-graph"); e.load(); e.warm_up()
for slots, name, arrivals in [(2, "1 stream", [0]), (2, "2 at once", [0, 0]), (2, "2, the second 1 s later", [0, 1.0]),
                              (4, "4, 0.5 s apart", [0, 0.5, 1.0, 1.5])]:
    batch = e.batch(slots=slots)
    scenario(e, batch, arrivals)                                       # warm: graphs captured
    runs = [scenario(e, batch, arrivals) for _ in range(3)]
    print(f"{name} (slots={slots}):")
    for i in range(len(arrivals)):
        first = statistics.median(r[i][0] for r in runs)
        margin = min(r[i][1] for r in runs)
        gaps = max(r[i][2] for r in runs)
        print(f"  stream {i + 1}: first audio {first:4.0f} ms, worst margin {margin:4.0f} ms, gaps {gaps:4.0f} ms", flush=True)
