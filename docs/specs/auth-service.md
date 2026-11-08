# auth-service and the service-auth library

**Status:** live since 2026-11-07 · `https://auth.lan.liustev6.ca` · CT 102 on pve01
**Code:** `python/services/auth-service/` (the issuer), `python/libs/service-auth/` (the library every
service uses) · **Deploy:** `infra/deployment/auth-service.sh` · Related: [file-management.md](file-management.md),
[api-service.md](api-service.md), [deployer.md](deployer.md), [secrets-manager.md](secrets-manager.md)

## What it is

auth-service is the **internal OAuth 2.0 authorization server** for steven-universe. Every service
that calls another service first asks auth-service for a **short-lived, signed access token** for
that one target, and the target checks the token **locally**, without calling auth-service back.

It replaced the old setup, where each link between two services used its own shared static key,
header and comparison code: one leaked key opened everything, logs couldn't say who called, and
rotating a key meant touching every service at once.

Two pieces:

- **auth-service** decides *who may do what*. It holds the policy (`policy.yaml`, reviewed in
  git), the client secret hashes, and the signing key, and issues tokens at `POST /oauth2/token`.
- **service-auth** is the library inside every other service. Callers use its `TokenClient` to get
  and cache tokens; receivers use `ServiceAuth` / `TokenVerifier` to check them and get a
  `Principal` (who is calling, for whom, with which permissions). Route code only ever sees the
  `Principal`.

Everything follows published standards (OAuth 2.0, JWT access tokens, token exchange, rich
authorization requests), so the same ideas carry over to Keycloak, Auth0, Okta or Entra ID.

### At a glance
| | |
|---|---|
| Issuer / URL | `https://auth.lan.liustev6.ca` (LAN only; Let's Encrypt certificate) |
| Host | CT 102 `auth-service` on pve01, `192.168.50.235` (DHCP reservation), boots first (`startup order=0`) |
| Runtime | Docker Compose, hardened container (uid 10001, read-only, no capabilities) |
| Endpoints | `POST /oauth2/token`, `GET /.well-known/oauth-authorization-server`, `GET /.well-known/jwks.json`, `GET /health` |
| Tokens | JWT (RFC 9068), ES256, 10 minutes, one audience each |
| Clients today | `api-service`, `pi-server`, `smoke-test` (see Policy) |
| Admin | `auth-service-admin` CLI on CT 102 only; no admin API |
| Tests | 52 issuer + 21 library unit tests; smoke test on every deploy |

## How it works

```
                     ┌──────────────── auth-service (CT 102) ────────────────┐
                     │ policy.yaml        who may get what, at which service │
  ① client secret   │ clients/*.sha256   hashes of client secrets            │  publishes
  (HTTP Basic) ────▶ │ keys/<kid>.pem     private signing key (ES256)         │  public keys
  ◀── ② token ────── │ POST /oauth2/token   /.well-known/jwks.json ───────────┼──────────────┐
                     └───────────────────────────────────────────────────────┘              │
                                                                                            ▼
 caller (pi-server) ── ③ Authorization: Bearer <token> ──▶ receiver (file-management) ── ④ verify
                                                             locally with the cached public key
```

1. **The caller authenticates** to auth-service with its client ID and secret (HTTP Basic) and
   names the service it wants to call (`resource=file-management`).
2. **auth-service looks up the policy** for that client at that service, fills in templates
   (`private/${client}/` → `private/pi-server/`), and **signs a token** with its private key:
   ```json
   {"iss": "https://auth.lan.liustev6.ca", "aud": "file-management",
    "sub": "service:pi-server", "client_id": "pi-server",
    "scope": "files:read files:write files:delete files:list",
    "authorization_details": [{"type": "file_access", "actions": ["read","write","delete","list"],
                               "locations": ["private/pi-server/"]}],
    "iat": 1762500000, "exp": 1762500600, "jti": "…"}
   ```
3. **The caller sends the token** as `Authorization: Bearer …`. The library caches it and reuses
   it for every request until ~60 s before it expires.
4. **The receiver verifies it locally**: signature against auth-service's **public** key (fetched
   once from the JWKS, cached), issuer, audience = itself, expiry, required claims. Then its own
   code decides on the exact resource (file-management's `authorize()`).

Receivers never hold a secret: the private key only **signs**, the public key only **verifies**.
A compromised receiver can't mint tokens.

### When a request passes through a middleman (relay)
From outside the LAN, pi-server reaches file-management through api-service (the only public
edge). api-service must not forward pi-server's token (it is for `api-service`, and forwarding
tokens lets a middleman misuse them), so it **exchanges** it:

```
pi-server ──T1 (aud=api-service, sub=service:pi-server)──▶ api-service
api-service ──"exchange T1 for file-management" (its own secret)──▶ auth-service
            ◀── T2 (aud=file-management, sub=service:pi-server, act={sub: service:api-service})
api-service ──T2──▶ file-management   → sees: service=api-service, on behalf of service:pi-server,
                                         with pi-server's grants (never api-service's own)
```

## Design decisions
| # | Decision | Why |
|---|---|---|
| A1 | **Route code depends on `Principal` only.** Tokens, keys and headers stay inside `service-auth`. | Swapping the issuer (e.g. for Keycloak) changes no route code. |
| A2 | **ES256-signed JWTs.** Verifiers hold only public keys; the verifier accepts ES256 only. | No shared secrets between services; a compromised service can't mint tokens. ES256 over EdDSA because every IdP and JWT library supports it. |
| A3 | **One audience per token.** | A token for file-management is useless anywhere else. |
| A4 | **10-minute tokens, no refresh tokens.** Downscoped tokens up to 1 hour. | Revocation is "disable the client"; outstanding tokens die on their own. |
| A5 | **Two levels of permission:** `scope` (which actions) and `authorization_details` (RFC 9396, which resources). Both must allow an operation. | Routes check scopes cheaply; the resource service checks locations. |
| A6 | **Policy decided at mint time, enforced locally.** Receivers never call auth-service per request. | No per-request latency or dependency on auth-service. |
| A7 | **Tokens can only narrow.** Every exchange yields a subset of what the requester may have, with a bounded lifetime. | No path from a weak credential to a stronger one. |
| A8 | **Delegation and relay follow RFC 8693** (`act` claim, actor chains); only the token's own audience may exchange it; relaying needs `can_relay_clients`. | Prevents token passthrough and the confused-deputy problem. |
| A9 | **No user login here.** End users log in at api-service / an identity provider (later). | auth-service only issues tokens to services. |
| A10 | **No database.** Policy is a YAML file in git; secret hashes and keys are files on CT 102, reloaded when they change. | Reviewable permissions, nothing to operate. |
| A11 | **Permissions in git, credentials never.** A client exists in `policy.yaml` first; its secret is created on CT 102 and piped straight to the client's host. | Every permission change is reviewed; secrets never pass through git, chat or a terminal. |
| A12 | **The issuer is a name we own, served over HTTPS with a public certificate** (`auth.lan.liustev6.ca`, Let's Encrypt via DNS-01). | Clients send secrets to it and trust its JWKS; a public CA means no custom CA on any host; the IP can change without touching services. |
| A13 | **Published on port 443.** The container listens on 8443; host 443 maps to it. | The issuer URL has no port, so discovery points clients to `:443`. |
| A14 | **Hardened Docker Compose, deployed by hand** from a pushed commit. | Same model as every service (deployer D11); manual because it is the root of trust. |
| A15 | **Every deploy ends with a smoke test as a dedicated `smoke-test` client** (only grant: `private/smoke-test/`). | Proves the live path works without ever touching a real client's credentials. |
| A16 | **No per-audience `delegable_scopes`.** Delegated tokens get the same scopes as direct ones. | No current use case; added back if a scope must be direct-only (see Later). |

## Standards
| Piece | Standard |
|---|---|
| Service tokens | OAuth 2.0 **client credentials** (RFC 6749 §4.4) |
| Client authentication | `client_secret_basic` (RFC 6749 §2.3.1); later `private_key_jwt` (RFC 7523) |
| Token format | **JWT Profile for OAuth 2.0 Access Tokens** (RFC 9068): `typ: at+jwt`, claims `iss sub aud exp iat jti client_id scope` |
| Signing and keys | JWS (RFC 7515), JWK Set (RFC 7517), ES256 (RFC 7518) |
| Naming the target | `resource` (RFC 8707); `audience` accepted (RFC 8693) |
| Resource-level permissions | **Rich Authorization Requests** (RFC 9396), `authorization_details` |
| Delegation, relay, downscoping | **OAuth 2.0 Token Exchange** (RFC 8693) |
| Policy templates (`${client}`, `${user}`) | modelled on IAM policy variables (`${aws:username}`) |
| Discovery | **Authorization Server Metadata** (RFC 8414) |
| Errors | RFC 6749 §5.2, plus `invalid_target` (RFC 8707), `invalid_authorization_details` (RFC 9396) |
| Using tokens | Bearer usage (RFC 6750), `WWW-Authenticate: Bearer error="…"` |
| Security guidance | OAuth 2.0 Security BCP (RFC 9700) |

The one non-standard piece is grant 2's gateway-asserted user (a custom `subject_token_type`),
a stepping stone until users come from an identity provider.

## Concepts
| Term | Meaning |
|---|---|
| **Client** | A service that requests tokens, e.g. `pi-server`. Needs a policy entry and a secret. |
| **Audience** | The service a token is for (`aud`). Each verifier accepts only its own name. Receivers need no client entry. |
| **Scope** | Coarse permission at an audience, `<area>:<action>`, e.g. `files:read`. |
| **Authorization detail** | RFC 9396 object naming exact resources: `{"type": "file_access", "actions": [...], "locations": [...]}`. Defined by the resource service (its *detail type*). |
| **Grant template** | A client's entry for one audience: scopes + detail templates with variables. |
| **Delegated / relayed token** | A token whose `sub` is someone other than the caller (a user, or a relayed service); `act` names the service acting for them. |
| **Downscoped token** | A narrower copy of a token the requester already holds, for less-trusted code. |
| **Principal** | What a verifier gives route code. |

```python
@dataclass(frozen=True)
class Principal:
    service: str                                   # who sent this request (the nearest actor)
    scopes: frozenset[str]
    authorization_details: tuple[Mapping, ...]     # already concrete, no variables
    on_behalf_of: str | None                       # "service:pi-server" when relayed, "user:42" later
    actor_chain: tuple[str, ...]                   # services in between, nearest first
    client_id: str | None
    subject: str
    token_id: str | None                           # jti, for logs
    def details(self, type_: str) -> tuple[Mapping, ...]: ...
    def has_scopes(self, *scopes: str) -> bool: ...    # "*" (dev principal) = all
```

## Policy (`infra/services/auth-service/policy.yaml`)
The deployed policy, deliberately small. The full multi-service design (users, gpu-server,
sandboxes, downscoping) lives in `tests/fixtures/policy.yaml`, which the tests use.

```yaml
issuer: https://auth.lan.liustev6.ca
token_ttl_seconds: 600
downscoped_max_ttl_seconds: 3600
max_delegation_depth: 3

audiences:
  api-service:                           # the public edge; scopes its routes require
    scopes: [files:read, files:write, files:delete, files:list]
  file-management:
    scopes: [files:read, files:write, files:delete, files:list]
    detail_types:
      file_access:
        actions: [read, write, delete, list]
        location_syntax: prefix          # "<bucket>/<key prefix>"; subset = string prefix
        location_roots: [public/, private/]   # must equal file-management's buckets

clients:
  api-service:
    can_relay_clients: true              # may pass an outside client's identity downstream
    grants:
      file-management:
        scopes: [files:read, files:list]
        authorization_details:           # its own token = what anonymous visitors may do
          - {type: file_access, actions: [read, list], locations: ["public/"]}
  pi-server:                             # Hey Pi's backend
    grants:
      api-service:
        scopes: [files:read, files:write, files:delete, files:list]
      file-management:
        scopes: [files:read, files:write, files:delete, files:list]
        authorization_details:
          - {type: file_access, actions: [read, write, delete, list], locations: ["private/${client}/"]}
  smoke-test:                            # deploy checks only; secret root-only on CT 102/103
    grants:
      file-management:
        scopes: [files:read, files:write, files:delete, files:list]
        authorization_details:
          - {type: file_access, actions: [read, write, delete, list], locations: ["private/${client}/"]}
```

| Template variable | Becomes | Available |
|---|---|---|
| `${client}` | the requesting client; when relayed, the **relayed** client | always |
| `${user}` | the user from a delegated `sub` (`user:42` → `42`) | delegated tokens only; templates with `${user}` are **dropped** otherwise |

**Validation** (on load, `check-policy`, and every deploy before installing): the issuer is
`https://`; every grant names an existing audience, scope, detail type and action; variables are
`${client}`/`${user}` only; locations stay under `location_roots`, end in `/` or name one object,
and contain no `..`, `//`, `\` or control characters. An invalid policy is rejected and the
running one stays. Special permissions default to off:

| Flag | Allows | Who has it |
|---|---|---|
| `can_relay_clients` | grant 3 with a service subject (relay) | api-service |
| `can_assert_users` | grant 2 (start acting for a user) | nobody yet (no users) |
| `can_downscope` | grant 4 | nobody yet (see Known issues) |

## Token endpoint: `POST /oauth2/token`
Form-encoded; client authentication with HTTP Basic only (a secret in the body is refused).
Responses are JSON with `Cache-Control: no-store`. Errors are RFC 6749 JSON
(`{"error", "error_description"}`): `invalid_client` 401 (identical for unknown client, wrong
secret, disabled client), `invalid_request` / `invalid_grant` / `invalid_scope` / `invalid_target`
/ `invalid_authorization_details` / `unauthorized_client` / `unsupported_grant_type` 400, 429 with
`Retry-After` over 30 requests/minute per client and IP.

Optional narrowing: `scope` and `authorization_details` (JSON) ask for **less** than the policy
allows; each requested item must be covered (same type, actions ⊆ allowed, location under an
allowed prefix with a `/` boundary).

| Grant | Request | Used for | Enabled today |
|---|---|---|---|
| 1 | `grant_type=client_credentials` | a service calling as itself | ✅ all clients |
| 2 | token exchange, `subject_token_type=urn:steven-universe:token-type:user-id` | api-service starting to act for a logged-in user | no (no users) |
| 3 | token exchange, `subject_token` issued **to** the requester | passing a user along a chain; api-service relaying an outside client | ✅ relay (api-service) |
| 4 | token exchange, the requester's **own** token for the same audience | narrower, longer-lived token for a sandbox or share link | no (Known issues) |

**Grant 3 details.** The subject token must verify and its `aud` must be the requester. For a
`service:` subject (relay) the requester needs `can_relay_clients` and the **relayed client's**
templates apply; for a `user:` subject the requester's own templates with `${user}`. Chain depth
≤ 3; `exp = min(now + 600, subject.exp)`.

**Grant 4 details.** The subject token's `client_id` must be the requester and its `aud` the
requested audience; requested permissions ⊆ the subject token's; `exp = now + min(expires_in, 3600)`.

Other endpoints:

| Endpoint | Returns |
|---|---|
| `/.well-known/oauth-authorization-server` | RFC 8414 metadata: issuer, token endpoint, JWKS URI, grant types, `client_secret_basic`, scopes, detail types |
| `/.well-known/jwks.json` | public signing keys (`EC P-256`, `ES256`, `kid`), `Cache-Control: max-age=300` |
| `/health` | `status`, `version`, `policy_version` (hash of the policy file), `clients_in_policy`, `published_keys`, `active_kid` |

No `/docs`, no introspection endpoint (tokens are verified locally).

## The service-auth library
**Receiving calls** (FastAPI):
```python
auth = ServiceAuth.from_env("file-management")      # SERVICE_AUTH_ISSUER

@router.put("/objects/{bucket}/{key:path}")
async def put_object(bucket: str, key: str, p: Principal = Depends(auth.require("files:write"))):
    authorize(p, "write", bucket, key)              # the service's own resource check
```
The verifier accepts only ES256 and `typ: at+jwt`; checks issuer, audience = this service,
`exp`/`nbf` with 30 s leeway, required claims, a single-string `aud`. Keys come from the JWKS:
cached 5 minutes, refetched early for an unknown `kid` (at most every 30 s), kept up to 24 h if
auth-service is down. Errors follow RFC 6750: 401 `invalid_token`, 403 `insufficient_scope`, 503
if no keys can be obtained. Local development: `ENV=local` + `SERVICE_AUTH_DEV_PRINCIPAL` (JSON)
gives a fixed principal; refused otherwise.

**Calling other services:**
```python
tokens = TokenClient.from_env()   # SERVICE_AUTH_ISSUER, SERVICE_AUTH_CLIENT_ID, SERVICE_AUTH_CLIENT_SECRET_FILE
files = httpx.AsyncClient(base_url="https://files.lan.liustev6.ca", auth=tokens.auth("file-management"))
relayed = httpx.AsyncClient(base_url=..., auth=tokens.auth_for(principal, "file-management"))  # grant 3
```
`token()`, `assert_user()`, `exchange()` and `downscope()` cover grants 1–4. Tokens are cached per
(audience, scopes, details, subject) and renewed 60 s before expiry; concurrent callers share one
request; issuer failures are retried with backoff, and a still-valid cached token is used while
auth-service is down; on a 401 from the target the token is dropped and the request retried once.
The secret must come from a file (`SERVICE_AUTH_CLIENT_SECRET_FILE` or systemd
`$CREDENTIALS_DIRECTORY/client_secret`); a secret in an environment variable is refused.

## Deployment and setup

| Item | Value |
|---|---|
| Container | CT 102 `auth-service`: Debian 13, unprivileged, `nesting=1,keyctl=1` (Docker), 1 core, 1 GB RAM, 8 GB disk, `onboot=1`, `startup order=0,up=15`, console autologin, SSH key only |
| Address | `192.168.50.235`, DHCP reservation for MAC `BC:24:11:51:80:B6`; no IPv6 |
| DNS | Cloudflare `A auth.lan.liustev6.ca → 192.168.50.235`, DNS only. Public name, private address; the router and 1.1.1.1 both pass private answers. |
| Certificate | Let's Encrypt via DNS-01 (certbot + Cloudflare token limited to DNS edit on `liustev6.ca`, `/root/.secrets/cloudflare.ini`); renewed by certbot's timer; `scripts/cert-hook.sh` copies it for uid 10001 and restarts the container |
| Runtime | `compose.yaml`: read-only filesystem, uid 10001, `cap_drop: [ALL]`, `no-new-privileges`, host 443 → 8443; `/etc/auth-service` mounted read-only except `clients/` |
| Admin CLI | `/usr/local/bin/auth-service-admin` runs `python -m app.admin` in a one-off container with **no network** |

Files on CT 102 (owner 10001, mode 700/600, read by the container):
```
/etc/auth-service/policy.yaml              installed by each deploy (validated first)
/etc/auth-service/keys/<kid>.pem, active   private signing keys; `active` names the signing one
/etc/auth-service/clients/<id>.sha256      hash of the current secret (.next.sha256 while rotating)
/etc/auth-service/clients/<id>.json        enabled, created, rotated; <id>.last_mint
/etc/auth-service/tls/cert.pem, key.pem    copied by the certbot hook
/root/.secrets/cloudflare.ini              certbot's Cloudflare token (root-only)
/root/.secrets/smoke-test-client           smoke-test client secret (root-only)
```

**Deploying** (`infra/deployment/auth-service.sh [ref]`, from the Mac): checks out a **pushed**
commit on CT 102, checks the config directory and certificate (warns under 21 days), refreshes the
admin CLI and certbot hook, builds the image (labelled with the commit), validates the policy and
shows its diff before installing, creates the first signing key if none exists, starts the
container and waits for health, calls `https://auth.lan.liustev6.ca/health` with the certificate
verified, and runs the smoke test (`scripts/smoke_test.py`, as `smoke-test`: discovery, JWKS, a
real mint verified against the live keys, wrong secret / wrong audience / no grant rejected). Each
step prints ✓/✗; the summary shows the commit and a rollback command. A no-change redeploy takes
about 5 seconds.

**First install** (done 2026-11-07): create the LXC, install Docker and certbot, reservation and
DNS, Cloudflare token, `certbot certonly --dns-cloudflare -d auth.lan.liustev6.ca --deploy-hook
/usr/local/sbin/auth-service-cert-hook`, then the deploy script.

## Operations runbook

| Task | How |
|---|---|
| Change permissions | Edit `policy.yaml` in git, push, run the deploy script (shows the diff; takes effect without a restart). |
| Onboard a client | 1. Add it to `policy.yaml`, deploy. 2. From the Mac: `python/services/auth-service/scripts/add-client.sh <id> <host> <secret path> <owner>` (pipes the new secret from CT 102 straight into a root-only file on the client's host). 3. Configure the client (`SERVICE_AUTH_CLIENT_ID`, secret file mounted read-only), restart, check `auth-service-admin list-clients` for its first mint. |
| Rotate a client secret | `scripts/add-client.sh … --rotate` (both secrets work), restart the client, then `ssh auth-host auth-service-admin promote-secret <id>`. Yearly, or at once on suspicion. |
| Stop a client now | `ssh auth-host auth-service-admin disable-client <id>`; its tokens expire within 10 minutes (downscoped within 1 h). |
| Rotate the signing key | `new-key` (published, not signing) → wait ≥ 1 h for verifiers' JWKS caches → `promote-key <kid>` → after ≥ 2 h `retire-key <old kid>`. Every ~90 days, or immediately if compromised (then retire at once). |
| Inspect | `auth-service-admin list-clients` (enabled, ages, rotation, last mint), `list-keys`, `explain <id> <audience>` (resolved permissions), `check-policy`. |
| Logs and audit | `docker compose logs -f auth-service`; one JSON line per mint or denial on `auth_service.audit` (client, grant, aud, sub, act, scope, details, `jti`, `exp`, IP; never a secret or token). |
| Roll back | `infra/deployment/auth-service.sh <previous sha>` (printed in every deploy summary). |

## Security
| Threat | Protection |
|---|---|
| Stolen client secret | Damage limited to that client's grants; audit shows mints from an unexpected IP; `disable-client` stops it; tokens die within 10 min. Secrets are 256-bit random, stored only as SHA-256, compared in constant time; `/oauth2/token` is rate-limited and logs failures. |
| Stolen token | One audience, ≤ 10 minutes; never logged (only `jti`). |
| Compromised receiver | Holds only public keys: can't mint tokens. |
| Forged or altered token | ES256 allowlist (`alg: none`, HS256 rejected), `typ` check, signature, issuer, single audience. |
| Fake auth-service / swapped JWKS | Clients only talk to `https://auth.lan.liustev6.ca` with a publicly trusted certificate. |
| Secrets in the wrong place | Secrets are files (mode 600) loaded via `/run/secrets` or `LoadCredential`, never environment variables (the library refuses them); the admin CLI writes secrets only to a pipe; nothing secret in git. |
| auth-service host compromised | The signing key is the crown jewel: CT 102 runs nothing else, SSH is key-only, the container is read-only and unprivileged; Proxmox backups of CT 102 contain the key and must be protected. |
| Clock skew | chrony on all hosts; 30 s leeway. |

**If auth-service is down:** cached tokens keep working until they expire (≤ 10 min), receivers
keep verifying with keys cached up to 24 h; new tokens can't be issued. During an internet outage
DNS lookups for the issuer fail once cached answers expire (the name is in public DNS).

## Testing
- **Unit tests** (`python/services/auth-service/tests`, `python/libs/service-auth/tests`): every
  grant with its allow and deny cases, policy validation and reload, key rotation, file-permission
  checks, the admin CLI, two end-to-end flows using the real library on both sides, and verifier
  attacks (`alg: none`, HS256, forged signature, wrong `typ`, wrong or multiple audiences, expired).
  Run: `cd python/services/auth-service && .venv/bin/python -m pytest tests ../../libs/service-auth/tests`.
- **Smoke test on every deploy** (see Deployment).
- **Verified once by hand against the live service** (2026-11-07): relay of pi-server through
  api-service, api-service's own `public/` token, and exchanging someone else's token (refused).

## Known issues and limitations
- **Downscoped tokens can be relayed** (grant 4 then grant 3) and the relay rebuilds permissions
  from the full policy, losing the narrowing. Fix planned: mark downscoped tokens and refuse them
  as a `subject_token`. Until then no client has `can_downscope`.
- Rate limiting is in memory, per process (fine for one worker).
- Denial audit lines use spaced JSON, mint lines compact JSON.
- Client authentication is shared secrets only (`private_key_jwt` later).
- The earlier test run left secrets for `pi-server` and `api-service` that nobody holds: onboard
  them with `add-client.sh … --rotate`.

## What's next
| When | What |
|---|---|
| With pi-server and api-service | issue their real secrets (`--rotate`); relay goes live through api-service |
| Before sandboxes or share links | fix the downscope relay gap, then enable `can_downscope` |
| With users | an identity provider; grant 2 accepts its tokens instead of asserted users; a `users` bucket |
| Later, each a standard on its own | `private_key_jwt` (RFC 7523); an admin API and Dynamic Client Registration (RFC 7591); sender-constrained tokens (mTLS RFC 8705, DPoP RFC 9449); SPIFFE/SPIRE; direct-only scopes; relationship-based access (OpenFGA/SpiceDB); federation with GitHub OIDC for the deployer; validating against Keycloak |

## Open questions
- Which identity provider for users?
- Machine clients truly outside the LAN and tailnet: publish `/oauth2/token` through api-service
  (rate-limited), or require the tailnet?
- Share links longer than 1 hour: a stored, revocable share record?

## Non-goals
End-user login, sessions, MFA; third-party clients, consent screens, refresh tokens; a database,
admin UI or introspection endpoint; interpreting detail types (each resource service owns the
meaning of its own `authorization_details`).
