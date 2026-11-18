"""A local test client for speech-service: a page to talk to it (speech to text) and to hear it (text to speech).

    python3 server.py [--service http://127.0.0.1:18443] [--port 8765]
    then open http://localhost:8765

Serves index.html and forwards its HTTP calls (/v1/voices, /v1/status) to the service, so the page
needs no CORS. The page opens its WebSockets straight to the service (no CORS for WebSockets).
Meant for a dev service (ENV=local, a dev principal): browsers can't set an Authorization header on
a WebSocket, so a token-checking service would refuse them; pass --token to forward one on HTTP.

A dev service on CT 108, reached from the Mac through an SSH tunnel (nothing new open on the network):

    ssh speech-host 'docker run -d --name speech-test --gpus all -p 127.0.0.1:8443:8443 \\
        -v /opt/speech-dev:/src -w /src -v <models>:/models:ro -e ENV=local \\
        -e SERVICE_AUTH_DEV_PRINCIPAL='"'"'{"service":"dev","scopes":["*"]}'"'"' <image> python -m app.main'
    ssh -f -N -L 18443:127.0.0.1:8443 speech-host
    python3 server.py                     # http://localhost:8765
    ssh speech-host docker rm -f speech-test     # when done: frees the GPU
"""

import argparse
import http.server
import json
import urllib.request
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--service", default="http://127.0.0.1:18443")
ap.add_argument("--port", type=int, default=8765)
ap.add_argument("--token", default="dev")
args = ap.parse_args()
PAGE = Path(__file__).with_name("index.html")


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = PAGE.read_bytes()
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
        elif self.path == "/config":
            body = json.dumps({"ws": args.service.replace("http", "ws", 1)}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
        elif self.path.startswith("/v1/") or self.path == "/health":
            req = urllib.request.Request(args.service + self.path, headers={"authorization": f"Bearer {args.token}"})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    body, status = r.read(), r.status
            except urllib.error.HTTPError as e:
                body, status = e.read(), e.code
            except OSError as e:
                body, status = json.dumps({"error": str(e)}).encode(), 502
            self.send_response(status)
            self.send_header("content-type", "application/json")
        else:
            self.send_error(404)
            return
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


print(f"http://localhost:{args.port}  (service: {args.service})")
http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
