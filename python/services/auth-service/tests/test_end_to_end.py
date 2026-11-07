"""The two end-to-end flows from the spec, using the service-auth library the way
real services will: TokenClient on the calling side, TokenVerifier/ServiceAuth on
the receiving side, all talking to this auth-service app over ASGI."""

import asyncio

import httpx
from fastapi import Depends, FastAPI, HTTPException
from service_auth import JWKSCache, Principal, TokenClient, TokenVerifier
from service_auth.fastapi_auth import ServiceAuth

from tests.conftest import ISSUER


def _http(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ISSUER)


def _verifier(http, audience: str) -> TokenVerifier:
    return TokenVerifier(issuer=ISSUER, audience=audience, jwks=JWKSCache(ISSUER, http=http))


def _authorize(p: Principal, action: str, resource: str) -> None:
    """A stand-in for file-management's authorize()."""
    for d in p.details("file_access"):
        if action in d["actions"] and any(resource.startswith(loc) for loc in d["locations"]):
            return
    raise HTTPException(403, "not granted")


def test_flow_1_hey_pi_through_api_service_to_file_management(app, secrets):
    async def flow():
        async with _http(app) as http:
            hey_pi = TokenClient(ISSUER, "hey-pi", secrets["hey-pi"], http=http)
            api = TokenClient(ISSUER, "api-service", secrets["api-service"], http=http)

            # ① hey-pi gets T1 for api-service
            t1 = await hey_pi.token("api-service", scopes=["files:read"])
            # ② api-service checks T1 locally
            caller = await _verifier(http, "api-service").verify(t1)
            assert caller.service == "hey-pi" and caller.has_scopes("files:read")
            # ③ api-service exchanges T1 for T2 (relay)
            t2 = await api.exchange(caller.token, "file-management")
            # ④ file-management checks T2 locally, then authorize()
            p = await _verifier(http, "file-management").verify(t2)
            assert p.on_behalf_of == "service:hey-pi"
            assert p.service == "api-service" and p.actor_chain == ("api-service",)
            _authorize(p, "read", "services/hey-pi/wallpapers/sunset.jpg")
            try:
                _authorize(p, "read", "public/blog/cat.jpg")  # api-service's grant does NOT apply
            except HTTPException as exc:
                assert exc.status_code == 403
            else:
                raise AssertionError("relayed client got api-service's permissions")

            # cached: the same token is reused, no new mint
            assert await hey_pi.token("api-service", scopes=["files:read"]) == t1

    asyncio.run(flow())


def test_flow_2_anonymous_visitor_uses_api_services_own_token(app, secrets):
    async def flow():
        async with _http(app) as http:
            api = TokenClient(ISSUER, "api-service", secrets["api-service"], http=http)
            own = await api.token("file-management")
            p = await _verifier(http, "file-management").verify(own)
            assert p.on_behalf_of is None and p.service == "api-service"
            _authorize(p, "read", "public/blog/cat.jpg")
            for private in ("users/42/report.pdf", "users/", "services/hey-pi/x"):
                try:
                    _authorize(p, "read", private)
                except HTTPException:
                    continue
                raise AssertionError(f"anonymous path could read {private}")

    asyncio.run(flow())


def test_a_resource_service_with_service_auth(app, secrets):
    """A tiny file-management-like service using ServiceAuth.require()."""
    files = FastAPI()

    async def run():
        async with _http(app) as auth_http:
            auth = ServiceAuth(_verifier(auth_http, "file-management"))

            @files.get("/objects/{bucket}/{key:path}")
            async def get_object(bucket: str, key: str, p: Principal = Depends(auth.require("files:read"))):
                _authorize(p, "read", f"{bucket}/{key}")
                return {"by": p.service, "for": p.on_behalf_of}

            tokens = TokenClient(ISSUER, "gpu-server", secrets["gpu-server"], http=auth_http)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=files),
                base_url="https://files.lab",
                auth=tokens.auth("file-management"),
            ) as files_http:
                ok = await files_http.get("/objects/models/qwen.tar.gz")
                assert ok.status_code == 200 and ok.json() == {"by": "gpu-server", "for": None}
                denied = await files_http.get("/objects/users/42/report.pdf")
                assert denied.status_code == 403

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=files), base_url="https://files.lab"
            ) as anonymous:
                missing = await anonymous.get("/objects/models/qwen.tar.gz")
                assert missing.status_code == 401
                assert missing.headers["www-authenticate"] == "Bearer"
                bad = await anonymous.get(
                    "/objects/models/qwen.tar.gz", headers={"Authorization": "Bearer not-a-jwt"}
                )
                assert bad.status_code == 401
                assert 'error="invalid_token"' in bad.headers["www-authenticate"]

            # a token meant for another audience is rejected (no token passthrough)
            hey_pi = TokenClient(ISSUER, "hey-pi", secrets["hey-pi"], http=auth_http)
            wrong_aud = await hey_pi.token("api-service")
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=files), base_url="https://files.lab"
            ) as c:
                r = await c.get("/objects/services/hey-pi/x", headers={"Authorization": f"Bearer {wrong_aud}"})
                assert r.status_code == 401

    asyncio.run(run())
