# Spec: secrets-manager (OpenBao)

**Path**: `infra/secrets-manager/` (server config, policies, bootstrap script; no secret values).
**Status**: not built. Related: [deployer.md](deployer.md) (the main reader),
[auth-service.md](auth-service.md) (how services prove who they are).

## Scope
One private, audited place for secrets that are **managed centrally** (RustFS keys, third-party API
keys, shared configuration), so they stop living in `.env` files on the laptop and on hosts. The
deployer reads them at deploy time and writes them to the target host as files.

It is **not** where every secret lives. Secrets used to authenticate *to* the secrets manager,
and secrets that never need to leave their host, stay as files on that host (see "What goes
where").

```
                 ┌─────────── secrets-manager (OpenBao, LXC, LAN only) ───────────┐
deployer ──1───▶ │ auth/auth-service (JWT method)                                  │
  token from     │   trusts https://auth.lan.liustev6.ca/.well-known/jwks.json     │
  auth-service   │   role "deployer": aud=secrets-manager, sub=service:deployer    │
  (aud=secrets-  │ policy "deployer": read kv/data/services/*                      │
   manager)      │ kv/ (KV v2, versioned)   audit log   integrated storage (Raft)  │
         ◀──2─── │ short-lived OpenBao token → read kv/services/<svc>/<name>        │
                 └─────────────────────────────────────────────────────────────────┘
deployer ──3──▶ ssh deploy-target: values on stdin → /etc/<svc>/credentials/<name> (600)
```

## Locked decisions
| ID | Decision |
|---|---|
| S1 | **OpenBao.** The open-source (MPL-2.0) fork of HashiCorp Vault, with the same API, CLI concepts, auth methods and policy language. Vault is the industry reference; OpenBao keeps that knowledge transferable without Vault's Business Source License. |
| S2 | **Private.** LXC on pve01, LAN only, no tunnel hostname. Address `https://bao.lan.liustev6.ca:8200`, Let's Encrypt certificate via DNS-01, same pattern as auth-service. |
| S3 | **Identity comes from auth-service.** Clients log in with an auth-service access token (`aud=secrets-manager`) through OpenBao's JWT auth method, which verifies it against auth-service's JWKS. No separate OpenBao credentials exist for services; the auth-service client secret is each service's only secret zero. |
| S4 | **Values only in OpenBao; names and policies in git.** `infra/secrets-manager/policies/*.hcl` and the role definitions are reviewed in git. Values are written by you with the CLI and never committed. |
| S5 | **Delivered as files at deploy time.** The deployer injects managed secrets into `/etc/<svc>/credentials/` (deployer D7). Services read files and don't talk to OpenBao, so a sealed or down OpenBao blocks **deploys only**; running and restarting services keep working. |
| S6 | **Sealed on restart.** Shamir unseal: 3 key shares, any 2 unseal. After a reboot you unseal it by hand. The initial root token is revoked after bootstrap. |
| S7 | **Audited.** A file audit device is on from the start; every request is logged with secret values HMAC'd (never in plain text). |
| S8 | **Least privilege by path.** Each reader gets a policy for exactly the paths it needs; the deployer's is read-only. |

## What goes where
| Secret | Lives | Why |
|---|---|---|
| auth-service client secrets | file on the client's host (`add-client.sh`) | it *is* the identity used to log in to OpenBao; storing it there would be circular |
| auth-service signing keys | auth-service host only | never leave their host |
| Secrets a service generates for itself (session keys) | file on that host | nobody else needs them |
| RustFS access keys | **OpenBao** `kv/services/<svc>/rustfs_*` | created once in RustFS, needed by deploys |
| Third-party API keys (OpenAI, weather, …) | **OpenBao** | central inventory, rotation, audit |
| OpenBao unseal key shares | password manager + printed paper + one more place | never on the OpenBao host or in its backups |
| Image pushes / deploy identity | none (GitHub `GITHUB_TOKEN` / OIDC) | deployer D2 |

Example: file-management's current `.env` holds `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`,
`INTERNAL_SECRET_KEY` and `FRONTEND_API_KEY`. The first two become managed secrets in OpenBao; the
last two disappear, replaced by auth-service tokens (file-management spec).

## Layout
KV v2 engine at `kv/`, one entry per secret, value in the field `value`:
```
kv/services/<service>/<name>      e.g. kv/services/file-management/rustfs_access_key
kv/shared/<name>                  used by several services (listed in their manifests)
```
Names match the file names in `/etc/<service>/credentials/` and the deployer manifest's
`secrets.managed` list. KV v2 keeps versions, so a bad change can be rolled back
(`bao kv rollback`).

## Policies (in git)
```hcl
# infra/secrets-manager/policies/deployer.hcl
path "kv/data/services/*" { capabilities = ["read"] }
path "kv/data/shared/*"   { capabilities = ["read"] }

# infra/secrets-manager/policies/admin.hcl   (you)
path "kv/*"               { capabilities = ["create", "read", "update", "delete", "list"] }
path "sys/policies/acl/*" { capabilities = ["create", "read", "update", "delete", "list"] }
path "auth/*"             { capabilities = ["create", "read", "update", "delete", "list", "sudo"] }
```

## Authentication
**Services (via auth-service).** JWT auth method mounted at `auth/auth-service`:
```
bao write auth/auth-service/config \
    jwks_url="https://auth.lan.liustev6.ca/.well-known/jwks.json" \
    bound_issuer="https://auth.lan.liustev6.ca" jwt_supported_algs="ES256"
bao write auth/auth-service/role/deployer role_type=jwt \
    bound_audiences="secrets-manager" user_claim="sub" bound_subject="service:deployer" \
    token_policies="deployer" token_ttl=10m token_max_ttl=10m
```
The deployer gets an auth-service token with `TokenClient` (`aud=secrets-manager`), logs in, and
receives a 10-minute OpenBao token. Additions to auth-service's `policy.yaml`:
```yaml
audiences:
  secrets-manager:
    scopes: [secrets:read]
clients:
  deployer:
    grants:
      secrets-manager:
        scopes: [secrets:read]
```
OpenBao checks `aud` and `sub`, not `scope`; the scope documents intent and keeps the grant
explicit in auth-service's policy.

**You (admin).** `userpass` with TOTP MFA, reachable on the LAN or tailnet only. The root token is
generated only for emergencies (`bao operator generate-root`, needs 2 unseal shares) and revoked
after use.

## Operations
- **Writing a secret:** `bao kv put kv/services/file-management/rustfs_access_key value=-`
  (reads stdin; nothing in shell history). The next deploy installs it.
- **Rotation:** put a new version, redeploy the services that use it, then revoke the old value at
  its source (e.g. delete the old RustFS key).
- **Unseal after reboot:** `bao operator unseal` twice, with two of the three shares. Until then
  deploys that need managed secrets fail with a clear error; services keep running (S5).
- **Backups:** daily `bao operator raft snapshot save` (systemd timer), copied to RustFS
  (`services/secrets-manager/backups/`) and off-site. A snapshot is encrypted and useless without
  the unseal shares, which are never stored with it.
- **Monitoring:** `/v1/sys/health` (sealed or not) checked by the status page.

## Deployment
- LXC `secrets-manager`: 1 core, 1 GB RAM, 8 GB disk, DHCP reservation, `startup order=1`
  (after auth-service, before services that deploy).
- OpenBao with integrated storage (Raft, single node), listener on 8200 with the Let's Encrypt
  certificate, audit log to `/var/log/openbao/audit.log` (rotated, shipped to journald).
- `infra/secrets-manager/bootstrap.sh`: init (3 shares, threshold 2), enable the audit device,
  `kv-v2` at `kv/`, the JWT auth method and roles, `userpass` + MFA, apply policies, revoke the
  root token. Idempotent where possible; policies re-applied on every run.

## Security notes
- **Root on the OpenBao LXC or pve01** can read secrets from memory while it is unsealed. The
  host gets the same hardening as auth-service and nothing else runs on it.
- **Proxmox backups** of the LXC contain the encrypted storage; without unseal shares they reveal
  nothing. Keep the shares out of every backup.
- **If auth-service is compromised,** an attacker can mint a token for `sub=service:deployer` and
  read managed secrets. auth-service is already the root of trust for every service; it gets the
  strictest hardening, and its deploys stay manual (deployer open question).
- **The deployer's read access is broad by design** (deployer spec, "Secrets"): whoever deploys
  code already controls what reads these secrets.

## Phases
| Phase | What ships |
|---|---|
| **1** | OpenBao LXC, bootstrap, KV layout, admin login; you move secrets out of laptop `.env` files into it. |
| **2** | JWT auth trusting auth-service; the deployer injects managed secrets (deployer phase 3). |
| **3** (optional) | Services fetch their own secrets at runtime with their own auth-service token (per-service roles and policies), for secrets that change often. |
| **4** (optional) | Dynamic secrets: credentials created per request with a lease (e.g. a database engine), once a service needs them. |

## Open questions
- Auto-unseal (transit seal on a second instance, or a cloud KMS) vs. unsealing by hand after
  every reboot?
- Should GitHub Actions ever read from OpenBao directly (JWT auth trusting GitHub's OIDC), e.g.
  for a build-time secret? Default: no (deployer D7).
- Which third-party keys move first: a list per service from the current `.env` files.

## Non-goals
- High availability (multi-node Raft): one node is enough for a home lab; backups cover loss.
- Storing auth-service client secrets or signing keys (see "What goes where").
- Services reading OpenBao at runtime in phases 1–2.
