"""Spike 04: the Pi-mic takes (recording/pi.html), once labelled (recording/label.py), sorted into the sets train.py
and evaluate.py read. Rerun as takes arrive: each run rebuilds the me-pi sets from transcripts.jsonl.

    python spikes/04-pi-mic/sort_recordings.py              # data/xiao_pai/recordings → data/xiao_pai/{positives,negatives,eval}

  wake, wake-bg   your wake word on the Pi's mic (quiet; a video playing) → positives/me-pi, every 4th clip of each
                  take held out → eval/me-pi-positive. In wake-bg, a clip speech-to-text didn't hear as 小X小X-like
                  (no 小 in it: "还有", "I would have") is probably the video through the mic's gate: left out of
                  both sides, never trained on as either.
  family          someone else's wake word → eval/pi-family-positive only: an unheard voice on the real mic
  near            the core near misses on the Pi's mic → negatives/me-pi-near, every 4th held out → eval/me-pi-near
  room            your room, whole 5-minute files → the last file of each take held out (eval/me-pi-room), the
                  others cut into 3 s clips (extra/me-pi-room: an extra negative set, not a near miss)
"""

import json
import re
import shutil
from pathlib import Path


HERE = Path(__file__).resolve().parents[2]
DATA = HERE / "data/xiao_pai"
REC = DATA / "recordings"
OUT = {k: DATA / v for k, v in {
    "pos": "positives/me-pi", "pos_eval": "eval/me-pi-positive", "family": "eval/pi-family-positive",
    "near": "negatives/me-pi-near", "near_eval": "eval/me-pi-near",
    "room": "extra/me-pi-room", "room_eval": "eval/me-pi-room"}.items()}
HOLD_OUT = 4                                               # every 4th clip of a take


def heard_as_wake(asr: str | None) -> bool:
    return bool(asr) and "小" in asr


rows = [json.loads(line) for line in (REC / "transcripts.jsonl").read_text().splitlines() if line.strip()]
rows = [r for r in rows if r["take"].startswith("pi-")]
for d in OUT.values():
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)

counts, dropped = {k: 0 for k in OUT}, []
by_take: dict[str, list[dict]] = {}
for r in rows:
    by_take.setdefault(r["take"], []).append(r)
for take, clips in sorted(by_take.items()):
    kind = clips[0]["kind"]
    if kind == "room":
        continue
    for i, r in enumerate(clips, 1):
        src = REC / r["clip"]
        if kind == "wake-bg" and not heard_as_wake(r["asr"]):
            dropped.append(f"{src.name}: {r['asr']!r}")
            continue
        if kind == "family":
            dest = "family"
        elif kind in ("wake", "wake-bg"):
            dest = "pos_eval" if i % HOLD_OUT == 0 else "pos"
        elif kind == "near":
            dest = "near_eval" if i % HOLD_OUT == 0 else "near"
        else:
            continue
        shutil.copy2(src, OUT[dest] / src.name)
        counts[dest] += 1

# the room: 5-minute files; the last of each take held out, the rest cut into 3 s clips (near-silence kept: the
# Pi's mic gates to digital zero, and what the model hears between words is that)
import soundfile  # noqa: E402

rooms: dict[str, list[Path]] = {}
for p in sorted(REC.glob("pi-room-*.wav")):
    rooms.setdefault(re.sub(r"-\d{2}$", "", p.stem), []).append(p)   # arecord's split: take-01.wav, take-02.wav…
for files in rooms.values():
    files = sorted(files)
    held = files[-1:] if len(files) > 1 else []
    for p in held:
        shutil.copy2(p, OUT["room_eval"] / p.name)
        counts["room_eval"] += 1
    for p in files[:len(files) - len(held)]:
        x, sr = soundfile.read(p, dtype="int16")
        n = 3 * sr
        for k in range(len(x) // n):
            soundfile.write(OUT["room"] / f"{p.stem}-{k:03d}.wav", x[k * n:(k + 1) * n], sr, subtype="PCM_16")
            counts["room"] += 1

for k, d in OUT.items():
    print(f"{counts[k]:5d}  {d.relative_to(HERE)}")
if dropped:
    print(f"left out ({len(dropped)}): probably the video, not you:\n  " + "\n  ".join(dropped))
