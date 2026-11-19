"""The playground's log page (CT 110, docs/playground.md): every run's log in /srv/experiments/*/logs, live in a
browser: the readable log, the raw output's last lines (progress bars made readable), whether the run's tmux
session is still going, how long since the log last changed, and the CPU. Standard library only.

    python3 experiments/logserver.py            # http://<playground>:8080
"""

import glob
import json
import os
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path("/srv/experiments")
PORT = 8080


def runs() -> list[dict]:
    out = []
    for log in sorted(glob.glob(str(ROOT / "*/logs/*.log")), key=os.path.getmtime, reverse=True):
        p = Path(log)
        if p.name.endswith(".raw.log"):
            continue
        out.append({"id": f"{p.parent.parent.name}/{p.stem}", "path": str(p), "changed": time.time() - p.stat().st_mtime})
    return out


def tail(path: Path, size: int) -> str:
    if not path.exists():
        return ""
    with open(path, "rb") as f:
        f.seek(max(0, path.stat().st_size - size))
        text = f.read().decode("utf-8", "replace")
    lines = [line.split("\r")[-1] for line in text.split("\n")]   # a progress bar: only its latest state
    return "\n".join(line for line in lines if line.strip())


def busy() -> dict:
    sessions = subprocess.run(["tmux", "ls"], capture_output=True, text=True).stdout
    load = os.getloadavg()[0]
    return {"tmux": [s.split(":")[0] for s in sessions.splitlines()], "load": round(load, 1), "cores": os.cpu_count()}


PAGE = """<!doctype html><meta charset="utf-8"><title>Playground logs</title>
<style>body{margin:0;font:14px system-ui;background:#121214;color:#eee}header{padding:12px 16px;border-bottom:1px solid #333;
display:flex;gap:14px;align-items:center;flex-wrap:wrap}select{font:inherit;padding:4px 8px;background:#222;color:#eee;border:1px solid #444;border-radius:6px}
.pill{padding:3px 9px;border-radius:99px;background:#222;border:1px solid #444;font-size:12px}.ok{color:#4ade80}.idle{color:#a1a1aa}
main{display:grid;grid-template-columns:1fr;gap:12px;padding:12px 16px}h2{font-size:13px;color:#a1a1aa;margin:0 0 4px;text-transform:uppercase;letter-spacing:.05em}
pre{margin:0;background:#18181b;border:1px solid #333;border-radius:8px;padding:10px;white-space:pre-wrap;word-break:break-word;
font:12px/1.45 ui-monospace,Menlo,monospace;max-height:42vh;overflow:auto}</style>
<header><b>Playground logs</b><select id="run"></select><span class="pill" id="state">…</span><span class="pill" id="age"></span>
<span class="pill" id="load"></span><a href="http://" id="tb" style="color:#818cf8">TensorBoard ↗</a></header>
<main><div><h2>Readable log</h2><pre id="log"></pre></div><div><h2>Latest raw output</h2><pre id="raw"></pre></div></main>
<script>
const $ = (id) => document.getElementById(id);
$("tb").href = `http://${location.hostname}:6006/`;
async function refresh() {
  const runs = await (await fetch("/runs")).json();
  const sel = $("run"), keep = sel.value;
  if (sel.options.length !== runs.runs.length) {
    sel.replaceChildren(...runs.runs.map((r) => new Option(r.id, r.id)));
    if (keep && runs.runs.some((r) => r.id === keep)) sel.value = keep;
  }
  const run = runs.runs.find((r) => r.id === sel.value) || runs.runs[0];
  if (!run) return;
  const name = run.id.split("/")[1], going = runs.busy.tmux.length > 0;
  $("state").textContent = going ? `running: ${runs.busy.tmux.join(", ")}` : "no tmux session running";
  $("state").className = "pill " + (going ? "ok" : "idle");
  $("age").textContent = `log changed ${Math.round(run.changed)} s ago`;
  $("load").textContent = `CPU load ${runs.busy.load} / ${runs.busy.cores} cores`;
  for (const [id, kind] of [["log", "log"], ["raw", "raw"]]) {
    const el = $(id), atEnd = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
    el.textContent = await (await fetch(`/tail?run=${encodeURIComponent(run.id)}&kind=${kind}`)).text();
    if (atEnd) el.scrollTop = el.scrollHeight;
  }
}
refresh(); setInterval(refresh, 3000);
</script>"""


class Handler(BaseHTTPRequestHandler):
    def send(self, body: str, kind: str) -> None:
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            return self.send(PAGE, "text/html; charset=utf-8")
        if url.path == "/runs":
            return self.send(json.dumps({"runs": runs(), "busy": busy()}), "application/json")
        if url.path == "/tail":
            q = parse_qs(url.query)
            run = q.get("run", [""])[0]
            match = [r for r in runs() if r["id"] == run]
            if not match:
                return self.send_error(404)
            path = Path(match[0]["path"])
            if q.get("kind", ["log"])[0] == "raw":
                return self.send(tail(path.with_name(path.stem + ".raw.log"), 40_000)[-6000:], "text/plain; charset=utf-8")
            return self.send(tail(path, 200_000), "text/plain; charset=utf-8")
        self.send_error(404)

    def log_message(self, *args):
        pass


print(f"http://0.0.0.0:{PORT}  →  {ROOT}/*/logs", flush=True)
ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
