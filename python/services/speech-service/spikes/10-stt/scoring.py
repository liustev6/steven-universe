# tokens() and error_rate() copied from pi-server/spikes/01-stt-on-pi/score.py (the Pi spike), unchanged:
# the same normalization (Traditional -> Simplified, punctuation and fillers removed), the same error rates.
import json
import re
import sys
import unicodedata

import jiwer
from opencc import OpenCC

T2S = OpenCC("t2s")
FILLERS = {"um", "uh", "uhm", "umm", "hmm", "mm", "mhm", "er", "erm", "ah", "eh", "嗯", "呃"}
CJK = re.compile(r"[㐀-䶿一-鿿]")


def tokens(text: str) -> list[str]:
    text = T2S.convert(unicodedata.normalize("NFKC", text)).lower()
    text = "".join(c if not unicodedata.category(c).startswith(("P", "S")) or c == "'" else " " for c in text)
    out = []
    for word in text.split():
        # split a run like "no我专业" into "no", "我", "专", "业"
        for piece in re.findall(r"[㐀-䶿一-鿿]|[^㐀-䶿一-鿿]+", word):
            piece = piece.strip("'")
            if piece and piece not in FILLERS:
                out.append(piece)
    return out


def error_rate(rows: list[dict]) -> float | None:
    pairs = [(" ".join(tokens(r["ref"])), " ".join(tokens(r["text"]))) for r in rows]
    pairs = [(ref, hyp) for ref, hyp in pairs if ref]
    if not pairs:
        return None
    o = jiwer.process_words([p[0] for p in pairs], [p[1] if p[1] else "∅" for p in pairs])
    return (o.substitutions + o.deletions + o.insertions) / sum(len(p[0].split()) for p in pairs)


