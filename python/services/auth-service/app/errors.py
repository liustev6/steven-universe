"""OAuth 2.0 error responses (RFC 6749 §5.2, RFC 8707, RFC 9396)."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class OAuthError(Exception):
    def __init__(self, error: str, description: str, status: int = 400):
        super().__init__(f"{error}: {description}")
        self.error = error
        self.description = description
        self.status = status


def invalid_request(description: str) -> OAuthError:
    return OAuthError("invalid_request", description)


def invalid_client() -> OAuthError:
    # Same response whether the client is unknown, disabled, or the secret is
    # wrong, so the error reveals nothing.
    return OAuthError("invalid_client", "client authentication failed", 401)


def invalid_grant(description: str) -> OAuthError:
    return OAuthError("invalid_grant", description)


def invalid_scope(description: str) -> OAuthError:
    return OAuthError("invalid_scope", description)


def invalid_target(description: str) -> OAuthError:
    return OAuthError("invalid_target", description)


def invalid_details(description: str) -> OAuthError:
    return OAuthError("invalid_authorization_details", description)


def unauthorized_client(description: str) -> OAuthError:
    return OAuthError("unauthorized_client", description)


NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


async def oauth_error_handler(request: Request, exc: OAuthError) -> JSONResponse:
    headers = dict(NO_STORE)
    if exc.status == 401:
        headers["WWW-Authenticate"] = 'Basic realm="auth-service"'
    return JSONResponse(
        {"error": exc.error, "error_description": exc.description},
        status_code=exc.status,
        headers=headers,
    )
