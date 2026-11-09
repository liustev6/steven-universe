# api-service

The only service reachable from the internet: Cloudflare → tunnel (cloudflared, CT 100) →
`https://api.lan.liustev6.ca` (CT 106). Spec: [docs/specs/api-service.md](../../../docs/specs/api-service.md).

**Today it is minimal**: `GET /health`, and `POST /deploy/v1/deployments`, which forwards GitHub
Actions deploy requests to the deployer (deployer spec D3, D15). The other routes (files, chat,
user login) move over from `web-gateway` one at a time (spec "Migration").

| Module | What |
|---|---|
| `app/edge.py` | access modes (deny by default; a test checks the reviewed route list), client IP (`CF-Connecting-IP` only from cloudflared) |
| `app/deploy_proxy.py` | the deploy proxy: rate limit, 4 KB body, a cheap check that the token *claims* to be GitHub's for the deployer, then forward unchanged and stream the NDJSON back. The deployer does the real verification. |
| `app/ratelimit.py` | in-memory token bucket per client IP |
| `app/main.py` | request IDs, security headers, one log line per request (never credentials) |

## Configuration
`infra/services/api-service/config.env` → `/etc/api-service/config.env`: cloudflared's IP and the
deployer's URL. No secrets of its own yet (the Cloudflare token for certbot comes from OpenBao).

## Deploy (by hand for now)
```sh
infra/deployment/deploy.sh api-service
```
First time, as for the deployer: the deploy stops at the certificate check; then on CT 106, once:
```sh
install -m 755 /opt/steven-universe/python/services/api-service/scripts/cert-hook.sh /usr/local/sbin/api-service-cert-hook
certbot certonly --dns-cloudflare --dns-cloudflare-credentials /root/.secrets/cloudflare.ini \
  -d api.lan.liustev6.ca --deploy-hook /usr/local/sbin/api-service-cert-hook
```
and deploy again.

## Tests
```sh
cd python/services/api-service && python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```
