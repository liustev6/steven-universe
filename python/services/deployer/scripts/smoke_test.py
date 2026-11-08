"""Smoke test against the running deployer. Never deploys anything and needs no credentials.

Run by infra/deployment/deploy-target.sh (the Smoke test step) after every deploy, inside the new
image with host networking, so requests go through DNS, port 443 and TLS like api-service's will:

    docker run --rm --network host --read-only -v <this file>:/t/smoke.py:ro --entrypoint python <image> /t/smoke.py

It checks that the service is up and healthy (host allowlist and SSH key present), and that
every request without a genuine GitHub token is refused before anything happens: no token, a
garbage token, and a token with all the right claims signed by a key that isn't GitHub's.
"""

import json
import sys
import time
import uuid

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

URL = "https://deploy.lan.liustev6.ca"
SHA = "0" * 40
failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def forged_token() -> str:
    """Every claim right, but signed by a key we just made up."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    claims = {"iss": "https://token.actions.githubusercontent.com", "aud": "steven-universe-deployer",
              "iat": now, "exp": now + 300, "jti": str(uuid.uuid4()), "sha": SHA,
              "ref": "refs/heads/main", "environment": "production", "runner_environment": "github-hosted"}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "forged"})


def main() -> int:
    body = json.dumps({"service": "file-management", "sha": SHA})
    with httpx.Client(base_url=URL, timeout=10) as http:
        r = http.get("/health")
        check("health: allowlist and SSH key present", r.status_code == 200 and r.json().get("status") == "healthy", r.text)
        r = http.post("/v1/deployments", content=body)
        check("no token is refused (401)", r.status_code == 401, str(r.status_code))
        r = http.post("/v1/deployments", content=body, headers={"Authorization": "Bearer not.a.token"})
        check("garbage token is refused (401)", r.status_code == 401, str(r.status_code))
        r = http.post("/v1/deployments", content=body, headers={"Authorization": f"Bearer {forged_token()}"})
        check("token not signed by GitHub is refused (401)", r.status_code == 401, f"{r.status_code} {r.text[:80]}")
        r = http.get("/v1/deployments")
        check("nothing to list (405)", r.status_code == 405, str(r.status_code))
        r = http.get("/docs")
        check("no API docs exposed (404)", r.status_code == 404, str(r.status_code))

    print("ALL PASSED" if not failures else f"{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
