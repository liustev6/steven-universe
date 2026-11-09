# Spec: deployer (CI/CD from GitHub Actions to the home lab)

**Path**: `python/services/deployer/` (service, not built yet), `infra/services/<service>/`
(manifests), `infra/deployment/` (`deploy-target.sh`, the host side, already in use),
`.github/workflows/`. **Status**: decisions locked 2026-11-08; phase 0b done: deploys are manifest-driven and get their secrets
from OpenBao, but are started by hand from the Mac (`infra/deployment/deploy.sh`). The deployer
replaces the Mac, not the host side. Related: [secrets-manager.md](secrets-manager.md),
[auth-service.md](auth-service.md), [api-service.md](api-service.md) (the public edge GitHub's call
comes through), [boot.md](boot.md).

## Scope
The deployer turns "a commit on `main` passed CI" into "that exact build is running on the right
host". It does this without any deployment credential stored in GitHub, and without letting a
request run anything except a known deployment.

- **GitHub Actions** (hosted runners) tests and builds the image, pushes it to GHCR, then asks the
  deployer to deploy it.
- **deployer** (its own LXC on pve01):
  - checks who is asking;
  - reads the service's manifest at that commit;
  - fetches the declared secrets from OpenBao with its own identity;
  - runs `deploy-target` on the host;
  - rolls back if that fails.
- **deploy-target** (on every target host) does the host-side work:
  - checks out the commit;
  - writes `config.env` and the secrets;
  - starts the new image;
  - runs the health check and the smoke test.

```
GitHub-hosted runner                                        LAN
┌──────────────────────────┐   Cloudflare tunnel      ┌─────────────┐
│ test → build → push GHCR │──POST /deploy/v1/───────▶│ api-service │ forwards /deploy/* only
│ GitHub OIDC token        │   deployments            └──────┬──────┘
└──────────────────────────┘   Bearer <GitHub OIDC>          ▼
          ┌──────────────────────── deployer ───────────────────────────────┐
          │ 1. verify GitHub OIDC (repo id, ref, environment, workflow, sha) │
          │ 2. infra/services/<svc>/deploy.yaml at that sha; host allowlist  │
          │ 3. auth-service token (aud=secrets-manager) ─▶ OpenBao JWT login │
          │ 4. read the manifest's secrets (read-only, generated policy)     │
          │ 5. ssh root@<host> (forced command) ─────────────────────────────┼─▶ deploy-target
          │ 6. failed? deploy the previous sha the same way                  │   checkout, secrets,
          └──────────────────────────────────────────────────────────────────┘   pull, up, health,
                                                                                  smoke test
```

## Locked decisions
| ID | Decision |
|---|---|
| D1 | **Build in GitHub, run on the LAN.** Hosted runners build and test. Images go to GHCR as `ghcr.io/liustev6/steven-universe/<service>:<image sha>` and are deployed **by digest**. Until phase 1, hosts build the image themselves, but only from a clean checkout of a *pushed* commit, never from a working copy. |
| D1a | **An image is named by the commit that last changed its inputs.** The inputs are the manifest's `code` folder plus `python/libs/`. When deploying commit X, the image sha is `git log -1 --format=%H X -- <code> python/libs`. A commit that only changes `infra/services/<svc>/` (config, secrets list) redeploys the existing image with the new config, and nothing is rebuilt. The image's `revision` label is the image sha. The deployed sha is recorded in the host's deploy log (D10). |
| D2 | **No deployment secrets in GitHub.** The workflow proves who it is with a **GitHub OIDC token** (`permissions: id-token: write`), a short-lived JWT signed by GitHub. Image pushes use the built-in `GITHUB_TOKEN`. There is nothing to store, rotate, or leak. |
| D3 | **In through the single edge.** GitHub calls `https://api.liustev6.ca/deploy/v1/deployments`; api-service forwards that path, unchanged, to the deployer (api-service E1). The deployer, auth-service and the secrets manager have no tunnel hostname. |
| D4 | **Requests name, they never command.** A request says *which service* at *which commit*. Everything else (host, compose file, secrets, health check, smoke test) comes from that commit's `infra/services/<service>/deploy.yaml`, plus the deployer's own host allowlist. No field is ever executed or used as a path. |
| D5 | **The token is bound to the request.** The `sha` in the body must equal the token's `sha` claim, so a valid token for one commit can't deploy another. |
| D6 | **Least privilege to hosts.** The deployer reaches each host as `root` with a key that can only run the installed wrapper. That key's `authorized_keys` line is `restrict,command="/usr/local/sbin/deploy-target" ssh-ed25519 …`. The restriction is per key: sshd stays at `PermitRootLogin prohibit-password`, so your own key keeps a normal shell for `deploy.sh` and break-glass. It is root because the deploy writes `/etc/<svc>`, chowns secrets and installs certbot hooks. A `docker` group member would be root-equivalent anyway, so the LXC is the boundary (D11). The wrapper accepts one form, `deploy <service> <40-hex sha> [<digest>]`, each part checked against a regex. It also refuses any service not listed in the host's `/etc/deploy-target/services`, so a host only ever runs its own service. |
| D7 | **Secrets never in images, git, GitHub, or environment variables.** Each secret is a file (mode 600, owned as the manifest says), mounted read-only into the container or read by a host tool such as certbot. **Managed** secrets come from OpenBao and are written on every deploy. **Host-only** files (e.g. auth-service's signing keys) are created on the host and only checked (see Secrets). |
| D8 | **Health-checked with automatic rollback.** A deploy succeeds only when the container is healthy, the health URL answers by name over TLS with the right revision, and the smoke test passes. Otherwise the deployer deploys the previously running sha the same way and reports `rolled_back`. Manual deploys print the rollback command instead. |
| D9 | **One deploy per service at a time; idempotent.** There is a per-service lock: `flock` in the wrapper, and `409` at the deployer. Redeploying the running sha is safe: it rewrites nothing that is unchanged and reports "same commit". |
| D10 | **Audited.** The deployer logs one line per request (GitHub run id, actor, sha, digest, result). Each host appends to `/var/lib/deploy-target/<service>.log` (time, sha, result), which is also where the previous sha for a rollback comes from. OpenBao's audit log records every secret read. |
| D11 | **Docker Compose everywhere on LXCs, one service per LXC.** The image is the unit of deployment; the LXC is the security boundary (Docker group membership is root-equivalent inside it). Each service's `compose.yaml` lives next to its code and carries the hardening (`read_only`, non-root `user`, `cap_drop: [ALL]`, `no-new-privileges`, secrets mounted read-only). The only exception is the Pi (pi-server: hardware access, not an LXC), which runs under systemd. |
| D12 | **`infra/` is the source of truth for how every service is configured and deployed**; `python/services/<svc>/` owns only code, the `Dockerfile` and the *shape* of the container (`compose.yaml`). Policies, per-environment settings, deployment manifests and (later) hosts and DNS live in `infra/`; secret **values** never do (only their names). One place per fact. |
| D13 | **The roots of trust deploy by hand only:** auth-service, the secrets manager and the deployer itself. The deployer logs in to OpenBao through auth-service, so neither may depend on it. The deployer's host allowlist does not contain their hosts; `deploy.sh` from the Mac stays their path, and the break-glass path for everything else. |
| D14 | **One host-side script for every path.** The Mac (`deploy.sh`) and the deployer both run the same `deploy-target.sh`, with the same stdin payload. The deployer replaces the caller, never the host logic, so a manual deploy tests exactly what the pipeline runs. |
| D15 | **Push through the edge, not pull.** GitHub calls the deployer through api-service (D3). The deployer has no public hostname: it is LAN-only and reachable from outside only through api-service's `/deploy/*` route, where GitHub's OIDC token is the gate. Polling GitHub (pull) is not planned. |
| D16 | **The manifest says who may deploy a service: `pipeline: true` or `false`** (default false). The workflow deploys only `pipeline: true` services. The deployer refuses the others with `403` and has no host entry for them either. D13 services stay `false`. |
| D17 | **The gate is approval per deploy, not pull requests** (one maintainer who pushes straight to `main`). The `production` environment requires your approval on every deploy and accepts only `main`. A ruleset on `main` blocks force pushes and deletion. Pull requests stay optional. |
| D18 | **LAN break-glass on the deployer:** `docker exec deployer python -m app.cli deploy <service> <sha>` runs a deploy without a GitHub token. Only someone already root on the deployer LXC can do this, and that person already holds all its power. It is used to test the deployer before the edge exists, and when GitHub is down. |

## GitHub side
One workflow, `.github/workflows/build.yml` (built 2026-11-08):

```
push to main ─▶ plan ─▶ libs (service-auth tests) ─▶ build (per missing image) ─▶ deploy (per service)
                 │                                                                 environment: production
                 ├─ missing images (D1a)                                           (waits for your approval)
                 └─ services to deploy: pipeline: true AND (its image inputs
                    or infra/services/<svc>/ changed in this push)
```
Each `deploy` job, one per service:
1. reads the image's digest from GHCR;
2. requests GitHub's OIDC token for `aud=steven-universe-deployer`;
3. POSTs `{service, sha: github.sha, image_digest}` to `https://api.liustev6.ca/deploy/v1/deployments`;
4. folds the deployer's NDJSON stream into one log group per step;
5. fails unless the result is `succeeded`. A `rolled_back` deploy is a failed job, with a warning.

`concurrency: deploy-<service>` queues a second deploy of the same service.

`workflow_dispatch` inputs:
- **`ref`:** build an older commit's missing images, for example before deploying it by hand.
- **`redeploy=<service>`:** redeploy it at `main`'s latest commit.

**Rolling back through the pipeline is `git revert` + push**, not "deploy an older sha". GitHub's
token always carries the sha of the commit the run is on, and D5 binds the request to it. For
emergencies, deploy any pushed commit by hand: `infra/deployment/deploy.sh <svc> <sha>` or the
deployer's CLI (D18).

Repository settings this design relies on (the repo is public; set by hand before phase 2):
- **Ruleset on `main`:** block force pushes and deletion. Pull requests aren't required (D17).
- **Environment `production`:** required reviewer (you), with "prevent self-review" off, since
  you approve your own pushes. Deployments only from `main`.
- **Fork pull requests** never get `id-token: write` or the `production` environment, so they
  can't obtain a deploy token.
- **GHCR packages public.** The code is public and images contain no secrets (D7), so hosts pull
  without credentials. A private package would need a read-only pull token on the host, as a
  managed secret.

## Deployer API
`POST /v1/deployments` (reached as `/deploy/v1/deployments` through api-service)
```json
{"service": "file-management", "sha": "827eb54…(40 hex)", "image_digest": "sha256:…"}
```
The response is `200`, streamed as NDJSON (one event per line), so the workflow log shows
`deploy-target`'s progress. The last line is the result. The workflow fails unless it is
`succeeded`.
```json
{"event": "step", "step": "Smoke test", "message": "…"}
{"event": "result", "status": "succeeded|failed|rolled_back", "sha": "…", "previous": "…"}
```
Errors before anything runs:

| Status | Meaning |
|---|---|
| `401` | token invalid |
| `403` | claims don't match, or the service is hand-deployed only (D13) |
| `404` | no manifest at that sha |
| `409` | a deploy for this service is running |
| `422` | bad body, sha mismatch, or the image wasn't built for that sha |

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
| `job_workflow_ref` | `liustev6/steven-universe/.github/workflows/build.yml@refs/heads/main` | only this workflow, as on `main` |
| `runner_environment` | `github-hosted` | not a self-hosted runner |
| `sha` | the request's `sha` | D5 |
| `jti` | not seen before (cache until `exp`) | no replay |

The image is accepted only if its OCI label `org.opencontainers.image.revision` equals `sha`.
Later: verify GitHub's build provenance attestation (`actions/attest-build-provenance`, SLSA)
instead of trusting the label.

### The deployer's identity (OpenBao access)
The deployer has no static OpenBao token. Per deploy:
1. It requests a client credentials token from auth-service as client `deployer`, with
   `aud=secrets-manager`. Its client secret is host-only on the deployer LXC, created with
   `add-client.sh`.
2. It logs in to OpenBao at `auth/auth-service/login`, role `deployer`. That gives a 10-minute,
   read-only token with the **generated** `deployer` policy: exactly the secrets some manifest
   declares (secrets-manager S15).
3. It reads the manifest's `secrets[].from`, then forgets the token.

**Already in place:**
- the OpenBao JWT auth method trusting auth-service's JWKS;
- role `deployer` (`bound_audiences secrets-manager`, `bound_subject service:deployer`);
- the generated policy (`infra/secrets-manager/tofu/`).

**Still to add (phase 2):**
- In `infra/services/auth-service/policy.yaml`:
  - an audience `secrets-manager` (scope `secrets:read`);
  - a client `deployer` granted it.
- The client's secret, on the deployer LXC.

## Configuration source of truth: `infra/` (D12)
Application code and environment configuration are kept apart, as in GitOps environment
repositories or Helm values next to charts. The rule for each value: *if the service moved to
another environment, would this change?* Yes → `infra/`. No → the service folder.

| Owned by the service (`python/services/<svc>/`) | Owned by `infra/` |
|---|---|
| code, tests, `Dockerfile`, smoke test, certbot hook script | **policies**: auth-service's `policy.yaml`, OpenBao policies (`infra/secrets-manager/tofu/`, generated from the manifests) |
| `compose.yaml` as the container's *shape*: image name, hardening, mounts, health check | **per-environment settings**: issuer URL, storage endpoint, buckets, limits, log level (`config.env`) |
| defaults in the service's settings class | **deployment manifests**: host, health URL, secret names, smoke test, hooks (`deploy.yaml`) |
| | later, as OpenTofu: Proxmox containers, Cloudflare DNS (`infra/tofu/`) |
| | secret **values**: never; they live in OpenBao (only their names are listed here) |

```
infra/
  services/
    deploy.schema.json               the manifest format (additionalProperties: false)
    <service>/
      deploy.yaml                    what to deploy where + which secrets (read at the deployed sha)
      config.env                     non-secret settings, written to the host on every deploy
      hooks/pre-start.sh             optional service-specific steps (auth-service: admin CLI, policy, key)
      policy.yaml                    auth-service only: the permissions
  secrets-manager/                   OpenBao: compose, openbao.hcl, tofu/ (auth, roles, generated policies)
  deployment/
    deploy.sh                        Mac: resolve a pushed sha, fetch secrets with your login, ssh
    deploy-target.sh, lib.sh         host: the deploy itself (also run by the deployer, D14)
    gpu-server.sh, …                 older per-service scripts, until phase 3
  tofu/                              later: Proxmox containers + Cloudflare DNS
```

```yaml
# infra/services/file-management/deploy.yaml (the real file; abridged)
service: file-management
host: files-host                       # an SSH alias; the deployer maps it through its host allowlist
code: python/services/file-management-service       # also the build workflow's path filter
compose: python/services/file-management-service/compose.yaml
config: infra/services/file-management/config.env   # -> /etc/file-management/config.env
health: https://files.lan.liustev6.ca/health
smoke_test:
  script: python/services/file-management-service/scripts/smoke_test.py
  secret: /root/.secrets/smoke-test-client
certificate:
  name: files.lan.liustev6.ca
  hook: python/services/file-management-service/scripts/cert-hook.sh
secrets:                               # values in OpenBao at kv/<from>, written to <to> on every deploy
  - from: services/file-management/rustfs_access_key
    to: /etc/file-management/credentials/rustfs_access_key
    owner: "10001:10001"               # the container user
  - from: shared/cloudflare_dns_token
    to: /root/.secrets/cloudflare.ini
    owner: "0:0"
    format: "dns_cloudflare_api_token = {value}\n"   # wrapped into the file a tool expects
# host_only: [paths]                   # created on the host, never delivered; the deploy checks they exist
```
The same `secrets` list feeds OpenTofu. It generates each service's read-only OpenBao policy and
the deployer's (secrets-manager S15). A change can touch several places, and that is intended:

| Change | Places it touches |
|---|---|
| a new bucket | file-management's `config.env`, and `infra/services/auth-service/policy.yaml` |
| a new secret | the manifest (reviewed), `tofu apply` (policies), and `bao kv put` (the value, by hand) |

The host allowlist is `infra/services/deployer/hosts.yaml`: host aliases with their address and
pinned SSH host key (public). It is in git like every other setting (D12), but it reaches the
deployer only through a **hand** deploy of the deployer (D13), which shows the diff and refuses
`auth-host`, `bao-host` and `deployer-host`. The deployer reads the installed copy
(`/etc/deployer/hosts.yaml`), never the file at a deployed commit. So a change merged to `main`
can't point a deploy at an arbitrary machine or widen the pipeline's reach until you redeploy the
deployer yourself.

## deploy-target (on each host)
**Today** `deploy.sh` (Mac) opens one SSH session. It:
1. clones `/opt/steven-universe` if it's missing;
2. fetches the repository and checks out the sha (detached);
3. runs that commit's `infra/deployment/deploy-target.sh <service> <sha>`.

**stdin** carries the secrets, one per line:
```
<destination path> TAB <owner uid:gid> TAB <base64 of the formatted value>
```
The script reads stdin into memory first, then closes it.

**Steps** (from the manifest; status output from `lib.sh`, and the failed step is named):
1. **Commit:** the running revision (from the container's label) → the new one.
2. **Config and secrets:**
   - `config.env` → `/etc/<svc>/`;
   - each secret written atomically, mode 600, with its owner, reported as written or unchanged;
   - every `host_only` path must exist;
   - the certificate must be present (expiry warned at 21 days).
3. **certbot hook:** installed from this commit.
4. **Build the image:** labelled `revision=<sha>`. Phase 1 replaces this with a **pull by digest**.
5. **Service hook** (`hooks.pre_start`, optional; auth-service: policy validate, diff and install).
6. **Start:** `compose up -d`, then wait for Docker's health check (60 s).
7. **Health by name over TLS:** `curl` the manifest's `health` URL. The running revision must equal
   the sha; from phase 1, the image sha (D1a).
8. **Smoke test:** the service's script runs in the new image, with host networking, read-only.

**What phase 2 adds on the host:** a fixed wrapper `/usr/local/sbin/deploy-target`. It is
installed once by hand (it is the only thing a deployer key can run, D6) and does the following:
1. Parses `SSH_ORIGINAL_COMMAND` (D6 regexes) and refuses anything else.
2. Takes `flock /run/deploy-target/<service>.lock` (D9).
3. Checks out the sha, as `deploy.sh`'s command does today.
4. Execs that commit's `deploy-target.sh` with stdin passed through.
5. Appends the result to `/var/lib/deploy-target/<service>.log` (D10).

`deploy.sh` then calls the same wrapper, so both paths are identical (D14). Running the deployed
commit's script as root is no new power: deploying a commit already means running its code.

## Secrets
| Kind | Examples | Declared as | Source | Deploy does |
|---|---|---|---|---|
| **Managed** | RustFS keys, the smoke-test client secret, the Cloudflare token (shared) | `secrets:` in the manifest | OpenBao `kv/services/<svc>/…`, `kv/shared/…` | fetches and writes them on every deploy |
| **Host-only** | auth-service signing keys and client hashes; the deployer's own client secret | `host_only:` | generated on the host | checks they exist; never reads or moves them |
| **Build** | a private package token, if ever needed | n/a | GitHub secrets | avoid (D7) |

Onboarding a new service:
1. A PR with the code, `compose.yaml`, and its grant in `infra/services/auth-service/policy.yaml`.
2. In the same PR, `infra/services/<svc>/` with `deploy.yaml` and `config.env`.
3. After the merge, run `tofu apply` (it generates the policies), `bao kv put` the values, and run
   the auth-service deploy (the policy).
4. Deploy. If step 3 was skipped, the deploy fails before touching the host. OpenBao answers 403 for
   a secret that isn't in the generated policy yet, and the error names the `tofu apply`.

Why the deployer may read every service's managed secrets: whoever controls deploys already
controls the code that reads those secrets, so this adds little power. The deployer is a
high-value host either way; it gets the hardening in Security notes.

## Security notes
- **What a stolen GitHub OIDC token can do:** deploy the one commit it was minted for, once,
  within ~5 minutes. Nothing else.
- **What a compromised deployer can do:**
  - deploy any commit to the allowlisted hosts;
  - read the managed secrets that manifests declare (for 10 minutes per login, audited).
  
  **What it can't do:** touch auth-service, OpenBao or itself (D13).

  **Mitigations:**
  - LAN only, no tunnel hostname;
  - minimal packages;
  - SSH keys usable only for the wrapper;
  - its OpenBao access is read-only and generated from the manifests.
- **What a compromised target can do:** only that host; it has no key to other hosts.
- **E4 (no token passthrough) in api-service** concerns tokens *addressed to api-service*. The
  GitHub token is addressed to the deployer; api-service forwards that one path as a plain
  reverse proxy, never consumes the token, and can pre-check `iss` and rate-limit.
- **Break-glass:** `infra/deployment/deploy.sh` from the Mac with your OpenBao login. It is the same
  host-side code path (D14). On the LAN there is also the deployer's own CLI (D18).

## Current state
**auth-service and file-management** deploy with the generic, manifest-driven pair (phase 0b, done
2026-11-08). `infra/deployment/deploy.sh <service> [ref]` on the Mac:
1. reads the pushed commit's `infra/services/<service>/deploy.yaml`;
2. fetches the declared secrets from OpenBao with your login;
3. runs `deploy-target.sh` on the host in one SSH session.

Rollback is a deploy of the older sha. Both deploys pass their smoke tests. The certbot renewal
dry-run on CT 103 passed using the OpenBao-delivered Cloudflare file.

**The other services** (gpu-server, stevenai-service and web-gateway, plus pi-server on the Pi)
still use their old per-service scripts in `infra/deployment/`, run from the laptop. These:
- rsync the working copy;
- `docker build` on the host;
- write a `.env` from the laptop's `.env`;
- `docker run --env-file`;
- `sleep 10`, then curl `/health`.

| ID | Problem | Status |
|---|---|---|
| P1 | Secrets sit in a plain-text `.env` on the laptop and are copied into a plain-text `.env` on the host, then passed as environment variables (visible in `docker inspect`, inherited by child processes). | fixed for the two migrated services; phase 3 for the rest |
| P2 | Images are built on the host from an rsynced working copy: uncommitted changes can ship, and nothing records which commit is running. | fixed for the two: built in GitHub from a pushed commit, pulled by digest-recorded tag |
| P3 | `:latest` only; no rollback. | fixed for the two: rollback = redeploy an older sha, a pull (commits from before phase 1 need `gh workflow run build.yml -f ref=<sha>` first) |
| P4 | The old container is removed before the new one is proven healthy (downtime, no recovery). | health + smoke test now gate success; automatic rollback in phase 2 |
| P5 | Deploys need the laptop and leave no record. | phase 2 |
| P6 | Four copies of the same script drift apart. | fixed for the two; phase 3 for the rest |
| P7 | Shared libraries are rsynced separately (`shared-schemas`). | fixed for the two (built in GitHub from the repo); the rest in phase 3 |

## Phases
| Phase | What ships | Status |
|---|---|---|
| **0** | Manual deploys with the conventions: secrets as files mounted into containers (not `.env`), and a hardened `compose.yaml` next to each service. | done |
| **0b** | `infra/` as source of truth (D12): manifests, `config.env`, the policy moved there, OpenBao policies generated from the manifests, the generic `deploy.sh` + `deploy-target.sh`, and managed secrets delivered from OpenBao (this absorbed the old "phase 3, secrets injection"). | **done 2026-11-08**; rotation test pending |
| **1** | **Images from GitHub.** | **done 2026-11-08** (both services pull from GHCR); config-only and rollback checks pending |
| **2** | **The deployer.** | next |
| **3** | **Migrate the remaining Docker services** (stevenai-service, web-gateway, gpu-server). | |
| **4** | **The Pi** (pi-server). | later |
| **5** (optional) | **Federation and provenance.** | |

**Phase 1: images from GitHub** (still triggered by hand; fixes P2, P3, P7)

| # | Step | Who |
|---|---|---|
| 1.1 | **Unify the image tag variable.** Both composes use `${IMAGE_TAG:-local}` (today `FILES_TAG`, `AUTH_SERVICE_TAG`). `build:` stays for local development. | me |
| 1.2 | **`.github/workflows/build.yml`.** On push to `main` and `workflow_dispatch`: (a) a `plan` job lists every manifest whose image inputs changed (D1a: `code` + `python/libs/`), reading names and build settings from `docker compose config`, so nothing is duplicated; (b) a `build` matrix job for each changed service runs its tests, then `docker/build-push-action` with context `python/`, `REVISION=<sha>`, the GitHub Actions cache, and a push of `:<sha>`. It also runs `actions/attest-build-provenance` (free now, verified in phase 5). Actions are pinned by commit sha; permissions are `contents: read, packages: write, id-token: write, attestations: write`. | me |
| 1.3 | **First push, then make the GHCR packages public** (package settings → visibility; once per package, D7 allows it). Check that an anonymous `docker pull` works from a host. | you |
| 1.4 | **`deploy-target.sh` step 4 becomes "Pull image".** Compute the image sha (D1a); `docker compose pull` with `IMAGE_TAG=<image sha>`; check the pulled image's revision label; record its digest; `compose up --no-build`. Step 7 compares the running label with the image sha. The previous deploy comes from `/var/lib/deploy-target/<service>.log` (D10), which this step starts writing. | me |
| 1.5 | **`deploy.sh` waits for the build.** If the image sha's `build` run is still in progress (`gh run list --commit`), wait for it. If it failed, or the image doesn't exist, stop with a clear message before connecting to the host. | me |
| 1.6 | **Verify.** Deploy auth-service and file-management by hand (both pulled now), then roll file-management back one commit and forward again (seconds, no build). Push a config-only change and check that nothing is built and the deploy reuses the image. | you run, I watch |

**Phase 2: the deployer** (fixes P4, P5)

| # | Step | Who |
|---|---|---|
| 2.1 | **Repository settings (D17).** Ruleset on `main`: block force pushes and deletion. Environment `production`: you as required reviewer, "prevent self-review" off, deployment branches `main` only. | you |
| 2.2 | **Manifest field `pipeline` (D16).** Schema plus `pipeline: false` for auth-service. file-management stays `false` until 2.11. | me |
| 2.3 | **The host wrapper (D6, D9, D10).** `infra/deployment/deploy-target-wrapper.sh` (reviewed in git), installed by hand on each host as `/usr/local/sbin/deploy-target`, with `/etc/deploy-target/services` listing that host's service. What it does: parse `SSH_ORIGINAL_COMMAND` (or its arguments, when you call it directly) → check it against the regexes and the services list → `flock` → fetch and check out the sha → exec that commit's `deploy-target.sh` with stdin passed through → append to the log; it prints `previous: <sha>` first. `deploy.sh` switches to it. | me write, you install on CT 102 and CT 103 |
| 2.4 | **The deployer LXC.** CT 105 `deployer`: Debian 13, unprivileged, nesting on (Docker), DHCP reservation, `onboot` order 2 (after auth-service and OpenBao), `deploy.lan.liustev6.ca` in DNS, certbot DNS-01 like the others. `boot.md` updated. | you create, I prepare commands |
| 2.5 | **Its identity (OpenBao).** `policy.yaml`: audience `secrets-manager` (scope `secrets:read`), client `deployer` granted it. Deploy auth-service by hand (the diff is shown). `add-client.sh deployer` puts the secret on CT 105 at `/etc/deployer/credentials/client_secret`, host-only. Test: mint a token and run `bao write auth/auth-service/login role=deployer jwt=…`. The resulting token reads a declared secret and is refused anything else. | me prepare, you run |
| 2.6 | **Its SSH key.** An ed25519 key generated on CT 105 (host-only, never leaves it). Its public key is added to CT 103's root `authorized_keys` with `restrict,command="/usr/local/sbin/deploy-target"`. The allowlist `infra/services/deployer/hosts.yaml` gets `files-host: {address, host_key}`, with host keys pinned (read once through pve01 and checked over the network); installed as `/etc/deployer/hosts.yaml` by the deployer's hand deploy. | you run, I prepare |
| 2.7 | **The service** (`python/services/deployer/`, FastAPI, hardened compose, uid 10001), in modules: `github_oidc` (JWKS cache, the claims table, a `jti` cache), `repo` (a bare, partial, read-only clone of the public repo; `git show <sha>:…`), `deploys` (manifest + schema at that commit, `pipeline` check, host allowlist, lock, background task, rollback to the `previous:` sha deploy-target prints, audit log), `openbao` (`service_auth.TokenClient` → OpenBao login → read the declared paths → revoke), `ssh` (OpenSSH, pinned host key, payload on stdin, timeout), `main` (the API: token first, then body; NDJSON stream with heartbeats) and `cli` (D18). Tests use a fake GitHub signer, a real git origin in a temp dir, and a fake host. **Written 2026-11-08** (54 tests). | me |
| 2.8 | **Its manifest and first deploy, by hand (D13).** `infra/services/deployer/` with `deploy.yaml` (`pipeline: false`, host-only key and client secret, the shared Cloudflare token) and `config.env`; `tofu apply`; `deploy.sh deployer`. | you run |
| 2.9 | **Test on the LAN, before anything is public.** `docker exec … app.cli deploy file-management <sha>` → a real deploy through the deployer. Then a rollback drill: deploy a commit whose health check fails, and confirm it reports `rolled_back` with the previous version running. | you run, I watch |
| 2.10 | **api-service, minimal** (**written 2026-11-08**: a new service, not the web-gateway rename; 22 tests). `python/services/api-service/` with only `/health` and the `/deploy/*` proxy route. The route streams the request through to the deployer, caps the body at 4 KB, rejects requests without a GitHub-issued bearer token, and rate-limits. It gets its own LXC (CT 106) and manifest, and is deployed by hand. Then add the public hostname `api.liustev6.ca` → api-service to the Cloudflare tunnel (CT 100). | me write, you do LXC + tunnel |
| 2.11 | **The deploy job** (**written 2026-11-08**). Add `deploy` to the workflow (environment `production`, `concurrency` per service, OIDC token, image digest from GHCR, the stream folded into log groups). Services: `pipeline: true` and whose image inputs or `infra/services/<svc>/` changed; or `workflow_dispatch -f redeploy=<svc>`. Flip file-management to `pipeline: true`. | me |
| 2.12 | **First pipeline deploy.** The push of 2.11 itself is the first one (it changes file-management's manifest): approve in GitHub → watch the stream in the Actions log → smoke test passes. Then `gh workflow run build.yml -f redeploy=file-management` (a redeploy through the pipeline). Rollback through the pipeline is `git revert` (see "GitHub side"). | you |

**Phase 3: migrate the remaining Docker services** (stevenai-service, web-gateway, gpu-server):
- each one gets a manifest, `config.env` and a hardened `compose.yaml`;
- its secrets go into OpenBao;
- its old script is deleted.

This fixes P1 and P6 everywhere.

**Phase 4: the Pi** (pi-server). Its CI/CD gets designed later: arm64 build plus a systemd target
in `deploy-target`, or a separate path (see Open questions).

**Phase 5 (optional): federation and provenance.**
- The GitHub token is exchanged at auth-service for an internal `aud=deployer` token, so the
  deployer trusts one issuer.
- Build provenance (SLSA) is verified instead of the revision label.

## Open questions
- **The Pi** (pi-server, systemd per D11): two options.
  - A `kind: systemd` target in `deploy-target`: release folders, a `current` symlink and
    `systemctl restart`. The Pi isn't on pve01 and needs a forced-command key like any host.
  - Keep its own script.
- **The deployer deploying itself** (as Argo CD and Flux can). Two problems to solve first:
  - **Its own restart:** the host would have to run the deploy detached (`systemd-run`) and roll
    back by itself, since the SSH session lives in the container being replaced.
  - **The pipeline loosening its own rules:** it could change `config.env` (which GitHub tokens
    are accepted) and `hosts.yaml` (where it may deploy).
  
  Likely shape: the pipeline may update the deployer's *code* only when those two files equal
  what's installed; a rule change still needs a hand deploy. Until then, D13.
- **Events from `deploy-target`:** today it prints human-readable status. The deployer could map
  `Step N/M` lines to NDJSON events, or `deploy-target` could gain a `--json` mode. Decide when
  building phase 2.

## Non-goals
- Kubernetes, Argo CD/Flux, or a general-purpose CI system on the LAN.
- Self-hosted GitHub runners (unsafe on a public repo).
- Building images on target hosts once phase 1 ships.
- Running arbitrary commands from a deploy request.
