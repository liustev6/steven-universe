"""Shared by the spike's scripts: the model's path, the sentences, timing and reporting."""
import json, statistics, time

MODEL = "/models/Qwen3-TTS-12Hz-0.6B-CustomVoice"
SR_FRAME = 1920            # samples per codec frame at 24 kHz (12.5 frames a second)
SENTENCES = [              # (language, speaker, text): short voice-style sentences, then two longer ones
    ("English", "Ryan",   "Hi, how can I help you today?"),
    ("English", "Aiden",  "The kitchen lights are on, and the door is locked."),
    ("Chinese", "Vivian", "好的，我现在帮你把客厅的灯关掉。"),
    ("Chinese", "Serena", "今天西雅图多云，最高气温十二度。"),
    ("English", "Ryan",   "Sure. Tomorrow looks cloudy in the morning, with a little rain in the afternoon, so take an umbrella if you go out after lunch."),
    ("Chinese", "Vivian", "明天早上会有点冷，记得多穿一件外套；下午可能会下小雨，出门的话最好带把伞。"),
]

class Clock:
    def __init__(self): self.t0 = time.perf_counter()
    def ms(self): return (time.perf_counter() - self.t0) * 1000

def report(name, rows):
    """rows: dicts with audio_s, total_ms and optionally first_ms."""
    rtf = [r["total_ms"] / 1000 / r["audio_s"] for r in rows]
    out = {"run": name, "sentences": len(rows),
           "rtf_median": round(statistics.median(rtf), 3), "rtf_max": round(max(rtf), 3),
           "audio_s_total": round(sum(r["audio_s"] for r in rows), 1)}
    if all("first_ms" in r for r in rows):
        firsts = [r["first_ms"] for r in rows]
        out["first_packet_ms_median"] = round(statistics.median(firsts))
        out["first_packet_ms_max"] = round(max(firsts))
    print("RESULT " + json.dumps(out), flush=True)
    for r in rows:
        print("   ", json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()}), flush=True)
