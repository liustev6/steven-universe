# Service containers (LXC) on pve01

How to create the container for a new service and onboard it to the deployer. Every service runs
in **its own unprivileged LXC with Docker inside**: the container is the security boundary, and
membership of the `docker` group is root-equivalent inside it (deployer spec D11). CT 102–106 were
all built this way.

Related: [deployer.md](specs/deployer.md) (how deploys work), [boot.md](specs/boot.md) (boot order),
[secrets-manager.md](specs/secrets-manager.md) (secrets), [auth-service.md](specs/auth-service.md)
(grants and client secrets).

**Contents:** Part 1, the container: steps 1–7. Part 2, onboard a service: steps 8–14. Then
troubleshooting.

Placeholders: `<id>` the container ID (next free: `pct list`), `<name>` the service
(`api-service`), `<host>` the SSH alias (`api-host`), `<dns>` its LAN name
(`api.lan.liustev6.ca`).

## Part 1: the container

### 1. Create it (on pve01)
```sh
ssh pve01
pct create <id> local:vztmpl/debian-13-standard_13.6-1_amd64.tar.zst \
  --hostname <name> --arch amd64 --ostype debian --unprivileged 1 \
  --features nesting=1,keyctl=1 --cores 1 --memory 1024 --swap 256 \
  --rootfs local-lvm:8 --net0 name=eth0,bridge=vmbr0,ip=dhcp,type=veth \
  --onboot 1 --startup order=<n>,up=10 --timezone host \
  --ssh-public-keys /root/.ssh/authorized_keys
pct start <id>
pct config <id> | grep -o 'hwaddr=[^,]*'      # MAC, for the DHCP reservation
pct exec <id> -- ip -4 -br addr show eth0      # its IP
```
- **Unprivileged with `nesting=1,keyctl=1` is all Docker needs.** Never add raw `lxc.*` lines such as
  `apparmor.profile: unconfined`, `cgroup2.devices.allow: a` or an empty `cap.drop`: they make the
  container nearly privileged and remove the boundary. None of CT 100–106 has them.
- **`--startup order`:** after everything the service depends on. auth-service is 0; RustFS and
  OpenBao 1; file-management and the deployer 2; api-service 3; cloudflared last. See
  [boot.md](specs/boot.md).
- **`--ssh-public-keys`** copies pve01's `authorized_keys`, which includes your Mac's key. SSH works
  right away: key-only, no password.

### 2. Network (you: router and Cloudflare)
1. **DHCP reservation** for that MAC. If the reserved IP differs from the current one:
   `pct reboot <id>`.
2. **Cloudflare DNS:** an `A` record `<dns>` → the IP, **DNS only** (grey cloud). LAN names resolve
   through public DNS, so certbot's DNS-01 challenge and every client find it.

### 3. Console autologin and root password (optional, the convention here)
Proxmox → CT → Console opens a root shell without a prompt. Copy the two overrides from an existing
container, then set the password:
```sh
for u in container-getty@ console-getty; do
  ssh pve01 "pct exec 105 -- cat /etc/systemd/system/$u.service.d/autologin.conf" | sed 's/CT 105/CT <id>/' |
    ssh pve01 "pct exec <id> -- sh -c 'mkdir -p /etc/systemd/system/$u.service.d && cat > /etc/systemd/system/$u.service.d/autologin.conf'"
done
ssh pve01 'pct exec <id> -- chpasswd'      # then type: root:<password>, Enter, Ctrl-D
ssh pve01 'pct exec <id> -- sh -c "systemctl daemon-reload; systemctl restart container-getty@1 console-getty"'
```
Each override is:
```ini
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin root --noreset --noclear --keep-baud 115200,57600,38400,9600 - ${TERM}
```
SSH stays key-only; the password only matters at a login prompt you open yourself.

### 4. Packages and Docker (inside the container)
```sh
ssh pve01 "pct exec <id> -- env LANG=C bash -c '
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get -y -qq full-upgrade
apt-get install -y -qq ca-certificates curl git openssl python3-yaml certbot python3-certbot-dns-cloudflare unattended-upgrades
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
echo \"deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian trixie stable\" > /etc/apt/sources.list.d/docker.list
apt-get update -qq && apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker certbot.timer
docker run --rm hello-world | grep -m1 Hello && docker image rm -f hello-world >/dev/null
docker compose version'"
```
| Package | Why |
|---|---|
| `git`, `python3-yaml` | the deploy checks out the commit and reads its manifest (`deploy-target.sh`) |
| `certbot`, `python3-certbot-dns-cloudflare` | the TLS certificate for `<dns>` (DNS-01, renewed by `certbot.timer`) |
| `unattended-upgrades` | security updates |
| Docker CE + Compose plugin | the service runs as a Compose project; images are pulled from GHCR, never built here |

`LANG=C` only silences the locale warnings that `pct exec` causes by passing pve01's `LANG`.

### 5. SSH from the Mac
Add the alias to `~/.ssh/config`:
```
Host <host>
  HostName <IP>
  User root
  ProxyJump pve01
```
Trust its host key **only after checking it through pve01**, a channel you already trust:
```sh
ssh pve01 "pct exec <id> -- ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub"        # expected fingerprint
ssh pve01 "ssh-keyscan -t ed25519 <IP> 2>/dev/null" | ssh-keygen -lf -               # what the network offers
ssh -o StrictHostKeyChecking=accept-new <host> hostname                              # only if they match
```

### 6. The deploy wrapper
The one command a deploy can run on the host (deployer spec D6). It's installed by hand and never
by a deploy, because it is the gatekeeper:
```sh
ssh <host> 'set -e
git clone -q --filter=blob:none https://github.com/liustev6/steven-universe.git /opt/steven-universe
install -m 755 -o root -g root /opt/steven-universe/infra/deployment/deploy-target-wrapper.sh /usr/local/sbin/deploy-target
install -d -m 755 /etc/deploy-target; echo <name> >/etc/deploy-target/services
/usr/local/sbin/deploy-target deploy other-service 0000000000000000000000000000000000000000 || true'
# expected: "deploy-target: other-service is not deployed on this host"
```
Reinstall it the same way, without the clone, only when `deploy-target-wrapper.sh` changes.

### 7. Boot order
Add the container to [boot.md](specs/boot.md): the boot sequence, the container table, and "Why
this order".

## Part 2: onboard a service (deploy by hand first, then the pipeline)

Every service follows the same pattern as file-management and api-service, so a new one needs files,
not new scripts.

### 8. The code (`python/services/<name>/`)
| File | Pattern (copy from `file-management-service` or `api-service`) |
|---|---|
| `Dockerfile` | two stages; non-root uid **10001**; `LABEL org.opencontainers.image.revision=${REVISION}`; `HEALTHCHECK` on `/health`; `CMD uvicorn app.main:app --factory`, with TLS from `/etc/<name>/tls/` |
| `Dockerfile.dockerignore` | build context is `python/`: send only the service and the libraries it uses |
| `compose.yaml` | `image: ghcr.io/liustev6/steven-universe/<name>:${IMAGE_TAG:-local}` plus `build:`; `read_only`, `user: 10001`, `cap_drop: [ALL]`, `no-new-privileges`, `tmpfs: /tmp`; `ports: 443:8443`; `env_file: /etc/<name>/config.env`; `credentials` → `/run/secrets:ro`, `tls` read-only |
| `app/main.py` | a zero-argument `app()` factory (the image's command), and `GET /health` |
| `scripts/smoke_test.py` | runs after every deploy inside the new image, with host networking, against `https://<dns>` |
| `scripts/cert-hook.sh` | certbot deploy hook: copies the certificate into `/etc/<name>/tls/` (uid 10001), restarts the container |
| `tests/`, `requirements*.txt`, `pytest.ini`, `.gitignore` | CI runs the tests before building the image |

### 9. Its configuration (`infra/services/<name>/`)
- **`deploy.yaml`:** `service`, `host: <host>`, `code`, `compose`, `config`, `health`, `smoke_test`,
  `certificate`, `secrets` (names only), `host_only`, `hooks`, and to start with
  **`pipeline: false`**. Format: `infra/services/deploy.schema.json`.
- **`config.env`:** the non-secret settings, written to `/etc/<name>/config.env` on every deploy.
- **`hooks/pre-start.sh`:** only for service-specific steps. auth-service's hook installs its
  policy; the deployer's installs its allowlist.

### 10. Identity and secrets
- **If it calls other services or is called with tokens:** add its grants to
  `infra/services/auth-service/policy.yaml`, deploy auth-service by hand (the policy diff is shown),
  then create its client secret straight onto the host, as a host-only file:
  ```sh
  python/services/auth-service/scripts/add-client.sh <name> <host> /etc/<name>/credentials/client_secret 10001
  ```
  First create the directory: `ssh <host> install -d -m 700 -o 10001 -g 10001 /etc/<name> /etc/<name>/credentials`.
- **For each `secrets[].from`:** `bao kv put kv/<from> value=…`, then `tofu apply` in
  `infra/secrets-manager/tofu/`. The OpenBao policies are generated from the manifests, so a new
  secret isn't readable until applied. Shared secrets (the Cloudflare token for certbot) already
  exist; declaring them is enough.

### 11. Push, then check the image
Push. GitHub builds `ghcr.io/liustev6/steven-universe/<name>:<sha>`. A new GHCR package can start
out **private**: GitHub → your profile → Packages → `<name>` → Package settings → visibility
**Public**, so hosts pull without credentials. `deploy.sh` checks this before it connects.

### 12. First deploy, by hand
```sh
infra/deployment/deploy.sh <name>     # 1st run: writes cloudflare.ini, then stops at "No certificate"
ssh <host> 'install -m 755 /opt/steven-universe/python/services/<name>/scripts/cert-hook.sh /usr/local/sbin/<name>-cert-hook
  certbot certonly --non-interactive --agree-tos --register-unsafely-without-email \
    --dns-cloudflare --dns-cloudflare-credentials /root/.secrets/cloudflare.ini \
    -d <dns> --deploy-hook /usr/local/sbin/<name>-cert-hook'
infra/deployment/deploy.sh <name>     # 2nd run: the whole deploy, ending with the smoke test
```
The certificate is a one-time manual step: the first deploy checks for it (step 2) before it would
install the hook (step 3). Later renewals run the hook by themselves.

### 13. Join the pipeline
This step is optional: services that must stay by hand (the roots of trust, D13) stop at step 12.
1. **The deployer's allowlist:** add the host to `infra/services/deployer/hosts.yaml` with its
   address and **pinned host key**, read through pve01 and compared with what the deployer sees:
   ```sh
   ssh pve01 "pct exec <id> -- cat /etc/ssh/ssh_host_ed25519_key.pub"
   ssh deployer-host "ssh-keyscan -t ed25519 <IP> 2>/dev/null"          # must show the same key
   ```
2. **The deployer's key on the host**, restricted to the wrapper. This is the same line every
   pipeline host has, and it uses the deployer's **public** key, from
   `ssh deployer-host cat /etc/deployer/credentials/ssh_key.pub`:
   ```sh
   ssh <host> 'A=/root/.ssh/authorized_keys; cp -p $A $A.bak-$(date +%Y%m%d%H%M%S)
   echo "restrict,command=\"/usr/local/sbin/deploy-target\" <deployer public key>" >> $A'
   ```
3. **Push, then hand-deploy the deployer**, which installs the allowlist and shows the diff
   `+ <host>`: `infra/deployment/deploy.sh deployer`.
4. **Check the key from CT 105:** it may run `deploy <name> …` and nothing else (deployer.md "On the
   host").
5. **Set `pipeline: true`** (and `wave: 2` only for the edge) in `deploy.yaml`, then push. That push
   is the service's first pipeline deploy. Watch it in the Actions log.

### 14. Done when
- [ ] `https://<dns>/health` answers, and the smoke test passes on every deploy
- [ ] the container is in [boot.md](specs/boot.md), with a DHCP reservation
- [ ] secrets are files under `/etc/<name>/credentials/` (mode 600, uid 10001), never `.env` files
- [ ] if in the pipeline: a push that changes `python/services/<name>/` or `infra/services/<name>/`
      deploys it, and the deployer's `audit.log` has the line

## Troubleshooting
| Symptom | Cause and fix |
|---|---|
| Docker fails to start containers in the LXC | the container needs `--features nesting=1,keyctl=1`: `pct set <id> --features nesting=1,keyctl=1`, then `pct reboot <id>` |
| `Host key verification failed` | the pinned key doesn't match: re-read it through pve01 (step 5 or 13). Never just accept a new key. |
| `deploy.sh`: "No image … in GHCR (or the package isn't public)" | the build is still running or failed (`gh run list --workflow build.yml`), or the package is private (step 11) |
| `deploy.sh`: "OpenBao is sealed" | unseal it ([boot.md](specs/boot.md)) |
| a deploy fails: `kv/<path>: not allowed` | the secret is declared but its policy isn't applied: `tofu apply` (step 10) |
| deployer: `host … is not in the deployer's allowlist` (403) | step 13.1–13.3 not done, or the deployer not redeployed |
| deployer: `<name> is not deployed on this host` | `/etc/deploy-target/services` on the host doesn't list it (step 6) |
| `No certificate in /etc/<name>/tls` | step 12 |

The older services (gpu-server, stevenai-service, web-gateway, pi-server) still use their own
scripts in `infra/deployment/`, which need `rsync` on the host. They move to this pattern in
phase 3 of the deployer spec.
