"""Errors raised by service-auth."""

from __future__ import annotations


class AuthError(Exception):
    """Base class for service-auth errors."""


class InvalidToken(AuthError):
    """A token is missing, malformed, expired, or not for this service.

    `reason` is for logs only; never send it to the caller.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class InsufficientScope(AuthError):
    """A valid token lacks a required scope."""

    def __init__(self, required: tuple[str, ...]):
        super().__init__(f"missing scopes: {' '.join(required)}")
        self.required = required


class JWKSUnavailable(AuthError):
    """The issuer's public keys can't be fetched and no usable cached copy exists."""


class TokenRequestError(AuthError):
    """auth-service refused or failed a token request.

    `error` is the OAuth error code (e.g. `invalid_scope`), or `unavailable` when
    auth-service couldn't be reached. `status` is the HTTP status, if any.
    """

    def __init__(self, error: str, description: str | None = None, status: int | None = None):
        super().__init__(f"{error}: {description}" if description else error)
        self.error = error
        self.description = description
        self.status = status

    @property
    def transient(self) -> bool:
        """True if retrying later may succeed (issuer down or overloaded)."""
        return self.status is None or self.status >= 500 or self.status == 429
