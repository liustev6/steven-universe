# auth-service

The internal OAuth 2.0 token issuer for steven-universe services.

**Path**: `python/services/auth-service/`
**Spec**: `docs/specs/auth-service.md`
**Library**: `python/libs/service-auth/` (token client + verifier used by every other service)

It mints short-lived JWT access tokens (ES256, RFC 9068) for services, and nothing else:
no user login, no admin API, no database.

| Grant | Request | Used for |
|---|---|---|
| 1 | `client_credentials` | A service calling as itself |
| 2 | token exchange, `subject_token_type=urn:steven-universe:token-type:user-id` | api-service starting to act for a user it authenticated |
| 3 | token exchange, subject = a token issued **to** the requester | Passing a user along a chain, or api-service relaying an outside client |
| 4 | token exchange, subject = the requester's **own** token for the same audience | Downscoping for a sandbox or a share link |

Each token is for one audience and carries `scope` (which actions) and
`authorization_details` (RFC 9396; which resources). Receivers verify tokens
locally against `/.well-known/jwks.json`; they never call auth-service per request.

## Endpoints

| Endpoint | What |
|---|---|
| `POST /oauth2/token` | All four grants. Client auth: HTTP Basic (`client_secret_basic`). |
| `GET /.well-known/oauth-authorization-server` | RFC 8414 discovery metadata |
| `GET /.well-known/jwks.json` | Public signing keys |
| `GET /health` | Liveness, policy version, active key |

## Layout

```
app/
  main.py          create_app(): loads policy, keys, clients; refuses to start on unsafe files
  api/token.py     POST /oauth2/token: form parsing, client auth, rate limit, audit log
  api/wellknown.py discovery + JWKS
  issuer.py        the four grants and minting
  permissions.py   template resolution and the "can only narrow" checks
  policy.py        policy.yaml model, validation, reload-on-change
  keys.py          signing keys (keys/<kid>.pem, keys/active), rotation
  clients.py       client secret hashes (clients/<id>.sha256), rotation, enable/disable
  admin.py         auth-service-admin CLI
  validation.py    client-id, user-id, and location rules
policy.yaml        the policy (reviewed in git; deployed to /etc/auth-service/policy.yaml)
Dockerfile         image (build context: python/, so libs/service-auth is included)
compose.yaml       how it runs: hardened service + a network-less `admin` one-off service
bin/auth-service-admin   host wrapper: runs the CLI in the `admin` container
scripts/add-client.sh    pipe a new secret from the auth host into a service's host
```

## Local development

```sh
cd python/services/auth-service
uv venv .venv --python 3.11
uv pip install -p .venv -r requirements-dev.txt      # includes ../../libs/service-auth
.venv/bin/python -m pytest tests ../../libs/service-auth/tests
```

Run it in-process against a scratch config directory (plain HTTP, local only):

```sh
export AUTH_SERVICE_CONFIG_DIR=$PWD/.local-config
mkdir -p $AUTH_SERVICE_CONFIG_DIR && cp policy.yaml $AUTH_SERVICE_CONFIG_DIR/
.venv/bin/python -m app.admin promote-key "$(.venv/bin/python -m app.admin new-key | awk '{print $2}')"
.venv/bin/python -m app.admin add-client pi-server --stdout > .local-config/pi-server.secret
.venv/bin/uvicorn app.main:app --factory --port 8443

curl -s -u "pi-server:$(cat .local-config/pi-server.secret)" \
  -d grant_type=client_credentials -d resource=file-management \
  http://127.0.0.1:8443/oauth2/token
```

(`.local-config/` is git-ignored only if you add it; don't commit secrets.)

Or run the real container locally (needs a cert in `tls/`; a self-signed one is fine):
```sh
export AUTH_SERVICE_HOST_CONFIG=$PWD/.local-config           # instead of /etc/auth-service
AUTH_SERVICE_COMPOSE=$PWD/compose.yaml sh bin/auth-service-admin list-clients
docker compose up -d --build && docker compose logs -f
```

## Admin

`auth-service-admin` exists only on the auth-service host; whoever has a shell
there is the admin. Commands: `add-client`, `rotate-secret`, `promote-secret`,
`disable-client`, `enable-client`, `list-clients`, `check-policy`, `explain`,
`new-key`, `promote-key`, `retire-key`, `list-keys`.

Onboard a service (permissions in git first, then the credential):

```sh
# 1. add the client + grants to policy.yaml, deploy it, then:
ssh auth-host auth-service-admin check-policy
# 2. create the secret and pipe it into the service host:
scripts/add-client.sh pi-server panel /etc/pi-server/credentials/client_secret liustev6
# 3. on the service: SERVICE_AUTH_ISSUER, SERVICE_AUTH_CLIENT_ID, SERVICE_AUTH_CLIENT_SECRET_FILE
#    (the secret file mounted read-only into its container), restart, then check list-clients
```

## Deployment (not deployed yet)

Docker Compose on its own LXC on pve01 (1 core, 512 MB, `startup order=0`), deployed **by hand**
(it is the root of trust; see the deployer spec). The container runs as uid 10001 with a read-only
filesystem, no capabilities, and `no-new-privileges`; only `/etc/auth-service/clients` is
writable (last-mint stamps).

1. **LXC:** unprivileged, `features: nesting=1,keyctl=1` (needed for Docker); install Docker
   Engine and the Compose plugin.
2. **Network:** DHCP reservation `192.168.50.235` for CT 102 (MAC `BC:24:11:51:80:B6`); Cloudflare `A auth.lan.liustev6.ca` → that IP,
   DNS only (spec, Deployment).
3. **Code:** `git clone https://github.com/liustev6/steven-universe /opt/steven-universe`.
4. **Config dir**, owned by the container user:
   ```sh
   install -d -m 700 -o 10001 -g 10001 /etc/auth-service /etc/auth-service/{keys,clients,tls}
   install -m 600 -o 10001 -g 10001 /opt/steven-universe/python/services/auth-service/policy.yaml /etc/auth-service/
   ```
5. **TLS:** Let's Encrypt via DNS-01 (certbot + Cloudflare API token limited to DNS edit on
   `liustev6.ca`). A deploy hook copies the files where the container can read them and restarts it:
   ```sh
   install -m 600 -o 10001 -g 10001 "$RENEWED_LINEAGE/fullchain.pem" /etc/auth-service/tls/cert.pem
   install -m 600 -o 10001 -g 10001 "$RENEWED_LINEAGE/privkey.pem"   /etc/auth-service/tls/key.pem
   docker compose -f /opt/steven-universe/python/services/auth-service/compose.yaml restart auth-service
   ```
6. **Admin CLI:** `install -m 755 bin/auth-service-admin /usr/local/bin/`, then
   `auth-service-admin new-key` and `auth-service-admin promote-key <kid>`.
7. **Start:** `cd /opt/steven-universe/python/services/auth-service && docker compose up -d --build`.

**Deploy / update from the Mac:** `infra/deployment/auth-service.sh [ref]` does steps 3–7 for a
pushed commit (default `main`): checkout on the host, config dir, admin CLI, policy (validated
first), first signing key if missing, build, start, health check over TLS. It stops if the TLS
certificate is missing, so do step 5 once by hand first.

**By hand on the host:** `git pull`, then `docker compose up -d --build`. Policy-only changes need no restart:
copy the new `policy.yaml` into `/etc/auth-service/` and it is reloaded (an invalid one is
rejected and the old one stays). Later the image comes from GHCR instead
(`AUTH_SERVICE_TAG=<sha> docker compose up -d`), built by CI.

**Smoke test:** every deploy ends with `scripts/smoke_test.py`, run in the new image as the
`smoke-test` client: discovery, JWKS, a real mint verified against the live keys, and three
rejections (wrong secret, wrong audience, no grant). `smoke-test` is a dedicated client in
`policy.yaml` whose only grant is `private/smoke-test/` at file-management, so real clients are
never touched. Its secret is created on the first deploy and kept root-only on the auth host in
`/root/.secrets/smoke-test-client`.

**Logs:** `docker compose logs -f auth-service` (includes the `auth_service.audit` lines).

## Notes and deviations from the spec

- **Phase 0 (static per-service keys) is not implemented.** This goes straight to
  issued tokens (phase 1), plus grants 2–4.
- **`location_roots`** in a detail type lists the allowed first path segments
  (the buckets). The policy is rejected if a grant points outside them.
- The policy "version" in `/health` and logs is a hash of the file, not a git sha.
- JWKS refresh in `service-auth` is lazy (on the first check after 5 minutes),
  not a background task; the effect is the same.
- Rate limiting is in memory, per process: fine for one uvicorn worker.
- The audit log is the `auth_service.audit` logger (one JSON line per mint or
  denial), written to the container log (`docker compose logs auth-service`).

## Known issue: don't enable `can_downscope` yet

A downscoped token (grant 4) can be sent to a relaying service and exchanged
(grant 3). The relay rebuilds permissions from the relayed client's full policy
grants, so the narrowing is lost: a read-only sandbox token sent to api-service
comes back with read/write/delete at file-management. Planned fix: mark
downscoped tokens and refuse them as a `subject_token`, so they work only at
their own audience. Until then the deployed `policy.yaml` gives no client
`can_downscope` (the test fixture still does, to cover the grant itself).
