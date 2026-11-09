"""A cheap filter for deploy requests: does the token at least *claim* to be GitHub's, for the
deployer, and not expired? Never a decision: the deployer verifies the signature and every claim."""

from __future__ import annotations

import base64
import json
import time

from app.config import Settings


def precheck(authorization: str, settings: Settings) -> str | None:
    """Why this can't be a GitHub deploy token, or None."""
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
