# infra/deployment

How services are deployed, from the Mac. Two generations live here:

## Generic, manifest-driven: `deploy.sh` (auth-service, file-management)
```sh
export BAO_ADDR=https://bao.lan.liustev6.ca:8200
export BAO_TOKEN="$(bao login -method=userpass -token-only username=steven)"   # valid 1 hour

infra/deployment/deploy.sh <service>          # deploy main (latest pushed commit)
infra/deployment/deploy.sh <service> <ref>    # a pushed branch, tag or sha (also how to roll back)
```

| File | Runs on | Does |
|---|---|---|
| `deploy.sh` | the Mac | resolves a **pushed** commit, reads that commit's `infra/services/<service>/deploy.yaml`, fetches the secrets it declares from OpenBao (in memory only), opens one SSH session that checks out the commit on the host and runs `deploy-target.sh` with the secrets on stdin |
| `deploy-target.sh` | the host | writes `config.env` and the secrets (atomically, mode 600, reports changed/unchanged), checks host-only paths and the certificate, installs the certbot hook, builds the image, runs the service's hook if any, starts, waits for health, checks health by name over TLS, runs the smoke test, prints a summary with the rollback command |
| `lib.sh` | the host | shared status output (`Step N/M`, ✓/✗, which step failed) |

Everything about a service lives in `infra/services/<service>/`:

| File | What |
|---|---|
| `deploy.yaml` | host, compose file, health URL, smoke test, certificate, and the **secrets** it needs (`from` a path in OpenBao, `to` a host path, `owner`, `format`); schema: `infra/services/deploy.schema.json` |
| `config.env` | non-secret settings, installed as `/etc/<service>/config.env` (compose loads it) |
| `hooks/pre-start.sh` | optional service-specific steps (auth-service: admin CLI, policy, first signing key) |

The same `deploy.yaml` feeds OpenTofu (`infra/secrets-manager/tofu/services.tf`), which generates
each service's read-only OpenBao policy and the deployer's. A new service needs a manifest and
`config.env` (and a hook only if it has special steps), not a new script.

`deploy-target.sh` is the deployer's host-side half (docs/specs/deployer.md); the future deployer
runs the same script, fetching the secrets with its own identity instead of your login.

**Needs:** on the Mac `git`, `yq`, `bao`, SSH aliases for the hosts (`auth-host`, `files-host`);
OpenBao unsealed (`docs/specs/boot.md`).

## Older per-service scripts (not migrated yet)
These copy your working copy to the host and read the service's `.env` (copy
`python/services/<service>/.env.example`):

| Script | Deploys | To |
|---|---|---|
| `gpu-server.sh` | `python/services/gpu-server` (+ `python/workers/gpu-server`) | the GPU VM (Docker, built on the VM) |
| `stevenai-service.sh` | `python/services/stevenai-service` | its LXC (Docker) |
| `web-gateway.sh` | `python/services/web-gateway` | its LXC (Docker) |
| `pi-server.sh` | `python/services/pi-server` | the Pi (systemd; `PI_HOST`, default `panel`) |

The Flutter panel app deploys with `dart/apps/hey-pi/scripts/run_deploy.sh`. The problems of the
older scripts and the path to retire them are in [docs/specs/deployer.md](../../docs/specs/deployer.md).
