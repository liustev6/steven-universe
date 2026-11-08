# file-management

**Status:** live since 2026-11-08 · `https://files.lan.liustev6.ca` · CT 103 on pve01
**Code:** `python/services/file-management-service/` (service id / token audience: `file-management`)
· **Deploy:** `infra/deployment/deploy.sh file-management` · Related: [auth-service.md](auth-service.md)
(tokens and policy), [api-service.md](api-service.md) (the public edge), [deployer.md](deployer.md),
[secrets-manager.md](secrets-manager.md)

## What it is

file-management is the **only service that talks to object storage** (RustFS, S3 API). Other
services never hold storage credentials: they call file-management with a token from
auth-service, and file-management checks that the token grants the operation on that exact
object before touching storage.

It is **internal**: LAN only, no public hostname. Anything from the internet (public images on the
website, later user uploads and share links) goes through api-service, which calls file-management
on the visitor's behalf.

It replaced an older service with three hard-coded access tiers (internal / signed URL / public),
two shared static keys, and unauthenticated delete routes (see History). The rewrite keeps one
idea from it, streaming uploads with backpressure and a SHA-256 checksum, and replaces the rest.

### At a glance
| | |
|---|---|
| URL | `https://files.lan.liustev6.ca` (LAN only; Let's Encrypt certificate) |
| Host | CT 103 `file-management` on pve01, `192.168.50.66` (DHCP reservation), boots after auth-service and RustFS (`startup order=2`) |
| Storage | RustFS on CT 101 (`192.168.50.123:9000`), buckets `public` and `private` |
| API | `PUT/GET/HEAD/DELETE /objects/{bucket}/{key}`, `GET /objects/{bucket}?prefix=`, `GET /health` |
| Who may do what | decided by auth-service's `policy.yaml`, carried in the caller's token |
| Callers today | `smoke-test` (deploy checks); next: pi-server, api-service |
| Runtime | Docker Compose, hardened container (uid 10001, read-only, no capabilities), TLS on 443 |
| Tests | 66 unit tests (incl. S3 against moto); 12-check smoke test on every deploy |

## How a request is handled

```
caller ── PUT https://files.lan.liustev6.ca/objects/private/pi-server/notes.txt
          Authorization: Bearer <token for aud=file-management>
   │
   │ 1. service_auth   signature (cached auth-service public key), issuer, audience, expiry;
   │                   the route's scope (files:write)                         → 401 / 403
   │ 2. key hygiene    bucket is known; key has no "..", "//", "\", control chars,
   │                   leading "/", ≤ 1024 bytes                                → 400
   │ 3. authorize()    a file_access grant allows "write" on a location covering
   │                   private/pi-server/notes.txt                              → 403
   │ 4. size limit     Content-Length, then enforced while streaming           → 413
   ▼ 5. storage        stream to RustFS in 8 MiB parts, SHA-256 on the way     → 200 / 404 / 502
```
Steps 1–3 happen before storage is touched, so a caller gets **403 before 404**: it can't learn
whether a key exists where it isn't allowed to read. Every allow and deny from step 3 is one JSON
audit line.

The access decision is one function:
```python
def authorize(p: Principal, action: Action, resource: str) -> None:
    # resource = "<bucket>/<key>" after key hygiene
    if p.has_scopes(f"files:{action}"):                       # level 1: the scope
        for d in p.details("file_access"):                   # level 2: the location
            if action in d["actions"] and any(covers(loc, resource) for loc in d["locations"]):
                return audit("allow", ...)
    audit("deny", ...); raise Forbidden()                     # deny by default
# covers(): a location ending in "/" covers everything under it; otherwise it names one object.
# For "list", the requested prefix itself must be inside a granted folder location.
```

## Design decisions
| # | Decision | Why |
|---|---|---|
| D1 | **Internal only.** No public hostname; `/health` is the only route without a token. Internet traffic goes through api-service. | One edge to secure; the old public hostname exposed unauthenticated deletes. |
| D2 | **Only services call it**, each with its own auth-service token, verified locally. Browsers never hold a file-service credential. | Caller identity in every log line; per-caller permissions; no shared keys. |
| D3 | **Storage never leaves the LAN.** No presigned storage URLs; bytes are streamed through. | RustFS stays private; access is always decided here. |
| D4 | **No access tiers.** Permissions are grants of actions on prefixes; "public", "sandbox" and "share link" are just particular grants. | The old tiers mixed *who may* with *how bytes are delivered*. |
| D5 | **One generic object API; one `authorize()`.** | No per-tier routers drifting apart (the old missing-auth bug came from that). |
| D6 | **Buckets are for storage behaviour, prefixes for access.** | Retention and versioning differ per bucket; access is per prefix. |
| D7 | **auth-service decides, file-management enforces.** | Changing access is a `policy.yaml` change, never a code change here. |
| D8 | **Managed like every service:** hardened Docker Compose on its own LXC, secrets as files owned by the container user, deployed from a pushed commit. | Same model as auth-service (deployer D11). |
| D9 | **TLS on the LAN** as `files.lan.liustev6.ca`, Let's Encrypt via DNS-01. | Callers send bearer tokens. |
| D10 | **Two buckets for now: `public` and `private`.** No users yet; each service's files live under `private/<client>/`. | Start small; add buckets when a caller needs them. |
| D11 | **Actions are independent:** `write` doesn't imply `read` or `list`; `read` doesn't imply `list`. | Write-only (drop box, outputs) and single-file sharing are real cases; this is how S3, GCS and Azure work. |
| D12 | **A `Storage` interface** with an S3 implementation (RustFS, async `aioboto3`) and an in-memory one (tests, local dev only). | API tests without a server; the S3 code is tested separately against a real S3 protocol server. |
| D13 | **Streaming both ways.** Uploads go to S3 in 8 MiB parts (memory ≈ one part per upload); failures abort the multipart upload so no parts are orphaned. Downloads stream, with `Range` and `If-None-Match` passed to storage. | Any file size with flat memory; no leftovers in storage. |
| D14 | **The RustFS key is limited** to object operations in the two buckets. | A file-management bug can't delete buckets, change versioning, or reach other data. |
| D15 | **Every deploy ends with a smoke test** as auth-service's `smoke-test` client, on real RustFS. | Proves the whole live path; never uses a real client's credentials. |

## Permission model

### Buckets
| Bucket | Layout | Holds | Storage behaviour |
|---|---|---|---|
| `public` | `public/<path>` | files anyone may read through api-service (website images, downloads) | versioning on |
| `private` | `private/<client>/<path>`, e.g. `private/pi-server/notes.txt`, `private/pi-server/workspaces/<ws>/…` | each service's own files; an agent's workspaces are sub-folders of its service's folder | versioning on |

Both buckets: no bucket policy (private in storage), old versions expire after 30 days, abandoned
multipart uploads after 1 day. Everything in `private` belongs to the client named by its first
path segment; the policy grants each client only `private/${client}/`.

**Later buckets** (not created): `models` (when gpu-server returns: model weights, `read` for
gpu-server), `users` (with an identity provider: `users/<id>/`, per-user quota), `workspaces`
(if sandboxes need automatic expiry). Adding one: add it to `FILES_BUCKETS` and create it in
RustFS (with versioning and lifecycle), extend the RustFS key's policy, then add it to the policy's
`location_roots` and grant it in `policy.yaml`. No code change.

### Actions and grants
| Action | Covers | Scope |
|---|---|---|
| `read` | `GET`, `HEAD` | `files:read` |
| `write` | `PUT` (create or overwrite) | `files:write` |
| `delete` | `DELETE` | `files:delete` |
| `list` | `GET /objects/{bucket}?prefix=` | `files:list` |

A grant is an RFC 9396 `file_access` detail in the token, already concrete (auth-service filled in
`${client}`):
```json
{"type": "file_access", "actions": ["read", "write", "delete", "list"], "locations": ["private/pi-server/"]}
```
Locations end in `/` (a folder) or name one object (`public/brochure.pdf`). An exact-object grant
never allows listing. Overwriting is destructive, so versioning keeps the old content for 30 days.

### Who gets what
| Caller | When | Token | Grants |
|---|---|---|---|
| `smoke-test` | **now** (deploy checks) | its own | all actions on `private/smoke-test/` |
| pi-server | next (needs its real secret) | its own | all actions on `private/pi-server/` |
| pi-server from outside, through api-service | with api-service | relayed by api-service | the same as pi-server direct; never api-service's own |
| api-service for anonymous visitors | with api-service | its own | `read`, `list` on `public/` |
| admin tooling (uploading public files) | when needed | its own | all actions on `public/` |
| a sandbox of pi-server's agent | after auth-service's downscoping fix | downscoped by pi-server | `read write list` on `private/pi-server/workspaces/<ws>/`, ≤ 1 h |
| gpu-server | when it returns (+ `models` bucket) | its own | `read` on `models/` |
| api-service for logged-in user 42 | with users (+ `users` bucket) | delegated | all actions on `users/42/` |

Rows other than **now** need only a `policy.yaml` change (plus the noted auth-service or bucket
work). Relayed and delegated requests are handled exactly like direct ones; the actor chain is
logged.

**Beyond tokens:** "user 42 shared a folder with user 7" or group access can't be written into a
token at mint time. When needed, `authorize()` would also ask a relationship-based access service
(OpenFGA / SpiceDB) when no grant matches; nothing else changes.

## API
| Route | Needs | Response |
|---|---|---|
| `PUT /objects/{bucket}/{key}` | `files:write` + grant | `{"bucket", "key", "size_bytes", "sha256", "etag"}`; body streamed; `Content-Type` from the request, else guessed from the key |
| `GET /objects/{bucket}/{key}` | `files:read` + grant | the bytes, streamed; `Content-Type`, `Content-Length`, `ETag`, `Last-Modified`, `Accept-Ranges`; `Range` → 206, `If-None-Match` → 304 |
| `HEAD /objects/{bucket}/{key}` | `files:read` + grant | headers only |
| `DELETE /objects/{bucket}/{key}` | `files:delete` + grant | 204, also if it didn't exist (idempotent) |
| `GET /objects/{bucket}?prefix=&cursor=&limit=` | `files:list` + grant on the prefix | `{"bucket", "prefix", "objects": [{key, size_bytes, etag, last_modified}], "next_cursor"}`, ≤ 1000 per page |
| `GET /health` | none | `{"status": "healthy", "storage": "ok"}`, or 503 `degraded` if a bucket is unreachable |

Status codes: 400 bad bucket or key; 401 no or invalid token (`WWW-Authenticate: Bearer
error="invalid_token"`); 403 not granted (`error="insufficient_scope"`); 404 only after
authorization; 413 too large; 416 bad range; 502 storage error; 503 token keys unavailable.
Bodies are generic (`{"detail": "forbidden"}`); reasons go to logs. No `/docs`, no CORS.

## Integration with auth-service
file-management is a **resource server** for the audience `file-management`. It is **not** an
auth-service client (it calls no other service), so it holds no client secret, only the issuer URL:
```sh
SERVICE_AUTH_ISSUER=https://auth.lan.liustev6.ca
```
It fetches auth-service's public keys (JWKS) over HTTPS on the first request and verifies every
token locally (`service_auth`): ES256 only, issuer, audience = `file-management`, expiry. Keys
are cached 5 minutes and kept up to 24 h if auth-service is down.

**Contract with the policy:** auth-service's `file_access` detail type has `actions: [read,
write, delete, list]` and `location_roots: [public/, private/]`, which **must equal**
`FILES_BUCKETS`. Change file-management first, then the policy.

## Storage (RustFS on CT 101)
How RustFS itself was set up, its keys, operations and known risks: [rustfs.md](rustfs.md).

- Buckets `public` and `private`, created 2026-11-07 with versioning, lifecycle rules (old versions
  30 days, abandoned multipart uploads 1 day), no bucket policy.
- file-management's **RustFS access key** is bound to this policy, nothing else:
  ```json
  {"Version": "2012-10-17", "Statement": [
    {"Effect": "Allow", "Action": ["s3:GetBucketLocation", "s3:ListBucket", "s3:ListBucketMultipartUploads"],
     "Resource": ["arn:aws:s3:::public", "arn:aws:s3:::private"]},
    {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject",
                                   "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"],
     "Resource": ["arn:aws:s3:::public/*", "arn:aws:s3:::private/*"]}]}
  ```
  Checked 2026-11-08: read, write, list and delete work in both buckets; creating or deleting
  buckets, changing versioning, setting bucket policies and reading other buckets are refused;
  listing buckets only shows these two.
- The RustFS root key stays in `/etc/default/rustfs` on CT 101 and is used only for the console.
- Console from outside the LAN: `ssh -N -L 9001:192.168.50.123:9001 -L 9000:192.168.50.123:9000 pve01`,
  then `http://localhost:9001/rustfs/console/`.

## Configuration (`FILES_*`, set in `compose.yaml`)
| Setting | Default | Meaning |
|---|---|---|
| `FILES_BUCKETS` | `["public", "private"]` | managed buckets (= policy `location_roots`) |
| `FILES_STORAGE_ENDPOINT` | `http://192.168.50.123:9000` | RustFS S3 API |
| `FILES_STORAGE_ACCESS_KEY_FILE` / `_SECRET_KEY_FILE` | `/run/secrets/rustfs_access_key` / `rustfs_secret_key` | the RustFS key, as files; `FILES_STORAGE_ACCESS_KEY` / `SECRET_KEY` env vars are refused |
| `FILES_MAX_UPLOAD_BYTES` | 1 GiB | per object; `FILES_MAX_UPLOAD_BYTES_PER_BUCKET` overrides per bucket |
| `FILES_PART_SIZE_BYTES` | 8 MiB | multipart part size (≥ 5 MiB) |
| `FILES_STORAGE_TIMEOUT_SECONDS` | 30 | storage connect/read timeout |
| `FILES_LIST_MAX_KEYS` | 1000 | page size cap |
| `SERVICE_AUTH_ISSUER` | — | `https://auth.lan.liustev6.ca` |
| `ENV=local` + `FILES_STORAGE=memory` + `SERVICE_AUTH_DEV_PRINCIPAL` | — | local development without RustFS or auth-service (refused without `ENV=local`) |

## Deployment and setup
| Item | Value |
|---|---|
| Container | CT 103 `file-management`: Debian 13, unprivileged, `nesting=1,keyctl=1` (Docker), 2 cores, 1 GB RAM, 8 GB disk, `onboot=1`, `startup order=2,up=10` (RustFS is order 1, auth-service 0), console autologin, SSH key only (`ssh files-host`) |
| Address | `192.168.50.66`, DHCP reservation for MAC `BC:24:11:C5:09:32` |
| DNS | Cloudflare `A files.lan.liustev6.ca → 192.168.50.66`, DNS only |
| Certificate | Let's Encrypt via DNS-01 (certbot + Cloudflare token in `/root/.secrets/cloudflare.ini`); renewed by certbot's timer; `scripts/cert-hook.sh` copies it for uid 10001 and restarts the container |
| Runtime | `compose.yaml`: read-only filesystem, uid 10001, `cap_drop: [ALL]`, `no-new-privileges`, host 443 → 8443; credentials and certificate mounted read-only |

Files on CT 103:
```
/etc/file-management/                         owner 10001, mode 700
  credentials/rustfs_access_key, rustfs_secret_key   the limited RustFS key (mode 600) → /run/secrets/
  tls/cert.pem, tls/key.pem                   copied by the certbot hook (mode 600)
/root/.secrets/cloudflare.ini                 certbot's Cloudflare token (root-only)
/root/.secrets/smoke-test-client              smoke-test client secret (root-only; same as on CT 102)
/usr/local/sbin/file-management-cert-hook     scripts/cert-hook.sh, refreshed by every deploy
/opt/steven-universe                          checkout of the deployed commit
```

**Deploying** (`infra/deployment/deploy.sh file-management [ref]`, from the Mac; (log in to OpenBao first: `export BAO_TOKEN="$(bao login -method=userpass -token-only username=steven)"`)):
the generic deploy reads `infra/services/file-management/deploy.yaml` at a **pushed** commit,
fetches the declared secrets from OpenBao (the RustFS key, the shared smoke-test secret and
Cloudflare token), and on CT 103 runs `infra/deployment/deploy-target.sh`: installs `config.env` and
the secrets (atomically, reporting changed/unchanged), checks the certificate (warns under 21 days),
refreshes the certbot hook, builds the image (labelled with the commit), starts the container and
waits for Docker's health check (which also checks both buckets), calls
`https://files.lan.liustev6.ca/health` with the certificate verified, and runs the smoke test.
Status lines with ✓/✗, a summary with the rollback command. About 12 seconds.

**Smoke test** (`scripts/smoke_test.py`, as `smoke-test`, through DNS and TLS on real RustFS):
health and storage; no token → 401; PUT with matching SHA-256; GET returns the same bytes; byte
range 206; LIST shows it; DELETE; 404 afterwards; and 403 for writing or reading
`private/pi-server/`, writing `public/`, and listing the whole `private` bucket.

**First install** (done 2026-11-08): create CT 103 like CT 102 (Docker, certbot, autologin);
RustFS boot order 1; buckets with versioning and lifecycle; the limited RustFS key; reservations
and DNS; place the RustFS key, Cloudflare token and smoke-test secret; `certbot certonly
--dns-cloudflare -d files.lan.liustev6.ca --deploy-hook /usr/local/sbin/file-management-cert-hook`;
then the deploy script.

## Operations runbook
| Task | How |
|---|---|
| Deploy / roll back | `infra/deployment/deploy.sh file-management [ref]` (logged in to OpenBao); the summary prints the rollback command |
| Give a service access | change its grant in auth-service's `policy.yaml` and deploy auth-service; nothing here |
| Onboard a caller | its own auth-service client (`add-client.sh`), then it calls `https://files.lan.liustev6.ca` with `TokenClient.auth("file-management")` |
| Rotate the RustFS key | create a new access key for the same policy in the RustFS console, replace the two files in `/etc/file-management/credentials/` (owner 10001, `read -rs`), redeploy, delete the old key |
| Logs | `ssh files-host`, `docker compose -f /opt/steven-universe/python/services/file-management-service/compose.yaml logs -f file-management` |
| Audit | `docker logs file-management-file-management-1 2>&1 \| grep file_management.audit`: one JSON line per decision (allow/deny, action, resource, service, on_behalf_of, actor chain, client, `jti`) |
| Recover an overwritten or deleted file | RustFS keeps old versions for 30 days (console, or `aws s3api list-object-versions` with the root key on CT 101) |

## Security
| Threat | Protection |
|---|---|
| A caller reaching another caller's files | grants per client folder; `/` boundary (`pi-server-evil/` isn't under `pi-server/`); 403 before 404 |
| Path tricks | key hygiene rejects `..`, `.`, `//`, `\`, control characters, leading `/` before authorization |
| Forged or wrong tokens | `service_auth`: ES256 only, issuer, audience, expiry; the service holds only public keys |
| A file-management compromise | the RustFS key reaches only objects in two buckets; no bucket admin; container read-only, unprivileged, no capabilities |
| Filling the disk | per-object size limit, checked on `Content-Length` and while streaming |
| Accidental overwrite or delete | versioning; old versions kept 30 days |
| Leaking internals | generic error bodies, no `/docs`, no CORS; storage errors logged, not returned |
| Secrets | files owned by the container user, mounted read-only; never environment variables or git |

## Testing
- **Unit tests** (`cd python/services/file-management-service && .venv/bin/python -m pytest`):
  - `test_authz.py` (43): the access rules and key hygiene, table-driven: own folder only, prefix
    boundaries, sandboxes can't list their parent, exact-object grants, both levels required, the
    dev principal's `*` scope.
  - `test_api.py` (17): the HTTP API with **real signed tokens** from a test issuer and in-memory
    storage: every route except `/health` returns 401 without a token (the test walks the routes),
    403 before 404, relayed callers, ranges, 304, size limits (declared and streamed), pagination.
  - `test_s3_storage.py` (6): `S3Storage` against **moto** (a local S3 server): a 12 MiB upload in
    3 parts, an oversize upload aborted with no orphaned parts, ranges, 304, pagination, and the
    full HTTP stack on S3.
- **Smoke test on every deploy** (see Deployment).

## Known issues and next steps
- **Delete markers accumulate:** each smoke run leaves one old version (removed after 30 days)
  and one delete marker (not removed by any rule). Fix: add an `ExpiredObjectDeleteMarker`
  lifecycle rule to both buckets.
- **Not implemented yet:** per-client quotas (507) and multipart upload routes for files larger
  than one request (needed through Cloudflare's 100 MB limit).
- **RustFS lifecycle:** the rules are accepted; confirm after 30 days that old versions are really
  removed (otherwise a nightly cleanup).
- **Callers:** pi-server (real secret via `add-client.sh --rotate`, then `TokenClient`), then the
  thin api-service (public files, relaying pi-server from outside).
- **Later:** the `models` and `users` buckets; sandboxes and share links after auth-service's
  downscoping fix; managed RustFS keys through the deployer and secrets manager.

### What api-service has to do (requirements from this design)
- **Public files:** `GET /files/public/{path}` without login, calling file-management with its own
  token (`read`/`list` on `public/`) and setting `Cache-Control` so Cloudflare caches them.
- **Relayed machine clients:** relay pi-server from outside (grant 3) so file-management sees
  pi-server's own grants.
- **Upload limits:** enforce a size limit before streaming; Cloudflare caps a proxied body at
  100 MB, so bigger uploads need the multipart routes or the LAN/tailnet.
- **Later:** user files (delegated tokens) and share links (downscoped tokens, `/files/s/{token}`,
  forwarded as an `Authorization` header, never logged, `Referrer-Policy: no-referrer`).

## History: the old service and what replaced it
The old `python/services/file-management` (removed 2026-11-07) had three routers for three
bucket tiers (internal `models`, signed-URL `user-uploads`/`model-outputs`, public-read `public`),
a shared `INTERNAL_SECRET_KEY` for every backend and a `FRONTEND_API_KEY` shipped to browsers,
presigned URLs rewritten through a public proxy route, and a public tunnel hostname.

| Old problem | Resolved by |
|---|---|
| `DELETE /signed/delete` and `/public/delete` had **no auth** | deny-by-default routes; a test walks every route; the public hostname was removed |
| A frontend key shipped to browsers (effectively public) | D2: only services, each with its own token |
| One internal secret for all services; `==` comparisons | per-service tokens from auth-service, verified with signatures |
| CORS `*` with credentials; errors and `/docs` exposed | no CORS, generic errors, no `/docs` |
| No upload size limit | per-object limit, declared and streamed |
| One upload thread for everything; blocking calls in async routes | async `aioboto3`, streaming multipart |
| No pagination; outages looked like "not found" | cursor pagination; storage errors are 502, not 404 |
| Tiers hard-coded in three copies of the same routes | one API, one `authorize()` |
| Secrets in `.env` files and environment variables | files owned by the container user, mounted read-only |
| Image built on the host from a working copy, `:latest`, plain HTTP | built from a pushed commit, labelled, TLS |

## Open questions
- Should end users ever write to `public`, or only admins and services?
- Quota counters: a small SQLite file (needs a writable volume) or recomputed from listings?
- When is user-to-user sharing (OpenFGA / SpiceDB) worth adding?

## Non-goals
User accounts and login (api-service / an identity provider); deciding policy (auth-service);
file versioning beyond storage-level versioning, deduplication, virus scanning; exposing
file-management or RustFS to the internet.
