"""Spike 01: the TTS's positives through speech-to-text: a clip that doesn't say xiao-pai twice (any tones:
小派, 校牌, 小排 are all the same sounds) is moved aside to `rejected/`, as a broken take. Run with
speech-service's venv, its test client running:

    ../../../python/services/speech-service/.venv/bin/python spikes/01-xiao-pai/check_clips.py data/xiao_pai/positives/tts
"""

import asyncio
import json
import sys
import wave
from pathlib import Path

import websockets

XIAO, PAI = set("小校晓筱孝笑效肖萧消销箫潇"), set("派排牌拍湃徘哌")


def says_it(text: str) -> bool:
    hanzi = [c for c in text if "一" <= c <= "鿿"]
    return len(hanzi) == 4 and hanzi[0] in XIAO and hanzi[2] in XIAO and hanzi[1] in PAI and hanzi[3] in PAI


async def hear(path: Path) -> str:
    with wave.open(str(path)) as w:
        pcm = w.readframes(w.getnframes())
    async with websockets.connect("ws://localhost:8765/v1/realtime/transcription?language=Chinese", max_size=None) as ws:
        e = json.loads(await ws.recv())
        while e["type"] == "queued":
            e = json.loads(await ws.recv())
        for off in range(0, len(pcm), 3200):
            await ws.send(pcm[off:off + 3200])
        await ws.send(json.dumps({"type": "commit"}))
        async for m in ws:
            e = json.loads(m)
            if e["type"] in ("final", "error"):
                return e.get("text", "")
    return ""


async def main():
    folder = Path(sys.argv[1])
    files = sorted(folder.glob("*.wav"))
    rejected = folder.parent / f"{folder.name}-rejected"
    queue, results = asyncio.Queue(), {}
    for f in files:
        queue.put_nowait(f)

    async def worker():
        while not queue.empty():
            f = queue.get_nowait()
            try:
                results[f] = await hear(f)
            except Exception as e:
                results[f] = f"(error {e})"

    await asyncio.gather(*(worker() for _ in range(2)))
    bad = [f for f in files if not says_it(results[f])]
    rejected.mkdir(exist_ok=True)
    for f in bad:
        f.rename(rejected / f.name)
    (rejected / "transcripts.json").write_text(json.dumps({f.name: results[f] for f in bad}, ensure_ascii=False, indent=1))
    print(f"{folder}: {len(files) - len(bad)} kept, {len(bad)} moved to {rejected.name}")
    for f in bad[:15]:
        print(f"   {f.name:24s} {results[f]!r}")


asyncio.run(main())
