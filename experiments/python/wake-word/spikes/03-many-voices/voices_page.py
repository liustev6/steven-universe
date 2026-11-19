"""Spike 03: a local listening page for the generated voices (data/datasets/full/generated/spike-03/index.html): each voice
with who it is (a designed voice's description; a cloned voice's speaker and its AISHELL-3 reference
recording), then what it said, by kind. Audio loads on play.

    .venv/bin/python spikes/03-many-voices/voices_page.py data/datasets/full/generated/spike-03
"""

import collections
import html
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
refs_dir = Path("data/datasets/full/aishell3-refs")
rows = {}
for line in open(root / "manifest.jsonl"):
    r = json.loads(line)
    if (root / r["clip"]).exists():
        rows[r["clip"]] = r                                   # a voice made again: the last line per clip counts
voices = collections.defaultdict(list)
for r in rows.values():
    voices[r["voice"]].append(r)
refs = {json.loads(line)["speaker"]: json.loads(line) for line in open(refs_dir / "refs.jsonl")}
css = """body{font:14px system-ui;margin:20px;max-width:1100px;background:#fafaf9;color:#1c1917}
section{background:#fff;border:1px solid #e7e5e4;border-radius:12px;padding:12px 16px;margin:12px 0}
h2{font-size:16px;margin:0 0 4px}.meta{color:#78716c;font-size:13px;margin-bottom:8px}
.k{font-size:12px;text-transform:uppercase;color:#78716c;margin:8px 0 4px}.row{display:flex;align-items:center;gap:8px;margin:2px 0}
audio{height:28px;width:260px}nav a{margin-right:8px}
@media(prefers-color-scheme:dark){body{background:#1c1917;color:#f5f5f4}section{background:#292524;border-color:#44403c}}"""
designed = sorted(v for v in voices if v.startswith("d"))
cloned = sorted(v for v in voices if v.startswith("c"))
parts = [f'<!doctype html><meta charset="utf-8"><title>Spike 03 voices</title><style>{css}</style>',
         f"<h1>Spike 03: the voices</h1><p>{len(designed)} designed, {len(cloned)} cloned; {len(rows)} clips. "
         "Each voice: who it is, then what it said. Audio loads when you press play.</p>",
         "<nav><b>designed</b> " + " ".join(f'<a href="#{v}">{v}</a>' for v in designed) + "</nav>",
         "<nav><b>cloned</b> " + " ".join(f'<a href="#{v}">{v}</a>' for v in cloned) + "</nav>"]
for v in designed + cloned:
    clips = sorted(voices[v], key=lambda r: r["clip"])
    r0 = clips[0]
    who = f'{html.escape(str(r0.get("gender")))} · {html.escape(str(r0.get("age")))} · {html.escape(str(r0.get("accent")))}'
    ref = ""
    if r0["source"] == "design":
        who = html.escape(r0.get("instruct") or "")
    else:
        spk = v[1:]
        rel = Path("../..") / refs_dir.relative_to("data") / f"{spk}.wav"
        ref = (f'<div class="row">reference ({html.escape(refs.get(spk, {}).get("text", ""))}): '
               f'<audio controls preload="none" src="{rel}"></audio></div>')
    parts.append(f'<section id="{v}"><h2>{v} · {r0["source"]}</h2><div class="meta">{who}</div>{ref}')
    for kind, title in (("positive", "the wake word"), ("near_core", "near misses (core)"), ("everyday", "everyday")):
        parts.append(f'<div class="k">{title}</div>')
        parts += [f'<div class="row"><audio controls preload="none" src="{r["clip"]}"></audio>{html.escape(r["text"])}</div>'
                  for r in clips if r["kind"] == kind]
    parts.append("</section>")
(root / "index.html").write_text("\n".join(parts))
print(f"{len(designed)} designed + {len(cloned)} cloned voices, {len(rows)} clips → {root / 'index.html'}")
