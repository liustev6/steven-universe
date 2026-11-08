"""Smoke test against the running auth-service, using the real service_auth library.

Run by infra/deployment/deploy-target.sh (the Smoke test step) after every deploy, inside the just-built image (it has
service_auth + httpx) with host networking, so requests go through DNS, port 443 and TLS exactly
like a real service's:

    docker run --rm --network host --read-only -v <secret>:/t/secret:ro -v <this file>:/t/smoke.py:ro \
      --entrypoint python <image> /t/smoke.py

It authenticates as `smoke-test`, a dedicated client (policy.yaml) whose only grant is its own
folder `private/smoke-test/` at file-management. Real clients are never touched, so this can run
any time. Grant 3 (relay) and the rest are covered by the unit tests; this checks the deployment.
"""

import asyncio
import sys
from pathlib import Path

import httpx
from service_auth import TokenClient
from service_auth.errors import InvalidToken, TokenRequestError
from service_auth.verifier import JWKSCache, TokenVerifier

ISSUER = "https://auth.lan.liustev6.ca"
CLIENT = "smoke-test"
failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


async def main() -> int:
    secret = Path("/t/secret").read_text().strip()

    # 1. Discovery and keys, as every service sees them.
    async with httpx.AsyncClient(timeout=5) as http:
        meta = (await http.get(f"{ISSUER}/.well-known/oauth-authorization-server")).json()
        check("discovery: issuer and token endpoint", meta["issuer"] == ISSUER
              and meta["token_endpoint"] == f"{ISSUER}/oauth2/token")
        kids = [k["kid"] for k in (await http.get(meta["jwks_uri"])).json()["keys"]]
        check("JWKS publishes a signing key", bool(kids), ", ".join(kids))

    client = TokenClient(ISSUER, CLIENT, secret)
    jwks = JWKSCache(ISSUER)
    as_files = TokenVerifier(issuer=ISSUER, audience="file-management", jwks=jwks)
    as_api = TokenVerifier(issuer=ISSUER, audience="api-service", jwks=jwks)
    try:
        # 2. A real mint, verified locally against the live JWKS, with exactly the policy's grant.
        token = await client.token("file-management")
        p = await as_files.verify(token)
        locations = sorted(loc for d in p.details("file_access") for loc in d["locations"])
        check("mint + verify against live JWKS", p.service == CLIENT, f"scopes={sorted(p.scopes)}")
        check("grant is exactly private/smoke-test/", locations == ["private/smoke-test/"], str(locations))

        # 3. Rejections.
        try:
            await TokenClient(ISSUER, CLIENT, "wrong-secret").token("file-management")
            check("wrong secret is rejected", False)
        except TokenRequestError as e:
            check("wrong secret is rejected", e.error == "invalid_client", f"{e.status} {e.error}")
        try:
            await as_api.verify(token)
            check("token for file-management is refused by api-service", False)
        except InvalidToken:
            check("token for file-management is refused by api-service", True)
        try:
            await client.token("api-service")
            check("no token for an audience it has no grant at", False)
        except TokenRequestError as e:
            check("no token for an audience it has no grant at", e.error == "invalid_target", e.error)
    finally:
        await client.aclose()
        await jwks.aclose()

    print("ALL PASSED" if not failures else f"{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
