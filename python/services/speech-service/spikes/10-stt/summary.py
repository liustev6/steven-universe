"""The table again, from asr_bench.py's per-clip results: python spikes/10-stt/summary.py /out/*.jsonl"""
import json, os, re, statistics, sys
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
from scoring import error_rate

pct = lambda x: "  -  " if x is None else f"{100 * x:5.1f}"
print(f"{'mode':<15}{'en':>6}{'zh':>6}{'mix':>6} | {'en*':>6}{'zh*':>6} | {'RTF':>6} {'final p50/p90 ms':>17} {'chunk max':>10} {'1st partial s':>14}")
for path in sys.argv[1:]:
    rows = [json.loads(l) for l in open(path)]
    by = {l: [x for x in rows if x["lang"] == l] for l in ("en", "zh", "mix")}
    nodig = {l: [x for x in xs if not re.search(r"\d", x["ref"])] for l, xs in by.items()}
    finals = [x["final_ms"] for x in rows]
    rtf = sum(x["compute_ms"] for x in rows) / 1000 / sum(x["dur"] for x in rows)
    cmax = max((x.get("chunk_ms_max", 0) for x in rows), default=0)
    fp = [x["first_partial_s"] for x in rows if x.get("first_partial_s") is not None]
    print(f"{os.path.basename(path)[:-6]:<15}{pct(error_rate(by['en'])):>6}{pct(error_rate(by['zh'])):>6}{pct(error_rate(by['mix'])):>6} | "
          f"{pct(error_rate(nodig['en'])):>6}{pct(error_rate(nodig['zh'])):>6} | {rtf:>6.3f} "
          f"{statistics.median(finals):>8.0f}/{np.percentile(finals, 90):<8.0f} {(f'{cmax:.0f}' if cmax else '-'):>10} "
          f"{(f'{statistics.median(fp):.1f}' if fp else '-'):>14}")
