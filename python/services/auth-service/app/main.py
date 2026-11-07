"""
auth-service - the internal OAuth 2.0 token issuer.

Mints short-lived ES256 JWT access tokens for services (client credentials),
exchanges them (acting for a user, relaying a client, downscoping), and
publishes its public keys. See docs/specs/auth-service.md.

Run (`app` is a factory, so importing this module never reads /etc/auth-service):
  uvicorn app.main:app --factory --host 0.0.0.0 --port 8443 \
    --ssl-certfile /etc/auth-service/tls/cert.pem --ssl-keyfile /etc/auth-service/tls/key.pem
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI

from app.api import health, token, wellknown
from app.clients import ClientStore
from app.core.config import Settings
from app.errors import OAuthError, oauth_error_handler
from app.issuer import Issuer
from app.keys import KeyStore
from app.policy import PolicyStore
from app.ratelimit import RateLimiter


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    """Build the app. Fails fast (refuses to start) on an invalid policy, no
    signing key, or credential/key files readable by others."""
    settings = settings or Settings()
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    policies = PolicyStore(settings.policy_path)
    keys = KeyStore(settings.keys_dir)
    clients = ClientStore(settings.clients_dir)
    clients.check_permissions()

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.policies = policies
    app.state.keys = keys
    app.state.clients = clients
    app.state.issuer = Issuer(policies, keys, clients, leeway=settings.LEEWAY_SECONDS)
    app.state.rate_limiter = RateLimiter(settings.RATE_LIMIT_PER_MINUTE)

    app.add_exception_handler(OAuthError, oauth_error_handler)  # type: ignore[arg-type]
    app.include_router(token.router)
    app.include_router(wellknown.router)
    app.include_router(health.router)

    logging.getLogger(__name__).info(
        "auth-service started: issuer=%s policy=%s active_kid=%s",
        policies.policy.issuer, policies.version, keys.active_kid,
    )
    return app


def app() -> FastAPI:  # pragma: no cover - uvicorn factory: `uvicorn app.main:app --factory`
    return create_app()
