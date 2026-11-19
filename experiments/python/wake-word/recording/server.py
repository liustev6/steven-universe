"""The recording pages on localhost; every take is a 16 kHz 16-bit mono WAV in data/datasets/full/recordings/ (not in
git: voice recordings are personal).

  /      the Mac's mic, in the browser (index.html): the wake word; Chinese and English lines that aren't it
         (phrases/xiao_pai_bank.yaml, split by language, each take named with its lines); short videos playing
         nearby, a part every 5 minutes. The browser sends each take here.
  /pi    the Pi's mic (pi.html): this server drives the Pi over SSH (`ssh pi`, the home network): a beep from the
         Pi's speaker, arecord on its speakerphone until Stop, two beeps, the file copied here. pi-server holds
         the mic, so the page stops it first (and starts it again).

    .venv/bin/python recording/server.py            # then open http://localhost:8770 or http://localhost:8770/pi
"""

import json
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

HERE = Path(__file__).parent
OUT = HERE.parent / "data/datasets/full/recordings"
PORT = 8770
PI = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "pi"]
PI_DIR = "wake-word"                                    # ~/wake-word on the Pi: start.wav, end.wav, the takes
PI_MIC = "plughw:CARD=Speakerphone,DEV=0"
PI_KINDS = {"wake", "wake-bg", "near", "room", "family"}
lock = threading.Lock()
recording: dict | None = None                           # {"name", "kind", "started"}


def pi(command: str, timeout: float = 30) -> str:
    return subprocess.run(PI + [command], capture_output=True, text=True, timeout=timeout).stdout.strip()


def pi_status() -> dict:
    out = pi(f"systemctl is-active pi-server; du -cb {PI_DIR}/{recording['name']}*.wav 2>/dev/null | tail -1"
             if recording else "systemctl is-active pi-server")
    lines = out.splitlines()
    size = int(lines[1].split()[0]) if recording and len(lines) > 1 and lines[1].split()[0].isdigit() else 0
    return {"pi_server": lines[0] if lines else "unknown", "recording": recording and recording["kind"],
            "seconds": round(time.time() - recording["started"]) if recording else 0, "bytes": size}


def pi_start(kind: str, label: str) -> dict:
    global recording
    if recording:
        raise RuntimeError("already recording")
    if pi("systemctl is-active pi-server") == "active":
        raise RuntimeError("pi-server holds the mic: stop it first")
    label = re.sub(r"[^a-zA-Z0-9_-]", "", label)[:20]
    name = f"pi-{kind}{'-' + label if label else ''}-{time.strftime('%Y%m%d-%H%M%S')}"
    split = "--max-file-time 300 " if kind == "room" else ""          # the room: a file every 5 minutes
    pi(f"cd {PI_DIR} && aplay -q start.wav && (nohup arecord -q -D {PI_MIC} -f S16_LE -r 16000 -c 1 {split}"
       f"{name}.wav </dev/null >/dev/null 2>&1 &)")
    recording = {"name": name, "kind": kind, "started": time.time()}
    return {"name": name}


def pi_stop() -> dict:
    global recording
    if not recording:
        raise RuntimeError("not recording")
    name, recording = recording["name"], None
    # [a]record: the pattern must not match this shell's own command line, or pkill stops it too
    files = pi(f"pkill -INT -f '[a]record .*{name}'; sleep 0.7; cd {PI_DIR} && aplay -q end.wav; ls {name}*.wav").split()
    OUT.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        subprocess.run(["scp", "-q", "-o", "BatchMode=yes", f"pi:{PI_DIR}/{f}", str(OUT / f)], check=True, timeout=600)
        saved.append({"file": f, "seconds": round(((OUT / f).stat().st_size - 44) / 32000, 1)})
        print(f"saved {f} ({saved[-1]['seconds']} s, from the Pi)", flush=True)
    return {"saved": saved}


class Handler(BaseHTTPRequestHandler):
    def reply(self, body: bytes, kind: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def json(self, obj: dict, status: int = 200) -> None:
        self.reply(json.dumps(obj, ensure_ascii=False).encode(), "application/json", status)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            return self.reply((HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/pi":
            return self.reply((HERE / "pi.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/bank":
            bank = yaml.safe_load(open(HERE.parent / "phrases/xiao_pai_bank.yaml"))
            return self.json(bank)
        if path == "/pi/status":
            try:
                return self.json(pi_status())
            except Exception as e:
                return self.json({"error": str(e)}, 502)
        self.send_error(404)

    def do_POST(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path.startswith("/pi/"):
            with lock:
                try:
                    if url.path == "/pi/start":
                        kind = q.get("kind", [""])[0]
                        if kind not in PI_KINDS:
                            return self.json({"error": f"kind: one of {sorted(PI_KINDS)}"}, 400)
                        return self.json(pi_start(kind, q.get("label", [""])[0]))
                    if url.path == "/pi/stop":
                        return self.json(pi_stop())
                    if url.path == "/pi/service":
                        action = q.get("action", [""])[0]
                        if action not in ("stop", "start"):
                            return self.json({"error": "action: stop | start"}, 400)
                        pi(f"sudo systemctl {action} pi-server", timeout=60)
                        return self.json(pi_status())
                except Exception as e:
                    return self.json({"error": str(e)}, 409)
            return self.send_error(404)
        kind = q.get("kind", ["take"])[0]
        lines = q.get("lines", [""])[0]                       # "12-80": the bank's lines this take covers
        lines = f"-lines{lines}" if re.fullmatch(r"\d+-\d+", lines) else ""
        part = q.get("part", [""])[0]                        # short videos: saved a part every 5 minutes
        lines += f"-part{int(part):02d}" if part.isdigit() else ""
        if url.path != "/save" or kind not in ("wake", "zh", "en", "video"):
            return self.send_error(400)
        data = self.rfile.read(int(self.headers["Content-Length"]))
        if data[:4] != b"RIFF":
            return self.send_error(400, "expected a WAV")
        OUT.mkdir(parents=True, exist_ok=True)
        path = OUT / f"mac-{kind}-{time.strftime('%Y%m%d-%H%M%S')}{lines}.wav"
        path.write_bytes(data)
        seconds = (len(data) - 44) / 32000
        print(f"saved {path.name} ({seconds:.1f} s)", flush=True)
        self.json({"file": path.name, "seconds": round(seconds, 1)})

    def log_message(self, *args):
        pass


print(f"http://localhost:{PORT} (the Mac's mic), http://localhost:{PORT}/pi (the Pi's)  →  {OUT}", flush=True)
ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
