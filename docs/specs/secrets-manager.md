# secrets-manager (OpenBao)

**Status: live** (2026-11-08). OpenBao on CT 104 (`192.168.50.206`), `https://bao.lan.liustev6.ca:8200`,
LAN only. Every deploy that needs managed secrets reads them from here: manual deploys with your
login, pipeline deploys with the deployer's own identity. Backups are deferred.
**Paths:**
- `infra/secrets-manager/`: the container, server config, certbot hook and the runbook (`README.md`);
- `infra/secrets-manager/tofu/`: OpenBao's configuration as code (no secret values);
- `infra/services/<service>/deploy.yaml`: which secrets each service needs (`secrets[]`).

Related: [deployer.md](deployer.md) (its main reader), [auth-service.md](auth-service.md) (how
services prove who they are), [boot.md](boot.md) (unsealing after a reboot), [rustfs.md](rustfs.md).

## What it is
One private, audited place for secrets that are **managed centrally**: RustFS keys, the shared
smoke-test secret, the Cloudflare DNS token. Each is stored once, encrypted, with every read logged.
A deploy reads the secrets its manifest declares and writes them to the target host as files.
**Services never talk to OpenBao**: they read files, so a sealed or down OpenBao blocks deploys and
nothing else.

It is **not** where every secret lives. Secrets used to authenticate *to* OpenBao (directly or
through auth-service), and secrets that never leave their host, stay as files on that host (see
"Inventory").

```
                 ┌──────────── secrets-manager (OpenBao, CT 104, LAN only) ───────────┐
deployer ──1───▶ │ auth/auth-service (JWT method)                                      │
  token from     │   trusts https://auth.lan.liustev6.ca/.well-known/jwks.json         │
  auth-service   │   role "deployer": aud=secrets-manager, sub=service:deployer        │
  (aud=secrets-  │ policy "deployer": read the secrets the manifests declare           │
   manager)      │ kv/ (KV v2, versioned)   audit log   integrated storage (Raft)      │
         ◀──2─── │ 10-minute OpenBao token → read kv/<from> for each declared secret   │
                 └─────────────────────────────────────────────────────────────────────┘
deployer ──3──▶ revoke the token; ssh deploy-target: values on stdin → <to> (mode 600)
```

**Two readers, one format.** Both write the same payload to `deploy-target`'s stdin (deployer D14):

| | Manual: `infra/deployment/deploy.sh` (Mac) | Pipeline: deployer (CT 105) |
|---|---|---|
| Login | your userpass login (`steven`, policy `admin`), kept 1 hour by the `bao` CLI | an auth-service token (`aud=secrets-manager`, `sub=service:deployer`) at `auth/auth-service`, role `deployer` |
| OpenBao token | yours, broad | 10 minutes, read-only, generated `deployer` policy; revoked after the reads |
| Reads | the manifest's `secrets[].from` | the same |

## Decisions
| ID | Decision | Why |
|---|---|---|
| S1 | **OpenBao**, the open-source (MPL-2.0) fork of HashiCorp Vault: same API, CLI, auth methods and policy language. | Vault is the industry reference; OpenBao keeps the knowledge transferable without Vault's Business Source License. |
| S2 | **Private:** CT 104 on pve01, LAN only, no tunnel (the Mac reaches it over the Tailscale subnet route). Let's Encrypt certificate (DNS-01), like the other services. | Every client verifies the certificate; no `skip verify` anywhere. |
| S3 | **Services log in with auth-service tokens** (`aud=secrets-manager`) through OpenBao's JWT auth method, which trusts auth-service's JWKS (ES256, `bound_issuer` = auth-service). | No separate OpenBao credentials for services: the auth-service client secret stays each service's only "secret zero". |
| S4 | **Delivered as files at deploy time.** A deploy reads the managed secrets and writes them to the paths the manifest names (deployer D7); services never read OpenBao at runtime. | A sealed or down OpenBao blocks **deploys only**; running and restarting services keep working. |
| S5 | **Sealed on restart, unsealed by hand:** Shamir 3 key shares, any 2 unseal; the shares live in three separate places, never on CT 104 or in its backups. | Sealing makes a copy of the disk or a backup useless. Storing the key next to the data would undo that. |
| S6 | **The initial root token is revoked** after setup; an emergency root token is generated only with 2 shares (`bao operator generate-root`). | No permanent all-powerful credential exists. |
| S7 | **Audited:** a file audit device declared in `openbao.hcl`, not through the API (OpenBao refuses that by default, `unsafe_allow_api_audit_creation = false`); values are HMAC'd in the log. | Who read which secret, when; and the audit device can't be changed through the API. |
| S8 | **Least privilege by path:** each reader's policy covers exactly its paths; the deployer's is read-only. | |
| S9 | **Install and initialization are manual runbooks** (one time); initialization is never scripted. | The unseal shares must only ever appear in your terminal. |
| S10 | **Configuration as code with OpenTofu:** the KV engine, auth methods, roles and policies are declared in `tofu/` and applied with `tofu plan` / `tofu apply`. The audit device (S7) and the admin user (S11) are the exceptions. | Reviewed in git like auth-service's `policy.yaml`; `plan` shows changes before they happen and detects drift. OpenTofu is the open-source Terraform; HashiCorp's Vault provider works with OpenBao. |
| S11 | **Secret values are never managed by OpenTofu.** They are written with `bao kv put … value=-` (stdin). Your admin user and its password are created by hand too. | Anything OpenTofu manages lands in its state file. |
| S12 | **OpenTofu state is encrypted** (built-in state encryption, PBKDF2 passphrase from `TF_VAR_state_passphrase`, `enforced = true` for state and plans; passphrase in your password manager). The state stays on the Mac, gitignored. | The state holds OpenBao's configuration; encryption means a leaked copy reveals nothing. |
| S13 | **Docker Compose** like every LXC service (deployer D11): `quay.io/openbao/openbao:2.7.2` pinned, uid 10001, read-only filesystem, no capabilities, `no-new-privileges`, **no swap** (`memswap_limit` = `mem_limit` = 512 MB; OpenBao 2.x has no mlock). | Same model and hardening as the other services; decrypted secrets in memory never reach disk. |
| S14 | **Not the community-scripts installer.** It initializes with one share, writes the unseal key **and the root token** in plain text into the file the server reads, auto-unseals from it, uses a self-signed certificate with verification turned off, and installs `latest` (reviewed 2026-11-08). | Each of these defeats S2, S5 or S6. |
| S15 | **The service manifests declare which secrets each service needs** (`secrets[].from`, `to`, `owner`, optional `format`); OpenTofu **generates** the read-only policies from them (`service-<name>`, and `deployer` = the union). | One declaration per secret; a secret no manifest declares can't be read by deploys, so least privilege stays up to date by itself. |
| S16 | **auth-service's deliverable secrets go into OpenBao too** (the shared smoke-test secret and the Cloudflare token). Its **signing keys and client-secret hashes stay host-only** on CT 102. | One source for shared secrets; key material never travels. No circular dependency: a manual deploy logs in with userpass, which doesn't involve auth-service, and auth-service is deployed by hand only (deployer D13). |
| S17 | **Manual deploys read OpenBao with your admin login** (userpass, 1-hour token); the deployer has its own identity (S3, role `deployer`). | Break-glass and the roots of trust (auth-service, OpenBao, the deployer) need a path that doesn't depend on the deployer. A least-privilege login for manual deploys is optional future work. |

## Inventory: every secret and where it lives
| Secret | Kind | Source of truth | Delivered to | Used by |
|---|---|---|---|---|
| RustFS access key + secret key | managed | **OpenBao** `kv/services/file-management/rustfs_access_key`, `rustfs_secret_key` | CT 103 `/etc/file-management/credentials/` (owner 10001) | file-management |
| smoke-test client secret | managed, shared | **OpenBao** `kv/shared/smoke_test_client_secret` | CT 102 and CT 103 `/root/.secrets/smoke-test-client` | auth-service's and file-management's smoke tests |
| Cloudflare DNS token (certbot) | managed, shared | **OpenBao** `kv/shared/cloudflare_dns_token` | CT 102, 103, 105, 106 `/root/.secrets/cloudflare.ini` (certbot format); CT 104 keeps its own copy by hand (OpenBao can't deliver to itself on a rebuild) | certbot renewals |
| auth-service signing keys | host-only | CT 102 `/etc/auth-service/keys/` | never travels | auth-service |
| client secret hashes | host-only | CT 102 `/etc/auth-service/clients/` | never travels (only hashes) | auth-service |
| client secrets (deployer, api-service, pi-server) | onboarded | the client's host, piped by `add-client.sh` (deployer: CT 105 `/etc/deployer/credentials/client_secret`) | — | the client; it *is* its identity, so never in OpenBao (circular) |
| deployer SSH key | host-only | CT 105 `/etc/deployer/credentials/ssh_key` | — | the deployer (restricted on each target, deployer D6) |
| RustFS root key | host-only | CT 101 `/etc/default/rustfs` | — | RustFS console/admin only |
| OpenBao unseal shares | — | your password manager + printed paper + one more place | never on CT 104 or in backups | unsealing |
| OpenBao admin password | — | your password manager | — | your `bao login` |
| OpenTofu state passphrase | — | your password manager | — | `tofu plan/apply` |
| Image pushes / deploy identity | — | none (GitHub `GITHUB_TOKEN` / OIDC) | — | deployer D2 |

Kinds: **managed** = value in OpenBao, written to the host on every deploy; **host-only** = created
where it's used and never copied; **onboarded** = placed once on the client's host (its identity).

## How it works
**The server** (`compose.yaml`, `openbao.hcl`): one node, integrated storage (Raft) encrypted with
the key that unsealing unlocks, HTTPS only, the UI on, the audit log in
`/var/lib/openbao/logs/audit.log`. Docker's health check counts "sealed" as healthy: the server
answers, it just waits for you. **Certificates:** after each renewal `cert-hook.sh` copies the
certificate for uid 10001 and sends `SIGHUP`; OpenBao reloads it and stays unsealed. Nothing
restarts OpenBao on its own, because a restart seals it.

**Configuration** (`tofu/`): `main.tf` declares the KV engine, the `userpass` method, the
`auth-service` JWT method with its `deployer` role, and the `admin` and `deployer` policies.
`services.tf` generates the policies from the manifests (S15), and its `check` block rejects a
manifest whose `service` doesn't match its folder or whose paths don't fit the KV layout.
`policies/admin.hcl` is you: secrets, policies, mounts, auth methods and Raft snapshots, but not
root (unseal, rekey and root-token generation need the shares).

**KV layout:** KV v2 at `kv/`, one entry per secret, the value in the field `value`:
```
kv/services/<service>/<name>      e.g. kv/services/file-management/rustfs_secret_key
kv/shared/<name>                  used by several services
```
A manifest names the path without the mount (`from: shared/cloudflare_dns_token`); the policy grants
`read` on `kv/data/<from>`, no `list` and no write. KV v2 keeps versions (`bao kv rollback`).

**A new secret:** declare it in the manifest, `tofu apply` (the policies now include it),
`bao kv put` the value, deploy. Skip the apply and the deploy fails with "not allowed".

## Operations
The runbook with exact commands is `infra/secrets-manager/README.md`.

| Task | How |
|---|---|
| Unseal after a reboot or restart | `bao operator unseal` twice, with two of the three shares (README step 3, boot.md step 1). Until then deploys fail clearly; services keep running (S4). |
| Write or change a secret | `bao kv put kv/<path> value=-` (stdin; nothing in shell history); the next deploy installs it |
| Rotate a managed secret | put a new version, redeploy the services that use it, revoke the old value at its source |
| Change policies, roles, mounts | edit `tofu/`, `tofu plan` (review), `tofu apply` as admin, commit |
| Check for drift | `tofu plan` shows "No changes" |
| Emergency root access | `bao operator generate-root` (needs 2 shares); revoke the token afterwards |
| Who read what | `ssh bao-host tail -f /var/lib/openbao/logs/audit.log` (values HMAC'd) |
| Upgrade OpenBao | change the image tag in `compose.yaml` after reading the release notes, commit, `git pull && docker compose up -d` on CT 104, unseal |

## Security notes
- **No backups yet.** If CT 104's disk or the NVMe fails, everything in OpenBao is lost. Acceptable
  while it only holds secrets that can be re-created at their source (a new RustFS key, a new
  Cloudflare token); add backups before it holds anything that can't. Losing 2 of the 3 unseal
  shares has the same effect, backups or not.
- **Root on CT 104 or pve01** can read secrets from memory while OpenBao is unsealed. CT 104 runs
  nothing else; SSH key only; the container is read-only and unprivileged.
- **Backups of CT 104** contain only the encrypted storage; without 2 shares they reveal
  nothing.
- **If auth-service is compromised,** an attacker can mint a token for `sub=service:deployer` and
  read every declared secret. auth-service is already the root of trust; it gets the strictest
  hardening and is deployed by hand.
- **The deployer's read access is broad by design** (every declared secret): whoever deploys code
  already controls what reads these secrets. Each login lasts 10 minutes and is audited.
- **The OpenTofu state** is encrypted and never committed; secret values are never in it (S11).

## History and what's next
Set up by hand on 2026-11-08 following the README (install, initialize, first `tofu apply` with the
root token, admin login, root token revoked). The same day the secrets moved out of `.env` files
into OpenBao, manual deploys started delivering them (deployer phase 0b), and the deployer got its
own login (deployer phase 2).

**Next:**
- **Backups (deferred).** Planned design: a `snapshot` policy and token role in `tofu/`, a daily
  systemd timer on CT 104 saving the Raft snapshot, uploaded to a Cloudflare R2 bucket with
  retention (bucket lock) and an R2 token limited to that bucket.
- **Phase 3 (optional):** services fetch their own secrets at runtime with their own auth-service
  tokens, using the `service-<name>` policies that already exist.
- **Phase 4 (optional):** dynamic secrets, created per request with a lease (for example a database).

## Open questions
- Unseal by hand after every reboot, or later an automatic unseal that doesn't keep the key on the
  same host (a transit seal on a second instance, or a cloud KMS)?
- Move Cloudflare DNS records and the Proxmox containers into the same OpenTofu setup (Cloudflare
  and `bpg/proxmox` providers)?
- MFA (TOTP) on the admin login?
- Where the OpenTofu state lives long term: a copy in your password manager, or RustFS as an S3
  backend.

## Non-goals
High availability (multi-node Raft); storing auth-service client secrets or signing keys; services
reading OpenBao at runtime (until phase 3); GitHub Actions reading OpenBao directly.
