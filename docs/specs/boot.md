# Boot and recovery (pve01)

**Status:** settings checked 2026-11-08; not yet proven by a full reboot of pve01 (see "Test
reboot"). Related: [auth-service.md](auth-service.md), [file-management.md](file-management.md),
[rustfs.md](rustfs.md), [secrets-manager.md](secrets-manager.md)

## What it is

What happens when pve01 starts (after a reboot, a crash or a power cut), in which order the
services come up, what needs a person, and how to check that everything is back. The goal:
**everything starts by itself, except unsealing OpenBao**, which is manual by design.

## Boot sequence
```
power on ─▶ pve01 (Proxmox VE 9, 192.168.50.101)
            │  tailscaled           remote access + subnet route 192.168.50.0/24
            │                       (forwarding: /etc/sysctl.d/99-tailscale.conf)
            ▼  pve-guests           starts every container with onboot=1, by startup order
 order 0   CT 102 auth-service      Docker → auth-service container          ready
           ... wait 15 s (up=15)
 order 1   CT 101 rustfs            systemd rustfs.service                   ready
 order 1   CT 104 secrets-manager   Docker → OpenBao container               running, SEALED
           ... wait 10 s
 order 2   CT 103 file-management   Docker → file-management container       ready
 order 2   CT 105 deployer          Docker → deployer container (phase 2)    ready
 no order  CT 100 cloudflared       systemd cloudflared (Cloudflare tunnel)  ready
```
Containers without an order start after all ordered ones. Shutdown runs in reverse order.

| CT | Name | Starts at boot | Order | Service inside | Restarts on crash |
|---|---|---|---|---|---|
| 100 | cloudflared | `onboot=1` | none (last) | `cloudflared` systemd, enabled | yes |
| 101 | rustfs | `onboot=1` | 1, `up=10` | `rustfs` systemd, enabled | yes (`on-failure`) |
| 102 | auth-service | `onboot=1` | **0**, `up=15` | Docker enabled; container `restart: unless-stopped` | yes |
| 103 | file-management | `onboot=1` | 2, `up=10` | Docker enabled; container `restart: unless-stopped` | yes |
| 104 | secrets-manager | `onboot=1` | 1, `up=10` | Docker enabled; container `restart: unless-stopped` | yes (comes back sealed) |
| 105 | deployer | `onboot=1` | 2, `up=10` | Docker enabled; the deployer service from deployer phase 2 (not deployed yet) | yes |

Also enabled on CT 102–105: `certbot.timer` (certificate renewal) and `unattended-upgrades`
(security updates).

## Why this order
| Service | Depends on | Why it's placed there |
|---|---|---|
| auth-service (0) | nothing on the LAN | every other service verifies tokens against its public keys; first, with 15 s to settle |
| rustfs (1) | nothing | file-management's storage, so before it |
| secrets-manager (1) | auth-service (only to verify service logins) | nothing reads it at runtime, so it doesn't block anyone |
| file-management (2) | auth-service (JWKS on first request), rustfs (health check) | after both |
| deployer (2) | auth-service and OpenBao (only when a deploy runs: token, then secrets) | after both; deploys wait for the unseal |
| cloudflared (last) | api-service later | the public entry comes up last |

The order is a convenience, not a guarantee: the services also tolerate starting early.
file-management fetches auth-service's keys lazily on the first request (and retries), and its
health check reports `degraded` until RustFS answers; Docker keeps restarting a crashed container.

## What needs a person

### 1. Unseal OpenBao (every boot, by design)
OpenBao keeps its decryption key only in memory, so every start (reboot, container restart,
upgrade) leaves it sealed. Unseal with 2 of the 3 shares:
```sh
ssh -t bao-host docker exec -it secrets-manager-openbao-1 bao operator unseal   # share 1
ssh -t bao-host docker exec -it secrets-manager-openbao-1 bao operator unseal   # share 2
```
**While it's sealed nothing breaks:** services get secrets as files at deploy time and never read
OpenBao while running. Only deploys that need managed secrets wait (once the deployer uses it).

### 2. Power-on after a power cut (one-time BIOS setting)
pve01 only starts by itself after a power cut if the BIOS says so: set **"Restore on AC Power
Loss" / "After Power Failure" = Power On**. Not checkable from software; check once in the BIOS.

## Things that can delay recovery
| Situation | Effect | What to do |
|---|---|---|
| **Internet down at boot** | services start, but `*.lan.liustev6.ca` names come from public DNS (Cloudflare): new tokens can't be fetched by name, file-management can't fetch auth-service's keys until DNS works | nothing; it recovers when the internet is back. Services already running with cached keys and tokens keep working. |
| **Router slow to boot** | containers get their DHCP addresses late (all have reservations) | wait; Docker restarts anything that failed |
| **The NVMe** (it had I/O errors before the reinstall) | containers fail to start or remount read-only | `dmesg`, `smartctl -a /dev/nvme0`. Suspected cause: NVMe power saving; the proposed fix (not applied yet) is the kernel parameters `nvme_core.default_ps_max_latency_us=0 pcie_aspm=off` |
| **A certificate expired while powered off** | HTTPS fails for that service | `certbot renew` on that container (the hook installs it) |

## Checking that everything is back
From the Mac (Tailscale on; the subnet route gives direct access to `192.168.50.x`):
```sh
curl -s https://auth.lan.liustev6.ca/health                        # auth-service: "healthy"
curl -s https://files.lan.liustev6.ca/health                       # file-management: storage "ok"
curl -s https://bao.lan.liustev6.ca:8200/v1/sys/seal-status        # OpenBao: "sealed": true until you unseal
curl -s -o /dev/null -w "%{http_code}\n" http://192.168.50.123:9000/   # RustFS: 403 = up (no credentials)
ssh pve01 'pct list; pct exec 100 -- systemctl is-active cloudflared'
```
The deeper check is the deploy: re-running `infra/deployment/deploy.sh auth-service` and
`infra/deployment/deploy.sh file-management` (no change, a few seconds each; needs OpenBao
unsealed and your login, since deploys deliver secrets from it) runs their health
checks and smoke tests against the real services.

## Test reboot (to do once)
1. Pick a quiet time; nothing runs that needs to be stopped first.
2. `ssh pve01 reboot` (or the Proxmox UI: pve01 → Reboot).
3. After ~2 minutes: the checks above; all except OpenBao should be up without any action.
4. Unseal OpenBao (this also activates the pending "no client-certificate prompt" setting).
5. Re-run both deploy scripts for their smoke tests.
6. Record the date and anything that didn't come back here.

## Adding a new service
- Container: `pct set <id> --onboot 1 --startup order=<n>,up=<seconds>`; choose `<n>` after
  everything it depends on.
- Inside: `systemctl enable` its service, or Docker enabled plus `restart: unless-stopped` in
  `compose.yaml`.
- A DHCP reservation for its MAC (addresses must not change between boots).
- Add it to the tables and the checks above.

## Not covered
The Pi (pi-server, hey-pi) is separate hardware with its own systemd services; the GPU server is
currently shut down.
