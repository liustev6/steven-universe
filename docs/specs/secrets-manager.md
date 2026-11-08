# secrets-manager (OpenBao)

**Status:** live since 2026-11-08 (setup steps 1–11 done; backups deferred) · `https://bao.lan.liustev6.ca:8200` on CT 104 (`192.168.50.206`)
**Code:** `infra/secrets-manager/` (container, server config, OpenTofu configuration; no secret
values) · Related: [deployer.md](deployer.md) (its main reader), [auth-service.md](auth-service.md)
(how services prove who they are), [rustfs.md](rustfs.md)

## What it is

One private, audited place for secrets that are **managed centrally**: RustFS keys, third-party API
keys, shared configuration. Today these live in `.env` files on the laptop and in files placed by
hand on each host. With the secrets manager, they are stored once, encrypted, with every read
logged, and the deployer writes them to the target host as files at deploy time.

It is **not** where every secret lives. Secrets used to authenticate *to* the secrets manager, and
secrets that never leave their host, stay as files on that host (see "What goes where").

```
                 ┌──────────── secrets-manager (OpenBao, CT 104, LAN only) ───────────┐
deployer ──1───▶ │ auth/auth-service (JWT method)                                      │
  token from     │   trusts https://auth.lan.liustev6.ca/.well-known/jwks.json         │
  auth-service   │   role "deployer": aud=secrets-manager, sub=service:deployer        │
  (aud=secrets-  │ policy "deployer": read kv/data/services/*                          │
   manager)      │ kv/ (KV v2, versioned)   audit log   integrated storage (Raft)      │
         ◀──2─── │ 10-minute OpenBao token → read kv/services/<svc>/<name>             │
                 └─────────────────────────────────────────────────────────────────────┘
deployer ──3──▶ ssh deploy-target: values on stdin → /etc/<svc>/credentials/<name> (mode 600)
```

## Design decisions
| ID | Decision | Why |
|---|---|---|
| S1 | **OpenBao**, the open-source (MPL-2.0) fork of HashiCorp Vault: same API, CLI, auth methods and policy language. | Vault is the industry reference; OpenBao keeps the knowledge transferable without Vault's Business Source License. |
| S2 | **Private:** CT 104 on pve01, LAN only, no tunnel. `https://bao.lan.liustev6.ca:8200` with a Let's Encrypt certificate (DNS-01), like auth-service and file-management. | Services and the CLI verify the certificate; no `skip verify` anywhere. |
| S3 | **Services log in with auth-service tokens** (`aud=secrets-manager`) through OpenBao's JWT auth method, which trusts auth-service's JWKS. | No separate OpenBao credentials for services: the auth-service client secret stays each service's only "secret zero". |
| S4 | **Delivered as files at deploy time.** The deployer reads managed secrets and writes them to `/etc/<svc>/credentials/` (deployer D7); services never talk to OpenBao. | A sealed or down OpenBao blocks **deploys only**; running and restarting services keep working. |
| S5 | **Sealed on restart, unsealed by hand:** Shamir 3 key shares, any 2 unseal; shares kept in three separate places, never on CT 104 or in its backups. | Sealing makes a copy of the disk or a backup useless. Storing the key next to the data (as the community script does) would undo that. |
| S6 | **The initial root token is revoked** after setup; an emergency root token is generated only with 2 shares (`bao operator generate-root`). | No permanent all-powerful credential exists. |
| S7 | **Audited:** a file audit device declared in `openbao.hcl` (OpenBao refuses creating audit devices through the API by default, `unsafe_allow_api_audit_creation = false`); values are HMAC'd in the log, never stored in plain text. | Who read which secret, when; and the audit device can't be changed through the API. |
| S8 | **Least privilege by path:** each reader's policy covers exactly its paths; the deployer's is read-only. | |
| S9 | **Install and initialization are manual runbooks** (one-time, like RustFS); initialization is never scripted. | The unseal shares must only ever appear in your terminal. |
| S10 | **Configuration as code with OpenTofu:** secrets engine, auth methods, roles and policies are declared in `infra/secrets-manager/tofu/` and applied with `tofu plan` / `tofu apply`. | Reviewed in git like auth-service's `policy.yaml`; `plan` shows changes before they happen and detects drift. OpenTofu is the open-source Terraform; its Vault provider works with OpenBao. |
| S11 | **Secret values are never managed by OpenTofu.** They are written with `bao kv put` (stdin). | Anything OpenTofu manages lands in its state file. |
| S12 | **OpenTofu state is encrypted** (OpenTofu's built-in state encryption, passphrase in your password manager); kept outside git, later in RustFS (S3 backend). | The state holds configuration details and the admin user's password hash. |
| S13 | **Docker Compose** like every LXC service (deployer D11): the official image pinned to a version (`quay.io/openbao/openbao:2.7.2`), uid 10001, read-only filesystem, no capabilities, **no swap** (`memswap_limit` = `mem_limit`, 512 MB; OpenBao 2.x has no mlock). | Same model and hardening as the other services. |
| S14 | **Not the community-scripts installer.** It initializes with one share, writes the unseal key **and the root token** in plain text into the file the server reads, auto-unseals from it, uses a self-signed certificate with verification turned off, and installs `latest` (reviewed 2026-11-08). | Each of these defeats S2, S5 or S6. |

## What goes where
| Secret | Lives | Why |
|---|---|---|
| auth-service client secrets | file on the client's host (`add-client.sh`) | it *is* the identity used to log in to OpenBao; storing it there would be circular |
| auth-service signing keys | CT 102 only | never leave their host |
| Secrets a service generates for itself | file on that host | nobody else needs them |
| RustFS keys (file-management's) | **OpenBao** `kv/services/file-management/rustfs_*` | created once in RustFS, needed by deploys |
| Third-party API keys (OpenAI, weather, …) | **OpenBao** `kv/services/<svc>/…` | central inventory, rotation, audit |
| OpenBao unseal shares | your password manager + printed paper + one more place | never on CT 104 or in its backups |
| OpenTofu state passphrase | your password manager | decrypts the state |
| Image pushes / deploy identity | none (GitHub `GITHUB_TOKEN` / OIDC) | deployer D2 |

## Repository layout
```
infra/secrets-manager/
  README.md            install, initialization and unseal runbooks; operations
  compose.yaml         the OpenBao container (pinned image, hardened)
  openbao.hcl          server config: Raft storage, TLS listener (auto-reloads certificates), UI,
                       audit log
  cert-hook.sh         certbot deploy hook: copy the certificate for uid 10001, restart
  tofu/
    versions.tf        OpenTofu + Vault provider versions; state encryption
    main.tf            kv-v2 at kv/, JWT auth trusting auth-service, roles, userpass (the admin
                       user and its password are created by hand, never in state)
    policies/admin.hcl, deployer.hcl
    variables.tf       address, admin user name (password comes from a prompt, not a file)
    .gitignore         *.tfstate*, .terraform/
```

### Server config (`openbao.hcl`, sketch)
```hcl
ui            = true
api_addr      = "https://bao.lan.liustev6.ca:8200"
cluster_addr  = "https://bao.lan.liustev6.ca:8201"

storage "raft" {
  path    = "/openbao/data"
  node_id = "bao-1"
}
listener "tcp" {
  address         = "0.0.0.0:8200"
  tls_cert_file   = "/openbao/tls/cert.pem"
  tls_key_file    = "/openbao/tls/key.pem"
  tls_auto_reload = true          # renewed certificates without a restart (a restart seals it)
}
audit "file" "file" {
  options { file_path = "/openbao/logs/audit.log" }
}
```

### OpenTofu configuration (`tofu/main.tf`, sketch)
```hcl
resource "vault_mount" "kv" {
  path    = "kv"
  type    = "kv"
  options = { version = "2" }
}
resource "vault_jwt_auth_backend" "auth_service" {
  path               = "auth-service"
  jwks_url           = "https://auth.lan.liustev6.ca/.well-known/jwks.json"
  bound_issuer       = "https://auth.lan.liustev6.ca"
  jwt_supported_algs = ["ES256"]
}
resource "vault_jwt_auth_backend_role" "deployer" {
  backend         = vault_jwt_auth_backend.auth_service.path
  role_name       = "deployer"
  role_type       = "jwt"
  bound_audiences = ["secrets-manager"]
  bound_subject   = "service:deployer"
  user_claim      = "sub"
  token_policies  = [vault_policy.deployer.name]
  token_ttl       = 600
  token_max_ttl   = 600
}
resource "vault_policy" "deployer" {
  name   = "deployer"
  policy = file("${path.module}/policies/deployer.hcl")
}
# + vault_policy.admin, vault_auth_backend "userpass" (the user itself is created by hand)
```

### Policies (`tofu/policies/*.hcl`)
```hcl
# deployer.hcl: read every service's managed secrets, nothing else
path "kv/data/services/*" { capabilities = ["read"] }
path "kv/data/shared/*"   { capabilities = ["read"] }

# admin.hcl: you
path "kv/*"               { capabilities = ["create", "read", "update", "delete", "list"] }
path "sys/policies/acl/*" { capabilities = ["create", "read", "update", "delete", "list"] }
path "sys/mounts*"        { capabilities = ["create", "read", "update", "delete", "list", "sudo"] }
path "sys/audit*"         { capabilities = ["create", "read", "update", "delete", "list", "sudo"] }
path "auth/*"             { capabilities = ["create", "read", "update", "delete", "list", "sudo"] }
path "sys/auth*"          { capabilities = ["create", "read", "update", "delete", "list", "sudo"] }
path "sys/storage/raft/snapshot" { capabilities = ["read"] }
```

### Secrets layout
KV v2 at `kv/`, one entry per secret, value in the field `value`:
```
kv/services/<service>/<name>      e.g. kv/services/file-management/rustfs_secret_key
kv/shared/<name>                  used by several services
```
Names match the files in `/etc/<service>/credentials/` and the deployer manifest's
`secrets.managed`. KV v2 keeps versions (`bao kv rollback`).

### Additions to auth-service's `policy.yaml` (with the deployer)
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
OpenBao checks `aud` and `sub`; the scope documents intent.

## Setup steps (proposed)
| # | Step | Who |
|---|---|---|
| 1 | **Tailscale subnet route** on pve01 (`192.168.50.0/24`), approved in the Tailscale admin, so the Mac reaches `bao.lan…:8200` directly for `tofu apply` and the UI | me + **you** (approve) |
| 2 | **OpenTofu on the Mac** (`brew install opentofu`) | **you** or me |
| 3 | **CT 104 `secrets-manager`** like CT 103: Debian 13, unprivileged, Docker, certbot, autologin, SSH alias `bao-host`; 1 core, 1 GB RAM, 8 GB disk, `startup order=1` | me |
| 4 | Router reservation for CT 104; Cloudflare `A bao.lan.liustev6.ca → <its IP>` (DNS only) | **you** |
| 5 | Cloudflare token on CT 104 (copy from CT 103 via pve01, or a new one); `certbot certonly --dns-cloudflare -d bao.lan.liustev6.ca` with the hook | me |
| 6 | Write `infra/secrets-manager/` (compose, `openbao.hcl`, hook, `tofu/`, README); review, commit, push | me → **you** review |
| 7 | **Start OpenBao** on CT 104 (`docker compose up -d`): running, sealed, not initialized | me |
| 8 | **Initialize** in your terminal: `bao operator init -key-shares=3 -key-threshold=2`. Save the 3 shares in 3 places and keep the root token for step 10 only. Unseal with 2 shares. | **you** (never through chat) |
| 9 | Choose the **OpenTofu state passphrase** and save it in your password manager | **you** |
| 10 | **First `tofu apply`** with the root token in your shell (`VAULT_TOKEN`): audit device, kv, JWT auth, policies, your admin login (password typed at the prompt). Then log in as admin and **revoke the root token**. | **you** run, I prepare and walk through |
| 11 | **Verify:** audit log written; the root token no longer works; a `smoke` read with an auth-service token (once a reader client exists) | me |
| 12 | **Backups: deferred (2026-11-08)**, for simplicity. Planned design when picked up: a `snapshot` policy + `raft-snapshot` token role in `tofu/`, a daily systemd timer on CT 104 saving the Raft snapshot over HTTPS, uploaded to a Cloudflare R2 bucket with retention (bucket lock), an R2 token limited to that bucket. | — |

Afterwards, with the deployer (deployer phase 3): the `secrets-manager` audience and `deployer`
client in auth-service, the RustFS keys into `kv/services/file-management/`, and managed-secret
injection in the deploy.

## Operations
| Task | How |
|---|---|
| Unseal after a reboot | `bao operator unseal` twice, with two of the three shares. Until then deploys needing managed secrets fail clearly; services keep running (S4). |
| Write or change a secret | `bao kv put kv/services/<svc>/<name> value=-` (reads stdin; nothing in shell history); the next deploy installs it |
| Change policies, roles, mounts | edit `tofu/`, `tofu plan` (review the diff), `tofu apply` as admin; commit |
| Check for drift | `tofu plan` should show no changes |
| Rotate a managed secret | put a new version, redeploy the services that use it, revoke the old value at its source |
| Emergency root access | `bao operator generate-root` (needs 2 shares); revoke the token afterwards |
| Backups | daily `bao operator raft snapshot save`; snapshots are encrypted and useless without the shares |
| Monitoring | `/v1/sys/health` (sealed or not) on the status page later |

## Security notes
- **No backups yet (deferred).** If CT 104's disk or the NVMe fails, everything stored in
  OpenBao is lost. Acceptable while it only holds secrets that can be re-created at their
  source (e.g. a new RustFS key); add step 12 before it holds anything that can't. Losing 2 of
  the 3 unseal shares has the same effect, backups or not.
- **Root on CT 104 or pve01** can read secrets from memory while OpenBao is unsealed. CT 104 runs
  nothing else; SSH key only; the container is read-only and unprivileged.
- **Backups of CT 104** contain the encrypted storage; without 2 shares they reveal nothing.
- **If auth-service is compromised**, an attacker can mint a token for `sub=service:deployer` and
  read managed secrets. auth-service is already the root of trust; it gets the strictest hardening
  and is deployed by hand.
- **The deployer's read access is broad by design**: whoever deploys code already controls what
  reads these secrets.
- **The OpenTofu state** is encrypted and never committed; secret values are never in it (S11).

## Phases
| Phase | What ships |
|---|---|
| **1** | Setup steps 1–11 (done; backups deferred): OpenBao running, initialized, configured by OpenTofu; you move secrets out of `.env` files into it. |
| **2** | The deployer logs in with its auth-service token and injects managed secrets (deployer phase 3). |
| **3** (optional) | Services fetch their own secrets at runtime with their own auth-service tokens (per-service roles). |
| **4** (optional) | Dynamic secrets: credentials created per request with a lease (e.g. a database). |

## Open questions
- Unsealing by hand after every reboot, or later an automatic unseal that doesn't keep the key on
  the same host (a transit seal on a second instance, or a cloud KMS)?
- Move Cloudflare DNS records and the Proxmox containers into the same OpenTofu setup (Cloudflare
  and `bpg/proxmox` providers)?
- MFA (TOTP) on the admin login from the start, or later?

## Non-goals
High availability (multi-node Raft); storing auth-service client secrets or signing keys; services
reading OpenBao at runtime in phases 1–2; GitHub Actions reading OpenBao directly.
