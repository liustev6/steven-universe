"""Everything at once, in real time (speech-service 4.5e step 4): N TTS streams and N STT streams,
each model's batch driven by its own work thread, the GPU shared through CUDA streams (spike 10).

    TTS   a work thread joins sentences as they arrive and steps the TTSBatch, handing each frame over
          (an event) right after the predictor; a thread per stream decodes its chunks on the
          high-priority stream into a simulated player: first audio, worst margin, gaps
    STT   a feeder per stream sends its clip 100 ms at a time in real time (as the Pi would), then its
          end; the work thread ticks the STTBatch every 100 ms: final latency (end of audio → final),
          passes, and how alike the final is to what was said

Clips: the TTS's own speech (made first, not timed), so what was said is known. Each scenario 3 times.

    docker run ... speech-spike python spikes/12-two-plus-two/run.py
"""
import os, queue, re, statistics, sys, threading, time
from difflib import SequenceMatcher
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import numpy as np, torch
from scipy.signal import resample_poly
from app.stt_engine.engine import QwenSTTEngine
from app.tts_engine.chunker import ChunkDecoder
from app.tts_engine.engine import QwenTTSEngine

SPEAK = [("aiden", "Sure. Tomorrow looks cloudy in the morning, with a little rain in the afternoon."),
         ("vivian", "好的，我现在帮你把客厅的灯关掉，然后把卧室的空调调到二十四度。"),
         ("ryan", "The kitchen lights are on, the door is locked, and the alarm is set for seven."),
         ("serena", "今天西雅图多云，最高气温十二度，晚上可能会下小雨。")]
HEAR = [("ryan", "Turn off the kitchen lights and lock the front door, please."),
        ("vivian", "明天早上七点叫我起床，然后告诉我天气怎么样。"),
        ("aiden", "What's on my calendar for this afternoon, and do I have time for a run?"),
        ("serena", "帮我把客厅的温度调到二十二度，再放一点轻音乐。")]
DONE = object()
same = lambda a, b: SequenceMatcher(None, *(re.sub(r"[\W_]+", "", t.lower()) for t in (a, b))).ratio()  # noqa: E731


def scenario(tts, stt, tts_batch, stt_batch, n, tts_at, stt_at, clips):
    start = time.perf_counter()
    now = lambda: time.perf_counter() - start  # noqa: E731
    prompts = [tts.prompt(t, v) for v, t in SPEAK[:n]]
    outs = [queue.Queue() for _ in range(n)]
    tts_res, stt_res, audio_in = [None] * n, [None] * n, queue.Queue()

    def tts_work():
        pending, slot_of = list(range(n)), {}
        with torch.inference_mode(), torch.cuda.stream(tts._generation):
            while pending or slot_of:
                for i in [i for i in pending if tts_at[i] <= now()]:
                    slot_of[tts_batch.join(prompts[i])] = i; pending.remove(i)
                if not slot_of:
                    time.sleep(0.001); continue

                def hand_over(frames):
                    ready = torch.cuda.Event(); ready.record()
                    for slot, frame in frames.items():
                        outs[slot_of[slot]].put((frame, ready))
                _, ended = tts_batch.step(on_frames=hand_over)
                for slot in ended:
                    outs[slot_of.pop(slot)].put(DONE)

    def play(i):
        def decode(frames):
            with torch.inference_mode():
                return tts._codec.decode([{"audio_codes": torch.stack(list(frames))}])[0][0]
        chunker, arrived = ChunkDecoder(decode, chunks=(1, 1, 2, 4)), []
        with torch.cuda.stream(tts._decoding):
            while (item := outs[i].get()) is not DONE:
                frame, ready = item
                torch.cuda.current_stream().wait_event(ready)
                if (audio := chunker.add(frame)) is not None:
                    arrived.append((now(), len(audio)))
            if (audio := chunker.flush()) is not None:
                arrived.append((now(), len(audio)))
        clock, margin, gaps = arrived[0][0], float("inf"), 0.0
        for k, (at, m) in enumerate(arrived):
            if k:
                margin = min(margin, clock - at)
            if at > clock:
                gaps += at - clock; clock = at
            clock += m / 24000
        tts_res[i] = ((arrived[0][0] - tts_at[i]) * 1000, margin * 1000, gaps * 1000)

    def feed(i):
        time.sleep(max(0.0, stt_at[i] - now()))
        clip, step = clips[i], 1600
        for k, off in enumerate(range(0, len(clip), step)):
            time.sleep(max(0.0, stt_at[i] + off / 16000 - now()))
            audio_in.put((i, clip[off:off + step]))
        time.sleep(max(0.0, stt_at[i] + len(clip) / 16000 - now()))
        audio_in.put((i, None))                                  # the end of speech

    def stt_work():
        slot_of, ended_at, done, passes0 = {}, {}, 0, stt_batch.passes
        while done < n:
            deadline = now() + 0.1
            while now() < deadline:
                try:
                    i, pcm = audio_in.get(timeout=max(0.0, deadline - now()))
                except queue.Empty:
                    break
                if i not in slot_of.values():
                    slot_of[stt_batch.join()] = i
                slot = next(s for s, x in slot_of.items() if x == i)
                if pcm is None:
                    stt_batch.end(slot); ended_at[i] = now()
                else:
                    stt_batch.add(slot, pcm)
            for slot, t in stt_batch.tick().items():
                if t.final:
                    i = slot_of.pop(slot)
                    stt_res[i] = ((now() - ended_at[i]) * 1000, same(t.text, HEAR[i][1])); done += 1
        stt_res.append(stt_batch.passes - passes0)

    threads = ([threading.Thread(target=tts_work), threading.Thread(target=stt_work)]
               + [threading.Thread(target=play, args=(i,)) for i in range(n)]
               + [threading.Thread(target=feed, args=(i,)) for i in range(n)])
    [t.start() for t in threads]; [t.join() for t in threads]
    return tts_res, stt_res[:n], stt_res[n]


tts = QwenTTSEngine(os.environ["TTS_MODEL"], predictor="ours-fused", talker="ours-graph"); tts.load(); tts.warm_up()
stt = QwenSTTEngine(os.environ["STT_MODEL"]); stt.load(); stt.warm_up()
clips = [resample_poly(np.concatenate(list(tts.stream(t, v, sample=False))), 2, 3).astype(np.float32) for v, t in HEAR]
print("clips (s):", [round(len(c) / 16000, 1) for c in clips], flush=True)
for n, name, tts_at, stt_at in [(2, "2 + 2, staggered", [0, 0.7], [0.3, 1.0]),
                                (2, "2 + 2, all at once", [0, 0], [0, 0]),
                                (4, "4 + 4, staggered", [0, 0.5, 1.0, 1.5], [0.2, 0.7, 1.2, 1.7])]:
    tb, sb = tts.batch(slots=n), stt.batch(slots=n)
    scenario(tts, stt, tb, sb, n, tts_at, stt_at, clips)       # warm: graphs captured
    runs = [scenario(tts, stt, tb, sb, n, tts_at, stt_at, clips) for _ in range(3)]
    print(f"{name} (slots={n} each), VRAM peak {torch.cuda.max_memory_allocated() / 2**30:.2f} GB:")
    for i in range(n):
        first = statistics.median(r[0][i][0] for r in runs); margin = min(r[0][i][1] for r in runs)
        gaps = max(r[0][i][2] for r in runs)
        print(f"  TTS {i + 1}: first audio {first:4.0f} ms, worst margin {margin:4.0f} ms, gaps {gaps:4.0f} ms")
    for i in range(n):
        lat = statistics.median(r[1][i][0] for r in runs); worst = max(r[1][i][0] for r in runs)
        alike = min(r[1][i][1] for r in runs)
        print(f"  STT {i + 1}: final {lat:4.0f} ms after the end (worst {worst:4.0f}), heard {alike:.2f} alike")
    print(f"  STT batched passes per run: {[r[2] for r in runs]}", flush=True)
