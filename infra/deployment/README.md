# infra/deployment

Manual deploy scripts, one per service, run from the Mac. Each script works from any directory
and reads that service's `.env` (copy `python/services/<service>/.env.example`).

| Script | Deploys | To |
|---|---|---|
| `auth-service.sh [ref]` | `python/services/auth-service` at a **pushed** commit (default `main`) | CT 102 `auth-host` (Docker Compose, built on the host from a clean checkout) |
| `file-management-service.sh [ref]` | `python/services/file-management-service` at a **pushed** commit (default `main`) | CT 103 `files-host` (Docker Compose, built on the host; health over TLS; smoke test) |
| `gpu-server.sh` | `python/services/gpu-server` (+ `python/workers/gpu-server`) | the GPU VM (Docker, built on the VM) |
| `stevenai-service.sh` | `python/services/stevenai-service` | its LXC (Docker) |
| `web-gateway.sh` | `python/services/web-gateway` | its LXC (Docker) |
| `pi-server.sh` | `python/services/pi-server` | the Pi (systemd; `PI_HOST`, default `panel`) |

```sh
infra/deployment/auth-service.sh
```

`auth-service.sh` differs from the others: it never copies your working copy. The host checks out
the pushed commit from GitHub and builds that, then installs the policy from the same commit and
checks `/health` over TLS (first-time setup: `python/services/auth-service/README.md`).

The Flutter panel app deploys with `dart/apps/hey-pi/scripts/run_deploy.sh`.

These scripts are phase 0 of [docs/specs/deployer.md](../../docs/specs/deployer.md), which lists
their problems (secrets in `.env`, images built on the host, `:latest` only, no rollback) and
the pipeline that replaces them.
