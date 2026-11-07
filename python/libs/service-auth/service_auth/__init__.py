"""service-auth: get and check auth-service tokens.

See docs/specs/auth-service.md. Route code only depends on `Principal`; the
FastAPI dependency lives in `service_auth.fastapi_auth` (needs the `fastapi` extra).
"""

from .client import TokenClient
from .details import file_access
from .errors import (
    AuthError,
    InsufficientScope,
    InvalidToken,
    JWKSUnavailable,
    TokenRequestError,
)
from .principal import Principal
from .verifier import JWKSCache, TokenVerifier, decode_claims, read_kid

__all__ = [
    "AuthError",
    "InsufficientScope",
    "InvalidToken",
    "JWKSCache",
    "JWKSUnavailable",
    "Principal",
    "TokenClient",
    "TokenRequestError",
    "TokenVerifier",
    "decode_claims",
    "file_access",
    "read_kid",
]
