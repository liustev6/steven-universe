"""Smoke test against the running api-service. Needs no credentials and deploys nothing.

Run by infra/deployment/deploy-target.sh (the Smoke test step) after every deploy, inside the new
image with host networking, through DNS, port 443 and TLS (the way cloudflared reaches it):

    docker run --rm --network host --read-only -v <this file>:/t/smoke.py:ro --entrypoint python <image> /t/smoke.py

Checks: health; nothing but the reviewed routes; junk is stopped at the edge; and the deploy proxy
really reaches the deployer (a GitHub-shaped token with a fake signature gets past the edge's
pre-checks and is refused by the deployer itself, which proves the whole path without deploying).
"""

import base64
import json
import sys
import time

import httpx

URL = "https://api.lan.liustev6.ca"
failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def github_shaped_token() -> str:
    def seg(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    claims = {"iss": "https://token.actions.githubusercontent.com", "aud": "steven-universe-deployer",
              "exp": int(time.time()) + 300, "sha": "0" * 40}
    return f"{seg({'alg': 'RS256', 'kid': 'smoke-test'})}.{seg(claims)}.bm90LWEtc2lnbmF0dXJl"


def main() -> int:
    body = json.dumps({"service": "file-management", "sha": "0" * 40})
    with httpx.Client(base_url=URL, timeout=15) as http:
        r = http.get("/health")
        check("health", r.status_code == 200 and r.json() == {"status": "ok"}, r.text)
        check("security headers", r.headers.get("x-content-type-options") == "nosniff"
              and "strict-transport-security" in r.headers)
        for path in ("/docs", "/openapi.json", "/status"):
            r = http.get(path)
            check(f"{path} does not exist (404)", r.status_code == 404, str(r.status_code))

        r = http.post("/deploy/v1/deployments", content=body)
        check("deploy without a token: stopped at the edge (401)",
              r.status_code == 401 and "message" in r.json(), f"{r.status_code} {r.text[:80]}")
        r = http.post("/deploy/v1/deployments", content=b"x" * 5000,
                      headers={"Authorization": f"Bearer {github_shaped_token()}"})
        check("oversized body: stopped at the edge (413)", r.status_code == 413, str(r.status_code))

        # Past the edge: the deployer answers (its errors have no "message"/"request_id" fields).
        r = http.post("/deploy/v1/deployments", content=body,
                      headers={"Authorization": f"Bearer {github_shaped_token()}"})
        reached = r.status_code == 401 and "message" not in r.json()
        check("forged GitHub token: forwarded, refused by the deployer (401)", reached,
              f"{r.status_code} {r.text[:100]}")

    print("ALL PASSED" if not failures else f"{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
