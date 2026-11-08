"""Smoke test against the running file-management, using the real service_auth library.

Run by infra/deployment/file-management-service.sh after every deploy, inside the just-built image
(it has service_auth + httpx) with host networking, so requests go through DNS, port 443 and TLS
exactly like a real caller's:

    docker run --rm --network host --read-only -v <secret>:/t/secret:ro -v <this file>:/t/smoke.py:ro \
      --entrypoint python <image> /t/smoke.py

It authenticates as `smoke-test`, a dedicated auth-service client whose only grant is
`private/smoke-test/` (auth-service policy.yaml). It writes one object there, reads, lists and
deletes it, and checks that everything outside its folder is refused. Real clients are never used.
"""

import asyncio
import hashlib
import sys
import uuid
from pathlib import Path

import httpx
from service_auth import TokenClient

ISSUER = "https://auth.lan.liustev6.ca"
FILES = "https://files.lan.liustev6.ca"
failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


async def main() -> int:
    tokens = TokenClient(ISSUER, "smoke-test", Path("/t/secret").read_text().strip())
    key = f"smoke-test/run-{uuid.uuid4().hex[:8]}.txt"
    body = f"smoke test {uuid.uuid4()}\n".encode()
    try:
        async with httpx.AsyncClient(base_url=FILES, timeout=10, auth=tokens.auth("file-management")) as files, \
                   httpx.AsyncClient(base_url=FILES, timeout=10) as anonymous:
            r = await anonymous.get("/health")
            check("health: storage reachable", r.status_code == 200 and r.json().get("storage") == "ok", r.text)
            r = await anonymous.get(f"/objects/private/{key}")
            check("no token is refused", r.status_code == 401, str(r.status_code))

            # Its own folder: the full round trip on real RustFS.
            r = await files.put(f"/objects/private/{key}", content=body, headers={"Content-Type": "text/plain"})
            ok = r.status_code == 200 and r.json().get("sha256") == hashlib.sha256(body).hexdigest()
            check("PUT in private/smoke-test/ (sha256 matches)", ok, str(r.status_code))
            r = await files.get(f"/objects/private/{key}")
            check("GET returns the same bytes", r.status_code == 200 and r.content == body, str(r.status_code))
            r = await files.get(f"/objects/private/{key}", headers={"Range": "bytes=0-4"})
            check("Range request", r.status_code == 206 and r.content == body[:5], str(r.status_code))
            r = await files.get("/objects/private", params={"prefix": "smoke-test/"})
            listed = [o["key"] for o in r.json().get("objects", [])] if r.status_code == 200 else []
            check("LIST shows it", key in listed, str(r.status_code))
            r = await files.delete(f"/objects/private/{key}")
            check("DELETE", r.status_code == 204, str(r.status_code))
            r = await files.get(f"/objects/private/{key}")
            check("gone after DELETE", r.status_code == 404, str(r.status_code))

            # Everything outside its folder is refused (403, never 404).
            for name, method, url in [
                ("write another client's folder", "PUT", "/objects/private/pi-server/smoke.txt"),
                ("read another client's folder", "GET", "/objects/private/pi-server/anything"),
                ("write public/", "PUT", "/objects/public/smoke.txt"),
                ("list the whole private bucket", "GET", "/objects/private"),
            ]:
                r = await files.request(method, url, content=b"x" if method == "PUT" else None)
                check(f"refused: {name}", r.status_code == 403, str(r.status_code))
    finally:
        await tokens.aclose()

    print("ALL PASSED" if not failures else f"{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
