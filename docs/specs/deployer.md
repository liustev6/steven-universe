# Spec: deployer (CI/CD from GitHub Actions to the home lab)

**Status: live** (2026-11-08). A push to `main` deploys the changed `pipeline: true` services:
file-management (wave 1), then api-service (wave 2; its first pipeline deploy cut its own stream
and resumed, run 42553181192). auth-service, OpenBao and the deployer deploy by hand (D13).
**Paths:**
- `python/services/deployer/`: the service, CT 105, `https://deploy.lan.liustev6.ca`, LAN only;
- `infra/services/<service>/`: manifests;
- `infra/deployment/`: the host side and the manual `deploy.sh`;
- `.github/workflows/` and `.github/scripts/`: the pipeline.

Related: [secrets-manager.md](secrets-manager.md), [auth-service.md](auth-service.md),
[api-service.md](api-service.md) (the public edge GitHub's call comes through), [boot.md](boot.md).

## What it is
The deployer turns "a commit on `main` passed CI" into "that exact build is running on the right
host". There is no deployment credential in GitHub, and a request can't run anything except a known
deployment.

```
GitHub Actions (hosted runner)                                LAN
┌───────────────────────────────┐   Cloudflare tunnel   ┌─────────────┐
│ test → build → push to GHCR   │──POST /deploy/v1/────▶│ api-service │ forwards /deploy/* only
│ GitHub OIDC token (per deploy)│   deployments          └──────┬──────┘
└───────────────────────────────┘                               ▼
         ┌──────────────────────────── deployer (CT 105) ────────────────────┐
         │ 1. verify GitHub's token (repo, main, environment, workflow, sha)  │
         │ 2. infra/services/<svc>/deploy.yaml at that sha; host allowlist    │
         │ 3. auth-service token ─▶ OpenBao login ─▶ the declared secrets     │
         │ 4. ssh root@<host> (restricted key) ───────────────────────────────┼─▶ deploy-target:
         │ 5. failed? deploy the previous commit the same way                 │   secrets, pull,
         └────────────────────────────────────────────────────────────────────┘   start, health,
                                                                                   smoke test
```

**Two ways to deploy, one host side.** Both connect to the host over SSH and run the same wrapper
and `deploy-target.sh` (D14), so the host can't tell them apart. The difference is who decides, and
how much power the decider holds:

| | Manual: `infra/deployment/deploy.sh` (Mac) | Pipeline: deployer |
|---|---|---|
| Who decides | you | a push to `main` whose tests passed |
| Proof | your SSH key and OpenBao login | GitHub's signed token, checked claim by claim |
| Power | broad: unrestricted root SSH, OpenBao admin | narrow: a key that only runs the wrapper; read-only access to the declared secrets; allowlisted hosts |
| May deploy | any service, any pushed commit | `pipeline: true` services, commits on `main`, the exact image GitHub built |
| On failure | prints the rollback command | rolls back by itself |
| Used for | the roots of trust (D13), rollbacks to any commit, break-glass | every routine deploy |

## Decisions
| ID | Decision |
|---|---|
| D1 | **Build in GitHub, run on the LAN.** Hosted runners test and build; images go to GHCR as `ghcr.io/liustev6/steven-universe/<service>:<image sha>` and are deployed **by digest**. Hosts and the deployer never build: the machine that holds the keys never runs third-party build code, and the tested image is the deployed image. |
| D1a | **An image is named by the commit that last changed its inputs** (the manifest's `code` folder plus `python/libs/`): `git log -1 --format=%H <sha> -- <code> python/libs`. A config-only commit redeploys the existing image with the new config. |
| D2 | **No deployment secrets in GitHub.** The workflow proves who it is with a **GitHub OIDC token** (`id-token: write`): short-lived, signed by GitHub, nothing to store or leak. Image pushes use the built-in `GITHUB_TOKEN`. |
| D3 | **In through the single edge.** GitHub calls `https://api.liustev6.ca/deploy/v1/deployments`; api-service forwards it unchanged to the deployer. The deployer, auth-service and OpenBao have no public hostname. |
| D4 | **Requests name, they never command.** A request says *which service* at *which commit*. Everything else comes from that commit's manifest and the deployer's host allowlist. No field is executed or used as a path. |
| D5 | **The token is bound to the request:** its `sha` must equal the request's. |
| D6 | **Least privilege to hosts.** The deployer's key is restricted in root's `authorized_keys` to `restrict,command="/usr/local/sbin/deploy-target"` (your own key keeps a normal shell). The wrapper accepts only `deploy <service> <sha> [<digest>]` and only the host's own service. Root because the deploy writes `/etc/<svc>` and chowns secrets; the LXC is the boundary (D11). |
| D7 | **Secrets never in images, git, GitHub or environment variables.** Each is a file (mode 600, owner from the manifest), mounted read-only or read by a host tool such as certbot. |
| D8 | **Health-checked, with automatic rollback.** A deploy succeeds only if the container is healthy, the health URL answers by name over TLS with the right revision, and the smoke test passes. Otherwise the deployer deploys the previous commit and reports `rolled_back`. |
| D9 | **One deploy per service at a time; redeploys are safe.** `flock` in the wrapper, `409` at the deployer. |
| D10 | **Audited in three places:** the deployer's `audit.log` (who asked: GitHub run, actor), the host's deploy log, and OpenBao's audit log (every secret read). |
| D11 | **Docker Compose on LXCs, one service per LXC.** Hardening lives in each service's `compose.yaml`. Exception: the Pi (systemd). |
| D12 | **`infra/` is the source of truth** for how every service is configured and deployed; service folders own code, `Dockerfile` and the container's shape. Secret values never live in git, only their names. |
| D13 | **The roots of trust deploy by hand only:** auth-service, OpenBao and the deployer itself. The deployer depends on the first two, and must not be able to replace itself or loosen its own rules. They are not in its host allowlist. |
| D14 | **One host-side script for every path.** The deployer replaces the caller (the Mac), never the host logic, so a manual deploy tests exactly what the pipeline runs. |
| D15 | **Push through the edge, not pull.** The deployer is LAN-only; from outside it is reachable only through api-service's `/deploy/*` routes, where GitHub's token is the gate. |
| D16 | **The manifest says who may deploy:** `pipeline: true` or `false` (default). The deployer refuses `false` services with `403`. |
| D17 | **The gate is automated, not a click.** The `production` environment accepts only `main`, without approval. What stands between a push and a broken service: tests before any image is pushed, health and smoke tests, automatic rollback, `pipeline: true` only. In exchange: two-factor authentication on the GitHub account and no unnecessary write tokens or apps, since pushing to `main` is deploying. |
| D18 | **LAN break-glass:** `docker exec deployer-deployer-1 python -m app.cli deploy <service> [<ref>]` deploys without a GitHub token (any pushed branch, `pipeline: false` allowed, allowlisted hosts only). Only root on CT 105 can run it, and root there already holds everything the deployer holds. |
| D19 | **Waves** (as Argo CD sync waves). Manifest `wave`: 1 = services (in parallel), 2 = the edge (api-service), only after wave 1 succeeded. Backends go before their callers, and the conduit every deploy stream passes through goes last. A change that needs a new auth-service grant deploys auth-service by hand first. |

## GitHub side
```
push to main ─▶ plan ─▶ checks ─▶ build (per missing image) ─▶ wave1 ─▶ wave2
                 │                                              (one deploy job per service,
                 ├─ missing images (D1a)                         environment production, main only)
                 └─ what to deploy: pipeline: true AND (its image inputs
                    or infra/services/<svc>/ changed in this push)
```
| File | What |
|---|---|
| `.github/workflows/build.yml` | the pipeline wiring: `plan`, `checks` (library and script tests), `build`, `wave1`, `wave2` |
| `.github/workflows/deploy.yml` | reusable, called once per wave: one deploy job per service. Tokens minted here carry `job_workflow_ref = …/deploy.yml@refs/heads/main`, the only workflow the deployer accepts |
| `.github/scripts/plan.sh` | what to build (D1a) and what to deploy in which wave (D16, D19) |
| `.github/scripts/deploy.sh` | one deploy (see below) |
| `.github/scripts/tests/` | tests for both, on a throwaway git history and a stand-in `curl` |

The scripts are always taken from the workflow's own commit, so building an older ref uses today's
scripts.

**One deploy job** (`deploy.sh`):
1. reads the image digest from GHCR;
2. requests a GitHub OIDC token for `aud=steven-universe-deployer`;
3. POSTs `{service, sha, image_digest}` and shows the stream as one log group per step;
4. if the stream drops (for example when the deploy replaces api-service, which carries it),
   requests a fresh token and reconnects from the last event seen, retrying for up to 15 minutes;
5. fails unless the result is `succeeded` (`rolled_back` is a failed job, with a warning).

`concurrency: deploy-<service>` queues a second deploy of the same service.

**By hand:**
```sh
gh workflow run build.yml -f redeploy=<service>   # redeploy at main's latest commit
gh workflow run build.yml -f ref=<sha>            # build an older commit's missing images
```
**Rolling back through the pipeline is `git revert` + push.** GitHub's token always carries the
commit the run is on (D5), so the pipeline can't deploy an older commit. To deploy any pushed commit
right away: `infra/deployment/deploy.sh <service> <sha>`, or the deployer's CLI (D18).

**Repository settings it relies on:**
- **Ruleset on `main`:** no force pushes, no deletion.
- **Environment `production`:** `main` only. The deployer requires `environment: production` in the
  token, so the environment must stay.
- **Fork pull requests** never get `id-token: write` or the environment.
- **GHCR packages are public:** the code is public and images hold no secrets, so hosts pull
  without credentials.

## Deployer API
**`POST /v1/deployments`** (as `/deploy/v1/deployments` through api-service)
```json
{"service": "file-management", "sha": "<40 hex>", "image_digest": "sha256:…"}
```
- **Response:** `200`, streamed as NDJSON.
- **Every event but heartbeats has a `seq`.** `accepted` carries the deployment `id`; the last
  event is the result.
- **`step` events** come from `deploy-target`'s `Step N/M` lines; `log` events carry every line.
```json
{"seq": 0, "event": "accepted", "id": "…", "host": "files-host", …}
{"seq": 7, "event": "step", "n": 4, "of": 7, "name": "Pulling image"}
{"seq": 41, "event": "result", "status": "succeeded|failed|rolled_back", "previous": "<sha>", …}
```
Refusals before anything runs:

| Status | Meaning |
|---|---|
| `401` | token invalid |
| `403` | claims don't match, or `pipeline: false` |
| `404` | no manifest at that commit |
| `409` | a deploy of this service is running |
| `422` | bad body, sha mismatch, or the commit isn't on `main` |

**`GET /v1/deployments/<id>/events?after=<seq>`**: reconnect after a dropped stream.
- **Authentication:** like a start, with a **fresh** GitHub token (each works once) for the same
  commit.
- **Response:** the events after `<seq>`, then live until the result.
- **The deploy never depends on a caller:** it runs as a background task and finishes, rolling back
  if needed, whether or not anyone is watching.
- **Memory:** the last 100 deployments are kept.

**`GET /health`**: the allowlist and SSH key are present.

### Token checks (GitHub OIDC)
Against GitHub's JWKS, RS256 only, 60 s leeway:

| Claim | Must equal | Why |
|---|---|---|
| `iss` | `https://token.actions.githubusercontent.com` | GitHub signed it |
| `aud` | `steven-universe-deployer` | minted for us |
| `repository_id`, `repository_owner_id` | the numeric ids of `liustev6/steven-universe` | names can be renamed or re-registered; ids can't |
| `ref` | `refs/heads/main` | only `main` deploys |
| `environment` | `production` | the protected environment (`main` only) |
| `job_workflow_ref` | `liustev6/steven-universe/.github/workflows/deploy.yml@refs/heads/main` | only the deploy workflow, as on `main` |
| `runner_environment` | `github-hosted` | not a self-hosted runner |
| `sha` | the request's `sha` | D5 |
| `jti` | not seen before | no replay |

These rules live in `infra/services/deployer/config.env`, which reaches CT 105 only by a hand
deploy.

## Identity and secrets
**The deployer's identity.** It holds no static OpenBao token. Per deploy:
1. a client-credentials token from auth-service as client `deployer`, with `aud=secrets-manager`
   (its client secret is host-only on CT 105);
2. an OpenBao login, role `deployer`: a 10-minute, read-only token whose policy OpenTofu
   **generates** from the manifests, so it covers exactly the declared secrets;
3. it reads the manifest's secrets, then revokes the OpenBao token.

**Secrets reach the host in memory only.** The values go into one line per secret,
`<destination> TAB <owner> TAB <base64>`, and are sent as **stdin** over SSH (never as arguments,
never to disk on the deployer or the Mac). `deploy-target.sh` writes each one atomically, mode 600,
with its owner. Services read files and never talk to OpenBao, so a sealed OpenBao blocks deploys,
not running services.

| Kind | Examples | Declared as | Deploy does |
|---|---|---|---|
| **Managed** | RustFS keys, the smoke-test client secret, the Cloudflare token (shared) | `secrets:` (values in OpenBao) | fetches and writes them on every deploy |
| **Host-only** | auth-service signing keys; the deployer's client secret and SSH key | `host_only:` | checks they exist; never reads or moves them |

**Onboarding a service:**
1. Code, `compose.yaml`, and its grant in `infra/services/auth-service/policy.yaml`.
2. `infra/services/<svc>/deploy.yaml` and `config.env`.
3. After the merge: `tofu apply` (the policies), `bao kv put` (the values), and a hand deploy of
   auth-service (the grant).
4. Its host in `infra/services/deployer/hosts.yaml`, plus the restricted key line on that host, then
   a hand deploy of the deployer.
5. Deploy.

A secret missing from the generated policy fails before touching the host, with a message naming
`tofu apply`.

## Configuration in `infra/` (D12)
The rule for each value: *if the service moved to another environment, would this change?* Yes →
`infra/`; no → the service folder.

```
infra/
  services/
    deploy.schema.json            the manifest format
    <service>/
      deploy.yaml                 host, wave, pipeline, health, smoke test, secret names (read at the deployed commit)
      config.env                  non-secret settings, written to /etc/<service>/ on every deploy
      hooks/pre-start.sh          optional service-specific steps
    deployer/hosts.yaml           the deployer's host allowlist (see below)
    auth-service/policy.yaml      who may get which tokens
  secrets-manager/tofu/           OpenBao: auth methods, roles, policies generated from the manifests
  deployment/                     deploy.sh (Mac), deploy-target-wrapper.sh, deploy-target.sh, lib.sh (hosts)
```

**The host allowlist** (`infra/services/deployer/hosts.yaml`) lists host aliases with their
address and pinned SSH host key.
- **It reaches the deployer only through a hand deploy of the deployer** (D13). That deploy shows the
  diff, and refuses `auth-host`, `bao-host` and `deployer-host`.
- **The deployer reads the installed copy**, never the file at a deployed commit. So a merged change
  can't widen the pipeline's reach until you redeploy the deployer yourself.

## On the host
| Script | Runs on | Started by |
|---|---|---|
| `infra/deployment/deploy.sh` | your Mac | you; the deployer's Python code does the same job |
| `infra/deployment/deploy-target-wrapper.sh` | each host, installed **by hand** as `/usr/local/sbin/deploy-target` | sshd (the only command the deployer's key can run) and `deploy.sh` |
| `infra/deployment/deploy-target.sh` | each host | the wrapper, from the **deployed commit** |
| `infra/deployment/lib.sh`, `infra/services/<svc>/hooks/pre-start.sh` | each host | `deploy-target.sh` |

The deployer runs no shell script on its own machine; it only executes `git` (its read-only clone)
and `ssh`.
```
deployer (CT 105, Python)
  │  ssh -i ssh_key root@<host> "deploy <service> <sha> <digest>"           (secrets on stdin)
  ▼
<host>: sshd forces /usr/local/sbin/deploy-target       ← deploy-target-wrapper.sh, installed by hand
  │  check the request, take the lock, git checkout <sha> in /opt/steven-universe
  ▼
/opt/steven-universe/infra/deployment/deploy-target.sh  ← from the deployed commit
  ├── source lib.sh
  └── source infra/services/<svc>/hooks/pre-start.sh   (if the manifest declares one)
```
- **The wrapper is the one fixed piece:** it is the gatekeeper, so a merged commit can't change what
  the restricted key may do.
- **Everything after it comes from the deployed commit:** a change to `deploy-target.sh` takes
  effect on the next deploy, and a rollback runs the older commit's own script.

**The wrapper:**
1. accepts `deploy [--color] <service> <40-hex sha> [sha256:<digest>]`, every word checked against a
   pattern;
2. refuses services not in `/etc/deploy-target/services`;
3. takes the per-service `flock`;
4. requires a commit on a pushed branch;
5. execs that commit's `deploy-target.sh`.

Every request, accepted or refused, goes to `journalctl -t deploy-target`.

**`deploy-target.sh` steps:**
1. **Commit:** last successful → new; it prints `previous: <sha>`, which the deployer rolls back to.
2. **Config and secrets:** `config.env` and the secrets from stdin (atomic, mode 600); `host_only`
   paths exist; the certificate is present.
3. **certbot hook,** from this commit.
4. **Pull the image** `<service>:<image sha>`. The revision label and digest must match. Hosts never
   build.
5. **Service hook,** if declared (auth-service: the policy; deployer: the allowlist).
6. **Start** (`compose up -d --no-build`), and wait for Docker's health check.
7. **Health by name over TLS,** with the running revision checked.
8. **Smoke test,** in the new image.

Each deploy appends `<time> <sha> <image sha> <digest> succeeded|failed` to
`/var/lib/deploy-target/<service>.log`.

## Operations
| Task | How |
|---|---|
| Deploy a pipeline service | push to `main` |
| Redeploy | `gh workflow run build.yml -f redeploy=<service>` |
| Roll back | `git revert` + push; or right away: `infra/deployment/deploy.sh <service> <older sha>` |
| Deploy auth-service, OpenBao config, the deployer | by hand: `infra/deployment/deploy.sh <service>` (D13) |
| GitHub or the edge is down | `infra/deployment/deploy.sh`, or `ssh deployer-host docker exec deployer-deployer-1 python -m app.cli deploy <service> [<ref>]` |
| Who deployed what | deployer: `/var/lib/deployer/audit.log`; host: `/var/lib/deploy-target/<service>.log` and `journalctl -t deploy-target`; OpenBao: its audit log |

## Security notes
- **A stolen GitHub token** deploys the one commit it was minted for, once, within minutes.
- **A compromised deployer** can deploy pushed commits to allowlisted hosts and read the declared
  secrets (10 minutes per login, audited). It can't touch auth-service, OpenBao, itself, or any host
  outside the allowlist, and it can't widen its own rules.
- **A compromised target host** has no key to any other host.
- **api-service** never consumes GitHub's token. It pre-checks that the token *claims* to be
  GitHub's, rate-limits, and forwards it to the deployer, which verifies it. This is api-service's
  `proxy` mode, not a breach of its "no token passthrough" rule, which concerns tokens addressed to
  api-service.
- **Write access to `main` is the ability to deploy** `pipeline: true` services (D17): protect the
  GitHub account accordingly.

## History and what's next
Built on 2026-11-08:
- **0b:** manifests, `config.env` and generated OpenBao policies; the generic `deploy.sh` and
  `deploy-target.sh`.
- **1:** images built in GitHub, pulled by digest.
- **2:** the wrapper; the deployer with its own identity and restricted key; the LAN tests (a real
  deploy and a rollback test); api-service with the public route; the first pipeline deploy (run
  42549115243).
- **2.13–2.14:** waves, workflows split into files with tested scripts, and resumable deploys.

- **Waves live:** api-service's first pipeline deploy (wave 2) cut its own stream mid-deploy and the
  workflow resumed it (run 42553181192).

**Next:**
- **Phase 3:** stevenai-service, web-gateway and gpu-server move to manifests, OpenBao and the
  pipeline. Until then they use their old scripts, which copy the laptop's `.env` files and build on
  the host.
- **Phase 4:** the Pi.
- **Optional:**
  - a least-privilege OpenBao login for manual deploys;
  - verifying GitHub's build provenance instead of the revision label;
  - exchanging GitHub's token at auth-service, so the deployer trusts one issuer.

## Open questions
- **The Pi** (pi-server, systemd per D11): a systemd target in `deploy-target` (release folders, a
  `current` symlink, `systemctl restart`, a restricted key on the Pi), or keep its own script.
- **The deployer deploying itself** (as Argo CD and Flux can). It needs two things first:
  - a detached host-side run with host-side rollback, since the SSH session lives in the container
    being replaced;
  - a guard that refuses pipeline deploys changing `config.env` or `hosts.yaml`.
  
  Until then, D13.

## Non-goals
- Kubernetes, Argo CD/Flux, or a general-purpose CI system on the LAN.
- Self-hosted GitHub runners (unsafe on a public repo).
- Building images on hosts or on the deployer.
- Running arbitrary commands from a deploy request.
