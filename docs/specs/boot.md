# Boot and recovery (pve01)

**Status:** settings checked 2026-11-17 (every guest `onboot` with its order, Docker enabled
everywhere, every container `restart: unless-stopped`, pve01's network names pinned, agent-service
recovering by itself from PostgreSQL starting late); not yet proven by a full reboot of pve01: the
GPU shutdown is the first one (see "Shutting down for hardware" and "Test reboot"). Related: [auth-service.md](auth-service.md), [file-management.md](file-management.md),
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
            ▼  pve-guests           starts every container and VM with onboot=1, by startup order
 order 0   CT 102 auth-service      Docker → auth-service container          ready
           ... wait 15 s (up=15)
 order 1   CT 101 rustfs            systemd rustfs.service                   ready
 order 1   CT 104 secrets-manager   Docker → OpenBao container               running, SEALED
           ... wait 10 s
 order 2   CT 103 file-management   Docker → file-management container       ready
 order 2   CT 105 deployer          Docker → deployer container              ready
 order 2   CT 108 speech-service       Docker → speech-service (the GPU)           ready
           ... wait 10 s
 order 3   CT 106 api-service       Docker → api-service container           ready
 order 3   CT 107 agent-service     Docker → agent-service, PostgreSQL       ready
           ... wait 10 s
 order 4   VM 200 sandbox-worker    Docker (+ gVisor) → sandbox-runner       ready
           ... wait 30 s (a VM boots its own kernel)
 no order  CT 100 cloudflared       systemd cloudflared (Cloudflare tunnel)  ready
```
Guests without an order start after all ordered ones. Shutdown runs in reverse order. Numbering:
containers from 100, VMs from 200.

| CT | Name | Starts at boot | Order | Service inside | Restarts on crash |
|---|---|---|---|---|---|
| 100 | cloudflared | `onboot=1` | none (last) | `cloudflared` systemd, enabled | yes |
| 101 | rustfs | `onboot=1` | 1, `up=10` | `rustfs` systemd, enabled | yes (`on-failure`) |
| 102 | auth-service | `onboot=1` | **0**, `up=15` | Docker enabled; container `restart: unless-stopped` | yes |
| 103 | file-management | `onboot=1` | 2, `up=10` | Docker enabled; container `restart: unless-stopped` | yes |
| 104 | secrets-manager | `onboot=1` | 1, `up=10` | Docker enabled; container `restart: unless-stopped` | yes (comes back sealed) |
| 105 | deployer | `onboot=1` | 2, `up=10` | Docker enabled; container `restart: unless-stopped` | yes |
| 106 | api-service | `onboot=1` | 3, `up=10` | Docker enabled; container `restart: unless-stopped` | yes |
| 107 | agent-service | `onboot=1` | 3, `up=10` | Docker enabled; agent-service and its PostgreSQL, both `restart: unless-stopped` ([control-plane.md](agent-service/control-plane.md)) | yes |
| 108 | speech-service | `onboot=1` | 2, `up=10` | Docker enabled; the GPU through `dev0`–`dev3` ([lxc-gpu-passthrough-setup.md](../lxc-gpu-passthrough-setup.md)); speech-service: Docker starts it; healthy ~15 s later (both models loaded and warmed; [speech-service.md](speech-service.md)). Nothing else may hold the GPU: one copy of the models fills it | yes |
| **VM 200** | sandbox-worker | `onboot=1` | 4, `up=30` | Docker enabled, gVisor (`runsc`) registered; sandbox-runner, container `restart: unless-stopped` ([sandbox-worker-setup.md](../sandbox-worker-setup.md)) | yes |

**pve01's firewall** (`pve-firewall`, enabled since 2026-11-13) starts with the host; its policies are
ACCEPT, so it filters only VM 200 (`infra/hosts/pve01/firewall/`). If VM 200 can't reach something it
needs after a boot, check `/var/log/pve-firewall.log` on pve01.

Also enabled on CT 102–108: `certbot.timer` (certificate renewal) and `unattended-upgrades`
(security updates); on VM 200, `unattended-upgrades` and the QEMU guest agent.

## Why this order
| Service | Depends on | Why it's placed there |
|---|---|---|
| auth-service (0) | nothing on the LAN | every other service verifies tokens against its public keys; first, with 15 s to settle |
| rustfs (1) | nothing | file-management's storage, so before it |
| secrets-manager (1) | auth-service (only to verify service logins) | nothing reads it at runtime, so it doesn't block anyone |
| file-management (2) | auth-service (JWKS on first request), rustfs (health check) | after both |
| deployer (2) | auth-service and OpenBao (only when a deploy runs: token, then secrets) | after both; deploys wait for the unseal |
| speech-service (2) | auth-service (token keys); the GPU's driver on pve01 (loaded with the host's kernel, before any guest) | after auth-service; nothing depends on it at boot |
| api-service (3) | the deployer (for `/deploy/*`) | after it; before cloudflared, which publishes it |
| agent-service (3) | auth-service, OpenBao (its login, restored on a new disk), file-management (nightly backups); the runner only when a session starts (it retries) | after its dependencies, before the worker whose sandboxes reach only its gateway |
| sandbox-worker (4) | agent-service's gateway (its sandboxes reach only that), when it exists | after the control plane; nothing depends on it at boot |
| cloudflared (last) | api-service | the public entry comes up last |

The order is a convenience, not a guarantee: the services also tolerate starting early.
file-management fetches auth-service's keys lazily on the first request (and retries), and its
health check reports `degraded` until RustFS answers; Docker keeps restarting a crashed container.

## What needs a person
**After any boot, one command does it all:** `ssh pve01`, then `./setup.sh` (it's `/root/setup.sh`,
installed from `infra/hosts/pve01/setup.sh`). It waits for the guests, asks for the OpenBao unseal
shares (hidden), resumes agents if the kill switch is on, checks every service through the status
page, and waits for agent-service's login to be saved to OpenBao again. Before hardware work,
`./setup.sh shutdown` pauses agents and shuts pve01 down cleanly. The steps it automates:


### 1. Unseal OpenBao (every boot, by design)
OpenBao keeps its decryption key only in memory, so every start (reboot, container restart,
upgrade) leaves it sealed. Unseal with 2 of the 3 shares:
```sh
ssh -t bao-host docker exec -it secrets-manager-openbao-1 bao operator unseal   # share 1
ssh -t bao-host docker exec -it secrets-manager-openbao-1 bao operator unseal   # share 2
```
**While it's sealed nothing breaks:** services get secrets as files at deploy time and never read
OpenBao while running. What waits:
- **deploys** (the deployer reads managed secrets from it): pipeline and hand deploys fail until it's
  unsealed;
- **agent-service's login copy:** it runs from the login on its own disk; saving a refreshed login to
  OpenBao (and reading it at start) fails until the unseal, then catches up by itself within 10
  minutes. Meanwhile the status page shows agent-service's `login.saved_to_openbao: false`.

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
| **agent-service starts before its PostgreSQL is ready** (Docker doesn't wait for `depends_on` at boot) | agent-service exits and Docker restarts it until PostgreSQL answers | nothing: checked 2026-11-17, healthy within ~12 s of PostgreSQL |
| **Agent sessions that were live at shutdown** | their pods are gone (VM 200 restarted); agent-service ends them as `sandbox_lost` within ~15 s of the runner being back | nothing: callers start a new session; the conversation carries over |
| **The GPU's driver isn't built for the kernel pve01 booted** (a kernel update whose DKMS build failed) | CT 108 still starts, but its `/dev/nvidia*` entries point at nothing: speech-service can't start | `dkms status`; [lxc-gpu-passthrough-setup.md](../lxc-gpu-passthrough-setup.md) "Updating later". `./setup.sh reboot` refuses a kernel the module isn't built for |
| **New PCIe hardware (a GPU)** | could renumber PCI devices, which renames network interfaces on many hosts and leaves `vmbr0` without its port | nothing on pve01: its NICs are pinned by MAC (`/usr/local/lib/systemd/network/50-pmx-nic0.link`, Proxmox 9's `pve-network-interface-pinning`), so `nic0` stays `nic0`. If the network doesn't come up anyway: the console, `ip -br link`, compare with `/etc/network/interfaces` |

## Checking that everything is back
**One look first:** the lab's status page checks every service (OpenBao reads `ok` even sealed, with
`sealed: true` in its details):
```sh
curl -s https://api.liustev6.ca/v1/status | python3 -m json.tool | less
```
Then, individually, from the Mac (Tailscale on; the subnet route gives direct access to `192.168.50.x`):
```sh
curl -s https://auth.lan.liustev6.ca/health                        # auth-service: "healthy"
curl -s https://files.lan.liustev6.ca/health                       # file-management: storage "ok"
curl -s https://bao.lan.liustev6.ca:8200/v1/sys/seal-status        # OpenBao: "sealed": true until you unseal
curl -s https://deploy.lan.liustev6.ca/health                      # deployer: "healthy"
curl -s https://api.liustev6.ca/health                             # api-service, through Cloudflare: "ok"
curl -s -o /dev/null -w "%{http_code}\n" http://192.168.50.123:9000/   # RustFS: 403 = up (no credentials)
curl -s https://sandbox-runner.lan.liustev6.ca/health                # sandbox-runner: warm pods
curl -s https://agent.lan.liustev6.ca/health                       # agent-service: "healthy"
ssh agent-host 'agent ask -p voice "say ok"'                       # a real turn, end to end
ssh pve01 'pct list; qm list; pct exec 100 -- systemctl is-active cloudflared'
```
The deeper check is the deploy: re-running `infra/deployment/deploy.sh auth-service` and
`infra/deployment/deploy.sh file-management` (no change, a few seconds each; needs OpenBao
unsealed and your login, since deploys deliver secrets from it) runs their health
checks and smoke tests against the real services.

## Shutting down for hardware (e.g. adding a GPU)
1. On pve01: `./setup.sh shutdown` (asks you to type `yes`): pauses agents (the kill switch, so no
   session is cut mid-turn), then `shutdown -h now`: Proxmox stops the guests in reverse boot order
   (cloudflared first, then VM 200 through its guest agent, ... auth-service last).
2. (By hand instead: `ssh agent-host agent stop`, then pve01 → Shutdown in the UI.)
3. Power off at the wall only after pve01 is off; install the hardware.
4. Power on. In the BIOS, check once that the new card didn't change the boot device or "Restore on
   AC Power Loss" (step 2 above), and that SVM / IOMMU stay enabled (needed to pass a GPU to a VM).
5. On pve01: `./setup.sh` (unseal, resume agents, check). Then "Test reboot" step 7.

## Test reboot (to do once)
1. Pick a quiet time; nothing runs that needs to be stopped first.
2. `ssh pve01 reboot` (or the Proxmox UI: pve01 → Reboot).
3. After ~3 minutes (VM 200 waits its turn, then boots its own kernel): the checks above; all except
   OpenBao should be up without any action.
4. Unseal OpenBao (this also activates the pending "no client-certificate prompt" setting).
5. Re-run the deploy scripts of auth-service and file-management for their smoke tests (the
   pipeline's services get theirs on their next deploy).
6. agent-service: within 10 minutes of the unseal, the status page shows its login
   `saved_to_openbao: true`; if the login was over 6 hours old, its log shows "login refreshed" then
   "login saved to OpenBao" (CP3's last open check).
7. Record the date and anything that didn't come back here.

## Adding a new service
Follow [lxc-setup.md](../lxc-setup.md): the container (`--onboot 1 --startup order=<n>`, with `<n>`
after everything it depends on), a DHCP reservation, Docker with `restart: unless-stopped`. Then add
it to the tables and checks above.

## Not covered
The Pi (pi-server, hey-pi) is separate hardware with its own systemd services; the GPU server is
currently shut down. A new GPU in pve01 gets its own entry here once it's given to a guest.
