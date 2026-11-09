"""POST /deploy/v1/deployments -> the deployer (api-service spec "Deploys"; deployer spec D3, D15).

Mode `proxy`: GitHub Actions calls this with a GitHub OIDC token addressed to the deployer, not to
api-service. api-service doesn't consume or verify it (that's the deployer's job, against GitHub's
keys); it only filters out what can't possibly be a deploy before it reaches the LAN:

    1. rate limit per client IP
    2. body at most 4 KB (a deploy request is ~150 bytes)
    3. a bearer token that *claims* to be GitHub's, for the deployer, not expired (unverified:
       a cheap filter, never a decision)
    4. forward body + Authorization unchanged; stream the deployer's NDJSON back as it comes

It is the only route that forwards a credential, and only to this one fixed URL (spec E4 note).
"""

from __future__ import annotations

import base64
import json
import logging
import time

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from app.config import Settings
from app.edge import Mode, access, client_ip
from app.ratelimit import RateLimiter

logger = logging.getLogger("api_service.deploy")


def error(status: int, code: str, message: str, request: Request, headers: dict | None = None) -> JSONResponse:
    body = {"error": code, "message": message, "request_id": getattr(request.state, "request_id", None)}
    return JSONResponse(body, status_code=status, headers=headers)


def precheck(authorization: str, settings: Settings) -> str | None:
    """Why this can't be a GitHub deploy token, or None. Unverified on purpose (see module doc)."""
    if not authorization.lower().startswith("bearer "):
        return "a GitHub OIDC bearer token is required"
    parts = authorization[7:].strip().split(".")
    if len(parts) != 3:
        return "malformed token"
    try:
        segment = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(segment))
    except (ValueError, TypeError):
        return "malformed token"
    if not isinstance(claims, dict) or claims.get("iss") != settings.DEPLOY_TOKEN_ISSUER:
        return "not a GitHub Actions token"
    aud = claims.get("aud")
    if settings.DEPLOY_TOKEN_AUDIENCE not in (aud if isinstance(aud, list) else [aud]):
        return "token not issued for the deployer"
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or exp < time.time() - 60:
        return "token expired"
    return None


def build_router(settings: Settings, http: httpx.AsyncClient, limiter: RateLimiter) -> APIRouter:
    router = APIRouter()
    target = settings.DEPLOYER_URL.rstrip("/") + "/v1/deployments"

    @router.post("/deploy/v1/deployments", dependencies=[Depends(access(Mode.PROXY))])
    async def deploy(request: Request):
        ip = client_ip(request, settings)
        if not limiter.allow(f"deploy:{ip}"):
            return error(429, "rate_limited", "too many deploy requests", request,
                         {"Retry-After": str(limiter.retry_after(f"deploy:{ip}"))})

        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > settings.DEPLOY_MAX_BODY_BYTES:
            return error(413, "too_large", "request body too large", request)
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > settings.DEPLOY_MAX_BODY_BYTES:
                return error(413, "too_large", "request body too large", request)

        authorization = request.headers.get("authorization", "")
        if problem := precheck(authorization, settings):
            logger.info("deploy refused at the edge from %s: %s", ip, problem)
            return error(401, "unauthorized", problem, request, {"WWW-Authenticate": "Bearer"})

        upstream = http.build_request("POST", target, content=body, headers={
            "Authorization": authorization,
            "Content-Type": request.headers.get("content-type", "application/json"),
            "X-Forwarded-For": ip,
            "X-Request-ID": request.state.request_id,
        })
        try:
            response = await http.send(upstream, stream=True)
        except httpx.HTTPError as exc:
            logger.warning("deployer unreachable: %s", exc)
            return error(502, "bad_gateway", "the deployer is unavailable", request)
        logger.info("deploy forwarded from %s: deployer answered %d", ip, response.status_code)
        # The deployer's answer (including its 4xx explanations) goes back unchanged: the caller is
        # a CI workflow whose log is where you read why a deploy was refused.
        return StreamingResponse(
            response.aiter_raw(),
            status_code=response.status_code,
            media_type=response.headers.get("content-type", "application/json"),
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
            background=BackgroundTask(response.aclose),
        )

    return router
