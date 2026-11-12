"""Checks the pod design (sandbox-runner.md R5, R11, R12) on the worker VM, by hand, before the runner exists.

    python3 scripts/pod_check.py <sandbox image> [runtime]     (as root on the VM; default runtime runsc)

Builds two pods (network + egress proxy + sandbox) and a stand-in gateway that echoes the headers it
receives, then, from inside the sandboxes:
  1. the proxy stamps the pod's sandbox id, replacing any X-Sandbox-Id the sandbox sends;
  2. a request for another host (absolute URL, other Host) still goes only to the gateway;
  3. replies stream (the first chunk arrives before the reply ends);
  4. the gateway can't be reached directly, nor the other pod's proxy, nor the internet, nor DNS.
Everything it creates is removed at the end. Exit status 0 = every check passed.
"""

from __future__ import annotations

import json
import subprocess
import uuid
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.egress import new_sandbox_id, proxy_config  # noqa: E402

PROXY_IMAGE = "nginxinc/nginx-unprivileged:stable-alpine"
GW_NET, GW_IP = "check-gw", "172.31.250.2"
PODS: dict[uuid.UUID, str] = {new_sandbox_id(): "172.29.1", new_sandbox_id(): "172.29.2"}   # id -> its pod's /28


def name(sid: uuid.UUID) -> str:
    """Docker names: short and readable; the full id is what the proxy stamps."""
    return sid.hex[:12]

ECHO_GATEWAY = r'''
import json, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def do_GET(self):
        if self.path.endswith("/stream"):
            self.send_response(200); self.send_header("Transfer-Encoding", "chunked"); self.end_headers()
            for i in range(5):
                data = f"chunk{i}\n".encode()
                self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data)); self.wfile.flush(); time.sleep(0.3)
            self.wfile.write(b"0\r\n\r\n"); return
        body = json.dumps({"path": self.path, "headers": self.headers.items()}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass
ThreadingHTTPServer(("0.0.0.0", 8080), H).serve_forever()
'''

SANDBOX_CHECKS = r'''
import json, socket, sys, time, urllib.request
me, proxy, other_proxy, gateway = sys.argv[1:5]
results = {}
def reach(host, port, timeout=2):
    try:
        socket.create_connection((host, port), timeout=timeout).close(); return True
    except OSError:
        return False
def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    return json.loads(urllib.request.urlopen(req, timeout=5).read())

seen = get(f"http://{proxy}:8080/headers", {"X-Sandbox-Id": "forged", "x-sandbox-id": "forged-2"})
stamps = [v for k, v in seen["headers"] if k.lower() == "x-sandbox-id"]
results["stamp is this pod's, once"] = stamps == [me]

s = socket.create_connection((proxy, 8080), timeout=5)
s.sendall(b"GET http://1.1.1.1/headers HTTP/1.1\r\nHost: 1.1.1.1\r\nConnection: close\r\n\r\n")
raw = b""
while chunk := s.recv(65536): raw += chunk
results["other hosts still go to the gateway"] = b"200 OK" in raw.split(b"\r\n")[0] and me.encode() in raw

t0 = time.time(); r = urllib.request.urlopen(f"http://{proxy}:8080/stream", timeout=5)
first = r.readline(); t_first = time.time() - t0; rest = r.read(); t_all = time.time() - t0
results["replies stream"] = first == b"chunk0\n" and t_first < 0.5 and t_all > 1.0

results["gateway not reachable directly"] = not reach(gateway, 8080)
results["other pod's proxy not reachable"] = not reach(other_proxy, 8080)
results["internet not reachable"] = not reach("1.1.1.1", 443)
try:
    socket.getaddrinfo("example.com", 443); results["no DNS"] = False
except OSError:
    results["no DNS"] = True
print(json.dumps(results))
'''


def sh(*args: str, check: bool = True) -> str:
    return subprocess.run(args, check=check, capture_output=True, text=True).stdout.strip()


def cleanup() -> None:
    names = ["check-gateway"] + [f"check-proxy-{name(s)}" for s in PODS] + [f"check-{name(s)}" for s in PODS]
    sh("docker", "rm", "-f", *names, check=False)
    for net in [GW_NET] + [f"check-pod-{name(s)}" for s in PODS]:
        sh("docker", "network", "rm", net, check=False)


def main(sandbox_image: str, runtime: str = "runsc") -> int:
    cleanup()
    hardening = ["--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true"]
    sh("docker", "network", "create", "--subnet", "172.31.250.0/24", GW_NET)
    sh("docker", "run", "-d", "--name", "check-gateway", "--network", GW_NET, "--ip", GW_IP, *hardening,
       "--entrypoint", "python3", sandbox_image, "-c", ECHO_GATEWAY)
    confdir = Path(tempfile.mkdtemp(prefix="pod-check-"))
    for sid, prefix in PODS.items():
        net = f"check-pod-{name(sid)}"
        sh("docker", "network", "create", "--internal", "--subnet", f"{prefix}.0/28", net)
        conf = confdir / f"{name(sid)}.conf"
        conf.write_text(proxy_config(sid, f"http://{GW_IP}:8080"))
        conf.chmod(0o444)
        sh("docker", "create", "--name", f"check-proxy-{name(sid)}", "--network", net, "--ip", f"{prefix}.2", *hardening,
           "--tmpfs", "/tmp", "-v", f"{conf}:/etc/nginx/nginx.conf:ro", PROXY_IMAGE)
        sh("docker", "network", "connect", GW_NET, f"check-proxy-{name(sid)}")
        sh("docker", "start", f"check-proxy-{name(sid)}")
    failed = 0
    try:
        for sid, prefix in PODS.items():
            other = next(p for s, p in PODS.items() if s != sid)
            out = sh("docker", "run", "--rm", "--name", f"check-{name(sid)}", "--runtime", runtime,
                     "--network", f"check-pod-{name(sid)}", "--ip", f"{prefix}.3", *hardening, "--user", "10001:10001",
                     "--entrypoint", "python3", sandbox_image, "-c", SANDBOX_CHECKS,
                     str(sid), f"{prefix}.2", f"{other}.2", GW_IP)
            for check, ok in json.loads(out).items():
                print(f"{'PASS' if ok else 'FAIL'}  {name(sid)}: {check}")
                failed += not ok
    finally:
        cleanup()
    print("\nALL PASSED" if not failed else f"\n{failed} FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
