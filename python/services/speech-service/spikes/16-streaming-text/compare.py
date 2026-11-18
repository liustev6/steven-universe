"""Spike 16: one continuous generation per reply instead of one per sentence.

Today the service speaks a reply as separate sentences, each its own generation: the voice's tone,
pace and pitch are drawn afresh for each, each ends as if it were the whole message, and the pause
between two is just the silence the two clips happen to carry. Qwen3-TTS can read text while it
speaks (its streaming mode: the prompt holds the first text token, then each audio frame's input gets
the next one, one token per 80 ms frame, then the end-of-text mark, then pads), so one generation
can speak a whole reply. Fed as the agent writes, waiting for its next token when there isn't one
yet, it sees the same inputs as with the whole text known: this spike's `streaming` is that output.

    sentences   the service today: split as it splits (first clause, sentences), normalized, each
                generated alone (Qwen's non-streaming mode), played back to back
    whole       one generation, all the text in the prompt (non-streaming mode, the whole reply)
    streaming   one generation, the text fed one token per frame (Qwen's streaming mode)

All with the live service's settings: the 1.7B, the steady-pace instruction, normalization, Qwen's
sampling. Measured: duration, pauses (150 ms or more inside the speech), the gaps at the joins
(sentences), how alike its speech-to-text is to the text, wall time. The WAVs are kept: tone is heard.
Runs in the service image with the GPU, the live service stopped (run.sh), the dev code at /src.
"""

from __future__ import annotations

import re
import sys
import time
import wave
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import torch
from qwen_tts import Qwen3TTSModel

from app.stt_engine.engine import QwenSTTEngine
from app.work.normalize import TextNormalizer
from app.work.sentences import Sentences

TTS = "/spike-models/Qwen3-TTS-12Hz-1.7B-CustomVoice"
STT = "/models/Qwen3-ASR-0.6B"
INSTRUCT = "Speak at a steady, natural pace, without long pauses. 用平稳自然的语速说话，不要拖长或停顿。"
OUT = Path("/out")
RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 2

TEXTS = {
    "story-en": ("ryan", """Old Thomas spent forty years in a cramped, sunlit shop at the edge of town, surrounded by the steady, nervous ticking of a thousand clocks. Pendulums swung like quiet pendulums of fate; brass gears gleamed under his jeweler's loupe.
To Thomas, time was not a master to be feared, nor a thief to be cursed. It was a guest he tended with oil, tweezers, and patience.
On a cold Tuesday evening, just as the cathedral bell struck six, the ticking in the shop stopped. All at once. Grandfather clocks, tiny pocket watches, silver mantlepieces—a sudden, heavy silence fell over the room.
Thomas set down his brass tweezers. He did not reach for a screwdriver. He stood up, stretched his stiff joints, and listened to the quiet. For the first time in forty years, the seconds were his own. He locked the wooden front door, turned the brass sign from Open to Closed, and walked out into the crisp night air to see what the rest of the world sounded like without a clock."""),
    "story-zh": ("dylan", "老王在镇子边上开了一家小钟表店，一开就是四十年。店里挂满了大大小小的钟，滴答声从早响到晚。"
                          "对他来说，时间不是要害怕的主人，也不是要咒骂的小偷，而是一位需要用心照料的客人。"
                          "一个寒冷的星期二傍晚，教堂的钟刚敲过六下，店里所有的钟忽然同时停了。"
                          "老王放下镊子，站起来伸了伸腰，静静地听着这份安静。四十年来第一次，每一秒都属于他自己。"),
    "mixed": ("dylan", "好的，我帮你查了一下：明天Seattle会下小雨，最高气温12度，最低7度。"
                       "记得带umbrella，出门前也可以先看看Google Maps上的traffic。"),
}

normalize = TextNormalizer()
tts = Qwen3TTSModel.from_pretrained(TTS, device_map="cuda:0", dtype=torch.bfloat16)
stt = QwenSTTEngine(STT, max_seconds=180)
stt.load()
OUT.mkdir(parents=True, exist_ok=True)


def say(text: str, voice: str, streaming: bool) -> np.ndarray:
    wavs, rate = tts.generate_custom_voice(text=text, speaker=voice, language="Auto", instruct=INSTRUCT,
                                           non_streaming_mode=not streaming)
    assert rate == 24000
    return np.asarray(wavs[0], dtype=np.float32)


def segments(text: str) -> list[str]:
    s = Sentences(first_clause=True)
    return [normalize(p) for p in s.add(text) + s.flush()]


def whole(text: str) -> str:
    return " ".join(segments(text)) if not re.search(r"[一-鿿]", text) else "".join(segments(text))


def pauses(x: np.ndarray, rate: int = 24000) -> list[float]:
    win = rate // 50
    n = len(x) // win
    db = 20 * np.log10(np.sqrt(np.mean(x[: n * win].reshape(n, win) ** 2, axis=1)) + 1e-9)
    voiced = np.flatnonzero(db > -45)
    if len(voiced) == 0:
        return []
    out, run = [], 0
    for v in (db > -45)[voiced[0]:voiced[-1] + 1]:
        if not v:
            run += 1
        elif run:
            out.append(run * 0.02)
            run = 0
    return [p for p in out if p >= 0.15]


def edges(x: np.ndarray, rate: int = 24000) -> tuple[float, float]:
    """Silence before and after the speech in a clip."""
    win = rate // 50
    n = len(x) // win
    db = 20 * np.log10(np.sqrt(np.mean(x[: n * win].reshape(n, win) ** 2, axis=1)) + 1e-9)
    v = np.flatnonzero(db > -45)
    return (v[0] * 0.02, (n - 1 - v[-1]) * 0.02) if len(v) else (0.0, 0.0)


def alike(heard: str, text: str) -> float:
    clean = lambda t: re.sub(r"[\W_]+", "", t.lower())  # noqa: E731
    return SequenceMatcher(None, clean(heard), clean(text)).ratio()


def hear(x: np.ndarray) -> str:
    x16 = np.interp(np.arange(0, len(x) * 2 // 3) * 1.5, np.arange(len(x)), x).astype(np.float32)
    return stt.transcribe(x16).text


def save(name: str, x: np.ndarray) -> None:
    with wave.open(str(OUT / f"{name}.wav"), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(24000)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


say("Warming up.", "ryan", True)
print(f"spike 16: {TTS.split('/')[-1]}, instruction on, {RUNS} runs", flush=True)
for name, (voice, text) in TEXTS.items():
    parts = segments(text)
    print(f"\n== {name} ({voice}): {len(parts)} segments as the service splits it; {len(text)} characters", flush=True)
    for mode in ("sentences", "whole", "streaming"):
        for run in range(1, RUNS + 1):
            t0 = time.perf_counter()
            joins = []
            if mode == "sentences":
                clips = [say(p, voice, streaming=False) for p in parts]
                for a, b in zip(clips, clips[1:]):
                    joins.append(edges(a)[1] + edges(b)[0])
                audio = np.concatenate(clips)
            else:
                audio = say(whole(text), voice, streaming=(mode == "streaming"))
            took = time.perf_counter() - t0
            p = pauses(audio)
            heard = hear(audio)
            save(f"{name}-{mode}-{run}", audio)
            print(f"  {mode:9} {run}  {len(audio) / 24000:5.1f} s (made in {took:4.1f} s)  pauses {len(p):2} "
                  f"(longest {max(p, default=0):4.2f} s, total {sum(p):4.1f} s)  heard {alike(heard, whole(text)):.2f} alike"
                  + (f"  joins: {' '.join(f'{j:.2f}' for j in joins)}" if joins else ""), flush=True)
            if run == 1:
                print(f"            heard: {heard[:160]}", flush=True)
