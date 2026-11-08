# file-management

The only service that talks to object storage (RustFS, S3 API). It is internal (LAN only, no
tunnel hostname); other services call it with tokens from auth-service, and it enforces what the
token grants.

**Path**: `python/services/file-management-service/` · **Audience / service id**: `file-management`
**Spec**: `docs/specs/file-management.md` · **Status**: rewritten, tested, **not deployed yet**
(migration step 4: LXC, `files.lan.liustev6.ca`, certificate, deploy script).

## API

| Route | Needs | Notes |
|---|---|---|
| `PUT /objects/{bucket}/{key}` | `files:write` + grant | Streaming upload; response `bucket, key, size_bytes, sha256, etag` |
| `GET /objects/{bucket}/{key}` | `files:read` + grant | Streaming download; `Range` (206), `If-None-Match` (304) |
| `HEAD /objects/{bucket}/{key}` | `files:read` + grant | Metadata only |
| `DELETE /objects/{bucket}/{key}` | `files:delete` + grant | Idempotent, 204 |
| `GET /objects/{bucket}?prefix=&cursor=&limit=` | `files:list` + grant on the prefix | Paginated (`next_cursor`), max 1000 |
| `GET /health` | none | `{"status", "storage"}`; 503 if storage is unreachable |

Buckets: `public` (what anonymous visitors may read through api-service) and `private`
(`private/<client>/…`, each service's own files). No `/docs`, no CORS.

Status codes: 400 bad bucket/key, 401 no or invalid token, 403 not granted, 404 not found (only
after authorization), 413 too large, 416 bad range, 502 storage error, 503 token keys unavailable.

## How a request is checked

```
Authorization: Bearer <token for aud=file-management>
   │ 1. service_auth (route's require("files:<action>")): signature, issuer, audience, expiry, scope
   │ 2. resource_name(): bucket is known; key has no "..", "//", "\", control chars, leading "/"
   │ 3. authorize(): a file_access grant has <action> on a location covering <bucket>/<key>
   ▼ 4. storage
```
Every allow/deny decision is one JSON line on the `file_management.audit` logger. What a caller
may do is decided in auth-service's `policy.yaml`, never here.

## Layout

```
app/
  main.py          create_app(): storage, auth, routes, error mapping; app() factory for uvicorn
  config.py        Settings (FILES_*); storage credentials read from files
  authz.py         resource_name() (key hygiene) and authorize() (the access decision)
  api/objects.py   the /objects routes
  storage/         Storage interface; s3.py (aioboto3, RustFS) and memory.py (tests, local dev)
  content_type.py  Content-Type from the client or the key's extension
tests/
  test_authz.py        table-driven authorize() and key rules
  test_api.py          HTTP API with real signed tokens and in-memory storage
  test_s3_storage.py   S3Storage and the full HTTP stack against moto (local S3)
Dockerfile, compose.yaml   hardened container (uid 10001, read-only, no capabilities, TLS on 443)
```

## Local development

```sh
cd python/services/file-management-service
uv venv .venv --python 3.11
uv pip install -p .venv -r requirements-dev.txt     # includes ../../libs/service-auth
.venv/bin/python -m pytest                           # 66 tests, no network needed
```

Run it without RustFS or auth-service (in-memory storage, a fixed dev identity; refused unless
`ENV=local`):
```sh
ENV=local FILES_STORAGE=memory \
SERVICE_AUTH_DEV_PRINCIPAL='{"service":"dev","scopes":["*"],"authorization_details":[{"type":"file_access","actions":["read","write","delete","list"],"locations":["public/","private/"]}]}' \
.venv/bin/uvicorn app.main:app --factory --port 8000
curl -X PUT --data-binary @README.md localhost:8000/objects/public/readme.md
```

## Deployment (not done yet)

Same model as auth-service (`python/services/auth-service/README.md`): its own LXC, Docker Compose,
`https://files.lan.liustev6.ca` with a Let's Encrypt certificate (DNS-01), and on the host:

```
/etc/file-management/            owner 10001, mode 700
  credentials/rustfs_access_key   mounted read-only at /run/secrets/
  credentials/rustfs_secret_key
  tls/cert.pem, tls/key.pem
```
The RustFS user should be limited to the `public` and `private` buckets. `infra/deployment/
file-management-service.sh` is the old script and refuses to run until it is rewritten like
`infra/deployment/auth-service.sh`.

## Not implemented yet (spec migration step 6)
- Per-client quotas (507), multipart routes for uploads over one request's limit.
