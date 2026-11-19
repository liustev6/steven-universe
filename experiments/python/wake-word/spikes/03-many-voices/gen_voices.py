"""Spike 03: many voices saying 小派小派 and what isn't it, on the playground's GPU (CT 110; speech-service on
CT 108 stopped by hand meanwhile: docs/playground.md). Two sources of voices, Qwen3-TTS 1.7B:
  design  VoiceDesign: a voice from a description (gender, age, timbre, accent, manner), ~150 of them
  clone   Base: a voice cloned from a reference clip (AISHELL-3's speakers, refs.jsonl)
Each voice says, in one batch per kind (one timbre): the wake word's 7 written forms twice (positive),
all 30 near_miss_core lines (always, every voice), and 12 everyday lines from the bank (everyday). 16 kHz
16-bit mono WAVs, silence trimmed to 150 ms; out/manifest.jsonl lists every clip. A voice already done
is skipped (out/<voice>/done), so a rerun resumes.

    docker run --rm --gpus all -v /srv/experiments/wake-word:/exp \
        -v /srv/src/steven-universe/experiments/python/wake-word:/code:ro --entrypoint python SPEECH_BASE \
        /code/spikes/03-many-voices/gen_voices.py --source design|clone [--limit N]

/exp: models/ (VoiceDesign, Base), data/datasets/aishell3-refs/ (fetch_refs.py), out/spike-03/ (written).
"""

import argparse
import json
import random
import re
import time
import wave
from pathlib import Path

import numpy as np
import torch
import yaml

ap = argparse.ArgumentParser()
ap.add_argument("--source", choices=["design", "clone"], required=True)
ap.add_argument("--limit", type=int, default=0, help="only the first N voices (a speed test)")
ap.add_argument("--exp", default="/exp", help="models/ and the reference clips under it; out/spike-03 written")
ap.add_argument("--bank", default="/code/phrases/xiao_pai_bank.yaml")
args = ap.parse_args()
EXP = Path(args.exp)
OUT = EXP / "out/spike-03"
REFS = EXP / "data/datasets/aishell3-refs"
BANK = yaml.safe_load(open(args.bank))
WAKE = ["小派小派", "小派小派。", "小派小派！", "小派小派？", "小派，小派。", "小派，小派！", "小派小派，"]
CJK = re.compile(r"[一-鿿]")


# ---- the voices -----------------------------------------------------------------------------------------------
def designed() -> list[dict]:
    """~150 descriptions, a seeded spread over who might be in a home."""
    ages = {"male": ["一个七八岁的小男孩", "一个十几岁的少年", "一位二十多岁的年轻男性", "一位三四十岁的中年男性", "一位六七十岁的老爷爷"],
            "female": ["一个七八岁的小女孩", "一个十几岁的少女", "一位二十多岁的年轻女性", "一位三四十岁的中年女性", "一位六七十岁的老奶奶"]}
    timbres = ["低沉", "清亮", "沙哑", "柔和", "浑厚", "尖细", "带一点鼻音", "有气声", "温暖", "干脆"]
    accents = ["标准普通话", "北京口音的普通话", "东北口音的普通话", "四川口音的普通话", "广东口音的普通话",
               "台湾口音的普通话", "上海口音的普通话", "带英语口音的普通话"]
    manners = ["语速稍快", "语速较慢", "懒洋洋的", "很有精神", "轻声细语", "像在大声叫人", "平静自然", "有点着急"]
    rng = random.Random(3)
    combos = [(g, a, t, c, m) for g in ages for a in ages[g] for t in timbres for c in accents for m in manners]
    rng.shuffle(combos)
    voices, seen = [], {}
    for g, a, t, c, m in combos:
        key = (g, a, c)                                    # spread: at most 2 voices share gender, age and accent
        if seen.get(key, 0) >= 2:
            continue
        seen[key] = seen.get(key, 0) + 1
        voices.append({"voice": f"d{len(voices):03d}", "source": "design", "gender": g, "age": a, "accent": c,
                       "instruct": f"{a}，声音{t}，说{c}，{m}。"})
        if len(voices) == 150:
            break
    return voices


def cloned() -> list[dict]:
    rows = [json.loads(line) for line in open(REFS / "refs.jsonl")]
    return [{"voice": f"c{r['speaker']}", "source": "clone", "gender": r.get("gender"), "age": r.get("age"),
             "accent": r.get("accent"), "ref_audio": str(REFS / f"{r['speaker']}.wav"), "ref_text": r["text"]}
            for r in rows]


# ---- what each voice says -------------------------------------------------------------------------------------------
def script(voice: str) -> list[tuple[str, str]]:
    rng = random.Random(voice)
    lines = [("positive", t) for t in WAKE * 2]
    lines += [("near_core", t) for t in BANK["near_miss_core"]]
    lines += [("everyday", t) for t in rng.sample(BANK["zh"], 8) + rng.sample(BANK["mixed"], 2) + rng.sample(BANK["en"], 2)]
    return lines


def to16k(wav: np.ndarray, sr: int) -> np.ndarray:
    try:
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(sr, 16000)
        y = resample_poly(wav, 16000 // g, sr // g)
    except ImportError:
        y = np.interp(np.arange(0, len(wav) * 16000 // sr) * sr / 16000, np.arange(len(wav)), wav)
    loud = np.flatnonzero(np.abs(y) > 10 ** (-45 / 20))
    if len(loud):
        y = y[max(0, loud[0] - 2400): loud[-1] + 2400]                 # 150 ms each side
    return (np.clip(y, -1, 1) * 32767).astype(np.int16)


def save(path: Path, pcm: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000), w.writeframes(pcm.tobytes())


def main():
    from qwen_tts import Qwen3TTSModel

    name = "Qwen3-TTS-12Hz-1.7B-VoiceDesign" if args.source == "design" else "Qwen3-TTS-12Hz-1.7B-Base"
    t0 = time.monotonic()
    model = Qwen3TTSModel.from_pretrained(str(EXP / "models" / name), device_map="cuda:0", dtype=torch.bfloat16,
                                          attn_implementation="sdpa")
    print(f"{name} loaded in {time.monotonic() - t0:.0f} s", flush=True)
    voices = designed() if args.source == "design" else cloned()
    if args.limit:
        voices = voices[: args.limit]
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = (OUT / "manifest.jsonl").open("a")
    clips = seconds = 0
    for v in voices:
        folder = OUT / v["voice"]
        if (folder / "done").exists():
            continue
        folder.mkdir(exist_ok=True)
        t = time.monotonic()
        lines = script(v["voice"])
        prompt = model.create_voice_clone_prompt(v["ref_audio"], v["ref_text"]) if args.source == "clone" else None
        for batch_start in range(0, len(lines), 14):          # 14 at a time: fits beside the model
            batch = lines[batch_start: batch_start + 14]
            texts = [t_ for _, t_ in batch]
            langs = ["Chinese" if CJK.search(t_) else "English" for t_ in texts]
            if args.source == "design":
                wavs, sr = model.generate_voice_design(text=texts, instruct=[v["instruct"]] * len(texts), language=langs)
            else:
                wavs, sr = model.generate_voice_clone(text=texts, language=langs, voice_clone_prompt=prompt * len(texts))
            for k, ((kind, text), wav) in enumerate(zip(batch, wavs), batch_start):
                pcm = to16k(np.asarray(wav, dtype=np.float32).reshape(-1), sr)
                path = folder / f"{v['voice']}-{kind}-{k:02d}.wav"
                save(path, pcm)
                seconds += len(pcm) / 16000
                clips += 1
                manifest.write(json.dumps({"clip": str(path.relative_to(OUT)), "voice": v["voice"], "source": v["source"],
                                           "kind": kind, "text": text, "gender": v.get("gender"), "age": v.get("age"),
                                           "accent": v.get("accent"), "instruct": v.get("instruct"),
                                           "ref_text": v.get("ref_text")}, ensure_ascii=False) + "\n")
        manifest.flush()
        (folder / "done").touch()
        print(f"{v['voice']}: {len(lines)} clips in {time.monotonic() - t:.0f} s "
              f"({clips} clips, {seconds / 60:.1f} min of audio, {(time.monotonic() - t0) / 60:.1f} min so far)", flush=True)


main()
