# Spec: file-management (object storage behind tokens)

**Status: live** (2026-11-08). `https://files.lan.liustev6.ca`, LAN only, CT 103. Deployed by the
pipeline (wave 1). Today's only caller is the deploy smoke test.
**Paths:**
- `python/services/file-management-service/`: the service; token audience `file-management`;
- `infra/services/file-management/`: `deploy.yaml` (the manifest) and `config.env` (settings);
- `infra/services/auth-service/policy.yaml`: who may do what.

Related: [auth-service.md](auth-service.md) (tokens and policy), [rustfs.md](rustfs.md) (the
storage), [api-service.md](api-service.md) (the public edge), [deployer.md](deployer.md),
[secrets-manager.md](secrets-manager.md).

## What it is
file-management is the **only service that talks to object storage** (RustFS, S3 API). Other
services never hold storage credentials: they call it with a token from auth-service, and it checks
that the token grants that action on that exact object before touching storage.

```
caller (a service)                 file-management (CT 103)                RustFS (CT 101)
┌───────────────┐  HTTPS + Bearer  ┌─────────────────────────────┐ S3 API  ┌─────────────┐
│ token from    │─────────────────▶│ verify token, check key,    │────────▶│ public      │
│ auth-service, │  /objects/b/k    │ authorize(), stream bytes   │ limited │ private     │
│ aud=file-mgmt │◀─────────────────│                             │◀────────│ (versioned) │
└───────────────┘                  └─────────────────────────────┘   key   └─────────────┘
```

It is **internal**: no public hostname. Anything from the internet (public website images, later
user uploads and share links) will go through api-service, which calls file-management on the
visitor's behalf.

## Decisions
| ID | Decision |
|---|---|
| D1 | **Internal only.** No public hostname; `/health` is the only route without a token. Internet traffic goes through api-service: one edge to secure. |
| D2 | **Only services call it**, each with its own auth-service token, verified locally. Browsers never hold a file-service credential. Every log line names the caller; no shared keys. |
| D3 | **Storage never leaves the LAN.** No presigned storage URLs; bytes stream through, so every access is decided here. |
| D4 | **No access tiers.** Permissions are grants of actions on prefixes; "public", "sandbox" and "share link" are just particular grants. |
| D5 | **One generic object API, one `authorize()`.** No per-tier routers drifting apart. |
| D6 | **Buckets are for storage behaviour, prefixes for access.** Retention and versioning are per bucket; access is per prefix. |
| D7 | **auth-service decides, file-management enforces.** Changing access is a `policy.yaml` change, never a code change here. |
| D8 | **Managed like every service:** hardened Docker Compose on its own LXC, secrets as files owned by the container user, image built in GitHub and deployed by digest (deployer D1, D11). |
| D9 | **TLS on the LAN** as `files.lan.liustev6.ca`, Let's Encrypt via DNS-01, because callers send bearer tokens. |
| D10 | **Two buckets for now: `public` and `private`.** Each service's files live under `private/<client>/`. Add buckets when a caller needs them. |
| D11 | **Actions are independent:** `write` doesn't imply `read` or `list`; `read` doesn't imply `list`. Write-only drop boxes and single-file sharing are real cases; S3, GCS and Azure work the same way. |
| D12 | **A `Storage` interface** (`app/storage/`) with an S3 implementation (RustFS, async `aioboto3`) and an in-memory one (tests and local development only). |
| D13 | **Streaming both ways.** Uploads go to S3 in 8 MiB multipart parts (memory is about one part per upload); a failure aborts the multipart upload, so no parts are orphaned. Downloads stream, with `Range` and `If-None-Match` passed to storage. |
| D14 | **The RustFS key is limited** to object operations in the two buckets: a bug here can't delete buckets, change versioning or reach other data. |
| D15 | **Every deploy ends with a smoke test** as auth-service's `smoke-test` client, on real RustFS. It proves the whole live path and never uses a real client's credentials. |

## How a request is handled
Every route in `app/api/objects.py` follows the same order:
```
caller ── PUT https://files.lan.liustev6.ca/objects/private/pi-server/notes.txt
          Authorization: Bearer <token for aud=file-management>
   │ 1. service_auth   signature, issuer, audience, expiry; the route's scope (files:write)  → 401 / 403
   │ 2. key hygiene    known bucket; no "", "." or ".." segments, no "\", control chars or
   │                   leading "/"; at most 1024 bytes                                     → 400
   │ 3. authorize()    a file_access grant allows "write" on a location covering the key    → 403
   │ 4. size limit     Content-Length, then enforced while streaming                       → 413
   ▼ 5. storage        stream to RustFS, SHA-256 on the way                    → 200 / 404 / 502
```
- **403 before 404.** Steps 1 to 3 run before storage is touched, so a caller can't learn whether a
  key exists where it may not read.
- **Key hygiene first.** S3 keys are literal strings (`private/a/../b` really is under
  `private/a/`); anything that later normalizes paths could disagree, so such keys are refused.
- **Two levels must both allow** (`app/authz.py`): the scope `files:<action>` in the token, and a
  `file_access` grant in `authorization_details` with that action on a covering location. Deny by
  default.
- **Covering:** a location ending in `/` covers everything under it (`private/pi-server/` does not
  cover `private/pi-server-evil/`); any other location names one object. For `list`, the requested
  prefix must be inside a granted folder, so an exact-object grant never allows listing.
- **Audit:** every `authorize()` decision is one JSON line on the `file_management.audit` logger
  (allow or deny, action, resource, service, `on_behalf_of`, actor chain, client, `jti`).

## Permission model

### Buckets
| Bucket | Layout | Holds |
|---|---|---|
| `public` | `public/<path>` | files anyone may read through api-service (website images, downloads) |
| `private` | `private/<client>/<path>`, e.g. `private/pi-server/workspaces/<ws>/…` | each service's own files; an agent's workspaces are sub-folders of its service's folder |

Both have versioning on, no bucket policy, old versions expiring after 30 days and abandoned
multipart uploads after 1 day ([rustfs.md](rustfs.md)). Everything in `private` belongs to the
client named by its first segment; the policy grants each client only `private/${client}/`.

**Later buckets** (not created): `models` (gpu-server's weights, read-only), `users` (`users/<id>/`,
with an identity provider), `workspaces` (if sandboxes need automatic expiry). Adding one needs no
code: create it in RustFS with versioning and lifecycle and extend the key's policy; add it to
`FILES_BUCKETS` and deploy; then add it to `location_roots`, grant it in `policy.yaml` and deploy
auth-service.

### Actions and grants
| Action | Routes | Scope |
|---|---|---|
| `read` | `GET`, `HEAD` an object | `files:read` |
| `write` | `PUT` (create or overwrite) | `files:write` |
| `delete` | `DELETE` | `files:delete` |
| `list` | `GET /objects/{bucket}?prefix=` | `files:list` |

A grant is an RFC 9396 `file_access` detail in the token, already concrete (auth-service filled in
`${client}`):
```json
{"type": "file_access", "actions": ["read", "write", "delete", "list"], "locations": ["private/pi-server/"]}
```
Locations end in `/` (a folder) or name one object (`public/brochure.pdf`). Overwriting is
destructive, so versioning keeps the old content for 30 days.

### Who gets what
| Caller | Status | Token | Grants |
|---|---|---|---|
| `smoke-test` | **in use** (deploy checks) | its own | all actions on `private/smoke-test/` |
| pi-server | granted in `policy.yaml`, not calling yet | its own | all actions on `private/pi-server/` |
| api-service for anonymous visitors | granted, not calling yet | its own | `read`, `list` on `public/` |
| pi-server from outside, through api-service | api-service may relay (`can_relay_clients`) | relayed | pi-server's own, never api-service's |
| admin tooling (uploading public files) | when needed | its own | all actions on `public/` |
| a sandbox of pi-server's agent | when auth-service enables downscoping | downscoped by pi-server | `read write list` on `private/pi-server/workspaces/<ws>/`, at most 1 h |
| gpu-server | with the `models` bucket | its own | `read` on `models/` |
| api-service for logged-in user 42 | with users and the `users` bucket | delegated | all actions on `users/42/` |

Each row is a `policy.yaml` change (plus the noted auth-service or bucket work), never code here.
Relayed and delegated requests are handled like direct ones; the actor chain is logged.

**Beyond tokens:** "user 42 shared a folder with user 7" or group access can't be written into a
token at mint time. When needed, `authorize()` would also ask a relationship-based access service
(OpenFGA / SpiceDB) when no grant matches; nothing else changes.

## API
| Route | Needs | Response |
|---|---|---|
| `PUT /objects/{bucket}/{key}` | `files:write` + grant | `{"bucket", "key", "size_bytes", "sha256", "etag"}`; `Content-Type` from the request, else guessed from the key |
| `GET /objects/{bucket}/{key}` | `files:read` + grant | the bytes, streamed, with `ETag`, `Last-Modified`, `Accept-Ranges`; `Range` → 206, `If-None-Match` → 304 |
| `HEAD /objects/{bucket}/{key}` | `files:read` + grant | headers only |
| `DELETE /objects/{bucket}/{key}` | `files:delete` + grant | 204, also if it didn't exist (idempotent) |
| `GET /objects/{bucket}?prefix=&cursor=&limit=` | `files:list` + grant on the prefix | `{"bucket", "prefix", "objects": [{key, size_bytes, etag, last_modified}], "next_cursor"}`, at most 1000 per page |
| `GET /health` | none | `{"status": "healthy", "storage": "ok"}`, or 503 `degraded` if a bucket is unreachable |

**Errors:** 400 bad bucket or key; 401 no token or an invalid one (`error="invalid_token"`); 403 scope
or grant missing (`error="insufficient_scope"`); 404 only after authorization; 413 too large; 416
bad range; 502 storage error (never reported as 404); 503 auth-service's keys unavailable. Bodies
are generic (`{"detail": "forbidden"}`); reasons go to logs. No `/docs`, no CORS.

## Integration with auth-service
file-management is a **resource server** for the audience `file-management`. It calls no other
service, so it holds no client secret, only the issuer URL (`SERVICE_AUTH_ISSUER`). The
`service_auth` library verifies every token locally against auth-service's public keys (JWKS):
ES256 only, issuer, audience, expiry. Keys are refreshed every 5 minutes and kept up to 24 h if
auth-service is down.

**Contract with the policy:** the `file_access` detail type in `policy.yaml` has `actions: [read,
write, delete, list]` and `location_roots: [public/, private/]`, which **must equal**
`FILES_BUCKETS`. Change file-management first, then the policy.

## Storage (RustFS on CT 101)
file-management reaches RustFS at `http://192.168.50.123:9000` (S3 API, SigV4) with its own access
key, allowed only object operations in `public` and `private` (D14); the root key stays on CT 101
for the console. RustFS's setup, the key's exact policy and how it was checked: [rustfs.md](rustfs.md).
The code: `app/storage/s3.py`, with an in-memory twin in `app/storage/memory.py`.

## Configuration
Settings live in `infra/services/file-management/config.env`; every deploy installs it as
`/etc/file-management/config.env`, which `compose.yaml` loads (`env_file`). The settings class and
defaults are in `app/config.py` (prefix `FILES_`).

| Setting | Value | Meaning |
|---|---|---|
| `SERVICE_AUTH_ISSUER` | `https://auth.lan.liustev6.ca` | where tokens come from |
| `FILES_STORAGE_ENDPOINT` | `http://192.168.50.123:9000` | RustFS S3 API |
| `FILES_BUCKETS` | `["public", "private"]` | managed buckets (= policy `location_roots`) |
| `FILES_MAX_UPLOAD_BYTES` | 1 GiB | per object; `FILES_MAX_UPLOAD_BYTES_PER_BUCKET` overrides per bucket |

The RustFS key is read only from files (`/run/secrets/rustfs_access_key`, `rustfs_secret_key`);
the environment-variable form is refused. Local development without RustFS or auth-service:
`ENV=local FILES_STORAGE=memory SERVICE_AUTH_DEV_PRINCIPAL=…` (see `app/main.py`).

## Deployment and secrets
**Deploys are automatic.** The manifest has `pipeline: true` (wave 1): a push to `main` that changes
the service's code, `python/libs/` or `infra/services/file-management/` is built by GitHub Actions
and deployed through api-service and the deployer to CT 103. By hand:
`infra/deployment/deploy.sh file-management [sha]`. The host-side steps (config, secrets,
certificate hook, pull by digest, health, smoke test, rollback) are in [deployer.md](deployer.md).

**Secrets** are declared by name in the manifest and delivered from OpenBao on every deploy as files
(mode 600):

| Secret (OpenBao `kv/`) | File on CT 103 | Used by |
|---|---|---|
| `services/file-management/rustfs_access_key`, `rustfs_secret_key` | `/etc/file-management/credentials/`, mounted read-only at `/run/secrets` | the container (uid 10001) |
| `shared/smoke_test_client_secret` | `/root/.secrets/smoke-test-client` | the smoke test |
| `shared/cloudflare_dns_token` | `/root/.secrets/cloudflare.ini` | certbot (DNS-01) |

**The host:** CT 103 on pve01, `192.168.50.66` (`files-host`), boots after auth-service and RustFS
([boot.md](boot.md)).
- **The container** (`compose.yaml`): read-only filesystem, uid 10001, `cap_drop: [ALL]`,
  `no-new-privileges`, host 443 → 8443, uvicorn serving TLS itself.
- **The certificate:** certbot on the host renews it; its hook (`scripts/cert-hook.sh`, installed as
  `/usr/local/sbin/file-management-cert-hook` by every deploy) copies it to
  `/etc/file-management/tls/` for uid 10001 and restarts the container.
- **The smoke test** (`scripts/smoke_test.py`) runs in the new image as `smoke-test`, through DNS
  and TLS on real RustFS: health; no token → 401; PUT with matching SHA-256; GET, a range, LIST,
  DELETE, then 404; and 403 for another client's folder, writing `public/`, and listing all of
  `private`.

## Operations
| Task | How |
|---|---|
| Deploy | push to `main`; redeploy: `gh workflow run build.yml -f redeploy=file-management` |
| Roll back | `git revert` + push; right away: `infra/deployment/deploy.sh file-management <older sha>` |
| Give a service access | its grant in `policy.yaml`, then deploy auth-service; nothing here |
| Onboard a caller | its own auth-service client, then `TokenClient(...).auth("file-management")` against `https://files.lan.liustev6.ca` |
| Rotate the RustFS key | new access key for the same RustFS user, `bao kv put` both values, redeploy, delete the old key ([rustfs.md](rustfs.md)) |
| Logs and audit | `ssh files-host docker logs -f file-management-file-management-1`; decisions: `grep file_management.audit` |
| Recover an overwritten or deleted file | old versions are kept 30 days ([rustfs.md](rustfs.md), Operations) |

## Security notes
| Threat | Protection |
|---|---|
| A caller reaching another caller's files | grants per client folder; the `/` boundary; 403 before 404 |
| Path tricks | key hygiene rejects them before authorization |
| Forged or wrong tokens | ES256 only, issuer, audience, expiry; the service holds only public keys |
| A file-management compromise | the RustFS key reaches only objects in two buckets; container read-only, unprivileged, no capabilities |
| Filling the disk | per-object size limit, on `Content-Length` and while streaming |
| Accidental overwrite or delete | versioning, 30 days |
| Leaking internals | generic error bodies, no `/docs`, no CORS; storage errors logged, not returned |
| Secrets | files from OpenBao, owned by the container user, mounted read-only; never environment variables or git |

## Testing
`.venv/bin/python -m pytest` in the service folder, no network needed: `tests/test_authz.py` (the
access rules and key hygiene, table-driven), `tests/test_api.py` (the HTTP API with real signed
tokens and in-memory storage; it walks every route for 401 without a token), and
`tests/test_s3_storage.py` (`S3Storage` against moto, a local S3 server: multipart, an oversize
upload aborted with no orphaned parts, ranges, pagination). Plus the smoke test on every deploy.

## History and what's next
It replaced `python/services/file-management` (removed 2026-11-07): three hard-coded access tiers,
shared static keys (one shipped to browsers), unauthenticated delete routes and a public hostname.
The rewrite kept streaming uploads with a SHA-256 and replaced the rest.

**Known issues:**
- **Delete markers accumulate:** each smoke run leaves an old version (expires after 30 days) and a
  delete marker no rule removes. Fix: an `ExpiredObjectDeleteMarker` lifecycle rule on both buckets.
- **Lifecycle unconfirmed:** check after 30 days that RustFS really removes old versions (otherwise
  a nightly cleanup).

**Next:**
- **Callers:** pi-server, then api-service: public files (`GET /files/public/{path}` with
  `Cache-Control` so Cloudflare caches them) and relaying pi-server from outside.
- **Not implemented:** per-client quotas (507), and multipart upload routes for files larger than
  one request (Cloudflare caps a proxied body at 100 MB).
- **Later:** the `models` and `users` buckets; sandboxes and share links once auth-service enables
  downscoping (share links as `/files/s/{token}` at api-service, forwarded as an `Authorization`
  header, never logged, `Referrer-Policy: no-referrer`).

## Open questions
- Should end users ever write to `public`, or only admins and services?
- Quota counters: a small SQLite file (needs a writable volume) or recomputed from listings?
- When is user-to-user sharing (OpenFGA / SpiceDB) worth adding?

## Non-goals
- User accounts and login (api-service, an identity provider).
- Deciding policy (auth-service).
- Versioning beyond storage-level versioning, deduplication, virus scanning.
- Exposing file-management or RustFS to the internet.
