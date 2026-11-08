# Spec: deployer (CI/CD from GitHub Actions to the home lab)

**Path**: `python/services/deployer/` (service), `deploy/` (per-service manifests and the
target-side script), `.github/workflows/deploy.yml`. **Status**: not built; services are deployed
by hand with the per-service scripts in `infra/deployment/` (see Current state). Related:
[secrets-manager.md](secrets-manager.md) (where deploy-time secrets come from),
[auth-service.md](auth-service.md) (internal tokens), [api-service.md](api-service.md) (the public
edge GitHub's call comes through).

## Scope
The deployer turns "a commit on `main` passed CI" into "that exact build is running on the right
host", without any deployment credential stored in GitHub and without letting a request run
anything except a known deployment.

- **GitHub Actions** (hosted runners) builds, tests, pushes an image to GHCR, then asks the
  deployer to deploy it.
- **deployer** (an LXC on pve01) checks who is asking, looks the service up, fetches deploy-time
  secrets, and runs the deployment on the target host.
- **deploy-target** (a small script on every target host) does the host-side work: pull the image,
  install secrets, restart, health check, roll back.

```
GitHub-hosted runner                 Cloudflare tunnel        LAN
┌──────────────────────────┐                            ┌─────────────┐
│ test → build → push GHCR │                            │ api-service │ proxies /deploy/* only
│ (GITHUB_TOKEN, built in) │──POST /deploy/v1/deployments──▶          │──┐
│ request GitHub OIDC token│   Bearer <GitHub OIDC>     └─────────────┘  │
└──────────────────────────┘                                             ▼
                                   ┌──────────────── deployer ──────────────────────────┐
                                   │ 1. verify GitHub OIDC (repo id, ref, env, workflow) │
                                   │ 2. request sha == token sha; image built for sha    │
                                   │ 3. manifest + compose.yaml at that sha              │
                                   │ 4. managed secrets ◀── secrets-manager (OpenBao)    │
                                   │ 5. ssh deploy@<host> (forced command) ──────────────┼─▶ deploy-target
                                   └─────────────────────────────────────────────────────┘    pull, secrets,
                                                                                               up, health,
                                                                                               rollback
```

## Locked decisions
| ID | Decision |
|---|---|
| D1 | **Build in GitHub, run on the LAN.** Hosted runners build and test; images go to GHCR as `ghcr.io/liustev6/steven-universe/<service>:<sha>` and are deployed **by digest**. Target hosts never build from a working copy. |
| D2 | **No deployment secrets in GitHub.** The workflow proves who it is with a **GitHub OIDC token** (`permissions: id-token: write`), a short-lived JWT signed by GitHub. Image pushes use the built-in `GITHUB_TOKEN`. There is nothing to store, rotate, or leak. |
| D3 | **In through the single edge.** GitHub calls `https://api.liustev6.ca/deploy/v1/deployments`; api-service forwards that path, unchanged, to the deployer (api-service E1). The deployer, auth-service and the secrets manager have no tunnel hostname. |
| D4 | **Requests name, they never command.** A request says *which service* at *which commit*. Everything else (host, image, how to run it, secrets, health check) comes from reviewed files in git at that commit plus the deployer's own host allowlist. No field is ever executed or used as a path. |
| D5 | **The token is bound to the request.** The `sha` in the body must equal the token's `sha` claim, so a valid token for one commit can't deploy another. |
| D6 | **Least privilege to hosts.** The deployer reaches each target over SSH with a key that can only run `deploy-target` (`authorized_keys`: `restrict,command="/usr/local/bin/deploy-target"`). `deploy-target` accepts three verbs: `deploy`, `rollback`, `check-secrets`. |
| D7 | **Secrets never in images, git, GitHub, or environment variables.** Each secret is a root-owned file in `/etc/<service>/credentials/<name>` (mode 600), mounted read-only into the container (Compose `secrets:` → `/run/secrets/<name>`). Onboarded secrets are placed once by hand and only *checked* by deploys; managed secrets are written by the deployer on every deploy (see Secrets). |
| D8 | **Health-checked with automatic rollback.** A deploy succeeds only when the service's health check passes; otherwise the previous digest is restored and the deploy reports `rolled_back`. |
| D9 | **One deploy per service at a time; idempotent.** A per-service lock; deploying the digest already running is a no-op success. |
| D10 | **Audited.** One log line per request (GitHub run id, actor, sha, digest, result) and a per-service history used for rollback. |
| D11 | **Docker Compose everywhere on LXCs, one service per LXC.** The image is the unit of deployment; the LXC is the security boundary (Docker group membership is root-equivalent inside it). Each service's `compose.yaml` lives next to its code and carries the hardening (`read_only`, non-root `user`, `cap_drop: [ALL]`, `no-new-privileges`, secrets mounted read-only). The only exception is the Pi (pi-server: hardware access, not an LXC), which runs under systemd. |
| D12 | **`infra/` is the source of truth for how every service is configured and deployed**; `python/services/<svc>/` owns only code, the `Dockerfile` and the *shape* of the container (`compose.yaml`). Policies, per-environment settings, deployment manifests and (later) hosts and DNS live in `infra/`; secret **values** never do (only their names). One place per fact. |

## GitHub side
`.github/workflows/deploy.yml`, triggered on push to `main` (path filters per service) and
`workflow_dispatch` (redeploy or roll back a chosen sha):

```yaml
permissions: {contents: read, packages: write, id-token: write}
jobs:
  build:    # per changed service (matrix): test, docker buildx (amd64; arm64 for the Pi), push :<sha>
  deploy:
    needs: build
    environment: production          # required reviewer: you
    concurrency: deploy-${{ matrix.service }}
    steps:
      - id: oidc
        run: |                       # GitHub's OIDC token for the deployer's audience
          curl -sS -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
            "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=steven-universe-deployer" | jq -r .value
      - run: curl --fail-with-body -N -X POST https://api.liustev6.ca/deploy/v1/deployments \
               -H "Authorization: Bearer <oidc token>" \
               -d '{"service": "file-management", "sha": "${{ github.sha }}", "image_digest": "sha256:…"}'
```

Repository settings this design relies on (the repo is public):
- **Branch protection on `main`:** pull requests required, no force pushes.
- **Environment `production`:** required reviewer (you), deployments only from `main`.
- **Fork pull requests** never get `id-token: write` or the `production` environment, so they
  can't obtain a deploy token.
- **GHCR packages public.** The code is public and images contain no secrets (D7), so hosts pull
  without credentials. If a package must be private, the host needs a read-only pull token
  (an onboarded secret).

## Deployer API
`POST /v1/deployments` (reached as `/deploy/v1/deployments` through api-service)
```json
{"service": "file-management", "sha": "827eb54…(40 hex)", "image_digest": "sha256:…"}
```
Response: `200`, streamed as NDJSON (one event per line) so the workflow log shows progress; the
last line is the result, and the workflow fails unless it is `succeeded` or `unchanged`.
```json
{"event": "step", "step": "pull", "message": "…"}
{"event": "result", "status": "succeeded|unchanged|failed|rolled_back", "digest": "sha256:…"}
```
Errors before anything runs: `401` (token invalid), `403` (claims don't match), `404` (unknown
service), `409` (a deploy for this service is running), `422` (bad body, sha mismatch, image not
built for that sha).

`GET /health`: liveness only.

### Token checks (GitHub OIDC)
Verified against GitHub's JWKS (`https://token.actions.githubusercontent.com/.well-known/jwks`),
RS256 only, 60 s leeway:

| Claim | Must equal | Why |
|---|---|---|
| `iss` | `https://token.actions.githubusercontent.com` | GitHub signed it |
| `aud` | `steven-universe-deployer` | minted for us, not for another relying party |
| `repository_id`, `repository_owner_id` | the numeric ids of `liustev6/steven-universe` | names can be renamed or re-registered; ids can't (this repo was renamed from `Yuanxyyds/…`) |
| `ref` | `refs/heads/main` | only `main` deploys |
| `environment` | `production` | passed the required review |
| `job_workflow_ref` | `liustev6/steven-universe/.github/workflows/deploy.yml@refs/heads/main` | only this workflow, as on `main` |
| `runner_environment` | `github-hosted` | not a self-hosted runner |
| `sha` | the request's `sha` | D5 |
| `jti` | not seen before (cache until `exp`) | no replay |

The image is accepted only if its OCI label `org.opencontainers.image.revision` equals `sha`.
Later: verify GitHub's build provenance attestation (`actions/attest-build-provenance`, SLSA)
instead of trusting the label.

## Configuration source of truth: `infra/` (D12)
Application code and environment configuration are kept apart, as in GitOps environment
repositories or Helm values next to charts. The rule for each value: *if the service moved to
another environment, would this change?* Yes → `infra/`. No → the service folder.

| Owned by the service (`python/services/<svc>/`) | Owned by `infra/` |
|---|---|
| code, tests, `Dockerfile` | **policies**: auth-service's `policy.yaml`, OpenBao policies (`infra/secrets-manager/tofu/`) |
| `compose.yaml` as the container's *shape*: image name, hardening, mounts, health check | **per-environment settings**: issuer URL, storage endpoint, buckets, limits, log level (`config.env`) |
| defaults in the service's settings class | **deployment manifests**: host, health URL, secret names (`deploy.yaml`) |
| | later, as OpenTofu: Proxmox containers, Cloudflare DNS (`infra/tofu/`) |
| | secret **values**: never; they live in the secrets manager (only their names are listed here) |

```
infra/
  README.md                          the map: what lives where, how a change flows
  secrets-manager/                   OpenBao: compose, openbao.hcl, tofu/ (policies, roles, auth)
  services/<service>/
    deploy.yaml                      what to deploy where + which secrets (read at the deployed sha)
    config.env                       non-secret settings, written to the host on every deploy
    policy.yaml                      auth-service only: the permissions (moved from python/services/auth-service/)
  deployment/                        manual deploy scripts now; deploy-target later
  tofu/                              later: Proxmox containers + Cloudflare DNS
python/services/<service>/
  Dockerfile, compose.yaml           how it builds and runs; compose reads config.env and /run/secrets
```

```yaml
# infra/services/file-management/deploy.yaml (the real file; abridged)
service: file-management
host: files-host                       # SSH alias; the deployer maps it through its host allowlist
compose: python/services/file-management-service/compose.yaml
config: infra/services/file-management/config.env     # -> /etc/file-management/config.env
health: https://files.lan.liustev6.ca/health
smoke_test: python/services/file-management-service/scripts/smoke_test.py
secrets:                               # values in OpenBao at kv/<from>, written to <to> on every deploy
  - from: services/file-management/rustfs_access_key
    to: /etc/file-management/credentials/rustfs_access_key
    owner: "10001:10001"
  - from: shared/cloudflare_dns_token
    to: /root/.secrets/cloudflare.ini
    owner: "0:0"
    format: "dns_cloudflare_api_token = {value}"
```
The same `secrets` list feeds OpenTofu, which generates each service's read-only OpenBao policy and
the deployer's (secrets-manager S15).
```sh
# infra/services/file-management/config.env (non-secret; reviewed like code)
SERVICE_AUTH_ISSUER=https://auth.lan.liustev6.ca
FILES_STORAGE_ENDPOINT=http://192.168.50.123:9000
FILES_BUCKETS=["public", "private"]
FILES_MAX_UPLOAD_BYTES=1073741824
```

**How a deploy uses it** (the manual scripts first, the deployer later):
```
infra/services/<svc>/config.env           ──▶ /etc/<svc>/config.env              (non-secret, mode 644)
secrets manager kv/services/<svc>/<name>  ──▶ /etc/<svc>/credentials/<name>      (owner = container user, 600)
infra/services/auth-service/policy.yaml            ──▶ /etc/auth-service/policy.yaml      (auth-service deploys only;
                                                                                 validated, diff shown first)
compose.yaml:  env_file: /etc/<svc>/config.env
               volumes:  /etc/<svc>/credentials:/run/secrets:ro
```
A change can touch two folders (a new bucket: file-management's `config.env` **and**
`infra/services/auth-service/policy.yaml`); that is intended: permission and environment changes are
separate, small, reviewable diffs, and `infra/` answers "how is everything configured?" alone.

The deployer's own config (`/etc/deployer/hosts.yaml`, not in the deployed commit) maps host
names to SSH addresses and host keys. A manifest can only choose among those hosts, so a change
merged to `main` can't point a deploy at an arbitrary machine.

## deploy-target (on each host)
Runs as user `deploy` (in the `docker` group, which is root-equivalent on that host; the host is
the blast radius). Reads the verb and arguments from `SSH_ORIGINAL_COMMAND`, validates them
against `^[a-z][a-z0-9-]{1,62}$` and `^sha256:[0-9a-f]{64}$`, and reads secret values from stdin.

`deploy <service> <digest>`:
1. `check-secrets`: `config.env` and managed values from stdin are written first (atomic; secrets
   mode 600, owned by the container user); then every
   onboarded and managed name must exist and be non-empty in `/etc/<service>/credentials/`.
   A missing onboarded secret fails the deploy with the command that creates it.
2. `docker pull <image>@<digest>`; keep the currently running digest as `previous`.
3. `docker compose up -d` with the new digest (compose file sent by the deployer).
4. Poll the health URL until healthy or timeout.
5. Healthy: record the digest. Not healthy: `compose up` with `previous`, report `rolled_back`.

`rollback <service>`: redeploy the previous recorded digest. `check-secrets <service>`: step 1
only (used for onboarding).

## Secrets
| Kind | Examples | Source | Deploy does |
|---|---|---|---|
| **Onboarded** | auth-service client secret; keys generated on the host | `scripts/add-client.sh`, `scripts/secret-set.sh`, or generated on the host | checks they exist; never reads or moves them |
| **Managed** | RustFS keys, third-party API keys, shared configuration | the secrets manager | fetches and writes them on every deploy |
| **Build** | a private package token, if ever needed | GitHub secrets | n/a; avoid (D7) |

Onboarding a new service: PR with code and `compose.yaml`, plus `infra/services/auth-service/policy.yaml` (its grant) and `infra/services/<svc>/` (`deploy.yaml`, `config.env`); the
first deploy fails `check-secrets` and lists the commands; run them once; redeploy.

Why the deployer may read managed secrets for every service: whoever controls deploys already
controls the code that reads those secrets, so this adds little power. The deployer is a
high-value host either way; it gets the hardening in Security notes.

## Security notes
- **What a stolen GitHub OIDC token can do:** deploy the one commit it was minted for, once,
  within ~5 minutes. Nothing else.
- **What a compromised deployer can do:** deploy any image and read managed secrets. Mitigations:
  LAN only, no tunnel hostname, minimal packages, SSH keys usable only for `deploy-target`, its
  secrets-manager access read-only and audited.
- **What a compromised target can do:** only that host; it has no key to other hosts.
- **E4 (no token passthrough) in api-service** concerns tokens *addressed to api-service*. The
  GitHub token is addressed to the deployer; api-service forwards that one path as a plain
  reverse proxy, never consumes the token, and can pre-check `iss` and rate-limit.
- **Break-glass:** you can still run `deploy-target` by hand over SSH; it is the same allowlisted
  code path.

## Current state
**auth-service and file-management** deploy with the generic, manifest-driven pair (phase 0b, done
2026-11-08): `infra/deployment/deploy.sh <service> [ref]` on the Mac reads the pushed commit's
`infra/services/<service>/deploy.yaml`, fetches the declared secrets from OpenBao with your login,
and in one SSH session runs `infra/deployment/deploy-target.sh` on the host (the `deploy-target` of
this spec, minus the forced-command key). What the deployer adds: being triggered by GitHub, its
own auth-service identity for OpenBao, the host allowlist and forced-command keys, and GHCR images.

**The other services** (gpu-server, stevenai-service, web-gateway, and pi-server on the Pi) still
use their old per-service scripts in `infra/deployment/`, run from the laptop: rsync the working
copy, `docker build` on the host, write a `.env` from the laptop's `.env`, `docker run --env-file`,
`sleep 10`, curl `/health`. The problems below are theirs (P1–P6 are solved for the two migrated
services).

| ID | Problem |
|---|---|
| P1 | Secrets sit in a plain-text `.env` on the laptop and are copied into a plain-text `.env` on the host, then passed as environment variables (visible in `docker inspect`, inherited by child processes). |
| P2 | Images are built on the host from an rsynced working copy: uncommitted changes can ship, and nothing records which commit is running. |
| P3 | `:latest` only; no rollback. |
| P4 | The old container is removed before the new one is proven healthy (downtime, no recovery). |
| P5 | Deploys need the laptop and leave no record. |
| P6 | Four copies of the same script drift apart. |
| P7 | Shared libraries are rsynced separately (`shared-schemas`). |

## Phases
| Phase | What ships |
|---|---|
| **0** (manual, now) | Keep manual deploys, but adopt the conventions: secrets as files in `/etc/<svc>/credentials/` mounted into containers (not `.env`), and a hardened `compose.yaml` next to each service (auth-service is the reference). |
| **0b** | **`infra/` as source of truth (D12)**, still manual (**done 2026-11-08** for auth-service and file-management: manifests, `config.env`, policy moved, OpenBao policies generated from the manifests, generic `deploy.sh` + `deploy-target.sh`, secrets delivered from OpenBao; rotation test pending): (1) `infra/README.md`; move `policy.yaml` to `infra/services/auth-service/` (auth-service's deploy script, the real-policy test and docs follow), deploy, expect "policy unchanged"; (2) `infra/services/file-management/config.env` + `env_file:` in its compose, written by its deploy script, deploy, smoke test must pass; (3) `deploy.yaml` for auth-service and file-management describing what the scripts already check (host, health, secret names, smoke test); (4) point the secrets-manager spec at `infra/services/`. |
| **1** | GitHub builds and pushes images to GHCR per commit; hosts pull by digest (manual trigger). Fixes P2, P3, P7. |
| **2** | deployer + `deploy-target`, triggered by the workflow through api-service with GitHub OIDC. Onboarded secrets only (checked, not managed). Fixes P4, P5, P6. |
| **3** | secrets-manager; the deployer injects managed secrets. Fixes P1 for everything not onboarded. |
| **4** (optional) | GitHub token exchanged at auth-service for an internal `aud=deployer` token (federation), so the deployer trusts one issuer; build provenance verification. |

## Open questions
- The Pi (pi-server, systemd per D11): a `kind: systemd` target in `deploy-target` (release
  folders + a `current` symlink + `systemctl restart`), or deployed by hand?
- Pull (the deployer polls GHCR/GitHub for approved builds; nothing inbound) as a fallback if the
  edge is down?
- Deploying the deployer itself: by hand only, or by a second deployer path?
- Deploys of auth-service: allowed through the pipeline, or by hand only (it is the root of trust)?

## Non-goals
- Kubernetes, Argo CD/Flux, or a general-purpose CI system on the LAN.
- Self-hosted GitHub runners (unsafe on a public repo).
- Building images on target hosts.
- Running arbitrary commands from a deploy request.
