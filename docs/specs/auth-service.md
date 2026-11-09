# Spec: auth-service and the service-auth library

**Status: live** (since 2026-11-07). `https://auth.lan.liustev6.ca`, CT 102 on pve01, LAN only.
Deployed by hand only: it is a root of trust ([deployer.md](deployer.md) D13).
**Paths:**
- `python/services/auth-service/`: the issuer (`app/`, `bin/auth-service-admin`, `scripts/`);
- `python/libs/service-auth/`: the library every other service uses;
- `infra/services/auth-service/`: `policy.yaml`, `config.env`, `deploy.yaml`, `hooks/pre-start.sh`.

Related: [deployer.md](deployer.md), [secrets-manager.md](secrets-manager.md),
[file-management.md](file-management.md), [api-service.md](api-service.md), [boot.md](boot.md).

## What it is
auth-service is the **internal OAuth 2.0 authorization server**. A service that calls another
service first asks auth-service for a **short-lived, signed access token** for that one target; the
target checks the token **locally**, without calling auth-service back. It replaced one shared
static key per pair of services (one leak opened everything, logs couldn't say who called, and
rotation touched every service at once).

```
                     ┌──────────────── auth-service (CT 102) ────────────────┐
                     │ policy.yaml        who may get what, at which service │
  ① client secret    │ clients/*.sha256   hashes of client secrets           │  publishes
  (HTTP Basic) ────▶ │ keys/<kid>.pem     private signing key (ES256)        │  public keys
  ◀── ② token ────── │ POST /oauth2/token   /.well-known/jwks.json ──────────┼─────────────┐
                     └───────────────────────────────────────────────────────┘             │
                                                                                           ▼
 caller (deployer) ── ③ Authorization: Bearer <token> ──▶ receiver (OpenBao, file-management)
                                                          ④ verify locally with the cached public key
```

Two pieces:
- **auth-service** decides *who may do what*. It holds the policy (reviewed in git), the client
  secret hashes and the signing keys, and issues tokens.
- **service-auth** is the library inside every other service. Callers use `TokenClient` to get and
  cache tokens; receivers use `ServiceAuth` / `TokenVerifier` to check them and get a `Principal`
  (who is calling, for whom, with which permissions). Route code only ever sees the `Principal`.

**Who uses it today:** the deployer (client, logs in to OpenBao with a token for
`aud=secrets-manager`), OpenBao (verifies those tokens against the JWKS,
`infra/secrets-manager/tofu/main.tf`), file-management (receiver), and the `smoke-test` client.
api-service and pi-server have grants in the policy but don't call auth-service yet.

## How it works
1. **The caller authenticates** with its client ID and secret (HTTP Basic) and names the service
   it wants to call (`resource=file-management`).
2. **auth-service looks up the policy** for that client at that audience, fills in templates
   (`private/${client}/` → `private/pi-server/`) and **signs a token**:
   ```json
   {"iss": "https://auth.lan.liustev6.ca", "aud": "file-management",
    "sub": "service:pi-server", "client_id": "pi-server",
    "scope": "files:read files:write files:delete files:list",
    "authorization_details": [{"type": "file_access", "actions": ["read","write","delete","list"],
                               "locations": ["private/pi-server/"]}],
    "iat": 1762500000, "exp": 1762500600, "jti": "…"}
   ```
3. **The caller sends it** as `Authorization: Bearer …`. The library caches it and reuses it until
   60 s before it expires.
4. **The receiver verifies it locally:** signature against the public key (from the JWKS, cached),
   issuer, audience = itself, expiry, required claims. Then its own code decides on the exact
   resource (file-management's `authorize()`).

**Relay through a middleman.** From outside the LAN, a client reaches file-management through
api-service, the only public edge. api-service must not forward the client's token (it is for
`api-service`, and forwarding tokens invites misuse), so it **exchanges** it (RFC 8693):
```
pi-server ──T1 (aud=api-service, sub=service:pi-server)──▶ api-service
api-service ──"exchange T1 for file-management" (its own secret)──▶ auth-service
            ◀── T2 (aud=file-management, sub=service:pi-server, act={sub: service:api-service})
api-service ──T2──▶ file-management   → sees: service=api-service, on behalf of service:pi-server,
                                         with pi-server's grants (never api-service's own)
```
This is enabled in the policy and tested; it goes into use when api-service adopts service-auth.

## Decisions
| ID | Decision | Why |
|---|---|---|
| A1 | **Route code depends on `Principal` only.** Tokens, keys and headers stay inside service-auth. | Swapping the issuer (e.g. for Keycloak) changes no route code. |
| A2 | **ES256-signed JWTs.** Verifiers hold only public keys and accept ES256 only. | No shared secrets between services; a compromised service can't mint tokens. ES256 over EdDSA because every IdP and JWT library supports it. |
| A3 | **One audience per token.** | A token for file-management is useless anywhere else. |
| A4 | **10-minute tokens, no refresh tokens.** Downscoped tokens up to 1 hour. | Revocation is "disable the client"; outstanding tokens die on their own. |
| A5 | **Two levels of permission:** `scope` (which actions) and `authorization_details` (RFC 9396, which resources). Both must allow an operation. | Routes check scopes cheaply; the resource service checks locations. |
| A6 | **Policy decided at mint time, enforced locally.** Receivers never call auth-service per request. | No per-request latency or dependency on auth-service. |
| A7 | **Tokens can only narrow.** Every exchange yields a subset of what the requester may have, with a bounded lifetime. | No path from a weak credential to a stronger one. |
| A8 | **Delegation and relay follow RFC 8693** (`act` claim, actor chains); only the token's own audience may exchange it; relaying needs `can_relay_clients`. | Prevents token passthrough and the confused-deputy problem. |
| A9 | **No user login here.** End users will log in at api-service through an identity provider. | auth-service only issues tokens to services. |
| A10 | **No database.** Policy is a YAML file in git; secret hashes and keys are files on CT 102, reloaded when they change. | Reviewable permissions, nothing to operate. |
| A11 | **Permissions in git, credentials never.** A client exists in `policy.yaml` first; its secret is created on CT 102 and piped straight to the client's host. | Every permission change is reviewed; secrets never pass through git, chat or a terminal. |
| A12 | **The issuer is a name we own, over HTTPS with a public certificate** (`auth.lan.liustev6.ca`, Let's Encrypt via DNS-01). | Clients send secrets to it and trust its JWKS; no custom CA on any host; the IP can change without touching services. |
| A13 | **Published on port 443.** The container listens on 8443; host 443 maps to it. | The issuer URL has no port, so discovery points clients to `:443`. |
| A14 | **Hardened Docker Compose, deployed by hand** from a pushed commit, with the image built by GitHub Actions. | Same model as every service (deployer D1, D11); by hand because it is the root of trust (D13). |
| A15 | **Every deploy ends with a smoke test as a dedicated `smoke-test` client** (only grant: `private/smoke-test/`). | Proves the live path works without touching a real client's credentials. |
| A16 | **No per-audience `delegable_scopes`.** Delegated tokens get the same scopes as direct ones. | No current use case; added back if a scope must be direct-only. |

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
| Errors and bearer usage | RFC 6749 §5.2 plus `invalid_target`, `invalid_authorization_details`; RFC 6750 `WWW-Authenticate: Bearer error="…"` |
| Security guidance | OAuth 2.0 Security BCP (RFC 9700) |

Because it follows these, the same ideas carry over to Keycloak, Auth0, Okta or Entra ID. The one
non-standard piece is grant 2's gateway-asserted user (a custom `subject_token_type`), a stepping
stone until users come from an identity provider.

## Concepts
| Term | Meaning |
|---|---|
| **Client** | A service that requests tokens. Needs a policy entry and a secret. |
| **Audience** | The service a token is for (`aud`). Each verifier accepts only its own name; receivers need no client entry. |
| **Scope** | Coarse permission at an audience, `<area>:<action>`, e.g. `files:read`. |
| **Authorization detail** | RFC 9396 object naming exact resources (`type`, `actions`, `locations`). The resource service defines its *detail type*. |
| **Grant template** | A client's entry for one audience: scopes and detail templates with variables. |
| **Delegated / relayed / downscoped** | `sub` is someone other than the caller (a user or a relayed service) and `act` names who acts for them; downscoped = a narrower copy of a token the requester holds. |
| **Principal** | What route code gets: `service` (nearest actor), `scopes`, `authorization_details`, `on_behalf_of`, `actor_chain`, `client_id`, `subject`, `token_id` (`service_auth/principal.py`). |

## Policy
`infra/services/auth-service/policy.yaml`: deliberately small. The full multi-service shape
(users, sandboxes, downscoping) lives in `python/services/auth-service/tests/fixtures/policy.yaml`,
which the tests use. Tokens live 10 minutes (`token_ttl_seconds: 600`), downscoped ones at most
1 hour, and an `act` chain is at most 3 deep.

**Audiences:**

| Audience | Scopes | Detail types |
|---|---|---|
| `api-service` | `files:read write delete list` | none |
| `file-management` | `files:read write delete list` | `file_access`: actions `read write delete list`, prefix locations under `public/` or `private/` (must equal file-management's buckets) |
| `secrets-manager` | `secrets:read` | none; what a login may read is OpenBao's policy, generated from the manifests |

**Clients:**

| Client | Audience: what it gets | Notes |
|---|---|---|
| `deployer` | secrets-manager: `secrets:read` | OpenBao login as role `deployer` (`sub=service:deployer`); secret host-only on CT 105 |
| `api-service` | file-management: `read`, `list` on `public/` | its own token = what anonymous visitors may do; `can_relay_clients` |
| `pi-server` | api-service: all `files:*`; file-management: all on `private/${client}/` | Hey Pi's backend |
| `smoke-test` | file-management: all on `private/${client}/` | deploy checks only (A15) |

**Templates:** `${client}` is the requesting client (when relayed, the **relayed** client);
`${user}` is the user from a delegated `sub` (`user:42` → `42`), and a template using it is
**dropped** from tokens without a user.

**Validation** (on load, `check-policy`, and every deploy before installing): the issuer is
`https://`; every grant names an existing audience, scope, detail type and action; variables are
`${client}` or `${user}` only; locations stay under the audience's roots, end in `/` or name one
object, and contain no `..`, `//`, `\` or control characters. An invalid policy is rejected and the
running one stays. The special permissions default to off: `can_relay_clients` (grant 3 relay;
api-service only), `can_assert_users` (grant 2; nobody, no users yet) and `can_downscope` (grant 4;
nobody, see Known issues).

## Token endpoint: `POST /oauth2/token`
Form-encoded (a repeated parameter is an error); client authentication by HTTP Basic only.
Responses are JSON with `Cache-Control: no-store`; errors are RFC 6749 JSON. `invalid_client`
(401) is identical for an unknown client, a wrong secret and a disabled client. Over 30 requests a
minute per client and IP: 429 with `Retry-After`. `scope` and `authorization_details` may ask for
**less** than the policy allows, never more.

| Grant | Request | Used for | Enabled |
|---|---|---|---|
| 1 | `grant_type=client_credentials` | a service calling as itself | all clients |
| 2 | token exchange, `subject_token_type=urn:steven-universe:token-type:user-id` | api-service starting to act for a logged-in user | no (no users) |
| 3 | token exchange, a `subject_token` issued **to** the requester | passing a user along a chain; api-service relaying an outside client | relay, for api-service |
| 4 | token exchange, the requester's **own** token for the same audience | a narrower, longer-lived token for a sandbox or share link | no (Known issues) |

- **Grant 3:** the subject token must verify and its `aud` must be the requester. A `service:`
  subject needs `can_relay_clients` and gets the **relayed client's** templates; a `user:` subject
  gets the requester's own templates with `${user}`. `exp = min(now + 600, subject.exp)`.
- **Grant 4:** the subject token's `client_id` is the requester and its `aud` the requested
  audience; the result is a subset of it, `exp = now + min(expires_in, 3600)`.

Code: `app/api/token.py` (request handling), `app/issuer.py` (the grants), `app/permissions.py`
(templates and narrowing).

**Other endpoints:**

| Endpoint | Returns |
|---|---|
| `/.well-known/oauth-authorization-server` | RFC 8414 metadata: issuer, token endpoint, JWKS URI, grant types, `client_secret_basic`, scopes, detail types |
| `/.well-known/jwks.json` | public signing keys (EC P-256, ES256, `kid`), cacheable 5 minutes |
| `/health` | `status`, `version`, `policy_version` (hash of the policy), `clients_in_policy`, `published_keys`, `active_kid` |

No introspection endpoint (tokens are verified locally) and no admin API.

## The service-auth library
```python
auth = ServiceAuth.from_env("file-management")      # receiving; SERVICE_AUTH_ISSUER
async def put_object(..., p: Principal = Depends(auth.require("files:write"))): ...

tokens = TokenClient.from_env()                      # calling; SERVICE_AUTH_ISSUER, _CLIENT_ID, _CLIENT_SECRET_FILE
files = httpx.AsyncClient(base_url=..., auth=tokens.auth("file-management"))
relayed = httpx.AsyncClient(base_url=..., auth=tokens.auth_for(principal, "file-management"))  # grant 3
```
- **Verifying** (`verifier.py`, `fastapi_auth.py`): ES256 and `typ: at+jwt` only; issuer, a single
  audience equal to this service, `exp`/`nbf` with 30 s leeway. Keys from the JWKS: cached
  5 minutes, refetched early for an unknown `kid` (at most every 30 s), kept up to 24 h if
  auth-service is down. Errors per RFC 6750: 401 `invalid_token`, 403 `insufficient_scope`, 503 if
  no keys can be had. `ENV=local` plus `SERVICE_AUTH_DEV_PRINCIPAL` gives a fixed principal for
  local development, refused otherwise.
- **Calling** (`client.py`): `token()`, `assert_user()`, `exchange()` and `downscope()` cover grants
  1–4. Tokens are cached and renewed 60 s before expiry; concurrent callers share one request;
  issuer failures are retried, and a still-valid cached token is used while auth-service is down;
  a 401 from the target drops the token and retries once. The secret must come from a file
  (`SERVICE_AUTH_CLIENT_SECRET_FILE` or systemd `$CREDENTIALS_DIRECTORY/client_secret`); a secret
  in an environment variable is refused.

## Deployment
| Item | Value |
|---|---|
| Host | CT 102 `auth-service` (SSH alias `auth-host`), unprivileged LXC with Docker; boots first ([boot.md](boot.md)) |
| DNS | Cloudflare `A auth.lan.liustev6.ca` → its LAN address, DNS only: a public name with a private address |
| Certificate | Let's Encrypt via DNS-01 (certbot, Cloudflare token limited to DNS edit); `scripts/cert-hook.sh` copies renewals for uid 10001 and restarts the container |
| Runtime | `compose.yaml`: read-only filesystem, uid 10001, `cap_drop: [ALL]`, `no-new-privileges`, host 443 → 8443; `/etc/auth-service` read-only except `clients/` (last-mint stamps) |
| Settings | `infra/services/auth-service/config.env` → `/etc/auth-service/config.env` (compose `env_file`); log level only today |
| Admin CLI | `/usr/local/bin/auth-service-admin`: `python -m app.admin` in a one-off container with **no network** |

**Deploying:** `infra/deployment/deploy.sh auth-service [ref]` from the Mac, with your OpenBao
login, runs `/usr/local/sbin/deploy-target` on CT 102, which runs that commit's
`infra/deployment/deploy-target.sh` (details in [deployer.md](deployer.md)). It installs
`config.env` and the managed secrets (smoke-test client secret, Cloudflare token), checks the
host-only files and the certificate, pulls the image from GHCR (built by
`.github/workflows/build.yml`, never on the host), and runs `hooks/pre-start.sh`: admin CLI, policy
validated by the new image, diffed and installed (the running service reloads it), and the first
signing key on a first deploy only. Then it starts the container, checks `/health` by name over TLS
and runs `scripts/smoke_test.py` as `smoke-test` (discovery, JWKS, a real mint verified against the
live keys; wrong secret, wrong audience and no grant rejected). `pipeline: false` in `deploy.yaml`,
so the deployer refuses it (D13, D16).

**Files on CT 102** (owner 10001, mode 700/600):
```
/etc/auth-service/config.env, policy.yaml   installed by every deploy
/etc/auth-service/keys/<kid>.pem, active    private signing keys; `active` names the signing one (host-only)
/etc/auth-service/clients/<id>.sha256       hash of the current secret, .next.sha256 while rotating (host-only)
/etc/auth-service/clients/<id>.json         enabled, created, rotated; <id>.last_mint
/etc/auth-service/tls/cert.pem, key.pem     copied by the certbot hook
/root/.secrets/cloudflare.ini, smoke-test-client   root-only, written from OpenBao by each deploy
```
Signing keys and client hashes are never copied anywhere, not even into OpenBao.

## Client registration and credential lifecycle
### Onboarding a new service
1. Add the client and its grants to `policy.yaml`, push, deploy auth-service by hand.
2. From the Mac: `python/services/auth-service/scripts/add-client.sh <id> <host> <secret path> <owner>`.
   It creates the secret on CT 102 and pipes it into a root-only file on the client's host; the
   secret is never shown or stored on the Mac.
3. Configure the client (`SERVICE_AUTH_ISSUER`, `SERVICE_AUTH_CLIENT_ID`,
   `SERVICE_AUTH_CLIENT_SECRET_FILE`, the file mounted read-only; list it under `host_only` in its
   manifest), restart, and check `auth-service-admin list-clients` for its first mint.

The deploy side (manifest, OpenBao values) is "Onboarding a service" in [deployer.md](deployer.md).

**Rotating a client secret:** `add-client.sh … --rotate` (old and new both work), restart the
client, then `ssh auth-host auth-service-admin promote-secret <id>`. Yearly, or at once on
suspicion.

**Stopping a client:** `ssh auth-host auth-service-admin disable-client <id>` (`enable-client`
undoes it). Its tokens expire within 10 minutes (downscoped: 1 hour).

## Operations
| Task | How |
|---|---|
| Change permissions | edit `policy.yaml`, push, `infra/deployment/deploy.sh auth-service` (shows the diff; no restart) |
| Rotate the signing key | `new-key` (published, not signing) → wait ≥ 1 h for JWKS caches → `promote-key <kid>` → after ≥ 2 h `retire-key <old kid>`. Every ~90 days; if compromised, promote a new key and retire the old one at once |
| Inspect | `auth-service-admin list-clients` (state, ages, last mint), `list-keys`, `explain <id> <audience>` (resolved permissions), `check-policy` |
| Logs and audit | `docker compose logs -f auth-service`; one JSON line per mint or denial on `auth_service.audit` (client, grant, aud, sub, act, scope, details, `jti`, `exp`, IP; never a secret or token) |
| Roll back | `infra/deployment/deploy.sh auth-service <previous sha>` |

## Security
| Threat | Protection |
|---|---|
| Stolen client secret | Limited to that client's grants; the audit log shows mints from an unexpected IP; `disable-client` stops it. Secrets are 256-bit random, stored only as SHA-256, compared in constant time; the token endpoint is rate-limited before the secret check. |
| Stolen token | One audience, ≤ 10 minutes; never logged (only `jti`). |
| Compromised receiver | Holds only public keys: can't mint tokens. |
| Forged or altered token | ES256 allowlist (`alg: none` and HS256 rejected), `typ` check, signature, issuer, single audience. |
| Fake auth-service or swapped JWKS | Clients only talk to `https://auth.lan.liustev6.ca` with a publicly trusted certificate. |
| Secrets in the wrong place | Secrets are mode-600 files, never environment variables (the library refuses them); the admin CLI writes secrets only to a pipe; nothing secret in git. |
| auth-service host compromised | The signing key is the crown jewel: CT 102 runs nothing else, SSH is key-only, the container is read-only and unprivileged, and the deployer can't reach it (D13). Proxmox backups of CT 102 contain the key and must be protected. |
| Clock skew | 30 s leeway when verifying. |

**If auth-service is down:** cached tokens work until they expire (≤ 10 minutes) and receivers
keep verifying with keys cached up to 24 h; no new tokens, so no pipeline deploys (the deployer
can't log in to OpenBao). The name is in public DNS, so during an internet outage lookups fail once
cached answers expire.

## Testing
- **Unit tests** (`python/services/auth-service/tests`, `python/libs/service-auth/tests`), run in
  CI before every image build: every grant's allow and deny cases, policy validation, key rotation,
  the admin CLI, end-to-end flows with the library on both sides, and verifier attacks (`alg: none`,
  HS256, forged signature, wrong `typ`, wrong or multiple audiences, expired).
- **Smoke test** on every deploy (see Deployment). Relay was checked by hand once against the live
  service (2026-11-07).

## Known issues
- **Downscoped tokens can be relayed** (grant 4, then grant 3), and the relay rebuilds permissions
  from the full policy, losing the narrowing. Fix: mark downscoped tokens and refuse them as a
  `subject_token`. Until then no client has `can_downscope`.
- Rate limiting is in memory, per process (fine for one worker).
- Denial audit lines use spaced JSON, mint lines compact JSON.
- Client authentication is shared secrets only.

## History and what's next
Live since 2026-11-07; moved to the generic `deploy.sh` and GHCR images with the deployer
(phases 0b–1). The deployer, logging in to OpenBao, is its first real caller.

| When | What |
|---|---|
| api-service and pi-server adopt service-auth | issue their secrets (`add-client.sh`, `--rotate` if one exists); relay goes live |
| Before sandboxes or share links | fix the downscope relay gap, then enable `can_downscope` |
| With users | an identity provider; grant 2 accepts its tokens instead of asserted users |
| Later, each a standard on its own | `private_key_jwt` (RFC 7523); an admin API and Dynamic Client Registration (RFC 7591); sender-constrained tokens (mTLS RFC 8705, DPoP RFC 9449); SPIFFE/SPIRE; direct-only scopes; relationship-based access (OpenFGA/SpiceDB); exchanging GitHub's OIDC token here for the deployer; validating against Keycloak |

## Open questions
- Which identity provider for users?
- Machine clients outside the LAN and tailnet: publish `/oauth2/token` through api-service
  (rate-limited), or require the tailnet?
- Share links longer than 1 hour: a stored, revocable share record?

## Non-goals
- End-user login, sessions, MFA.
- Third-party clients, consent screens, refresh tokens.
- A database, admin UI or introspection endpoint.
- Interpreting detail types: each resource service owns the meaning of its own
  `authorization_details`.
