"""The recording page (recording/index.html) on localhost: it records your voice in the browser and this server
saves each take as a 16 kHz 16-bit mono WAV in data/xiao_pai/recordings/ (not in git: voice recordings are
personal). The not-the-wake-word card reads its lines from phrases/xiao_pai_bank.yaml.

    .venv/bin/python recording/server.py            # then open http://localhost:8770
"""

import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

HERE = Path(__file__).parent
OUT = HERE.parent / "data/xiao_pai/recordings"
PORT = 8770


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            body, kind = (HERE / "index.html").read_bytes(), "text/html; charset=utf-8"
        elif path == "/bank":
            bank = yaml.safe_load(open(HERE.parent / "phrases/xiao_pai_bank.yaml"))
            body, kind = json.dumps(bank, ensure_ascii=False).encode(), "application/json"
        else:
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        kind = q.get("kind", ["take"])[0]
        lines = q.get("lines", [""])[0]                       # "12-80": the bank's lines this take covers
        lines = f"-lines{lines}" if re.fullmatch(r"\d+-\d+", lines) else ""
        if url.path != "/save" or kind not in ("wake", "negative"):
            return self.send_error(400)
        data = self.rfile.read(int(self.headers["Content-Length"]))
        if data[:4] != b"RIFF":
            return self.send_error(400, "expected a WAV")
        OUT.mkdir(parents=True, exist_ok=True)
        path = OUT / f"mac-{kind}-{time.strftime('%Y%m%d-%H%M%S')}{lines}.wav"
        path.write_bytes(data)
        seconds = (len(data) - 44) / 32000
        print(f"saved {path.name} ({seconds:.1f} s)", flush=True)
        reply = json.dumps({"file": path.name, "seconds": round(seconds, 1)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *args):
        pass


print(f"http://localhost:{PORT}  →  saves to {OUT}", flush=True)
ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
