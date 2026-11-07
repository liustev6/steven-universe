# service-auth

Get and check auth-service tokens. Spec: `docs/specs/auth-service.md`.

Install in a service (dev): `-e ../../libs/service-auth[fastapi]` in `requirements-dev.txt`.

## Receiving requests (FastAPI)

```python
from service_auth import Principal
from service_auth.fastapi_auth import ServiceAuth

auth = ServiceAuth.from_env("file-management")   # SERVICE_AUTH_ISSUER

@router.get("/objects/{bucket}/{key:path}")
async def get_object(bucket: str, key: str, p: Principal = Depends(auth.require("files:read"))):
    authorize(p, "read", bucket, key)            # the service's own resource check, using p.details("file_access")
```

Each request's token is checked locally: ES256 only, `typ: at+jwt`, issuer,
audience = this service, expiry (30 s leeway), required claims. Public keys
come from the issuer's JWKS, cached 5 minutes, refreshed early for an unknown
`kid`, and kept for up to 24 hours if the issuer is down. Errors follow RFC 6750
(401 `invalid_token`, 403 `insufficient_scope`).

Local development without tokens: `ENV=local` and
`SERVICE_AUTH_DEV_PRINCIPAL='{"service":"dev","scopes":["*"]}'` (refused otherwise).

## Calling other services

```python
from service_auth import TokenClient, file_access

tokens = TokenClient.from_env()   # SERVICE_AUTH_ISSUER, SERVICE_AUTH_CLIENT_ID, SERVICE_AUTH_CLIENT_SECRET_FILE

# as this service (grant 1)
files = httpx.AsyncClient(base_url=..., auth=tokens.auth("file-management", scopes=["files:read"]))
# for the caller in an incoming request (grant 3)
as_caller = httpx.AsyncClient(base_url=..., auth=tokens.auth_for(principal, "file-management"))
# for a user api-service authenticated (grant 2)
as_user = httpx.AsyncClient(base_url=..., auth=tokens.auth_as_user("42", "file-management"))
# a narrower token for a sandbox (grant 4)
sandbox = await tokens.downscope(own_token, "file-management",
    details=[file_access(["read", "write", "list"], "workspaces/stevenai-v2/ws-7f3/", identifier="ws-7f3")],
    expires_in=3600)
```

Tokens are cached per (audience, scopes, details, subject) and renewed 60 s
before expiry; concurrent callers share one request. Issuer failures are
retried with backoff, and a still-valid cached token is used while the issuer
is down. On a 401 from the target service, the token is dropped and the request
retried once. The client secret must come from a file; a secret in an
environment variable is refused.

Tests: `python/services/auth-service/.venv/bin/python -m pytest python/libs/service-auth/tests`
