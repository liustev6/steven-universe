# Spec: file-management (file service)

**Path**: `python/services/file-management/`. **Status**: the current code is described below; a
rewrite is planned (gpu-server, its main caller, is temporarily shut down, so the API can change
freely). This document records the current design, its known problems, and the target design.

## Locked decisions
| # | Decision |
|---|---|
| D1 | **file-management is internal only.** It has no public hostname and no unauthenticated routes except `/health`. Every internet-facing file operation (public images, user uploads, downloads, share links) goes through **api-service**, the only edge. |
| D2 | **Only services call file-management**, using service tokens from [auth-service.md](auth-service.md) (phase 0: static per-service keys behind the same `Principal`). Browsers never hold a file-service credential. |
| D3 | **Storage is never exposed outside the LAN.** No storage presigned URLs are handed to browsers; the gateway streams bytes. |
| D4 | **No access tiers.** The old internal / signed / public split mixed up *who may do what* (authorization) with *how bytes are delivered*. Permissions are now **grants of actions on resource prefixes**, carried in the caller's token. "Public", "share link", and "sandbox" are just particular grants. |
| D5 | **One generic object API.** No separate routes per permission level; every route calls the same `authorize(principal, action, bucket, key)`. |
| D6 | **Buckets are for storage behaviour, prefixes are for access.** A small fixed set of buckets, each with its own retention / quota rules; all access decisions are made on `bucket/key` prefixes. |
| D7 | **auth-service decides, file-management enforces.** Grants are computed when the token is minted and travel in the token (`authorization_details`, RFC 9396). file-management checks them locally and never calls auth-service per request. |

## Scope
file-management is the only service that talks to object storage (S3 API). Other services never
hold storage credentials; they call file-management, which:

- stores and serves objects under a fixed set of buckets and namespaces;
- streams uploads of any size into storage, with a SHA-256 checksum;
- enforces, on every request, that the caller's token grants the action on that object or prefix;
- enforces size limits and per-namespace quotas, and keeps an audit log.

Its main consumer is **gpu-server** (model weights). Next come **api-service** (public files, user
files, share links) and **agent sandboxes** (the next steven-ai, the Hey Pi agent), each confined
to its own workspace prefix.

### System context (target)
```
internet ── Cloudflare Tunnel ──▶ api-service ──(token for a user)──────────┐
                                  (the only edge: user auth, size limits,   │
                                   public-file caching, share links)        ▼
gpu-server ─────────(token: read models/)─────────────────────────▶ file-management ──S3──▶ storage (RustFS)
stevenai-v2 / hey-pi-agent ──(token: workspaces/<client>/)────────▶   (LAN only)            (LAN only)
   └─ sandbox ws-7f3 ──(downscoped token: workspaces/stevenai-v2/ws-7f3/ only)──▶
```

| Component | Owns | Does not do |
|---|---|---|
| **auth-service** | Who may get which grants (policy), minting and narrowing tokens | Touching files |
| **api-service** | Internet routes, end-user login, upload limits, cache headers, share-link URLs | Storage access, holding storage credentials |
| **file-management** | Enforcing grants, key rules, quotas, streaming, the only storage credentials | Deciding policy; being reachable from the internet; user login |
| **Storage (RustFS)** | Bytes; per-bucket lifecycle rules | Any per-caller access logic |
| **Orchestrators** (stevenai-v2, hey-pi-agent) | Creating workspaces, handing sandboxes downscoped tokens | Giving sandboxes credentials |

### Storage backend
The community edition of MinIO is archived. The replacement is **RustFS**, running in LXC CT 101
`rustfs` on pve01, with the API on `:9000` and the console at `:9001/rustfs/console/`. The service
only uses standard S3 calls through boto3, so switching backends is a configuration change: set
the storage endpoint and keys. Under D3/D4 every bucket is **private** in storage, so
`put_bucket_policy` is no longer needed.

## Permission model (target)

### Buckets and namespaces
| Bucket | Namespace layout | Holds | Storage behaviour |
|---|---|---|---|
| `models` | `models/<model_id>.tar.gz` | Model weights | Versioning on; no expiry |
| `public` | `public/<path>` | Files anyone may read through the gateway | No expiry; cacheable |
| `users` | `users/<user_id>/<path>` (e.g. `users/42/outputs/…`) | A user's own files, including model outputs | Per-user quota |
| `workspaces` | `workspaces/<client>/<workspace_id>/<path>` | Agent sandbox files (stevenai-v2, hey-pi-agent) | **Lifecycle expiry** (e.g. 7 days after last write); per-workspace quota |
| `services` | `services/<client>/<path>` | A service's private scratch / state | Per-service quota |

Adding a namespace means adding a bucket (or a prefix inside one) and grant templates in
auth-service's policy. file-management code doesn't change.

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
{"type": "file_access", "actions": ["read", "write", "list"], "locations": ["workspaces/stevenai-v2/ws-7f3/"]}
```
- `locations` are `<bucket>/<prefix>` and end in `/`, or name one exact object
  (`users/42/report.pdf`).
- Grants arrive **already concrete** in the token. Templates such as `users/${user}/` are
  resolved by auth-service when the token is minted.
- The token's `scope` must also include the matching coarse scope (`files:read` for `read`, and
  so on). Both levels must allow the operation.

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
  `workspaces/stevenai-v2/` with a grant on `workspaces/stevenai-v2/ws-7f3/` is denied.
- Every decision (allow and deny) is logged with `jti`, `client_id`, `sub`, actor chain,
  action, resource, and the detail `identifier` (e.g. the workspace id).

### Key hygiene (enforced before `authorize`)
S3 treats keys as literal strings, so `users/42/../43/x` really is a key inside `users/42/`.
Anything that later mounts or normalizes paths (FUSE, proxies, the gateway) could disagree, so
keys are rejected (400) if they contain empty segments (`//`), `.` or `..` segments, `\`,
control characters, a leading `/`, or are longer than 1024 bytes. Buckets must be one of the
configured buckets.

### Quotas and limits
- **Per-request size limit** (configurable per bucket; e.g. 50 GB for `models`, 1 GB elsewhere),
  checked against `Content-Length` and enforced on the stream.
- **Byte quotas per namespace root**: per user (`users/<id>/`), per workspace
  (`workspaces/<client>/<ws>/`), per service. Usage is tracked by file-management (a small
  counter store, rebuilt from listings if lost); writes over quota get 507.
- These matter most for sandboxes (agents write a lot) and because a full LVM thin pool causes
  I/O errors on every guest.

### How each caller gets its access
| Caller | Token | Grant(s) in the token |
|---|---|---|
| gpu-server (load weights) | service token | `read` on `models/` |
| gpu-server writing a user's output | delegated token for user 42 (chained) | `write` on `users/42/outputs/` |
| api-service, public route (anonymous visitors) | service token | `read, list` on `public/` |
| api-service, logged-in user 42 | delegated token | `read, write, delete, list` on `users/42/` |
| Share link for one file | downscoped from the user's token | `read` on `users/42/report.pdf`, ≤ 1 h |
| stevenai-v2 (orchestrator) | service token | everything on `workspaces/stevenai-v2/` |
| Sandbox `ws-7f3` | downscoped by stevenai-v2 | `read, write, list` on `workspaces/stevenai-v2/ws-7f3/`, ≤ 1 h, renewed by the orchestrator |
| hey-pi-agent sandbox | downscoped by hey-pi-agent | its own `workspaces/hey-pi-agent/<ws>/` |
| admin-cli | service token | everything on `models/`, `public/` |

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
507 over quota. Error bodies are generic; details go to logs.

Removed from the current API: the `/internal`, `/signed`, `/public` route families, the frontend
key, the URL rewriting that depended on token type, the `/signed/download` proxy, `…/url`
endpoints, and public-read bucket policies.

## api-service responsibilities (requirements this design puts on it)
- **Public files:** `GET /files/public/{path}`, no login. The gateway calls file-management with
  its own service token (`read` on `public/`) and sets `Cache-Control`, so Cloudflare caches
  popular files.
- **User files:** upload, download, list, and delete routes for a logged-in user. The gateway
  gets a delegated token for that user (auth-service grant 2) and forwards it.
- **Share links:** the gateway downscopes the user's token to `read` on one object
  (auth-service grant 4) and returns `/files/s/{token}`. That route forwards the token as an
  `Authorization` header; it never logs these URLs and sets `Referrer-Policy: no-referrer`.
- **Upload limits:** enforce a size limit before streaming. Cloudflare limits a proxied request
  body to **100 MB** on the free plan, so larger uploads use the multipart routes in parts of up
  to ~95 MB, or come in over the LAN or Tailscale.
- **Known gateway gaps to fix first:** its auth is one shared `API_KEY` (`X-API-Key`, compared
  with `!=`), the same weakness as the old frontend key; it needs real per-user login before it
  serves anyone's files. A failed key check also logs the first 10 characters of the submitted
  key.

## Current design (code as it is today)
This predates D1–D7.

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
- `infra/deployment/file-management.sh` builds a Docker image and runs it in an LXC (`docker run -p 8000:8000
  --env-file .env --restart unless-stopped`).

## Problems (current code)
In priority order. P1–P6 are reachable from the internet only through file-management's tunnel
hostname, so **removing that hostname (migration step 1) takes them off the internet at once**.
They still get fixed in the rewrite, because the LAN and the tailnet aren't trusted either.

| # | Problem | Where | Impact |
|---|---|---|---|
| **P1** | `DELETE /signed/delete` and `DELETE /public/delete` have **no auth** | `signed.py` `delete_from_signed_bucket`, `public.py` `delete_from_public_bucket` | Anyone who can reach the service can delete any user upload, model output, or public file. Root cause: auth is added per route (fails open); the `router_auth` name implies protection it doesn't give. |
| **P2** | The frontend key is a shared secret shipped to browsers, so it is effectively public | `auth.py` `verify_api_access` | Anyone can upload to or overwrite `public`, list `user-uploads`, and mint a signed URL for **any** user's file. No user identity, so no per-user ownership. Removed by D2. |
| **P3** | One internal secret for all services | `auth.py` | No caller identity in logs. No scoping (gpu-server only needs to read `models` but can delete). Rotation means changing every service at once. |
| **P4** | Tokens compared with `==` / `!=` | `auth.py` | Not timing-safe; use `hmac.compare_digest`. |
| **P5** | CORS `allow_origins=["*"]` with `allow_credentials=True`; `PUT` missing from `allow_methods` | `main.py` | Too permissive. Under D1 file-management needs no CORS at all. |
| P6 | Error details sent to clients (`str(e)`, the storage response text); `/docs` public | all routers, `main.py` | Leaks internals. |
| P7 | No upload size limit: `MAX_FILE_SIZE` is defined but never used | `app/s3/config.py` | With P2, anyone can fill the disk. A full LVM thin pool causes I/O errors on every guest. |
| P8 | All uploads share one worker thread (`max_workers=1`, `MAX_CONCURRENCY = 1`) | `client.py`, `s3/config.py` | One large model upload blocks every other upload. |
| P9 | Download proxy uses blocking `requests.get` inside an `async` route, with no timeout, and doesn't close the response on errors | `signed.py` `download_file` | A slow download stalls the whole server. |
| P10 | Signed-bucket listing returns unsigned direct URLs to internal callers | `signed.py` `list_signed_bucket_files` | Those URLs return 403 (private bucket). |
| P11 | `list_files` has no pagination; `file_exists` treats every error as "not found" | `client.py` | Listing stops at 1,000 objects; a 403 or an outage looks like a missing file. |
| P12 | Access tiers are hard-coded into three routers that repeat the same handlers | `app/api/*`, `config.py` | Copies drift (P1 is such a drift), and any new permission shape (a sandbox, a per-user area) would need a new router. Replaced by D4/D5. |
| P13 | Small issues: `ensure_bucket_exists` on every upload; `Content-Disposition` built by concatenating the key; `MAX_BUFFERED_CHUNKS: int = 50` in `.env.example` isn't valid `.env` syntax | various | Extra requests, header safety, config confusion. |

## Implementation notes (rewrite)
- **Deny by default.** Auth is a dependency on the whole app; `/health` is the only exception. A
  test walks `app.routes` and fails if any other route answers without a token, and a table-driven
  test runs `authorize()` against the caller table above (including the denials: a sandbox
  listing its parent, `users/42/` touching `users/43/`, `..` keys).
- **Async storage I/O** (`aioboto3`, or boto3 in a sized thread pool) with timeouts.
- **No CORS middleware** (browsers never call file-management). `/docs` off, or LAN only.
- **Network:** bind to the LAN; the Proxmox firewall on the container allows only the gateway,
  gpu-server, orchestrators, and sandbox networks.
- **Storage credentials:** file-management's RustFS key is limited (RustFS policy) to these
  buckets, so a bug in file-management can't reach anything else in storage.
- **Dev bypass:** the `service-auth` dev principal, which refuses to start unless `ENV=local`.

## Migration order
1. **Now:** remove file-management's public hostname from the Cloudflare tunnel (D1). That takes
   P1–P6 off the internet with no code change.
2. `service-auth` phase 0 (static per-service keys → `Principal` with scopes and grants), the
   deny-by-default dependency, `authorize()`, key hygiene, and the route-coverage and
   authorization tests.
3. The `/objects` API on RustFS: create the buckets (all private, with lifecycle and versioning
   as in the bucket table) and copy `models` over (`rclone sync`). Restart gpu-server against it
   with a `read models/` key.
4. Size limits, quotas, pagination, multipart routes, async I/O.
5. auth-service phase 1–2: issued tokens with `authorization_details`, user delegation; the
   gateway's per-user routes and public file serving.
6. auth-service phase 2b: downscoped tokens for share links and agent sandboxes (stevenai-v2,
   hey-pi-agent).

## Open questions
- What does api-service use for user login (an identity provider such as Authentik, or
  something else)? Everything for user files depends on it.
- Does RustFS support the lifecycle (expiry) and versioning rules the bucket table assumes? If
  not, file-management runs a nightly cleanup for `workspaces`.
- Should end users write to `public`, or only admins and internal services?
- Where do quota counters live: a small SQLite file in the container, or recomputed from
  listings on startup?
- When does user-to-user sharing (OpenFGA / SpiceDB) become worth adding?

## Non-goals
- User accounts and login: owned by api-service or a future identity provider.
- Deciding policy: owned by auth-service.
- File versioning beyond storage-level versioning, deduplication, virus scanning.
- Exposing file-management or storage directly to the internet.
