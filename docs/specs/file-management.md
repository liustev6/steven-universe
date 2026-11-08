# Spec: file-management (file service)

**Path**: `python/services/file-management-service/` (audience and service id: `file-management`). **Status**: **rewritten** on `service_auth` (target API,
`public`/`private` buckets, 66 tests including S3 against moto); **not deployed yet** (migration
step 4). "Before the rewrite" below records the old code and its problems. auth-service is **live**
(`https://auth.lan.liustev6.ca`, CT 102), so the rewrite goes straight to issued tokens. gpu-server,
the old main caller, is shut down, so the API can change freely. Related:
[auth-service.md](auth-service.md) (tokens and policy), [api-service.md](api-service.md) (the
edge), [deployer.md](deployer.md) and [secrets-manager.md](secrets-manager.md) (how it is deployed
and gets its secrets).

## Locked decisions
| # | Decision |
|---|---|
| D1 | **file-management is internal only.** It has no public hostname and no unauthenticated routes except `/health`. Every internet-facing file operation (public images, user uploads, downloads, share links) goes through **api-service**, the only edge. |
| D2 | **Only services call file-management**, each with its own access token from auth-service (audience `file-management`), verified locally with the `service_auth` library. Browsers never hold a file-service credential. |
| D3 | **Storage is never exposed outside the LAN.** No storage presigned URLs are handed to browsers; the gateway streams bytes. |
| D4 | **No access tiers.** The old internal / signed / public split mixed up *who may do what* (authorization) with *how bytes are delivered*. Permissions are **grants of actions on resource prefixes**, carried in the caller's token. "Public", "share link", and "sandbox" are just particular grants. |
| D5 | **One generic object API.** No separate routes per permission level; every route calls the same `authorize(principal, action, bucket, key)`. |
| D6 | **Buckets are for storage behaviour, prefixes are for access.** A small fixed set of buckets, each with its own retention / quota rules; all access decisions are made on `bucket/key` prefixes. |
| D7 | **auth-service decides, file-management enforces.** Grants are computed when the token is minted and travel in the token (`authorization_details`, RFC 9396). file-management checks them locally and never calls auth-service per request. Changing who may access what is a `policy.yaml` change in auth-service, never a file-management change. |
| D8 | **Managed like every other service.** Docker Compose on its own LXC with the hardening from auth-service (deployer D11); secrets are files readable only by the container user (uid 10001, mode 600), mounted read-only, never `.env` or environment variables (deployer D7); deployed from a pushed commit, by hand now and by the deployer later. |
| D9 | **TLS on the LAN.** Callers send bearer tokens, so file-management serves HTTPS as `https://files.lan.liustev6.ca`, with a Let's Encrypt certificate obtained like auth-service's (DNS-01). |
| D10 | **Two buckets for now: `public` and `private`.** There is no user concept yet, so no `users` bucket; each service's files live under `private/<client>/`. More buckets (`models`, `users`, `workspaces` with expiry) are added only when a caller needs them, as a config + policy change (see "Later buckets"). |

## Scope
file-management is the only service that talks to object storage (S3 API). Other services never
hold storage credentials; they call file-management, which:

- stores and serves objects under a fixed set of buckets and namespaces;
- streams uploads of any size into storage, with a SHA-256 checksum;
- enforces, on every request, that the caller's token grants the action on that object or prefix;
- enforces size limits and per-namespace quotas, and keeps an audit log.

**Callers, in rollout order:** **pi-server** (Hey Pi's backend) for its own files under
`private/pi-server/`; **api-service** for public files and for relaying outside machine clients;
then **gpu-server** (model weights) when it comes back; later **users** through api-service (once
there is an identity provider) and **agent sandboxes** confined to one folder (once downscoping is
fixed in auth-service). gpu-server and users each bring a new bucket (D10).

### System context (target)
```
internet ── Cloudflare Tunnel ──▶ api-service ──(own token: public/ │ relayed client │ later: user)──┐
                                  (the only edge)                                                    ▼
pi-server (LAN) ───────────(token: private/pi-server/)────────────────────────────▶ file-management ──S3──▶ RustFS
gpu-server (later) ────────(token: read models/, a later bucket)──────────────────▶   (LAN only, TLS)      (CT 101)
orchestrator sandboxes (later) ──(downscoped token: one workspace)──────────────────▶
                                                         ▲
                          auth-service (CT 102) ── mints tokens; publishes JWKS ──┘ (fetched + cached)
```

| Component | Owns | Does not do |
|---|---|---|
| **auth-service** | Who may get which grants (`policy.yaml`), minting and narrowing tokens, signing keys | Touching files |
| **api-service** | Internet routes, end-user login (later), upload limits, cache headers, share-link URLs | Storage access, holding storage credentials |
| **file-management** | Enforcing grants, key rules, quotas, streaming, the only storage credentials | Deciding policy; being reachable from the internet; user login |
| **Storage (RustFS)** | Bytes; per-bucket lifecycle rules | Any per-caller access logic |
| **Orchestrators** (pi-server's agent, later stevenai-v2) | Creating workspaces, handing sandboxes downscoped tokens | Giving sandboxes credentials |

### Storage backend
The community edition of MinIO is archived. The replacement is **RustFS**, running in LXC CT 101
`rustfs` on pve01 (currently `192.168.50.123` by DHCP; it needs a reservation like CT 102), with
the API on `:9000` and the console at `:9001/rustfs/console/`. The service only uses standard S3
calls, so switching backends is a configuration change. Under D3/D4 every bucket is **private**
in storage, so `put_bucket_policy` is no longer needed.

## Integration with auth-service (as deployed)
file-management is a **resource server** (audience `file-management`). It is **not** an
auth-service client: it calls no other service, so it has no client secret. Configuration:

```sh
SERVICE_AUTH_ISSUER=https://auth.lan.liustev6.ca     # the only auth setting it needs
```
```python
auth = ServiceAuth.from_env("file-management")        # service_auth.fastapi_auth

@router.put("/objects/{bucket}/{key:path}")
async def put_object(bucket: str, key: str, p: Principal = Depends(auth.require("files:write"))):
    authorize(p, "write", bucket, key)                # level 2: the location
```
- **Verification is local** (`service_auth`): ES256 only, `typ: at+jwt`, issuer, `aud` =
  `file-management`, expiry, required claims. Public keys come from auth-service's JWKS, cached
  5 minutes, refreshed early for an unknown `kid`, kept up to 24 h if auth-service is down. If no
  keys can be obtained at all, requests get 503.
- **What arrives in a token**, today (auth-service `policy.yaml`):

  | Caller | Token | `Principal` | Grants |
  |---|---|---|---|
  | pi-server (direct, on the LAN) | its own token | `service=pi-server` | `read write delete list` on `private/pi-server/` |
  | pi-server through api-service (from outside) | relayed (grant 3) | `service=api-service`, `on_behalf_of=service:pi-server` | the same as pi-server direct; never api-service's own |
  | api-service, public route (anonymous visitors) | its own token | `service=api-service` | `read list` on `public/` |

  `authorize()` looks only at the grants, so a relayed request is handled exactly like a direct
  one; the actor chain is logged.
- **Detail type contract.** auth-service's policy defines `file_access` with `actions: [read,
  write, delete, list]` and `location_roots: [public/, private/]`.
  Those roots **must equal** file-management's bucket list. Adding a bucket means changing both
  (file-management config, then the policy), in that order.
- **Verified end to end** against the live auth-service (2026-11-07, with the real
  `service_auth` library): pi-server's grants, api-service's relay of pi-server, api-service's own
  `public/` token, and the rejections (wrong secret, wrong audience, exchanging someone else's
  token). Every auth-service deploy now repeats the core of this as the `smoke-test` client
  (`python/services/auth-service/scripts/smoke_test.py`), whose only grant is `private/smoke-test/`.

## Permission model (target)

### Buckets and namespaces
| Bucket | Namespace layout | Holds | Storage behaviour |
|---|---|---|---|
| `public` | `public/<path>` | Files anyone may read through api-service (images, downloads on the website) | No expiry; cacheable; versioning on |
| `private` | `private/<client>/<path>` (e.g. `private/pi-server/notes.txt`, `private/pi-server/workspaces/<ws>/…`) | Each service's own files; a service's agent workspaces are sub-folders of its own folder | Per-client quota; versioning on |

Everything in `private` belongs to exactly one client, named by the first path segment, and the
policy grants each client only `private/${client}/`. Both buckets keep old versions, because
`write` can overwrite (see Actions).

#### Later buckets (not created now)
| Bucket | Added when | Holds | Storage behaviour |
|---|---|---|---|
| `models` | gpu-server returns | `models/<model_id>.tar.gz`, model weights | Versioning on; no expiry |
| `users` | an identity provider exists | `users/<user_id>/<path>`, a user's own files and model outputs | Per-user quota |
| `workspaces` | sandboxes need automatic expiry | `workspaces/<client>/<ws>/<path>` | **Lifecycle expiry** (e.g. 7 days after last write) |

Adding a bucket means: add it to file-management's bucket list and create it in RustFS, then add
it to the policy's `location_roots` and grant it in `policy.yaml`. file-management code doesn't
change.

### Actions
| Action | Covers |
|---|---|
| `read` | `GET` / `HEAD` an object |
| `write` | `PUT` an object (create or overwrite), multipart upload |
| `delete` | `DELETE` an object |
| `list` | List keys under a prefix |

### Grants
A grant is an RFC 9396 authorization detail of type `file_access`:
```json
{"type": "file_access", "actions": ["read", "write", "list"], "locations": ["private/pi-server/workspaces/run-7/"]}
```
- `locations` are `<bucket>/<prefix>` and end in `/`, or name one exact object
  (`public/brochure.pdf`).
- Grants arrive **already concrete** in the token. Templates such as `private/${client}/` are
  resolved by auth-service when the token is minted.
- The token's `scope` must also include the matching coarse scope (`files:read` for `read`, and
  so on). Both levels must allow the operation: the route's `require(...)` checks the scope, then
  `authorize()` checks the location.

### The authorization check
Every route calls one function; nothing else decides access:
```python
def authorize(p: Principal, action: Action, bucket: str, key_or_prefix: str) -> None:
    resource = f"{bucket}/{key_or_prefix}"
    validate_key(resource)                                   # key hygiene, below
    if f"files:{action}" not in p.scopes:
        raise Forbidden()                                    # 403 insufficient_scope
    for d in p.details("file_access"):
        if action in d["actions"] and any(resource.startswith(loc) for loc in d["locations"]):
            return                                           # allowed
    raise Forbidden()                                        # deny by default
```
- For `list`, the requested prefix itself must start with a granted location. Listing
  `private/pi-server/` with a grant on `private/pi-server/workspaces/run-7/` is denied.
- Every decision (allow and deny) is logged with `jti`, `client_id`, `sub`, `on_behalf_of`, the
  actor chain, action, resource, and the detail `identifier` (e.g. the workspace id).

### Key hygiene (enforced before `authorize`)
S3 treats keys as literal strings, so `private/pi-server/../api-service/x` really is a key inside
`private/pi-server/`.
Anything that later mounts or normalizes paths (FUSE, proxies, the gateway) could disagree, so
keys are rejected (400) if they contain empty segments (`//`), `.` or `..` segments, `\`,
control characters, a leading `/`, or are longer than 1024 bytes. Buckets must be one of the
configured buckets. (auth-service applies the same rules to policy locations.)

### Quotas and limits
- **Per-request size limit** (configurable per bucket; e.g. 50 GB for `models`, 1 GB elsewhere),
  checked against `Content-Length` and enforced on the stream.
- **Byte quotas per client** (`private/<client>/`), and a total for `public/`. Usage is tracked by file-management (a small
  counter store, rebuilt from listings if lost); writes over quota get 507.
- These matter most for sandboxes (agents write a lot) and because a full LVM thin pool causes
  I/O errors on every guest.

### How each caller gets its access
| Caller | When | Token | Grant(s) in the token |
|---|---|---|---|
| pi-server | **now** | its own token | `read write delete list` on `private/pi-server/` |
| pi-server from outside, via api-service | **now** | relayed by api-service | the same |
| api-service, public route (anonymous visitors) | **now** | its own token | `read list` on `public/` |
| admin tooling (uploading public files) | when needed | its own token | `read write delete list` on `public/` |
| Sandbox of pi-server's agent | after the downscoping fix | downscoped by pi-server | `read write list` on `private/pi-server/workspaces/<ws>/`, ≤ 1 h |
| gpu-server (load weights) | when it returns (+ `models` bucket) | its own token | `read` on `models/` |
| api-service, logged-in user 42 | with users (+ `users` bucket, identity provider) | delegated token | `read write delete list` on `users/42/` |
| Share link for one user file | with users, after the downscoping fix | downscoped from the user's token | `read` on `users/42/report.pdf`, ≤ 1 h |

"When" rows that aren't **now** need a `policy.yaml` change (and, for users and downscoping, the
auth-service features noted), never a file-management change.

### What this model can't express (and the upgrade path)
Grants are written into tokens when they're minted, so they cover ownership, sandboxes, and
short share links. They can't express "user 42 shared a folder with user 7" or "everyone in
group X", because that needs a lookup on each request. When that's needed, the industry answer
is relationship-based access control (Zanzibar; **OpenFGA** or **SpiceDB**): `authorize()`
additionally asks that service when no token grant matches. All decisions already go through
`authorize()`, so nothing else changes.

## Target API
| Route | Action checked | Notes |
|---|---|---|
| `PUT /objects/{bucket}/{key}` | `write` | Streaming upload (pipeline below); size limit and quota checked first. Response: `bucket`, `key`, `size_bytes`, `sha256`, `etag`. |
| `GET /objects/{bucket}/{key}` | `read` | Streaming download with `Content-Type`, `Content-Length`, `ETag`, `Last-Modified`; supports `Range` and `If-None-Match`. |
| `HEAD /objects/{bucket}/{key}` | `read` | Metadata only. |
| `DELETE /objects/{bucket}/{key}` | `delete` | Idempotent (204 even if missing, once authorized). |
| `GET /objects/{bucket}?prefix=&cursor=&limit=` | `list` on `bucket/prefix` | Paginated (`next_cursor`), max 1000 per page. |
| `POST /objects/{bucket}/{key}:multipart` (+ `…/parts/{n}`, `…:complete`, `…:abort`) | `write` | For uploads above one request's limit (e.g. via the gateway, where Cloudflare caps a request at 100 MB). Follows S3 multipart semantics. |
| `GET /health` | none | Storage connectivity. The only unauthenticated route. |

Status codes: 400 bad key/bucket, 401 missing/invalid token (`WWW-Authenticate: Bearer
error="invalid_token"`), 403 not granted (`error="insufficient_scope"`), 404 not found (only
returned *after* authorization, so callers can't probe for keys they can't read), 413 too large,
503 token keys unavailable, 507 over quota. Error bodies are generic; details go to logs. The
401/403/503 responses come from `service_auth` and follow RFC 6750.

Removed from the current API: the `/internal`, `/signed`, `/public` route families, the frontend
key, the URL rewriting that depended on token type, the `/signed/download` proxy, `…/url`
endpoints, and public-read bucket policies.

## Deployment and secrets (target, D8/D9)
Same model as auth-service (its README is the reference):

| Item | Value |
|---|---|
| Host | its own unprivileged LXC on pve01 (`nesting=1,keyctl=1` for Docker), DHCP reservation, `startup order` after auth-service and RustFS |
| Name | `files.lan.liustev6.ca` → that IP (Cloudflare, DNS only) |
| Runtime | `python/services/file-management-service/compose.yaml`: read-only filesystem, non-root user, `cap_drop: [ALL]`, `no-new-privileges`; published on 443 → container port, so callers use `https://files.lan.liustev6.ca` |
| TLS | Let's Encrypt via DNS-01 (its own Cloudflare token limited to DNS edit on `liustev6.ca`), certbot hook copies the certificate for the container user |
| Non-secret config | environment in `compose.yaml`: `SERVICE_AUTH_ISSUER`, storage endpoint, bucket list, size limits |
| Secrets | `/etc/file-management/credentials/rustfs_access_key`, `rustfs_secret_key` (owner 10001, the container user; mode 600), mounted read-only at `/run/secrets/`; the app reads files and refuses `FILES_STORAGE_*_KEY` env vars |
| Storage credential scope | a RustFS user and policy limited to the `public` and `private` buckets, so a file-management bug can't reach anything else in storage |
| Deploy now | `infra/deployment/file-management-service.sh`, rewritten like `auth-service.sh`: build from a **pushed** commit on the host, health-checked, readable status |
| Deploy later | deployer manifest `deploy/services/file-management/deploy.yaml`; the RustFS keys become **managed** secrets in the secrets manager (`kv/services/file-management/rustfs_*`), injected on each deploy |

Secrets that disappear in the rewrite: `INTERNAL_SECRET_KEY` and `FRONTEND_API_KEY` (replaced by
auth-service tokens) and `PUBLIC_SERVICE_URL` (no more URL rewriting).

## api-service responsibilities (requirements this design puts on it)
- **Public files (now):** `GET /files/public/{path}`, no login. The gateway calls file-management
  with its own token (`read list` on `public/`) and sets `Cache-Control`, so Cloudflare caches
  popular files.
- **Relayed machine clients (now):** a client such as pi-server calling from outside gets its
  request relayed (auth-service grant 3), so file-management sees pi-server's own grants.
- **User files (with an identity provider):** upload, download, list, and delete routes for a
  logged-in user, with a delegated token for that user (auth-service grant 2).
- **Share links (after the downscoping fix):** the gateway downscopes the user's token to `read`
  on one object (grant 4) and returns `/files/s/{token}`. That route forwards the token as an
  `Authorization` header, never logs these URLs, and sets `Referrer-Policy: no-referrer`.
- **Upload limits:** enforce a size limit before streaming. Cloudflare limits a proxied request
  body to **100 MB** on the free plan, so larger uploads use the multipart routes in parts of up
  to ~95 MB, or come in over the LAN or Tailscale.

## Before the rewrite (for reference)
The old code, replaced by the rewrite; kept because the problems below explain the design.

### Bucket tiers (`app/core/config.py`)
Buckets are configured lists; a bucket's tier decides which router may touch it. Each route
rejects buckets of the wrong tier with 400.

| Tier | Buckets | Storage policy | Who reads | Who writes |
|---|---|---|---|---|
| **Internal** | `models` | private | internal token (streamed through the service) | internal token |
| **Signed** | `user-uploads`, `model-outputs` | private | anyone holding a valid signed URL | internal or frontend token |
| **Public** | `public` | public-read | anyone | internal or frontend token |

Buckets are created at startup (`lifespan` in `app/main.py`) and again before each upload
(`ensure_bucket_exists`). The policy is set only when a bucket is created.

### API
| Route | Auth today | Notes |
|---|---|---|
| `PUT /internal/upload/{bucket}/{key}` | internal | Raw-body streaming upload |
| `GET /internal/download/{bucket}/{key}` | internal | Streams from storage; used by gpu-server |
| `GET /internal/list`, `GET /internal/url` | internal | `url` is the direct storage URL |
| `DELETE /internal/delete` | internal | |
| `PUT /signed/upload/{bucket}/{key}` | internal or frontend | Returned URL depends on the token type |
| `POST /signed/url` | internal or frontend | Internal: direct presigned URL. Frontend: presigned URL rewritten to `PUBLIC_SERVICE_URL/signed/download/...` |
| `GET /signed/download/{bucket}/{key}?X-Amz-...` | none (the signature is the auth) | Forwards the query string to storage, which checks signature and expiry |
| `GET /signed/list` | internal or frontend | |
| `DELETE /signed/delete` | **none (bug, see P1)** | |
| `PUT /public/upload/{bucket}/{key}` | internal or frontend | |
| `GET /public/download/{bucket}/{key}`, `GET /public/url`, `GET /public/list` | none | |
| `DELETE /public/delete` | **none (bug, see P1)** | |
| `GET /health`, `GET /`, `/docs` | none | `/health` checks the storage connection |

Request and response models live in `python/libs/shared-schemas` (`shared_schemas.file_service`,
`shared_schemas.common`).

### Auth (`app/core/auth.py`)
There are two static bearer keys, both set from environment variables:

- `INTERNAL_SECRET_KEY`: every backend service uses the same key.
- `FRONTEND_API_KEY`: intended for browser/frontend clients.

`verify_internal_token` accepts only the internal key. `verify_api_access` accepts either key
and returns which one was used (`TokenType`). Endpoints use that to decide which kind of URL to
return: a direct storage URL for internal callers, a public-domain proxy URL for frontend callers.
Every route adds auth itself with a `Depends(...)` parameter; the routers named `router_auth`
apply no auth on their own.

### Upload pipeline (`app/s3/client.py`, `app/utils/streaming.py`)
```
request.stream() ─▶ AsyncChunkBuffer (bounded asyncio.Queue, MAX_BUFFERED_CHUNKS × ~256 KB, SHA-256)
                 ─▶ boto3 upload_fileobj in a ThreadPoolExecutor (multipart: 5 MB threshold, 10 MB parts)
                 ─▶ storage
```
`AsyncChunkBuffer` connects the async request body to boto3's blocking file-like `read()` using
`run_coroutine_threadsafe`. The bounded queue gives backpressure, so memory stays flat for any
file size. The response includes `sha256` and `size_bytes`. The rewrite keeps this pipeline
(with a properly sized thread pool, see P8).

### Signed download proxy (`app/api/signed.py`)
Storage sits on the LAN, so presigned URLs are rewritten from `http://<storage>/<bucket>/<key>?...`
to `<PUBLIC_SERVICE_URL>/signed/download/<bucket>/<key>?...`. The proxy route rebuilds the storage
URL, forwards the untouched query string, and streams the response back. Storage stays the
authority on signature and expiry. The rewrite removes it: share links become downscoped tokens
served through the gateway.

### Config and deploy
- Settings come from `.env` through pydantic-settings: storage endpoint and keys, `PUBLIC_SERVICE_URL`,
  the two auth keys, signed-URL expiry (default 1 h, max 24 h), `MAX_BUFFERED_CHUNKS`, and the bucket lists.
- `infra/deployment/file-management-service.sh` (before the rewrite) rsyncs the working copy to the LXC, writes a `.env` there
  from the laptop's `.env`, builds the image on the host, and runs it (`docker run -p 8000:8000
  --env-file .env --restart unless-stopped`).

## Problems (old code)
In priority order. P1–P6 are reachable from the internet only through file-management's tunnel
hostname, so **removing that hostname (migration step 1) takes them off the internet at once**.
They still get fixed in the rewrite, because the LAN and the tailnet aren't trusted either.

| # | Problem | Where | Impact |
|---|---|---|---|
| **P1** | `DELETE /signed/delete` and `DELETE /public/delete` have **no auth** | `signed.py` `delete_from_signed_bucket`, `public.py` `delete_from_public_bucket` | Anyone who can reach the service can delete any user upload, model output, or public file. Root cause: auth is added per route (fails open); the `router_auth` name implies protection it doesn't give. |
| **P2** | The frontend key is a shared secret shipped to browsers, so it is effectively public | `auth.py` `verify_api_access` | Anyone can upload to or overwrite `public`, list `user-uploads`, and mint a signed URL for **any** user's file. No user identity, so no per-user ownership. Removed by D2. |
| **P3** | One internal secret for all services | `auth.py` | No caller identity in logs. No scoping (gpu-server only needs to read `models` but can delete). Rotation means changing every service at once. Removed by D2. |
| **P4** | Tokens compared with `==` / `!=` | `auth.py` | Not timing-safe. Gone with the static keys (D2). |
| **P5** | CORS `allow_origins=["*"]` with `allow_credentials=True`; `PUT` missing from `allow_methods` | `main.py` | Too permissive. Under D1 file-management needs no CORS at all. |
| P6 | Error details sent to clients (`str(e)`, the storage response text); `/docs` public | all routers, `main.py` | Leaks internals. |
| P7 | No upload size limit: `MAX_FILE_SIZE` is defined but never used | `app/s3/config.py` | With P2, anyone can fill the disk. A full LVM thin pool causes I/O errors on every guest. |
| P8 | All uploads share one worker thread (`max_workers=1`, `MAX_CONCURRENCY = 1`) | `client.py`, `s3/config.py` | One large model upload blocks every other upload. |
| P9 | Download proxy uses blocking `requests.get` inside an `async` route, with no timeout, and doesn't close the response on errors | `signed.py` `download_file` | A slow download stalls the whole server. |
| P10 | Signed-bucket listing returns unsigned direct URLs to internal callers | `signed.py` `list_signed_bucket_files` | Those URLs return 403 (private bucket). |
| P11 | `list_files` has no pagination; `file_exists` treats every error as "not found" | `client.py` | Listing stops at 1,000 objects; a 403 or an outage looks like a missing file. |
| P12 | Access tiers are hard-coded into three routers that repeat the same handlers | `app/api/*`, `config.py` | Copies drift (P1 is such a drift), and any new permission shape (a sandbox, a per-user area) would need a new router. Replaced by D4/D5. |
| P13 | Small issues: `ensure_bucket_exists` on every upload; `Content-Disposition` built by concatenating the key; `MAX_BUFFERED_CHUNKS: int = 50` in `.env.example` isn't valid `.env` syntax | various | Extra requests, header safety, config confusion. |
| P14 | Secrets live in a plain-text `.env` on the laptop and on the host and reach the app as environment variables | `.env`, deploy script | Visible in `docker inspect`, inherited by child processes. Replaced by D8. |
| P15 | Plain HTTP on the LAN, and the image is built on the host from an rsynced working copy as `:latest` | deploy script, `docker run` | Tokens (today: the static keys) cross the LAN unencrypted; no record of which commit runs, no rollback. Replaced by D8/D9. |

## Implementation notes (rewrite)
- **Deny by default.** Auth is a dependency on the whole app (`ServiceAuth.require(...)` per
  route); `/health` is the only exception. A test walks `app.routes` and fails if any other route
  answers without a token, and a table-driven test runs `authorize()` against the caller table
  above (including the denials: a sandbox listing its parent folder, `private/pi-server/`
  touching `private/api-service/`, writing to `public/` with a read-only grant, `..` keys).
- **Tests with real tokens.** Use the `FakeIssuer` pattern from `service_auth`'s tests (an
  in-process issuer with its own JWKS) rather than mocking `Principal`, so the verifier path is
  exercised too.
- **Async storage I/O** (`aioboto3`, or boto3 in a sized thread pool) with timeouts.
- **No CORS middleware** (browsers never call file-management). `/docs` off.
- **Network:** LAN only; the Proxmox firewall on the container allows only api-service, the Pi,
  and later gpu-server and orchestrators.
- **Dev bypass:** the `service-auth` dev principal, which refuses to start unless `ENV=local`.

## Migration order
1. **Now:** remove file-management's public hostname from the Cloudflare tunnel (D1). That takes
   P1–P6 off the internet with no code change.
2. **Rewrite on `service_auth`:** `ServiceAuth.from_env("file-management")`, `require()` per
   route, `authorize()`, key hygiene, the route-coverage and authorization tests. Drop the static
   keys.
3. **`/objects` API on RustFS:** create the `public` and `private` buckets (both private in
   storage, versioning on) and a RustFS user limited to them. The old `models` data stays where
   it is until gpu-server returns and the `models` bucket is added.
4. **Deploy like auth-service (D8/D9):** LXC, `files.lan.liustev6.ca` + certificate,
   `compose.yaml`, secrets as files, `infra/deployment/file-management-service.sh` rewritten to deploy a
   pushed commit. Verify with a smoke test: pi-server writes and reads `private/pi-server/`,
   is denied elsewhere.
5. **pi-server as the first caller** (its client secret via `scripts/add-client.sh --rotate`),
   then **api-service**'s public and relayed routes.
6. Size limits, quotas, pagination, multipart routes, async I/O.
7. **Later, each a bucket and/or policy change plus the noted auth-service work:** gpu-server
   (`models` bucket, `read models/`), users (`users` bucket, identity provider, grant 2), share
   links and sandboxes (downscoping fix, grant 4).

## Open questions
- What does api-service use for user login (an identity provider such as Authentik, or
  something else)? User files depend on it.
- RustFS **accepts** versioning and lifecycle rules (checked 2026-11-07): `public` and `private`
  have versioning on, old versions expire after 30 days, abandoned multipart uploads after 1 day.
  Still to confirm that expiry actually runs (look again after 30 days); if not, file-management
  runs a nightly cleanup.
- Should end users write to `public`, or only admins and internal services?
- Where do quota counters live: a small SQLite file in the container (would need one writable
  volume), or recomputed from listings on startup?
- When does user-to-user sharing (OpenFGA / SpiceDB) become worth adding?

## Non-goals
- User accounts and login: owned by api-service or a future identity provider.
- Deciding policy: owned by auth-service.
- File versioning beyond storage-level versioning, deduplication, virus scanning.
- Exposing file-management or storage directly to the internet.
