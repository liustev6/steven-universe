"""The /deploy/* routes. Order per request: rate limit -> body limit -> token pre-check -> forward
unchanged to the deployer -> stream its NDJSON back. It is the only place that forwards a
credential, and only to the deployer's fixed URLs (api-service spec E4 note)."""

from __future__ import annotations

import logging
import re

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from app.config import Settings
from app.deploy.precheck import precheck
from app.edge import Mode, access, client_ip
from app.ratelimit import RateLimiter

logger = logging.getLogger("api_service.deploy")
DEPLOYMENT_ID = re.compile(r"^[0-9a-f]{32}$")


def error(status: int, code: str, message: str, request: Request, headers: dict | None = None) -> JSONResponse:
    body = {"error": code, "message": message, "request_id": getattr(request.state, "request_id", None)}
    return JSONResponse(body, status_code=status, headers=headers)


def build_router(settings: Settings, http: httpx.AsyncClient) -> APIRouter:
    router = APIRouter()
    base = settings.DEPLOYER_URL.rstrip("/") + "/v1/deployments"
    # Starting deploys is rare; reconnecting (a few polls while api-service itself restarts) less so.
    starts = RateLimiter(settings.DEPLOY_RATE_BURST, settings.DEPLOY_RATE_PER_MINUTE)
    reconnects = RateLimiter(settings.DEPLOY_EVENTS_RATE_BURST, settings.DEPLOY_EVENTS_RATE_PER_MINUTE)

    async def forward(request: Request, limiter: RateLimiter, method: str, url: str,
                      params: dict | None = None):
        ip = client_ip(request, settings)
        if not limiter.allow(ip):
            return error(429, "rate_limited", "too many deploy requests", request,
                         {"Retry-After": str(limiter.retry_after(ip))})
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
            logger.info("deploy request refused at the edge from %s: %s", ip, problem)
            return error(401, "unauthorized", problem, request, {"WWW-Authenticate": "Bearer"})

        headers = {"Authorization": authorization, "X-Forwarded-For": ip, "X-Request-ID": request.state.request_id}
        if body:
            headers["Content-Type"] = request.headers.get("content-type", "application/json")
        upstream = http.build_request(method, url, params=params, content=body or None, headers=headers)
        try:
            response = await http.send(upstream, stream=True)
        except httpx.HTTPError as exc:
            logger.warning("deployer unreachable: %s", exc)
            return error(502, "bad_gateway", "the deployer is unavailable", request)
        logger.info("deploy %s forwarded from %s: deployer answered %d", method, ip, response.status_code)
        # The deployer's answer (including its 4xx explanations) goes back unchanged: the caller is
        # a CI workflow whose log is where you read why a deploy was refused.
        return StreamingResponse(
            response.aiter_raw(),
            status_code=response.status_code,
            media_type=response.headers.get("content-type", "application/json"),
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
            background=BackgroundTask(response.aclose),
        )

    @router.post("/deploy/v1/deployments", dependencies=[Depends(access(Mode.PROXY))])
    async def start(request: Request):
        return await forward(request, starts, "POST", base)

    @router.get("/deploy/v1/deployments/{deployment_id}/events", dependencies=[Depends(access(Mode.PROXY))])
    async def events(deployment_id: str, request: Request, after: int = -1):
        if not DEPLOYMENT_ID.match(deployment_id) or after < -1:
            return error(404, "not_found", "not found", request)
        return await forward(request, reconnects, "GET", f"{base}/{deployment_id}/events", {"after": after})

    return router
