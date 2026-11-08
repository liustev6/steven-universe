# deployer

Turns an approved GitHub Actions run into a deploy on the LAN: checks GitHub's OIDC token, reads
the service's manifest at that commit, fetches its secrets from OpenBao with its own identity, and
runs the host's `deploy-target` over a restricted SSH key, rolling back if the deploy fails.
Spec: [docs/specs/deployer.md](../../../docs/specs/deployer.md).

```
GitHub Actions ──▶ api-service /deploy/* ──▶ deployer (CT 105) ──ssh, restricted key──▶ deploy-target on the host
                                               ├─ auth-service token ─▶ OpenBao login (role deployer)
                                               └─ git: manifest at the commit
```

| Module | What |
|---|---|
| `app/github_oidc.py` | GitHub's token: signature (GitHub's JWKS, RS256), `aud`, repository/owner ids, `main`, `production`, the workflow, GitHub-hosted, one use |
| `app/repo.py` | a bare, partial clone of the public repository; files at a commit; the image sha (D1a) |
| `app/deploys.py` | checks that refuse before anything runs, the deploy as a background task, rollback, audit log |
| `app/openbao.py` | the manifest's secrets: auth-service token → OpenBao login → read → revoke |
| `app/ssh.py` | `deploy <service> <sha> [<digest>]` over SSH with the pinned host key, payload on stdin |
| `app/main.py` | `POST /v1/deployments` (NDJSON stream), `GET /health` |
| `app/cli.py` | LAN break-glass: `python -m app.cli deploy <service> [<ref>]` (D18) |

## Configuration (all in `infra/services/deployer/`, D12)
| File | Installed as | What |
|---|---|---|
| `deploy.yaml` | | where and how it is deployed: **by hand only** (`pipeline: false`, D13) |
| `config.env` | `/etc/deployer/config.env` | which GitHub tokens may deploy (repository and owner ids, workflow, environment); OpenBao address; its auth-service client |
| `hosts.yaml` | `/etc/deployer/hosts.yaml` | the host allowlist with pinned host keys; installed only by this hand deploy, diff shown |

Host-only on CT 105 (never in git or OpenBao), owned by uid 10001:
`/etc/deployer/credentials/client_secret` (auth-service client `deployer`, from `add-client.sh`) and
`/etc/deployer/credentials/ssh_key` (its public key is restricted in each target host's
`authorized_keys`).

## Deploy (by hand)
```sh
infra/deployment/deploy.sh deployer
```
The first time, the TLS certificate is missing: the deploy checks out the commit, writes the
Cloudflare credentials (`/root/.secrets/cloudflare.ini`) and stops at the certificate check (before
it would install the certbot hook). Then on CT 105, once:
```sh
install -m 755 /opt/steven-universe/python/services/deployer/scripts/cert-hook.sh /usr/local/sbin/deployer-cert-hook
certbot certonly --dns-cloudflare --dns-cloudflare-credentials /root/.secrets/cloudflare.ini \
  -d deploy.lan.liustev6.ca --deploy-hook /usr/local/sbin/deployer-cert-hook
```
and deploy again (every later deploy reinstalls the hook from its commit).

## Deploy something from the LAN (break-glass, D18)
```sh
ssh deployer-host docker exec deployer-deployer-1 python -m app.cli deploy file-management       # main
ssh deployer-host docker exec deployer-deployer-1 python -m app.cli deploy file-management <sha> # any pushed commit
```
Same checks, secrets, rollback and audit as an API deploy, without a GitHub token. `pipeline:
false` services are allowed, but only hosts in the allowlist.

## Logs
`docker compose logs -f deployer` (on CT 105); every deploy is also a line in
`/var/lib/deployer/audit.log`; every request on a target host is in its `journalctl -t deploy-target`.

## Tests
```sh
cd python/services/deployer && python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```
