"""Smoke test against the running agent-service, as the `smoke-test` client. Starts no session.

Run by infra/deployment/deploy-target.sh (the Smoke test step) after every deploy, inside the new
image with host networking, through DNS, port 443 and TLS, with the smoke-test client's secret at
/t/secret:

    docker run --rm --network host --read-only -v <this file>:/t/smoke.py:ro -v <secret>:/t/secret:ro \\
        --entrypoint python <image> /t/smoke.py

Checks: health; no token is refused; a real smoke-test token is accepted (its conversations: chat:read);
errors are problem details; someone else's (here: a random) session is 404; it may not stop the
service (chat:admin) or read the status page (status:read). A turn on the model comes with the
callers' smoke tests (CP3 3.6).
"""

import asyncio
import sys
import uuid

import httpx
from service_auth import TokenClient

URL = "https://agent.lan.liustev6.ca"
ISSUER = "https://auth.lan.liustev6.ca"
failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


async def main() -> int:
    tokens = TokenClient(ISSUER, "smoke-test", open("/t/secret").read().strip())
    async with httpx.AsyncClient(base_url=URL, timeout=15) as anon, \
            httpx.AsyncClient(base_url=URL, timeout=15, auth=tokens.auth("agent-service")) as http:
        r = await anon.get("/health")
        check("health", r.status_code == 200 and r.json() == {"status": "healthy"}, r.text[:100])
        r = await anon.get("/v1/conversations")
        check("no token: 401", r.status_code == 401, str(r.status_code))

        r = await http.get("/v1/conversations")
        check("smoke-test's token accepted (its conversations)", r.status_code == 200
              and isinstance(r.json().get("conversations"), list), f"{r.status_code} {r.text[:100]}")
        r = await http.post("/v1/sessions", json={"profile": "no-such-profile"})
        check("errors are problem details", r.status_code == 400
              and r.headers.get("content-type") == "application/problem+json"
              and r.json().get("code") == "unknown_profile", f"{r.status_code} {r.text[:100]}")
        r = await http.get(f"/v1/sessions/{uuid.uuid4()}")
        check("a session that isn't yours: 404", r.status_code == 404, str(r.status_code))
        r = await http.post("/v1/admin/stop")
        check("no kill switch without chat:admin: 403", r.status_code == 403, str(r.status_code))
        r = await http.get("/v1/status")
        check("no status page without status:read: 403", r.status_code == 403, str(r.status_code))
    await tokens.aclose()
    print("ALL PASSED" if not failures else f"{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
