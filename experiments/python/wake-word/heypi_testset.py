""""Hey Pi"'s sets in the common test (evaluate.py): the 51 held-out voices' clips (gen_voices.py --lines heypi
--out heypi-test) into datasets/processed/test/heypi-tts-03 and heypi-tts-03-near (set-asides: 00/rejected). Each positive goes through speech-to-text first, as
the 小派小派 test clips did (spike 01's check_clips.py): a take not heard as "hey" + "pi" (any spelling) is set aside,
since a voice that can't say it isn't a miss of the model's. Run on the Mac with speech-service's venv, its test
client running:

    ../../../python/services/speech-service/.venv/bin/python heypi_testset.py data/datasets/full/generated/heypi-test data
"""

import asyncio
import json
import re
import shutil
import sys
from pathlib import Path

import websockets

SRC, DATA = Path(sys.argv[1]), Path(sys.argv[2])
# "hey" then "pi", however speech-to-text spells the sounds (any tones, as 小派小派's check): 嘿，屁 and 嗨皮 are "Hey Pi"
SAID = re.compile(r"(hey|hay|hei|hai|hi|嘿|黑|嗨|海)\W*(p\W?i|pie|py|pai|派|拍|排|牌|π|屁|皮|匹|披|批)", re.I)


async def hear(pcm: bytes) -> str:
    async with websockets.connect("ws://localhost:8765/v1/realtime/transcription", max_size=None) as ws:
        e = json.loads(await ws.recv())
        while e["type"] == "queued":
            e = json.loads(await ws.recv())
        for off in range(0, len(pcm), 3200):
            await ws.send(pcm[off:off + 3200])
        await ws.send(json.dumps({"type": "commit"}))
        async for m in ws:
            e = json.loads(m)
            if e["type"] == "final":
                return e.get("text", "")
            if e["type"] == "error":
                raise RuntimeError(e)
    return ""


async def main():
    t = DATA / "datasets/processed/test"
    pos, near, aside = t / "heypi-tts-03", t / "heypi-tts-03-near", DATA / "00/rejected"
    for d in (pos, near, aside):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
    kept = rejected = 0
    for f in sorted(SRC.glob("*/*.wav")):
        if "-near-" in f.name:
            shutil.copy2(f, near / f.name)
            continue
        text = await hear(f.read_bytes()[44:])
        ok = bool(SAID.search(text))
        shutil.copy2(f, (pos if ok else aside) / f.name)
        kept, rejected = kept + ok, rejected + (not ok)
        if not ok:
            print(f"  set aside {f.name}: heard {text!r}")
    print(f"positives: {kept} kept, {rejected} set aside ({rejected / max(1, kept + rejected):.0%}); "
          f"near misses: {len(list(near.glob('*.wav')))}")


asyncio.run(main())
