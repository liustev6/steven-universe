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
bin/auth-service-admin   host wrapper for the CLI (runs as authsvc)
scripts/add-client.sh    pipe a new secret from the auth host into a service's host
deploy/auth-service.service  hardened systemd unit
```

## Local development

```sh
cd python/services/auth-service
uv venv .venv --python 3.11
uv pip install -p .venv -r requirements-dev.txt      # includes ../../libs/service-auth
.venv/bin/python -m pytest tests ../../libs/service-auth/tests
```

Run it against a scratch config directory (plain HTTP, local only):

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
#    (or systemd LoadCredential=client_secret:...), restart, then check list-clients
```

## Deployment (not deployed yet)

Target: an LXC on pve01 (1 core, 512 MB), `startup order=0`.

1. Create the `authsvc` user; code in `/opt/auth-service` with a venv
   (`pip install -r requirements.txt ../../libs/service-auth`).
2. `/etc/auth-service/` owned by `authsvc`, mode 700: `policy.yaml`, `keys/`, `clients/`, `tls/`.
3. Network: DHCP reservation `192.168.50.21` on the router; Cloudflare `A auth.lan.liustev6.ca`
   → that IP, DNS only. TLS certificate in `tls/` from Let's Encrypt via DNS-01 (Cloudflare API
   token), renewed automatically. Details in the spec's Deployment section.
4. `auth-service-admin new-key` then `promote-key <kid>`.
5. Install `deploy/auth-service.service` and `bin/auth-service-admin`
   (`/usr/local/bin`), `systemctl enable --now auth-service`.

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
  denial), which ends up in journald.

## Known issue: don't enable `can_downscope` yet

A downscoped token (grant 4) can be sent to a relaying service and exchanged
(grant 3). The relay rebuilds permissions from the relayed client's full policy
grants, so the narrowing is lost: a read-only sandbox token sent to api-service
comes back with read/write/delete at file-management. Planned fix: mark
downscoped tokens and refuse them as a `subject_token`, so they work only at
their own audience. Until then the deployed `policy.yaml` gives no client
`can_downscope` (the test fixture still does, to cover the grant itself).
