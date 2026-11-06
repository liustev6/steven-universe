# Spec: auth-service (internal token issuer) and service-auth library

**Paths**: `python/services/auth-service/` (the issuer), `python/libs/service-auth/` (shared
client and verifier library). **Status**: proposed. Phase 0 can ship with the file-management
rewrite before the issuer exists.

## Scope
Services in this repo call each other over the LAN: api-service (the only internet-facing
service; see [api-service.md](api-service.md)), stevenai-service, gpu-server, file-management, and
later home-hub, agent sandboxes (the next steven-ai, the Hey Pi agent).
Today each link uses its own static key, header name, and comparison code. This spec replaces
that with:

- **auth-service**: a small issuer that mints **short-lived, signed access tokens** for services.
  Each token is for one target service (audience) and carries what the caller may do there: coarse
  **scopes** (which actions) and fine-grained **authorization details** (on which resources). It
  can also mint tokens that let a service **act for a user**, and **narrower copies** of a token
  for less-trusted code such as sandboxes or share links.
- **service-auth**: a library every service uses to **get** tokens (client side) and **check**
  them (server side). Route code only ever sees a `Principal`.

```
                        ┌──────────────────────────────────────────┐
                        │ auth-service  (issuer = https://auth.lab) │
   client credentials   │  POST /oauth2/token  (mint, exchange,    │   GET /.well-known/jwks.json
  ───────────────────▶  │                       downscope)         │  ─────────────────────────▶ every verifier
   ◀── access token ──  │  policy.yaml  (clients, grant templates) │      (public keys, cached)
                        │  signing keys (ES256, kid)               │
                        └──────────────────────────────────────────┘

api-service ──Bearer <aud=stevenai-service>──▶ stevenai-service ──Bearer <aud=gpu-server>──▶ gpu-server
     │                                               │                                       │
     └──Bearer <aud=file-management>──▶ file-management ◀──────────────────────────────────────┘
                                             ▲
              sandbox ──Bearer <downscoped: workspaces/stevenai-v2/ws-7f3/ only>
```

## Standards
This project is for learning, so every piece follows a published standard. Choices made here
should carry over to production identity providers (Keycloak, Auth0, Okta, Entra ID, Stytch).

| Piece | Standard |
|---|---|
| Service-to-service tokens | OAuth 2.0 **client credentials grant** (RFC 6749 §4.4) |
| Client authentication | `client_secret_basic` (RFC 6749 §2.3.1); later `private_key_jwt` (RFC 7523) |
| Token format | **JWT Profile for OAuth 2.0 Access Tokens** (RFC 9068): `typ: at+jwt`, required claims `iss sub aud exp iat jti client_id`, `scope` |
| Signing and keys | JWS (RFC 7515), JWK / JWK Set (RFC 7517), `alg: ES256` (RFC 7518) |
| Naming the target service | `resource` (RFC 8707 Resource Indicators); `audience` accepted too (RFC 8693) |
| Fine-grained, resource-level permissions | **Rich Authorization Requests** (RFC 9396): the `authorization_details` request parameter and token claim |
| Delegation / acting for a user | **OAuth 2.0 Token Exchange** (RFC 8693): `act` claim, `subject_token`, actor chains |
| Downscoping (narrower token for less-trusted code) | RFC 8693 token exchange with a narrower `scope` / `authorization_details`; the same idea as AWS STS session policies and Google Cloud Credential Access Boundaries |
| Policy templates (`${user}`) | Modelled on IAM policy variables (e.g. `${aws:username}`) |
| Discovery | **Authorization Server Metadata** (RFC 8414): `/.well-known/oauth-authorization-server` |
| Errors | RFC 6749 §5.2 error codes, plus `invalid_target` (RFC 8707) and `invalid_authorization_details` (RFC 9396) |
| Calling a protected service | Bearer token usage (RFC 6750), `WWW-Authenticate: Bearer error="..."` |
| Security guidance | OAuth 2.0 Security Best Current Practice (RFC 9700) |

The one deliberate non-standard piece is phase 2's gateway-asserted user (a custom
`subject_token_type`); phase 3 replaces it with a standard identity-provider token.

Related industry approaches, not used here: **SPIFFE/SPIRE** (workload identity via mTLS
certificates instead of secrets), **sender-constrained tokens** (mTLS-bound, RFC 8705; DPoP,
RFC 9449), and **relationship-based access control** (Zanzibar; OpenFGA, SpiceDB) for
user-to-user sharing. All are listed under later work.

## Locked decisions
| # | Decision |
|---|---|
| A1 | **Route code depends on `Principal` only.** Tokens, keys, and headers are handled inside `service-auth`. Swapping static keys (phase 0) for issued tokens (phase 1+) changes no route code. |
| A2 | **Tokens are JWTs signed with ES256 (ECDSA P-256).** Verifiers hold only public keys from the JWKS, so a compromised service can't mint tokens for others. No HMAC shared secrets between services. ES256 is chosen over EdDSA because every major identity provider and JWT library supports it. |
| A3 | **One audience per token.** A token minted for `file-management` is rejected everywhere else. |
| A4 | **Short lifetime, no refresh tokens.** Access tokens live 10 minutes (downscoped tokens up to 1 hour); services mint a new one with their client credentials. Revocation means disabling the client; outstanding tokens expire on their own. |
| A5 | **Delegation follows RFC 8693.** In a delegated token, `sub` is the user and `act` is the acting service (nested for chains). Only clients granted `can_assert_users` can start a delegation; others can only pass one along. |
| A6 | **No user login here.** End-user authentication belongs to api-service / an identity provider. auth-service only issues tokens for services. |
| A7 | **No database.** Policy is a reviewed YAML file in the repo; client secret hashes and signing keys are files on the auth-service host. |
| A8 | **Two levels of permission.** `scope` says *which actions* are allowed at the audience (`files:read`); `authorization_details` (RFC 9396) says *on which resources* (`users/42/`). A request needs both. Resource services define their detail types; auth-service only resolves and narrows them. |
| A9 | **Tokens can only narrow.** Every exchange (chained delegation, downscoping) yields a token whose scopes, actions, and locations are a subset of what the requester is allowed, and whose lifetime is bounded. Nothing ever widens a token. |
| A10 | **auth-service decides, resource services enforce.** Policy is evaluated at mint time and the result is written into the token. Resource services check the token locally; they don't call auth-service per request. |
| A11 | **"Anonymous" is decided at the edge, not here.** api-service decides whether a request is anonymous, a user, or a machine client. auth-service never sees anonymous visitors. api-service's **own** service token (no user) carries exactly what anonymous visitors may do, so a bug in an anonymous route can't reach user data. |
| A12 | **Every hop verifies its own token; no token passthrough.** A service never forwards the token it received; it exchanges it for one whose audience is the next service. Traffic from outside goes through api-service (north-south). Services on the LAN call each other directly (east-west), not through api-service. |

## Concepts
| Term | Meaning |
|---|---|
| **Client** | A service that requests tokens. Identified by `client_id` (= service name, e.g. `gpu-server`). |
| **Audience** | The service a token is for (`aud`), e.g. `file-management`. Each verifier accepts only its own name. |
| **Scope** | A coarse permission at an audience, written `<area>:<action>`, e.g. `files:read`. Names are owned by the audience service. |
| **Authorization detail** | An RFC 9396 object `{type, actions, locations, identifier?}` naming exact resources, e.g. `{"type": "file_access", "actions": ["read"], "locations": ["models/"]}`. |
| **Grant template** | Policy entry for a client at an audience: scopes plus authorization-detail templates that may contain variables (`${client}`, `${user}`). |
| **Delegated token** | A token whose `sub` is someone other than the caller: a user (`user:42`), or a machine client relayed by api-service (`service:hey-pi`). `act` names the service acting for them. |
| **Relay** | api-service passing an outside machine client's **own** identity and permissions downstream (grant 3 with a service subject), instead of using its own. |
| **Anonymous request** | A request to api-service with no credentials. It never reaches auth-service; downstream it is api-service acting as itself. |
| **Downscoped token** | A narrower copy of a token the requester already holds, for handing to less-trusted code (a sandbox, a share link). |
| **Principal** | What a verifier gives route code: who is calling, for whom, and with which permissions. |

### Principal (the contract every service codes against)
```python
@dataclass(frozen=True)
class Principal:
    service: str                                   # calling service, e.g. "gpu-server"
    scopes: frozenset[str]                         # e.g. {"files:read"}
    authorization_details: tuple[Mapping, ...]     # RFC 9396 objects, already concrete (no variables)
    on_behalf_of: str | None                       # "user:42" or a relayed "service:hey-pi"; None if direct
    actor_chain: tuple[str, ...]                   # services in the chain, nearest first; () if direct
    token_id: str | None                           # jti, for logs

    def details(self, type_: str) -> tuple[Mapping, ...]: ...   # e.g. p.details("file_access")
```
FastAPI usage (the resource service interprets its own detail type):
```python
@router.get("/objects/{bucket}/{key:path}")
async def get_object(bucket: str, key: str, p: Principal = Depends(require("files:read"))):
    authorize(p, "read", bucket, key)      # file-management's own check, see file-management.md
```

## Token format (RFC 9068 + RFC 9396)
Header: `{"alg": "ES256", "typ": "at+jwt", "kid": "<key id>"}`.

The issuer identifier is an HTTPS URL, e.g. `https://auth.lab` (configurable as
`SERVICE_AUTH_ISSUER`). Audiences are service identifiers, e.g. `file-management`. RFC 7519 allows
any string; URLs such as `https://files.lab` would also work.

| Claim | Service token | Delegated token | Downscoped token | Notes |
|---|---|---|---|---|
| `iss` | `https://auth.lab` | same | same | Verifiers check exactly |
| `sub` | `service:<client_id>` | `user:<id>`, or `service:<client_id>` when relayed | same as the token it came from | |
| `act` | absent | `{"sub": "service:<client_id>", "act": {...}}` | same as the token it came from | Nested, nearest actor outermost; max depth 3 |
| `aud` | `<target service>` | same | same | Exactly one string |
| `scope` | space-separated | same | subset | |
| `authorization_details` | array of concrete objects | same, `${user}` resolved | subset; may add `identifier` | Absent if the audience defines no detail types |
| `client_id` | `<client_id>` | same | the requester | |
| `iat`, `nbf`, `exp` | `exp = iat + 600` | never later than the token it came from | `≤ iat + 3600` | |
| `jti` | random 128-bit | same | same | Logged, not stored |

Example delegated token (user → api-service → file-management):
```json
{
  "iss": "https://auth.lab", "aud": "file-management",
  "sub": "user:42", "act": {"sub": "service:api-service"},
  "client_id": "api-service",
  "scope": "files:read files:write files:list",
  "authorization_details": [
    {"type": "file_access", "actions": ["read", "write", "list"], "locations": ["users/42/"]}
  ],
  "iat": 1762400000, "exp": 1762400600, "jti": "…"
}
```
Example downscoped sandbox token (stevenai-v2 hands this to sandbox `ws-7f3`):
```json
{
  "iss": "https://auth.lab", "aud": "file-management",
  "sub": "service:stevenai-v2", "client_id": "stevenai-v2",
  "scope": "files:read files:write files:list",
  "authorization_details": [
    {"type": "file_access", "identifier": "ws-7f3",
     "actions": ["read", "write", "list"], "locations": ["workspaces/stevenai-v2/ws-7f3/"]}
  ],
  "iat": 1762400000, "exp": 1762403600, "jti": "…"
}
```
`Principal` mapping: `service` = `act.sub` (or `sub` for service tokens), `on_behalf_of` = `sub`
for delegated tokens, `actor_chain` = the nested `act` subjects, `authorization_details` as-is.

## Policy (`python/services/auth-service/policy.yaml`)
Reviewed in git; contains no secrets.
```yaml
issuer: https://auth.lab
token_ttl_seconds: 600
downscoped_max_ttl_seconds: 3600
max_delegation_depth: 3

audiences:
  api-service:                           # the edge; scopes its routes require from authenticated callers
    scopes: [files:read, files:write, files:delete, files:list, chat:stream]
    delegable_scopes: [files:read, files:write, files:delete, files:list, chat:stream]
  file-management:
    scopes: [files:read, files:write, files:delete, files:list]
    delegable_scopes: [files:read, files:write, files:delete, files:list]
    detail_types:
      file_access:
        actions: [read, write, delete, list]
        location_syntax: prefix          # "<bucket>/<key prefix>"; subset = string prefix
  gpu-server:
    scopes: [tasks:run, sessions:read, sessions:admin]
    delegable_scopes: [tasks:run]
  stevenai-service:
    scopes: [chat:stream]
    delegable_scopes: [chat:stream]

clients:
  api-service:
    can_assert_users: true               # may start a delegation for a user it authenticated
    can_relay_clients: true              # may pass an outside machine client's own identity downstream
    can_downscope: true                  # share links
    grants:
      stevenai-service:
        scopes: [chat:stream]
      file-management:
        scopes: [files:read, files:write, files:delete, files:list]
        authorization_details:
          # its OWN token (no user) gets only this: exactly what anonymous visitors may do
          - {type: file_access, actions: [read, list],                locations: ["public/"]}
          # only in tokens acting for a user (${user} can't resolve otherwise)
          - {type: file_access, actions: [read, write, delete, list], locations: ["users/${user}/"]}
  hey-pi:                                # pi-server on the panel; from outside it comes in via api-service
    grants:
      api-service:
        scopes: [files:read, files:list]
      file-management:
        scopes: [files:read, files:list]
        authorization_details:
          - {type: file_access, actions: [read, list], locations: ["services/${client}/"]}
  stevenai-v2:                           # next steven-ai, runs agent sandboxes
    can_downscope: true
    grants:
      gpu-server:
        scopes: [tasks:run]
      file-management:
        scopes: [files:read, files:write, files:delete, files:list]
        authorization_details:
          - {type: file_access, actions: [read, write, delete, list], locations: ["workspaces/${client}/"]}
          - {type: file_access, actions: [read, write, list],         locations: ["users/${user}/"]}
  hey-pi-agent:
    can_downscope: true
    grants:
      file-management:
        scopes: [files:read, files:write, files:delete, files:list]
        authorization_details:
          - {type: file_access, actions: [read, write, delete, list], locations: ["workspaces/${client}/"]}
  gpu-server:
    grants:
      file-management:
        scopes: [files:read, files:write]
        authorization_details:
          - {type: file_access, actions: [read],  locations: ["models/"]}
          - {type: file_access, actions: [write], locations: ["users/${user}/outputs/"]}
  admin-cli:
    grants:
      file-management:
        scopes: [files:read, files:write, files:delete, files:list]
        authorization_details:
          - {type: file_access, actions: [read, write, delete, list], locations: ["models/", "public/"]}
      gpu-server:
        scopes: [sessions:read, sessions:admin]
```

**Template variables:**

| Variable | Resolves to | Available in |
|---|---|---|
| `${client}` | the requesting `client_id`; when api-service relays a machine client (grant 3), the **relayed** client | every token |
| `${user}` | the user id from the delegated `sub` (`user:42` → `42`) | delegated tokens only; templates containing `${user}` are **dropped** from non-delegated tokens |

Startup fails if the policy references an undefined audience, scope, detail type, or action;
uses an unknown variable; or has a location that doesn't start with a known bucket name.

## Credentials (how clients prove who they are)
- **Client secret (phase 1).** Each client gets a 256-bit random secret, generated by the admin
  CLI (see the next section). auth-service stores only `sha256(secret)`. The secret is
  high-entropy, so a plain SHA-256 is enough (password hashes like bcrypt exist for guessable
  human passwords). The client sends it with HTTP Basic auth on `/oauth2/token`
  (`client_secret_basic`).
- **Checking a secret** happens only when that client asks for a token:
  1. look up `client_id` (exists in `policy.yaml`, not disabled);
  2. `h = sha256(received secret)`;
  3. `hmac.compare_digest(h, current hash)` or, during rotation, the `.next` hash;
  4. match → continue with the grant; otherwise 401 `invalid_client` (same response whether the
     client doesn't exist or the secret is wrong).

  When A calls B, B's secret plays no part; B only uses it when *B* requests a token.
- **Two active secrets per client** (current and `.next`) allow rotation without downtime.
- **Later (optional): `private_key_jwt` (RFC 7523).** The client signs a short assertion with
  its own key (`client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer`),
  so no shared secret exists at all. The rest of the token request stays the same.
- **Sandboxes and share links never get client credentials.** They only ever hold downscoped
  tokens (grant 4).

## Client registration and credential lifecycle
Clients are created with an admin CLI that **exists only on the auth-service host**. There is no
admin API on the network (an admin API and self-registration, RFC 7591, are later options).
Whoever can reach that host's shell (SSH or `pct exec` from Proxmox) is an auth-service admin.

### Client IDs
- `client_id` = the service name: lowercase DNS-label style, `^[a-z][a-z0-9-]{1,62}$`
  (`gpu-server`, `hey-pi`, `api-service`).
- Not secret: it appears in tokens (`sub=service:<id>`), storage paths (`services/<id>/`), and
  audit logs.
- **Never renamed or reused.** A renamed service (web-gateway → api-service) gets a new client;
  the old one is disabled and kept, so old audit entries still resolve.

### Files on the auth-service host
| Path | Content | Mode |
|---|---|---|
| `/etc/auth-service/policy.yaml` | Clients and their grants (deployed from git) | 644 |
| `/etc/auth-service/clients/<id>.sha256` | Hash of the current secret | 600 |
| `/etc/auth-service/clients/<id>.next.sha256` | Hash of the next secret (only during rotation) | 600 |
| `/etc/auth-service/clients/<id>.json` | `{"enabled", "created_at", "rotated_at"}` | 600 |
| `/etc/auth-service/keys/<kid>.pem` | Signing keys | 600 |

auth-service re-reads a client's files when they change (by file modification time), so adding,
rotating, or disabling a client takes effect without a restart. Plaintext secrets are never
stored on this host.

### `auth-service-admin` commands
Run on the auth-service host as root (or the `authsvc` user), e.g.
`ssh auth-host auth-service-admin list-clients` or `pct exec <ctid> -- auth-service-admin …`.

| Command | What it does |
|---|---|
| `add-client <id> --stdout` | Refuses unless `<id>` is in `policy.yaml` and has no credential yet. Generates a 256-bit secret (`secrets.token_urlsafe(32)`), writes its hash and `<id>.json`, and writes the plaintext **once** to stdout (for piping; see below). |
| `rotate-secret <id> --stdout` | Generates a new secret, writes `<id>.next.sha256`, prints the plaintext once. Both secrets now work. |
| `promote-secret <id>` | The `.next` hash becomes current; the old hash is deleted. |
| `disable-client <id>` / `enable-client <id>` | Sets `enabled`. A disabled client can't mint; its existing tokens expire on their own (≤ 10 min, downscoped ≤ 1 h). |
| `list-clients` | ID, enabled, secret age, rotation in progress, last successful mint. |
| `check-policy` | Validates `policy.yaml` (same checks as startup). |
| `explain <id> <audience> [--user N]` | Prints the resolved scopes and details, for debugging policy. |
| `new-key` / `promote-key` / `retire-key` | Signing-key rotation (see Signing keys). |

The CLI never prints a secret except through `--stdout`, never logs it, and refuses to write it
to a terminal (stdout must be a pipe) unless `--force-tty` is given.

### Onboarding a new service
Permissions and credentials are separate steps: **permissions go through git review;
credentials never touch git.**
1. **Permissions:** add the client and its grants to `policy.yaml` in git; deploy it to the
   auth-service host; `auth-service-admin check-policy`.
2. **Credential:** from your Mac, pipe the new secret straight from the auth-service host into
   the service's host. It passes through the pipe only; it's never shown, saved on the Mac, or in
   shell history:
   ```sh
   ssh auth-host auth-service-admin add-client hey-pi --stdout \
     | ssh panel 'sudo install -D -m 600 -o liustev6 /dev/stdin /etc/pi-server/credentials/client_secret'
   ```
   A small wrapper, `scripts/auth/add-client.sh <id> <host> <path> <owner>`, runs exactly this.
   The auth-service host needs no SSH access to other hosts.
3. **Configure the service** (client ID is plain config; the secret is a file):
   - `SERVICE_AUTH_CLIENT_ID=hey-pi`
   - `SERVICE_AUTH_CLIENT_SECRET_FILE=…` (the `_FILE` convention used by Docker and Postgres
     images). The library refuses a secret in an environment variable.
   - **systemd services:** `LoadCredential=client_secret:/etc/pi-server/credentials/client_secret`;
     the service reads `$CREDENTIALS_DIRECTORY/client_secret`, which only it can see.
   - **Docker services:** mount the file read-only at `/run/secrets/client_secret`.
4. **Restart and verify:** restart the service; `auth-service-admin list-clients` shows its
   first successful mint.

### Rotating a secret
```sh
ssh auth-host auth-service-admin rotate-secret hey-pi --stdout \
  | ssh panel 'sudo install -m 600 -o liustev6 /dev/stdin /etc/pi-server/credentials/client_secret'
ssh panel 'sudo systemctl restart pi-server'
ssh auth-host auth-service-admin promote-secret hey-pi   # after the service's first mint with the new secret
```
Rotate yearly, whenever someone who had host access leaves the picture, and immediately if a leak
is suspected (`disable-client` first if it's being abused).

### Threat model for client secrets
| Where | How it could leak | Protection |
|---|---|---|
| **Service host** (most likely) | Container or host compromise; file readable by others; secret in environment variables (visible in `/proc`, crash dumps, `docker inspect`) | File mode 600, owned by the service user; loaded via `LoadCredential` or `/run/secrets`; never an environment variable |
| **Backups** | Proxmox backups of the container include the file | Encrypted backups; restrict who can read them |
| **Git** | A committed `.env` or credential file | `.gitignore`; secret scanning (e.g. gitleaks) as a pre-commit hook |
| **Network** | HTTP Basic is readable without encryption | TLS on `/oauth2/token` (required) |
| **Logs** | Libraries logging request headers | Never log `Authorization`; redact in middleware; the CLI never logs secrets |
| **Onboarding** | Secret copied by hand through chat, a terminal, or a note | The pipe in step 2; `--stdout` refuses a terminal |
| **auth-service host** | Only hashes are stored | Can't be used to log in. The **signing key** on the same host is the far more valuable target; host access is therefore restricted to you. |
| **Guessing** | 256-bit random | Infeasible; `/oauth2/token` rate limits and WARN logs on repeated `invalid_client` |

**If a secret is stolen**, the damage is limited to that client's own grants. The audit log
shows mints from an unexpected IP. `disable-client` stops new mints at once, and outstanding
tokens expire within 10 minutes (downscoped within 1 hour).

## Token endpoint: `POST /oauth2/token`
Form-encoded (`application/x-www-form-urlencoded`), per RFC 6749. The client authenticates with
HTTP Basic (`client_id:secret`). Successful responses are JSON with `Cache-Control: no-store`:
`{"access_token", "token_type": "Bearer", "expires_in", "scope", "authorization_details"}`, plus
`"issued_token_type": "urn:ietf:params:oauth:token-type:access_token"` for token exchange.
Errors use RFC 6749 §5.2 JSON (`{"error", "error_description"}`): `invalid_client` 401
(with `WWW-Authenticate: Basic`), `invalid_request` / `invalid_grant` / `invalid_scope` /
`invalid_target` / `invalid_authorization_details` 400, `unauthorized_client` 400,
`unsupported_grant_type` 400.

**Naming the target service:** `resource` (RFC 8707) or `audience` (RFC 8693); both map to
`aud`. Exactly one target per request.

**Requesting resources:** an optional `authorization_details` parameter (RFC 9396 §3,
URL-encoded JSON array) asks for **less** than the policy allows. If omitted, the token gets
everything the resolved templates allow.

### How a token's permissions are computed (all grants)
1. `allowed_scopes` = the client's `grants[audience].scopes` (for delegated tokens, intersected
   with the audience's `delegable_scopes`).
2. `allowed_details` = the client's templates for that audience, with variables resolved; templates
   whose variables can't be resolved are dropped.
3. If the request names `scope` / `authorization_details`, each requested item must be
   **covered** by an allowed one, or the request fails with `invalid_scope` /
   `invalid_authorization_details`. *Covered* means: same `type`; requested `actions` ⊆ allowed
   `actions`; every requested location starts with an allowed location (prefix subset, see
   below).
4. The token gets the requested items, or all allowed ones if nothing was requested.

**Location subset rule (`location_syntax: prefix`):** locations are `<bucket>/<prefix>` and must
end in `/` unless they name one exact object. `A` covers `B` if `B` starts with `A`. Locations
are rejected if they contain empty segments, `.` or `..` segments, `\`, or control characters,
so that string prefixes and paths can't disagree.

### Grant 1: service token (`client_credentials`)
```
grant_type=client_credentials&resource=file-management&scope=files:read
&authorization_details=[{"type":"file_access","actions":["read"],"locations":["models/"]}]
```
1. Authenticate the client; it must be enabled in policy.
2. The client must have a grant for the audience (otherwise `invalid_target`).
3. Compute permissions as above, without `${user}`.
4. Mint: `sub=service:<client>`, no `act`.

### Grant 2: start a delegation (`token-exchange`, user assertion)
Used by api-service after it has authenticated an end user. **This is the one non-standard
step:** RFC 8693 allows custom token-type URIs, but having a client simply assert a user id is
a stepping stone until phase 3. It is only allowed for clients with `can_assert_users`.
```
grant_type=urn:ietf:params:oauth:grant-type:token-exchange
&subject_token=user:42
&subject_token_type=urn:steven-universe:token-type:user-id
&resource=file-management&scope=files:read files:list
```
1. Authenticate the client; it must have `can_assert_users: true`.
2. Compute permissions with `${user}=42` and delegable scopes only.
3. Mint: `sub=user:42`, `act={"sub": "service:api-service"}`.

**Phase 3:** `subject_token_type=urn:ietf:params:oauth:token-type:id_token` with the user's
identity-provider token. auth-service verifies it against the provider's JWKS (issuer, audience,
expiry) and takes `sub` from it. Then even the gateway can't invent users.

### Grant 3: pass an identity along (`token-exchange`, chained or relayed)
Used by a service that received a token and needs to call the next service **for the same
subject**. The subject is either a user (chained delegation) or, at the edge only, an outside
machine client (relay).
```
grant_type=urn:ietf:params:oauth:grant-type:token-exchange
&subject_token=<the token this service received>
&subject_token_type=urn:ietf:params:oauth:token-type:access_token
&resource=file-management&scope=files:write
```
1. Authenticate the client.
2. Verify `subject_token` fully (signature, `iss`, `exp`), and check that **`subject_token.aud`
   equals the requesting client**. A service can only pass along tokens that were issued *to it*.
3. Decide whose permissions apply, from `subject_token.sub`:

   | Subject | Allowed when | Permissions come from |
   |---|---|---|
   | `user:<id>` (chained delegation) | always | the **requesting client's** templates with `${user}=<id>`. A service acts for a user only where its own policy allows (gpu-server can write `users/42/outputs/`, nothing else). |
   | `service:<client>` (relay) | the requester has `can_relay_clients` (api-service only) | the **relayed client's** templates, with `${client}=<relayed client>`; `${user}` templates are dropped. api-service's own grants don't apply, so a relayed client never gets more than it would by calling directly. |

4. Only the target audience's `delegable_scopes` are allowed; any requested `scope` /
   `authorization_details` must be covered (same rules as above).
5. Chain depth after adding this hop ≤ `max_delegation_depth`.
6. Mint: same `sub`; `act = {"sub": "service:<client>", "act": <subject_token.act>}`;
   `exp = min(now + ttl, subject_token.exp)`, so a chain never outlives the original token.

### Grant 4: downscope (`token-exchange`, narrowing)
Used to hand a **strictly narrower** token to less-trusted code: an agent sandbox, a share link.
Only for clients with `can_downscope`.
```
grant_type=urn:ietf:params:oauth:grant-type:token-exchange
&subject_token=<a token the requester holds for file-management>
&subject_token_type=urn:ietf:params:oauth:token-type:access_token
&requested_token_type=urn:ietf:params:oauth:token-type:access_token
&resource=file-management&scope=files:read files:write files:list
&authorization_details=[{"type":"file_access","identifier":"ws-7f3",
   "actions":["read","write","list"],"locations":["workspaces/stevenai-v2/ws-7f3/"]}]
&expires_in=3600
```
1. Authenticate the client; it must have `can_downscope: true`.
2. Verify `subject_token` fully; its `client_id` must be the requester and its `aud` must be the
   requested audience. (The requester narrows its *own* token for the *same* target.)
3. Requested scopes ⊆ `subject_token.scope`; every requested detail must be covered by a
   detail in `subject_token`. `identifier` is free-form and is logged for audit.
4. `exp = now + min(expires_in, downscoped_max_ttl_seconds)`.
5. Mint: same `sub` and `act` as `subject_token`, with the narrower `scope` and
   `authorization_details`.

The requester can always mint a fresh subject token, so the downscoped lifetime isn't tied to
the subject token's `exp`. It is capped instead (1 h). Long-running sandboxes get a renewed token
from their orchestrator (e.g. rewritten to a mounted token file), never credentials.

### Other endpoints
| Endpoint | Purpose |
|---|---|
| `GET /.well-known/oauth-authorization-server` | RFC 8414 metadata: `issuer`, `token_endpoint`, `jwks_uri`, `grant_types_supported` (`client_credentials`, token exchange), `token_endpoint_auth_methods_supported` (`client_secret_basic`), `scopes_supported`, `authorization_details_types_supported` (RFC 9396). Clients and verifiers are configured with the **issuer URL only** and discover everything else here. |
| `GET /.well-known/jwks.json` | Public signing keys (`kty: EC`, `crv: P-256`, `alg: ES256`, `kid`, `use: sig`). `Cache-Control: max-age=300`. |
| `GET /health` | Liveness; reports how many keys are loaded and the policy version (git sha). |

No introspection endpoint: tokens are verified locally.

## The edge: anonymous and authenticated callers
api-service is the only internet-facing service (D1 in file-management.md, A11). It answers
"who is this request from?"; auth-service answers "what may this call do downstream?".

| Caller at api-service | How it proves identity | What api-service sends downstream |
|---|---|---|
| **Anonymous visitor** (public website) | Nothing | api-service's **own** service token (grant 1): public-only permissions |
| **Logged-in user** | Identity-provider session cookie or OIDC access token | A delegated token for that user (grant 2; the provider's token in phase 3) |
| **Machine client from outside** (Hey Pi or the phone app off-LAN, scripts, integrations) | Its own auth-service token, `aud=api-service` (grant 1) | A relayed token: the client's **own** permissions (grant 3, service subject) |

- **api-service's own permissions = the anonymous set.** Anything an anonymous route could
  trigger is limited to what api-service's own token holds (`public/` read). User and client
  permissions only exist in tokens that carry a real user or a relayed client. That protects
  against the confused-deputy problem.
- **Machine clients get credentials from auth-service**, created with `auth-service-admin
  add-client` (later: Dynamic Client Registration, RFC 7591, or `private_key_jwt` keys the client
  generates itself). Their scopes live in `policy.yaml`. On the LAN or tailnet they request tokens
  from auth-service directly. Clients that are truly outside need a published token endpoint
  (open question).
- **On the LAN, skip api-service.** A LAN service (Hey Pi at home, stevenai, gpu-server) gets a
  token for the target service and calls it directly (east-west). api-service is for traffic from
  outside (north-south).

## End-to-end flows
Every receiver checks its token **locally** (signature against the cached JWKS, `aud`, `exp`,
scopes); auth-service is only called to mint or exchange, and those results are cached.

### Flow 1: machine client from outside (Hey Pi) → api-service → file-management
Hey Pi (its pi-server, client `hey-pi`) reads a wallpaper from its own area, `services/hey-pi/`,
while off the LAN.
```
 pi-server (hey-pi)      auth-service           api-service              file-management        RustFS
      │                       │                      │                          │                   │
 ①   │── POST /oauth2/token ─▶│                      │                          │                   │
      │   client_credentials   │ check hey-pi's       │                          │                   │
      │   resource=api-service │ secret + policy      │                          │                   │
      │◀── T1 ─────────────────│                      │                          │                   │
      │   aud=api-service  sub=service:hey-pi  scope=files:read  exp=10 min      │                   │
      │   (cached, reused for every request, renewed at ~9 min)                  │                   │
      │                       │                      │                          │                   │
 ②   │── GET /files/services/hey-pi/wallpapers/sunset.jpg ─▶│                     │                   │
      │   Authorization: Bearer T1                    │ verify T1 LOCALLY:       │                   │
      │                       │                      │  signature (cached JWKS) │                   │
      │                       │                      │  aud=api-service, exp,   │                   │
      │                       │                      │  route needs files:read  │                   │
 ③   │                       │◀── exchange T1 ──────│  (grant 3, relay)        │                   │
      │                       │ check T1 + hey-pi's  │                          │                   │
      │                       │ grants at f-m        │                          │                   │
      │                       │── T2 ───────────────▶│ (cached ~9 min)          │                   │
      │                       │  aud=file-management  sub=service:hey-pi  act=api-service           │
      │                       │  read, list on services/hey-pi/ only            │                   │
 ④   │                       │                      │── GET /objects/services/hey-pi/wallpapers/sunset.jpg
      │                       │                      │   Bearer T2 ────────────▶│ verify T2 LOCALLY │
      │                       │                      │                          │ (aud=file-mgmt)   │
      │                       │                      │                          │ authorize(read,   │
      │                       │                      │                          │  services/hey-pi/…)│
 ⑤   │                       │                      │                          │── GetObject ─────▶│
      │◀═══════════════════ bytes stream back ═══════│◀═════════════════════════│◀══════════════════│
```

| Step | Who checks | What |
|---|---|---|
| ① | auth-service | hey-pi's credential and what policy grants it (once per ~9 min) |
| ② | api-service | T1 is genuine, meant for api-service, not expired; the route's scope |
| ③ | auth-service | T1 is genuine and issued to api-service; hey-pi's own grants at file-management (once per ~9 min, cached) |
| ④ | file-management | T2 is genuine, meant for file-management; `authorize()` on the exact object |

At home, on the LAN, Hey Pi skips api-service. It mints a token with
`resource=file-management` (step ①) and calls file-management directly (step ④). That's one
mint and one check, with no exchange.

### Flow 2: anonymous web visitor → api-service → file-management
```
 browser            Cloudflare            api-service             auth-service    file-management      RustFS
    │                   │                      │                        │                │                  │
 ① │── GET https://api.liustev6.ca/files/public/blog/cat.jpg   (no credentials)          │                  │
    │──────────────────▶│ edge cache HIT? ─── yes ──▶ serve from cache, STOP            │                  │
    │                   │ MISS: through tunnel (cloudflared, CT 100) ──▶│                │                  │
 ② │                   │                      │ route mode = public    │                │                  │
    │                   │                      │ rate limit by           │                │                  │
    │                   │                      │ CF-Connecting-IP        │                │                  │
 ③ │                   │                      │ use its OWN token T ◀─ mint once per ~9 min (cached)     │
    │                   │                      │  aud=file-management  sub=service:api-service            │
    │                   │                      │  read, list on public/ ONLY  (no ${user} → no users/)    │
 ④ │                   │                      │── GET /objects/public/blog/cat.jpg  Bearer T ──────────▶│
    │                   │                      │                        │  verify T LOCALLY              │
    │                   │                      │                        │  authorize(read, public/…)     │
 ⑤ │                   │                      │                        │                │── GetObject ───▶│
    │◀══ bytes ═════════│◀═ Cache-Control: public, max-age=86400 ═══════│◀═══════════════│◀════════════════│
    │                   │ stores a copy at the edge; the next visitor's request stops at ①            │
```

If an anonymous visitor requests a private file (`GET /files/me/report.pdf`), two separate
checks stop it:
1. **api-service:** the route is `mode=user` and there's no login, so **401**. file-management is
   never called.
2. **Even if that route had a bug** and called file-management with api-service's own token T:
   T only grants `public/`, so file-management returns **403**. The anonymous path can't obtain a
   token that touches `users/`.

### Background work (both flows)
```
api-service, file-management ── every 5 min ──▶ auth-service /.well-known/jwks.json   (public keys, for local checks)
```
- **JWKS refresh (receivers).** Lets each receiver verify signatures without calling
  auth-service, and picks up key rotation or removal. An unknown `kid` triggers an immediate
  refresh. If auth-service is down, the last good key set is kept for up to 24 h.
- **Token cache (callers).** One token per (target, subject, permissions), reused for every
  request and renewed about 60 s before `exp`.

Example: gpu-server sends 1,000 requests to file-management in 10 minutes. That costs 1 mint
plus about 2 JWKS fetches, and 1,000 local checks at well under a millisecond each. No request
waits on auth-service.

## Verifier (service-auth, server side)
`authenticate(request) -> Principal` performs, in order:
1. Read `Authorization: Bearer <jwt>` (RFC 6750); otherwise 401 `WWW-Authenticate: Bearer`.
2. Parse the header; **`alg` must be `ES256`** (an allowlist, never taken from the token; reject
   `none` and HS*). `typ` must be `at+jwt` (RFC 9068 §4), which stops ID tokens or other JWTs
   being replayed as access tokens.
3. Find the key by `kid` in the cached JWKS. If it's unknown, refresh the JWKS once (at most
   once per 30 s), then reject.
4. Verify the signature; then `iss == <configured issuer URL>`, `aud == <this service>`,
   `nbf`/`exp` with 30 s leeway, and that `sub`, `client_id`, `iat`, `jti`, and `scope` are
   present. `authorization_details`, if present, must be an array of objects with a `type`.
5. Build the `Principal`. `require(*scopes)` checks the scopes (403 otherwise). Resource-level
   checks (`authorization_details`) are done by the resource service, which knows its own types.

JWKS cache: refreshed every 5 minutes in the background. If auth-service is unreachable,
**keep the last good key set for up to 24 hours**, so verification keeps working while the
issuer is down.

Errors follow RFC 6750 §3: 401 with `WWW-Authenticate: Bearer error="invalid_token"` for
missing, invalid, or expired tokens (the client should get a new one); 403 with
`error="insufficient_scope"` for valid tokens lacking a scope or resource permission. The
response body is generic; the reason goes to logs, along with `jti`, `client_id`, `sub`, and
`aud`. Tokens are never logged.

## Token client (service-auth, client side)
```python
tokens = TokenClient(issuer_url, client_id, client_secret)   # discovers token_endpoint via RFC 8414
files = httpx.AsyncClient(auth=tokens.for_audience("file-management", scopes=["files:read"]))
# for the user in the incoming request:
as_user = tokens.exchange_for(principal_token, audience="file-management", scopes=["files:write"])
# narrower token for a sandbox:
sandbox_token = await tokens.downscope("file-management",
    details=[file_access(["read", "write", "list"], "workspaces/stevenai-v2/ws-7f3/", identifier="ws-7f3")],
    expires_in=3600)
```
- Caches one token per `(audience, scopes, details, subject)` and renews it **60 s before
  `exp`**. Concurrent callers share one in-flight request.
- On a 401 from the target service: drop the cached token, mint a new one, retry once.
- Retries the issuer with exponential backoff (max 3 tries). A cached valid token is still
  used while the issuer is down.

## Signing keys
- ECDSA P-256 private keys live in `/etc/auth-service/keys/<kid>.pem` (mode 600). `kid` is the
  date plus a random suffix, e.g. `2026-11-a1b2`. (In production these would live in a KMS or
  HSM, which signs without ever exposing the key; the rotation steps stay the same.)
- **Rotation** (planned every ~90 days, or at once if a key is compromised):
  1. Generate a new key and **publish** it in the JWKS without signing with it yet. Wait at least
     the JWKS cache time (5 min; use 1 h to be safe).
  2. Switch signing to the new key.
  3. Remove the old key from the JWKS once every token signed with it has expired (1 h for
     downscoped tokens; use 2 h to be safe).
- If a key is compromised: remove it from the JWKS immediately and restart verifiers (or wait
  for the next refresh). Tokens signed with it then fail.

## Security notes
- **Transport.** `/oauth2/token` carries client secrets, so it must use TLS even on the LAN. Use a
  certificate from a small internal CA or Caddy. Service-to-service calls carry bearer tokens
  and should move to TLS too; until then, short lifetimes and single audiences limit the damage
  of a sniffed token.
- **Tokens in URLs.** Share links carry a downscoped token. RFC 6750 §2.3 discourages tokens in
  query strings because URLs end up in logs and `Referer` headers. The gateway therefore puts it
  in the path, forwards it as an `Authorization` header, sets `Referrer-Policy: no-referrer`, and
  never logs share-link URLs. Share tokens are read-only, cover one object, and are capped at 1 h
  (longer-lived sharing needs a stored share record; see open questions).
- **Clock sync.** Every host runs chrony (Proxmox and Debian do by default); leeway is 30 s.
- **Rate limiting.** `/oauth2/token` allows 30 requests/min per client and IP; repeated
  `invalid_client` failures are logged at WARN.
- **Audit log** for every mint: `client_id`, grant type, `aud`, scopes, `authorization_details`
  (with `identifier`), `sub`, chain, `jti`, `exp`. Never secrets or tokens.
- **Startup checks.** auth-service refuses to start with a world-readable key or secret file,
  an invalid policy, or no signing key.
- **Dev mode.** `SERVICE_AUTH_DEV_PRINCIPAL='{"service":"dev","scopes":["*"],
  "authorization_details":[...]}'` gives a fixed `Principal` without tokens. The library refuses
  it unless `ENV=local`.

## Failure behaviour
| Failure | Effect |
|---|---|
| auth-service down | Cached tokens keep working until `exp` (≤ 10 min; downscoped ≤ 1 h); verifiers keep the last known keys for 24 h. New mints fail; clients retry with backoff. |
| Client disabled or secret rotated | Its next mint fails with `invalid_client`; its existing tokens, and any downscoped from them, die within 1 h. |
| Policy narrowed | Takes effect on the next mint. |
| Clock skew > 30 s | Tokens rejected as not yet valid / expired; fix chrony. |

## Deployment
- An LXC on pve01, `auth-service`: 1 core, 512 MB, 4 GB disk, fixed IP, **`startup order=0`**
  so it comes up before every other service.
- FastAPI + uvicorn behind TLS, port 8443. Libraries: `pyjwt[crypto]` (ES256, JWKS client) and
  `cryptography`. Use the libraries for JWT/JWK work; don't hand-roll signing or parsing.
- Files: `policy.yaml` (deployed from git), `/etc/auth-service/keys/`, `/etc/auth-service/clients/`
  (see "Client registration and credential lifecycle").
- The admin CLI `auth-service-admin` is installed on this host only; there is no admin API.
- First install: `auth-service-admin new-key` + `promote-key` (first signing key), deploy
  `policy.yaml`, then onboard clients one by one.

## Phases
| Phase | What ships | Route code changes |
|---|---|---|
| **0** | `service-auth` with `Principal`, `require()`, and a **static-key** backend: a config file maps each service's hashed key to scopes and detail templates; `${user}` is resolved from `X-On-Behalf-Of`, accepted only from keys allowed to assert users. file-management adopts it in its rewrite; gpu-server's file client moves to its own key. | Routes use `Depends(require(...))` and `authorize(...)` |
| **1** | auth-service with `client_credentials`, `authorization_details`, and JWKS. Static-key backend replaced by the JWT verifier; clients use `TokenClient`. | None |
| **2** | Delegation: grant 2 (api-service asserts users) and grant 3 (user chains, and relaying outside machine clients). `X-On-Behalf-Of` removed; `${user}` comes from the token. | None |
| **2b** | Downscoping: grant 4, for agent sandboxes and share links. | None |
| **3** | Grant 2 accepts identity-provider tokens, so users are verified instead of asserted by the gateway. | None |

**Later (optional, each a standard on its own):**
- `private_key_jwt` client authentication (RFC 7523), so no client secrets exist.
- An admin API (LAN or tailnet only, scope `clients:admin`) with the CLI as a thin client, and
  self-registration of services (Dynamic Client Registration, RFC 7591/7592) using one-time
  registration tokens. With `private_key_jwt`, no person ever handles a credential.
- Sender-constrained tokens: mTLS-bound (RFC 8705) or DPoP (RFC 9449), so a stolen token is
  useless without the client's key.
- Workload identity with SPIFFE/SPIRE, replacing client secrets with automatically rotated
  certificates.
- Relationship-based access (OpenFGA or SpiceDB) for user-to-user sharing and groups. These
  can't be written into a token at mint time, so resource services would also ask that service.
- Validating the setup against an off-the-shelf issuer (e.g. Keycloak's client credentials and
  token exchange), which should work with `service-auth` without code changes.

Signs it's time to move from phase 0 to phase 1: a third hop needs user delegation, sandboxes
need their own narrow credentials, a service's access must be revoked without redeploying
others, or keeping keys in sync across `.env` files becomes painful.

## Scope and detail catalog (initial)
| Audience | Scope | Detail type | Meaning | Delegable |
|---|---|---|---|---|
| file-management | `files:read` / `files:write` / `files:delete` / `files:list` | `file_access` (actions `read write delete list`, locations `<bucket>/<prefix>`) | Object operations, limited to the detail's locations | yes |
| gpu-server | `tasks:run` | — | Run predefined tasks | yes |
| | `sessions:read` / `sessions:admin` | — | Inspect / kill GPU sessions | no |
| stevenai-service | `chat:stream` | — | Chat with RAG | yes |

## Open questions
- Which identity provider will api-service use for users (needed for phase 3)?
- Machine clients that are truly outside the LAN and tailnet: publish auth-service's
  `/oauth2/token` and `/.well-known/*` through api-service (e.g. `https://api.liustev6.ca/oauth2/token`,
  rate-limited), or require them to join the tailnet?
- TLS: an internal CA (e.g. `step-ca`), Caddy with an internal issuer, or Tailscale certificates?
- Share links longer than 1 hour: a stored share record in the gateway (revocable), or a
  separate long-lived detail type?
- Do workers inside gpu-server's Docker containers ever call other services? If so, they get
  downscoped tokens from gpu-server, never credentials.
- Should pi-server / home-hub join this, or keep device tokens of their own?

## Non-goals
- End-user login, sessions, passwords, MFA (identity provider / api-service).
- Third-party OAuth clients, consent screens, refresh tokens.
- A database, admin UI, admin API (for now), or token introspection endpoint.
- Interpreting detail types: each resource service owns the meaning of its own
  `authorization_details` and enforces it.
