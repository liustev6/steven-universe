# Spec: api-service (the public edge)

**Path**: `python/services/api-service/` (new, 2026-11-08), `infra/services/api-service/`.
**Status**: minimal and live-ready: `GET /health` and the deploy proxy only (built for the deployer, deployer.md D3, D15),
on CT 106 behind the Cloudflare tunnel. The other routes come over from
`python/services/web-gateway/` one at a time (see Migration); this spec describes the target. Related: [auth-service.md](auth-service.md)
(tokens), [file-management.md](file-management.md) (files).

## Scope
api-service is the **only service reachable from the internet**, through the Cloudflare tunnel.
It:

- decides **who a request is from**: an anonymous visitor, a logged-in user, or a machine client;
- enforces each route's access mode (deny by default), rate limits, and request size limits;
- calls internal services **with the right token** (its own, a user's delegated token, or a
  relayed machine client's) and streams their responses back;
- sets caching and security headers so Cloudflare can serve public content from its edge.

It holds no business logic of its own and no downstream secrets other than its own auth-service
client credential.

```
internet ──▶ Cloudflare (cache, WAF, rate rules) ──tunnel──▶ cloudflared (CT 100) ──▶ api-service
                                                                                         │
                     ┌───────────────────────────────┬──────────────────────┬────────────┤
                     ▼                               ▼                      ▼            ▼
              file-management              stevenai-service           gpu-server    auth-service
              (files, LAN only)            (chat + RAG)               (via stevenai) (token mint/exchange)
```

## Locked decisions
| # | Decision |
|---|---|
| E1 | **The only public entry point.** api-service is the only internal service with a Cloudflare tunnel hostname. Every other service listens on the LAN only. |
| E2 | **Deny by default.** Every route declares an access mode; the default is `user`. `public` routes are listed explicitly and checked by a test. |
| E3 | **Its own permissions = what anonymous visitors may do.** api-service's own service token (no user) only holds public permissions (e.g. `read` on `public/`). Anything else needs a token carrying a real user or a relayed client (auth-service A11). |
| E4 | **No token passthrough.** api-service never forwards the credential it received. It exchanges it at auth-service for a token whose audience is the downstream service (auth-service A12, RFC 8693). |
| E5 | **No user login logic of its own.** Users log in through an OpenID Connect identity provider. api-service acts as the backend for the website (the "Backend-for-Frontend" pattern): it completes the OIDC login and holds the session, and the browser only gets an `HttpOnly` session cookie. |
| E6 | **North-south only.** Traffic from outside goes through api-service. LAN services call each other directly with their own tokens; they never route through api-service. |

## Callers and how they're identified
| Caller | Credential | Becomes |
|---|---|---|
| Anonymous visitor | None | `Caller(kind="anonymous")` |
| Logged-in user (browser) | Session cookie from the OIDC login (`HttpOnly; Secure; SameSite=Lax`) | `Caller(kind="user", user_id=…)` |
| Logged-in user (app) | OIDC access token from the identity provider, `aud=api-service` | `Caller(kind="user", user_id=…, scopes=…)` |
| Machine client (Hey Pi off-LAN, scripts, integrations) | auth-service token, `aud=api-service` (client credentials) | `Caller(kind="client", client_id=…, scopes=…)` |

An `Authorization` header is checked first, then the session cookie. A credential that's present
but invalid is a 401. It never falls back to anonymous, so a broken login can't silently look
like a visitor.

## Route access modes
```python
@router.get("/files/public/{path:path}")
async def public_file(path: str, caller: Caller = Depends(edge_auth(mode="public"))): ...

@router.get("/files/me/{path:path}")
async def my_file(path: str, caller: Caller = Depends(edge_auth(mode="user", scopes=["files:read"]))): ...
```
| Mode | Who gets through | Downstream token |
|---|---|---|
| `public` | Everyone | api-service's own token |
| `optional` | Everyone; does more if logged in | Own token if anonymous, delegated if logged in |
| `user` (default) | Logged-in users only; 401 otherwise | Delegated token for the user (auth-service grant 2) |
| `user` + `scopes` | Users or machine clients whose credential carries the scopes | Delegated (user) or relayed (client) token (grant 2 / grant 3) |
| `client` | Machine clients only | Relayed token (grant 3) |
| `proxy` | Whoever the downstream service accepts; api-service only pre-checks and rate-limits | The caller's own credential, forwarded unchanged (allowlisted paths only; today just `/deploy/*`) |

Tests: one walks `app.routes` and fails if a route has no mode, or if the set of `public` routes
differs from a reviewed list. Another checks that every non-public route returns 401 without
credentials.

## Request pipeline
Middleware runs in this order:
1. **Request ID and tracing:** accept or create a W3C `traceparent`; pass it to every
   downstream call; include it in logs and error bodies.
2. **Client IP:** use `CF-Connecting-IP`, trusted only when the request comes from cloudflared.
   Anything else uses the socket address.
3. **Rate limit:** per IP for anonymous callers, per user or client for authenticated ones (token
   bucket in memory; Cloudflare rate rules in front as the first layer).
4. **Body size limit:** per route (e.g. 1 MB for JSON, 95 MB for an upload part; see Uploads).
5. **Edge auth:** identify the `Caller`, then apply the route's access mode.
6. **Handler:** gets the downstream token from `service-auth` (own, delegated, or relayed;
   cached ~9 min) and calls the internal service with streaming where relevant.
7. **Response headers:** caching (below), `Referrer-Policy`, `X-Content-Type-Options: nosniff`,
   `Strict-Transport-Security`, and CORS.

## Routes
### Existing (from web-gateway)
| Route | Mode (target) | Downstream | Notes |
|---|---|---|---|
| `GET /health` | `public` | none | Liveness only; no internal details |
| `GET /status` | `user` (admin) | Proxmox API, services' `/health` | Today it holds Proxmox API tokens; see Problems |
| `POST /chat/stream` | `user` + `chat:stream` | stevenai-service (delegated token) | SSE streaming |
| `POST /classifications/food` | `user` | food101 (future) | Stub today |
| `GET /predictions/landsink` | `public` or `user` (decide) | landsink (future) | Returns 501 today |

### Files (new; backed by file-management's `/objects` API)
| Route | Mode | file-management call | Token |
|---|---|---|---|
| `GET /files/public/{path}` | `public` | `GET /objects/public/{path}` | own |
| `GET /files/me/{path}` | `user` + `files:read` | `GET /objects/users/{user}/{path}` | delegated |
| `PUT /files/me/{path}` | `user` + `files:write` | `PUT /objects/users/{user}/{path}` | delegated |
| `DELETE /files/me/{path}` | `user` + `files:delete` | `DELETE /objects/users/{user}/{path}` | delegated |
| `GET /files/me?prefix=&cursor=` | `user` + `files:list` | `GET /objects/users?prefix={user}/…` | delegated |
| `POST /files/me/{path}:share` | `user` + `files:read` | none (mints a downscoped token, grant 4) | delegated → downscoped |
| `GET /files/s/{share_token}` | `public` (the link is the credential) | `GET /objects/{bucket}/{key}` | the share token itself, sent as `Authorization` |
| `GET /files/services/{client}/{path}` | `client` + `files:read` | `GET /objects/services/{client}/{path}` | relayed |
| multipart: `POST /files/me/{path}:multipart`, `PUT …/parts/{n}`, `POST …:complete` | `user` + `files:write` | the matching multipart routes | delegated |

api-service builds the object key from the **verified** identity (`users/{user}/…`). A request
can't name another user's prefix; file-management also enforces this through the token's grants.

### Deploys (new; see [deployer.md](deployer.md))
| Route | Mode | Downstream | Notes |
|---|---|---|---|
| `POST /deploy/v1/deployments` | `proxy` | deployer `POST /v1/deployments`, body and `Authorization` unchanged, response streamed | The caller is a GitHub Actions workflow with a GitHub OIDC token whose `aud` is the deployer. api-service is not the token's audience: it pre-checks `iss` = GitHub, rate-limits, and forwards. The deployer does the full verification. |
| `GET /deploy/v1/deployments/{id}/events?after=` | `proxy` | deployer `GET /v1/deployments/{id}/events`, streamed | Reconnect after a dropped stream (e.g. while api-service itself is replaced by a wave-2 deploy). Same pre-checks; its own, more generous rate limit. |

`proxy` is the only mode that forwards a credential, and only on this allowlisted path. It does
not conflict with E4, which is about tokens addressed to api-service itself.

## Caching
- **Public content** (`/files/public/*`, public pages):
  `Cache-Control: public, max-age=86400, stale-while-revalidate=600`, with the `ETag` from
  file-management. Cloudflare caches it at the edge, so repeat visits don't reach the home network.
- **Anything for a user or client:** `Cache-Control: private, no-store`. It's never cached at the
  edge.
- **Share links:** `Cache-Control: private, no-store`, plus `Referrer-Policy: no-referrer`; these
  URLs are never written to logs.

## Uploads
- Cloudflare limits a proxied request body to **100 MB** on the free plan. Single `PUT`s are
  limited to 95 MB; bigger files use the multipart routes (parts of ≤ 95 MB), which map onto
  file-management's multipart API.
- Large one-off transfers (model weights) don't go through api-service at all. They come in over
  the LAN or Tailscale, straight to file-management with an admin token.

## Browser security
- **CORS:** only the website's own origin(s), from config; `allow_credentials=True` (cookie
  sessions); methods `GET, POST, PUT, DELETE, OPTIONS`. Never `*` together with credentials.
- **CSRF:** cookie sessions are `SameSite=Lax`. State-changing routes (`POST`, `PUT`, `DELETE`)
  also require a CSRF token (double-submit) or a custom header that forms can't send.
- **Bot protection:** Cloudflare Turnstile on any anonymous route that writes (contact forms, if
  any). Anonymous routes are read-only by default.

## Errors and logging
- JSON errors `{"error": "<code>", "message": "<generic>", "request_id": "…"}`. Internal errors
  never include downstream details. Downstream 401/403 become 403 for the caller; downstream 5xx
  become 502.
- Logs include the request ID, route, caller kind, user or client id, status, and latency. They
  **never** include tokens, cookies, credentials (or prefixes of them), or share-link URLs.

## Current state and problems (web-gateway today)
| # | Problem | Where | Impact |
|---|---|---|---|
| G1 | One shared `API_KEY` (`X-API-Key`) for all callers, effectively public once shipped to a website | `app/core/dependencies.py` `verify_api_key` | No user identity; anyone with the key can use every route |
| G2 | Key compared with `!=` | same | Not timing-safe |
| G3 | A failed check logs the first 10 characters of the submitted key | same | A typo'd real key half-leaks into logs |
| G4 | It holds **Proxmox API tokens** for `/status` | `app/core/config.py`, `app/api/status.py` | The most exposed service holds the most powerful credential. Move status collection to an internal service (with a read-only Proxmox API role), and keep the route admin-only. |
| G5 | Per-service static keys for every downstream (`STEVENAI_SERVICE_API_KEY`, `FILE_SERVICE_API_KEY`, `GPU_SERVICE_API_KEY`) | `config.py` | Replaced by its single auth-service client credential |
| G6 | `/status` and `/health` reveal internal service layout to anyone | `status.py`, `health.py` | `/health` should say "ok" only; `/status` admin-only |
| G7 | README still describes it as `web-server` at `python/services/web-server/` | `README.md` | Outdated docs |

## Migration
Not a rename: web-gateway's code carries G1–G6 and isn't running anywhere, so api-service started
fresh (2026-11-08) with only what phase 2 of the deployer needed, and the rest is **ported route by
route, fixed on the way**. web-gateway is deleted when nothing is left in it.

1. **Done:** `python/services/api-service/`, one package per route group (`app/deploy/` today), with deny-by-default modes (`public`, `proxy`;
   `app/edge.py`) and the reviewed route list test; request IDs, security headers, client IP from
   `CF-Connecting-IP` only via cloudflared, rate limit; `/health` minimal (G6); the deploy proxy.
   Hardened compose on CT 106, manifest-driven deploys, its own LE certificate (`api.lan`).
2. ~~Quick fixes on the old code~~: not needed; G1–G3 and G5 disappear as routes are ported.
3. **Edge auth:** `Caller`, `edge_auth(mode=…)` with the remaining modes (`user`, `optional`,
   `client`).
   Until a user login exists, `user` routes stay closed and `client` routes use auth-service
   tokens (auth-service is live; relaying with `can_relay_clients` is already in its policy).
4. **service-auth client:** replace the per-service keys (G5) with one auth-service client;
   anonymous routes use the own token.
5. **File routes:** public files with caching, then `files/me`, share links, multipart.
6. **User login:** pick the identity provider, add the OIDC login (Backend-for-Frontend), sessions,
   and CSRF; then `user` routes open up, using auth-service's grant 2 (users asserted by
   api-service, later verified identity-provider tokens).
7. **Move Proxmox access out** of api-service (G4).

## Open questions
- **Identity provider** for users: Authentik, Keycloak, or Zitadel (self-hosted), or a hosted one?
- **Website:** the tunnel publishes `api.liustev6.ca`; is the website on the same site (simpler
  cookies, no CORS) or a different one?
- **Anonymous writes:** are there any (contact form, guest uploads), or is anonymous access
  strictly read-only?
- **Outside machine clients:** does api-service publish auth-service's token endpoint
  (`/oauth2/token`, rate-limited), or must such clients join the tailnet?
- **`/predictions/landsink`:** public demo or user-only?

## Non-goals
- Business logic (chat, classification, files): owned by the internal services.
- Being called by LAN services (E6).
- Storing user accounts or passwords (identity provider).
