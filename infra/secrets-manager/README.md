# infra/secrets-manager (OpenBao)

The secrets-manager: OpenBao on CT 104 (`secrets-manager`, `192.168.50.206`), reachable at
`https://bao.lan.liustev6.ca:8200` on the LAN and over the Tailscale subnet route.
Spec and design decisions: `docs/specs/secrets-manager.md`.

| File | What |
|---|---|
| `compose.yaml` | the OpenBao container: official image pinned to 2.7.2, uid 10001, read-only, no capabilities |
| `openbao.hcl` | server config: Raft storage, TLS listener (auto-reloads renewed certificates), UI, audit log |
| `cert-hook.sh` | certbot hook: copies the certificate for uid 10001; never restarts (a restart seals OpenBao) |
| `tofu/` | OpenTofu: KV store, auth methods, roles, policies. Never secret values. |

Install and initialization are one-time manual runbooks (below). **Initialization is never
scripted**: the unseal shares must only ever appear in your terminal.

## 1. Install (one time; CT 104 already has Docker and certbot)

On CT 104 (`ssh bao-host`):
```sh
# Directories, owned by the container user
install -d -m 700 -o 10001 -g 10001 /etc/openbao /etc/openbao/tls /var/lib/openbao /var/lib/openbao/data /var/lib/openbao/logs

# Certificate (Cloudflare token already in /root/.secrets/cloudflare.ini)
install -m 755 /opt/steven-universe/infra/secrets-manager/cert-hook.sh /usr/local/sbin/openbao-cert-hook
certbot certonly --non-interactive --agree-tos --register-unsafely-without-email \
  --dns-cloudflare --dns-cloudflare-credentials /root/.secrets/cloudflare.ini \
  --dns-cloudflare-propagation-seconds 30 -d bao.lan.liustev6.ca \
  --deploy-hook /usr/local/sbin/openbao-cert-hook

# Code and start
git clone --filter=blob:none https://github.com/liustev6/steven-universe /opt/steven-universe   # first time
cd /opt/steven-universe/infra/secrets-manager && docker compose up -d
docker compose exec openbao bao status          # Initialized: false, Sealed: true
```

## 2. Initialize (one time, by you, in your own terminal)

```sh
ssh -t bao-host docker exec -it secrets-manager-openbao-1 bao operator init -key-shares=3 -key-threshold=2
```
It prints **3 unseal key shares** and an **initial root token**, once.
- Put each share in a **different** place: your password manager, a printed copy kept at home, and
  one more (a second password manager or a trusted person). Any 2 unseal; losing 2 means the data
  is gone for good.
- Keep the root token only until step 4, then it is revoked.
- Never paste shares or the token into chat, notes in the repo, or a file on CT 104.

## 3. Unseal (after initialization and after every restart or reboot)

```sh
ssh -t bao-host docker exec -it secrets-manager-openbao-1 bao operator unseal   # share 1 (hidden prompt)
ssh -t bao-host docker exec -it secrets-manager-openbao-1 bao operator unseal   # share 2
```
`bao status` then shows `Sealed: false`. While sealed, services keep running (their secrets are
files); only deploys that need managed secrets wait.

## 4. Configure with OpenTofu (first time: with the root token)

On the Mac (OpenTofu and the `bao` CLI: `brew install opentofu openbao`), with the Tailscale
subnet route approved so `bao.lan.liustev6.ca` is reachable. One terminal session throughout:
OpenTofu reads `VAULT_*`, the `bao` CLI reads `BAO_*`, so both are set.
```sh
cd infra/secrets-manager/tofu
export VAULT_ADDR=https://bao.lan.liustev6.ca:8200 BAO_ADDR=https://bao.lan.liustev6.ca:8200
read -rs TF_VAR_state_passphrase && export TF_VAR_state_passphrase   # the state passphrase (password manager)
read -rs ROOT                                                        # the initial root token (hidden)
export VAULT_TOKEN="$ROOT" BAO_TOKEN="$ROOT"
bao token lookup | grep -E "^policies"     # [root]

tofu init
tofu plan -out=first.tfplan   # review: 6 to add (kv, 2 policies, userpass, auth-service JWT, deployer role)
tofu apply first.tfplan && rm first.tfplan
```
Create your admin login. The password is read without echo and piped in, so it is never on a
command line, in history, or in OpenTofu's state:
```sh
read -rs P && printf %s "$P" | bao write auth/userpass/users/steven \
    token_policies=admin token_ttl=1h token_max_ttl=8h password=- && unset P
ADMIN="$(bao login -method=userpass -token-only username=steven)"   # prompts for the password
export VAULT_TOKEN="$ADMIN" BAO_TOKEN="$ADMIN"
bao token lookup | grep -E "^policies"     # [admin default]
```
Revoke the root token and prove it's dead:
```sh
bao token revoke "$ROOT"
BAO_TOKEN="$ROOT" bao token lookup >/dev/null 2>&1 && echo "STILL VALID" || echo "root token revoked"
unset ROOT
tofu plan                                   # as admin now: "No changes"
```
Delete the root token from wherever you noted it. A new one can only be created with 2 unseal
shares (`bao operator generate-root`).

From then on, every configuration change is: edit `tofu/`, `tofu plan`, review, `tofu apply`,
commit. `tofu plan` showing changes you didn't make means someone changed OpenBao by hand.

## Operations
| Task | How |
|---|---|
| Write a secret | `bao kv put kv/services/<svc>/<name> value=-` (type or paste, Ctrl-D; nothing in shell history) |
| Read a secret | `bao kv get -field=value kv/services/<svc>/<name>` |
| Old versions | `bao kv metadata get …`, `bao kv rollback -version=N …` |
| Emergency root token | `bao operator generate-root` (needs 2 shares); revoke it afterwards |
| Audit log | `ssh bao-host tail -f /var/lib/openbao/logs/audit.log` (values HMAC'd) |
| Upgrade OpenBao | change the image tag in `compose.yaml` after reading the release notes, commit, `git pull && docker compose up -d` on CT 104, then unseal |
| Certificate | renewed by certbot's timer; the hook copies it and OpenBao reloads it while staying unsealed |
| State backup | `tofu/terraform.tfstate` is encrypted; keep a copy (password manager attachment or RustFS later) |
