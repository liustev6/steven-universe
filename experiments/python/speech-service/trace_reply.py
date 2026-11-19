"""A real reply traced through the streaming engine (T19), step by step (from spike 16).

A reply arrives as an agent streams it (small deltas at the agent's pace), the session's splitter
cuts it into pieces, each piece is normalized and fed to the reply's row as its tokens; the batch
steps in real time. Logged: each delta, each piece handed over (and its tokens), each step (a frame,
or the row waiting for text), the end mark, the end. The frames are decoded and kept as a WAV.
Runs in the service image with the GPU (on the playground, speech-service stopped: docs/playground.md),
the service's code at /src (it imports the engine) and this folder at /exp:

    PYTHONPATH=/src python /exp/trace_reply.py /models/Qwen3-TTS-12Hz-1.7B-CustomVoice dylan /out/trace.wav
"""

import sys
import time
import wave

import numpy as np
import torch

from app.tts_engine.engine import QwenTTSEngine
from app.work.normalize import TextNormalizer
from app.work.reply_text import QwenReplyText, joined
from app.work.sentences import Sentences

MODEL, VOICE = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "dylan"
WAV = sys.argv[3] if len(sys.argv) > 3 else "/tmp/trace.wav"
INSTRUCT = "Speak at a steady, natural pace, without long pauses. 用平稳自然的语速说话，不要拖长或停顿。"
REPLY = "好的，我帮你查了一下：明天Seattle会下小雨，最高气温12度，最低7度。记得带umbrella，出门前也可以先看看Google Maps上的traffic。"
DELTA_CHARS, DELTA_MS = 4, 60          # the agent: ~4 characters every 60 ms (~65 characters a second)

engine = QwenTTSEngine(MODEL, predictor="ours-fused", talker="ours-graph", instruct=INSTRUCT)
engine.load()
batch = engine.batch(slots=2)
text = QwenReplyText(engine, TextNormalizer())
tok = engine._tts._tokenize_texts


def tokens(s: str) -> int:
    return int(tok([s])[0].numel())


# warm the graphs (the service does this at start)
w = batch.join(text.prompt("Warming up.", VOICE, None))
batch.finish(w, text.end())
while batch.state[w] is not None:
    batch.step()
torch.cuda.synchronize()

deltas = [REPLY[i:i + DELTA_CHARS] for i in range(0, len(REPLY), DELTA_CHARS)]
splitter, said, slot, frames, log = Sentences(first_clause=True), "", None, [], []
t0 = time.perf_counter()
ms = lambda: (time.perf_counter() - t0) * 1000  # noqa: E731
next_delta, waiting_since, first_frame = 0, None, None


def note(what: str) -> None:
    log.append(f"{ms():7.0f} ms  {what}")


def hand_over(pieces: list[str]) -> None:
    global slot, said
    for p in pieces:
        normalized = text.normalize(p)
        if slot is None:
            slot = batch.join(text.prompt(p, VOICE, None))
            n = tokens(normalized)
            note(f"piece 1 {p!r} → prompt (its first token) + {n - 1} text tokens queued; the row starts")
            said = p
        else:
            batch.feed(slot, text.more(p, said), limit=text.limit(said + " " + p))
            note(f"piece {p!r} → {tokens(joined(said, normalized))} more text tokens queued")
            said += " " + p


ended_text, done = False, False
while not done:
    # the agent's deltas, at its own pace
    while next_delta < len(deltas) and ms() >= next_delta * DELTA_MS:
        note(f"agent delta {deltas[next_delta]!r}")
        hand_over(splitter.add(deltas[next_delta]))
        next_delta += 1
    if next_delta == len(deltas) and not ended_text:
        hand_over(splitter.flush())
        batch.finish(slot, text.end())
        note("the reply is complete → the end mark queued")
        ended_text = True
    if slot is None or slot not in batch.ready():
        if slot is not None and waiting_since is None:
            waiting_since = ms()
        time.sleep(0.002)
        continue
    if waiting_since is not None:
        note(f"   (the row waited {ms() - waiting_since:.0f} ms for text)")
        waiting_since = None
    s = batch.state[slot]
    queued = s.trailing.shape[1] - (s.made - 1)
    out, ended = batch.step()
    torch.cuda.synchronize()
    if slot in out:
        frames.append(out[slot])
        if first_frame is None:
            first_frame = ms()
        if len(frames) <= 3 or len(frames) % 25 == 0 or queued <= 1:
            note(f"step: frame {len(frames)} made ({len(frames) * 80} ms of audio), "
                 f"{max(queued - 1, 0)} text tokens still queued")
    if slot in ended:
        note(f"the model ended the speech: {len(frames)} frames = {len(frames) * 0.08:.1f} s of audio")
        done = True

print("\n".join(log))
print(f"\nfirst frame at {first_frame:.0f} ms; {len(frames)} frames generated in {ms():.0f} ms "
      f"(RTF {ms() / (len(frames) * 80):.2f})")
with torch.inference_mode():
    audio = engine._codec.decode([{"audio_codes": torch.stack(frames)}])[0][0]
audio = np.asarray(audio, dtype=np.float32)
with wave.open(WAV, "wb") as f:
    f.setnchannels(1), f.setsampwidth(2), f.setframerate(24000)
    f.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
print(f"saved {WAV}")
