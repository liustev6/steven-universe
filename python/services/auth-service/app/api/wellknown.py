"""Discovery (RFC 8414) and public keys (RFC 7517)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from service_auth.constants import (
    GRANT_CLIENT_CREDENTIALS,
    GRANT_TOKEN_EXCHANGE,
    JWKS_PATH,
    METADATA_PATH,
    TOKEN_PATH,
)

router = APIRouter()


@router.get(METADATA_PATH)
async def metadata(request: Request) -> JSONResponse:
    policy = request.app.state.policies.current()
    issuer = policy.issuer
    scopes = sorted({s for aud in policy.audiences.values() for s in aud.scopes})
    detail_types = sorted({t for aud in policy.audiences.values() for t in aud.detail_types})
    return JSONResponse(
        {
            "issuer": issuer,
            "token_endpoint": issuer + TOKEN_PATH,
            "jwks_uri": issuer + JWKS_PATH,
            "grant_types_supported": [GRANT_CLIENT_CREDENTIALS, GRANT_TOKEN_EXCHANGE],
            "token_endpoint_auth_methods_supported": ["client_secret_basic"],
            "scopes_supported": scopes,
            "authorization_details_types_supported": detail_types,
            "response_types_supported": [],  # no authorization endpoint
        },
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get(JWKS_PATH)
async def jwks(request: Request) -> JSONResponse:
    return JSONResponse(
        request.app.state.keys.jwks(), headers={"Cache-Control": "public, max-age=300"}
    )
