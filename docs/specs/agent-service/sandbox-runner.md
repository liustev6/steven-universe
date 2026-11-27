# Spec: sandbox-runner (who runs the sandboxes)

**Status: live** (decided 2026-11-09, agent-service decision 1; built and locked by CP2, 2026-11-13).
Deployed on VM 200 by the pipeline (wave 1); behind the Proxmox firewall (R5). The decisions below
are measured, not proposed ([tracker.md](tracker.md) CP2). Since CP3 (2026-11-17) its caller is
agent-service, and the pods' proxies forward to agent-service's gateway
(`https://agent.lan.liustev6.ca:8443`); the CP2 results below were measured with a temporary dev
gateway in that place.
The worker VM exists (VM 200, [sandbox-worker-setup.md](../../sandbox-worker-setup.md)); gVisor
measured (below).
**Paths:**
- `python/services/sandbox-runner/`: the runner (`app/pool.py` the pods, `egress.py` the proxy's config,
  `mounts.py` the mounters' folders, `backend.py` Docker);
- `infra/services/sandbox-runner/`: manifest, config and `templates.yaml`.
The proxy and the mounter are stock images, configured at start (no Dockerfile of ours): "A pod in
detail" below.

Part of [agent-service](README.md). Related: [deployer.md](../deployer.md), [lxc-setup.md](../../lxc-setup.md)
(host setup pattern), [boot.md](../boot.md).

## What it is
sandbox-runner **creates, keeps warm, hands out and destroys** the sandboxes agent sessions run
in. It runs on a dedicated **VM**, the sandbox worker, and starts each sandbox as a **Docker
container under gVisor** inside that VM. It decides nothing about sessions: agent-service tells it
when to hand out a sandbox and when to destroy one.

```
pve01
└── VM "sandbox-worker"               own kernel: the security boundary
    ├── Docker, with gVisor (runsc) installed as a runtime
    ├── sandbox-runner    container; uses the VM's Docker socket; HTTPS API for agent-service
    └── pod-1 … N         one per sandbox (R12): created together, destroyed together
        ├── network pod-N   --internal, its own small subnet; no route out, no other pod on it
        ├── egress-proxy-N  stock nginx; on pod-N and the VM's network; stamps "sandbox-N" and
        │                   forwards only to agent-service's gateway
        ├── sandbox-N       agent-runtime under runsc; reaches only egress-proxy-N, by IP (R11);
        │                   /workspace = /srv/workspaces/<id> on the XFS disk, under a quota (R13)
        └── mounter-N       rclone mount of the session's files (the gateway's /dav), bind-mounted
                            read-only into sandbox-N at /workspace/inputs (R10, CP6)
```

## Decisions
| ID | Decision | Why |
|---|---|---|
| R1 | **The runner carries out, agent-service decides.** agent-service picks the profile and session, and when a session ends; the runner creates, claims, releases and destroys sandboxes and keeps the warm pool. agent-service never talks to Docker. | Policy and mechanism apart, as with a Kubernetes control plane and the kubelet, or GitHub and an Actions runner. The service holding credentials never also controls Docker. |
| R2 | **The worker is a VM** (Proxmox QEMU), not an LXC. | An LXC shares pve01's kernel: a sandbox escape would be one step from the whole host. A VM has its own kernel, so an escape from gVisor *and* Docker lands in a machine that holds nothing valuable. gVisor also works poorly nested in an unprivileged LXC. |
| R3 | **The runner is a container on the VM,** with the VM's Docker socket mounted. That makes it **root on the VM**; the container is for packaging, not isolation. | It deploys like every service: manifest, pipeline, `deploy-target`, `compose.yaml`. Root on a VM that exists only for sandboxes is acceptable. The socket is never exposed on the network: agent-service reaches only the runner's API. |
| R4 | **Sandboxes are Docker containers under gVisor** (`--runtime=runsc`, the systrap platform, which needs no nested virtualization): read-only root, non-root uid, no capabilities, `no-new-privileges`, CPU, memory and process limits, size-limited `/tmp` and `/workspace`. | gVisor puts its own user-space kernel between model-written code and the VM's kernel; the container settings remove the rest of the attack surface. |
| R5 | **Sandboxes reach only agent-service's gateway.** Each sits on **its own** `--internal` Docker network, with no route out; its pod's **egress-proxy** is the only other member and forwards only to the gateway, stamping the sandbox's id on each request (R12). **A second layer, applied (CP2 2.7):** the Proxmox firewall at VM 200's network card (`infra/hosts/pve01/firewall/200.fw`): out, only DNS, auth-service and the internet (no LAN: no pve01, router admin, OpenBao, file-management, RustFS); in, only pve01 and the deployer. Checked by 21 probes: before it, root on the VM reached pve01's admin page and SSH, OpenBao, RustFS and more. **To tighten in CP3:** the internet (443/80) is open for image pulls, packages, certificates and, while it lives on the VM, the dev gateway's model calls. | A sandbox can't reach the LAN, the internet, pve01 or the router, whatever runs in it. The gateway can check that a session capability is used from its own sandbox. |
| R6 | **Single-use, from a warm pool.** The runner keeps N sandboxes started, with `agent-runtime` loaded and waiting. A claim hands one over in milliseconds; a released sandbox is destroyed and replaced, never reused. | Warm keeps voice responses fast; single-use means nothing carries over between sessions. |
| R7 | **The session is handed over by file, not network.** At claim, the runner writes the session config (session id, capability, gateway URL) into that sandbox's bind-mounted `/run/session/` (on the VM's tmpfs); `agent-runtime` waits for that file, then starts. | A warm sandbox was started before its session existed; this delivers the session without opening any port into the sandbox. |
| R8 | **Sandbox images are built by CI and rolled out automatically.** CI builds `agent-runtime` like a service image (named by the last commit touching it); the runner's manifest lists it as an input, so a change to it redeploys the runner, whose templates (R14) name the new image, pinned by digest at deploy. The redeploy rebuilds the whole pool from the new image (pods are never updated in place); the deploy then removes the image's older versions (a rollback pulls again, in seconds, like every service's). The VM pulls from GHCR; sandboxes never pull anything. | The same image-by-commit and by-digest rules as every service (deployer D1, D1a), with no hand-edited digests. |
| R9 | **The runner's state is disposable.** On start, it destroys every sandbox it finds and rebuilds the pool; sessions that had a sandbox fail, and agent-service reports it. | No unknown leftovers. Recovering an in-flight sandbox isn't worth the risk at home scale. |
| R10 | **One mounter per pod** (CP6, [files-and-mounts.md](files-and-mounts.md) F2, F5, F9; built 2026-11-27, 6.6): the pod's fifth part, started **at warm-up** after its proxy and before its sandbox: the stock rclone image (`MOUNTER_IMAGE`, `rclone/rclone:1.69.1`) with `/dev/fuse`, `SYS_ADMIN` and AppArmor unconfined (nothing else of the hardening removed), in the pod's network at `.4`, mounting the gateway's `/dav` through the pod's proxy, read-only. Its FUSE mount, `/run/sandbox-runner/mounts/<pod>/inputs`, reaches the VM through an `rshared` bind; the warm-up waits for it (`MOUNT_TIMEOUT_SECONDS`), then the sandbox binds it **read-only** at `/workspace/inputs` (`work/` and `outputs/` are local, R13). **Identical for every pod:** its token is read from `<session dir>/capability`: written empty at warm-up (rclone reads it once at start, and a failing token command is fatal), replaced by the claim (before `session.json`); until then nothing is read. A pod is warm in ~5.8 s with its mounter (was ~1 s). rclone runs with `--low-level-retries 3` (its default 10): the pre-claim 401s otherwise push its pacer's delay to 2 s, and a session's first listing took 5.5 s; with 3, 377 ms (6.8). And `--disable About`: rclone never asks for the disk size, so a warm pod sends nothing before the claim (no 401s at all); a session's first listing 128 ms. One 401 remains right after the claim: rclone re-reads its token file only when the server rejects the token it holds. The runner binds `/run/sandbox-runner` `rshared` itself, so it sees the mounts. **Teardown:** the sandbox, then the mounter stopped with SIGTERM (rclone unmounts; SIGKILL after 10 s), then anything still mounted unmounted (`umount2(MNT_DETACH)`) and the folder removed; leftover mounts swept on start (R9). A warm pod whose mounter stops is replaced, as for its proxy. `MOUNTER_IMAGE` empty: no mounter, no `/workspace/inputs`. | The sandbox gets files without FUSE privileges; the claim stays instant; the runner never learns a session's files. The mounter holds only the session capability. |
| R11 | **Sandboxes have no DNS: they get the gateway (later, egress-proxy) by IP address** in the session file. | Measured in CP2: under gVisor, Docker's embedded DNS (`127.0.0.11`) doesn't work, because gVisor's own network stack bypasses the firewall rules it relies on. No name resolution is also one less way out (2.7 checks it). |
| R12 | **One pod per sandbox: its own network and its own egress proxy** (the sidecar pattern, as in a Kubernetes pod). The runner creates the network (a /28 from a pool; the proxy at `.2`; **`inhibit_ipv4`**, so the network's bridge has no address on the VM: otherwise a sandbox reaches the VM's own ports, its SSH included, as CP2 found), starts the proxy from a stock nginx image (pinned by digest) with a config it generates (this pod's sandbox id, a UUID the runner makes and never takes from a request; the gateway as the only upstream, TLS-verified; any `X-Sandbox-Id` from the sandbox replaced; no buffering, so replies stream), then the sandbox. All of it during warm-up, so a claim stays instant; all of it destroyed together. | **Identity is configuration, not a lookup:** the proxy was started for one sandbox, so the stamp can't be wrong or forged, with no table of IPs to trust. **Sandboxes can't reach each other** (or each other's mounters). A failed proxy affects one sandbox. Chosen in CP2 over one shared proxy, with sandboxes identified by source IP, or one proxy joined to every sandbox's network. |
| R13 | **Workspaces live on a dedicated XFS disk with a quota per sandbox** (`/srv/workspaces` on VM 200, mounted with `prjquota`). At warm-up, the runner creates the pod's directory (`work/`, `outputs/`, owned by the sandbox's uid), gives it an XFS project quota (default 10 GB) and bind-mounts it at `/workspace`; it's deleted with the pod. `/tmp` stays in RAM, capped (512 MB). The mounter's rclone cache (milestone 2) goes under the same pod's quota. | Real tasks (a cloned repo, a dataset) don't fit in RAM, and one task must not fill the VM. The quota is enforced by the VM's kernel, so it holds under gVisor: **measured in CP2**, a sandbox writing past a 20 MB quota stopped at exactly 20 MB with "No space left on device". The disk is separate from the system disk, so a full workspace disk can't take the VM down. The same mechanism as Docker's and Kubernetes' per-container storage limits. Not chosen: workspaces in RAM (they eat memory), a filesystem image per sandbox (one more filesystem and a loop device each). |
| R14 | **Templates: the kinds of sandbox, by name** (`templates.yaml` next to the runner's config: image, runtime, warm count, cap, memory, CPU, `/tmp`, workspace quota). **A claim names a template, never an image or a path:** the image and every mount are fixed when the pod is created, from the runner's reviewed file; the claim only writes the session file. Which template a session gets is its profile's choice (agent-service, R1). **Capacity:** each template keeps its `warm` pods ready, at most **3 per template** (`MAX_PER_TEMPLATE`, or the template's own `max`) and **6 in all** (`MAX`); a template with `warm: 0` is started on demand (~1 s). **When the VM is full, a claim evicts a warm spare of another template** (the one with the most spares; never a claimed pod), so idle capacity never blocks real demand; an evicted pod's subnet stays taken until its teardown ends. | Different work needs different images and sizes (chat: small and fast; coding: more tools, room, memory). A caller that could name an image or a host path could take over the VM; a name looked up in the runner's own file can't. The per-template cap keeps one kind from crowding out the others. Measured live: a warm claim 6 ms, an on-demand one 1.1 s, an unknown name refused (400), limits and quotas per template. |

## API (for agent-service only)
HTTPS on the VM. It's authenticated with an auth-service token: audience `sandbox-runner`, client
`agent-service`.

| Route | What |
|---|---|
| `POST /v1/sandboxes/claim` `{template, session_id, capability, profile}` | hand over a warm sandbox of that template (R14): write its session file (R7), with the pod's proxy as `gateway_url`; returns `sandbox_id`, `gateway_url`. If none is warm, starts one (evicting another template's spare if the VM is full) and waits up to `CLAIM_WAIT_SECONDS`; `400` unknown template; `503` (`Retry-After`) if none |
| `POST /v1/sandboxes/{id}/release` | end of session: stop and destroy it, then start a replacement |
| `GET /v1/sandboxes` | the pool per template: warm, starting, claimed, cap, image |
| `GET /health` | Docker and gVisor available; the pool at its target |

There's no route to run a command in a sandbox, read its files or change its network. The runner
can only create and destroy.

## Lifecycle
```
starting ──(agent-runtime ready)──▶ warm ──claim──▶ claimed ──release or timeout──▶ destroyed
    └────────────── failed to start ─────────────────────────────────────────────▶ destroyed
```
- **Pool size:** per template, its `warm` pods ready, at most 3 of it, and 6 in all (R14). A claim
  beyond the caps gets `503`, and agent-service tells the user to try again shortly.
- **Hard timeout:** a claimed sandbox is destroyed after the session's wall-time limit even if
  agent-service never releases it.
- **Deploying the runner:** it restarts at once and **running sessions are lost**: the old runner's
  shutdown destroys every pod, the new one cleans anything left (R9) and warms a fresh pool.
  agent-service reports the lost sandbox; the caller starts a new session, which picks up the
  conversation once history exists (decision 2). Chosen over draining: simpler, and deploys are rare.

## The worker VM
| Item | Plan |
|---|---|
| VM | Debian 13, for example 4 vCPU, 8 GB RAM, 40 GB disk; starts after agent-service ([boot.md](../boot.md)) |
| Software | Docker CE with the Compose plugin; gVisor's `runsc` registered as a Docker runtime |
| Network | DHCP reservation; `sandbox-runner.lan.liustev6.ca` for the API; Proxmox firewall per R5 |
| Deploys | like every service: the deploy wrapper, `infra/services/sandbox-runner/deploy.yaml`, joins the pipeline in wave 1 |

The setup steps follow [lxc-setup.md](../../lxc-setup.md), adapted for a VM: `qm` instead of `pct`,
plus the gVisor install. A runbook gets written when it's built.

## A pod in detail: containers, host folders, network
Checked on VM 200 (2026-11-27). A pod isn't a Docker object: it's the runner's name for the
containers that share one network and one lifecycle (the idea of a Kubernetes pod), tied together
by their names (`proxy-`, `mounter-`, `sandbox-<id>`), labels, and network `pod-<id>`.

```
LAN 192.168.50.0/24
├── CT 107 agent-service: the gateway, https://agent.lan.liustev6.ca:8443   SERVER, outside the VM
├── CT 103 file-management                                                  SERVER, outside the VM
└── VM 200 "sandbox-worker" 192.168.50.95   (Debian, Docker, gVisor)
    ├── container sandbox-runner     SERVER: :8443, published as the VM's :443 (agent-service calls it)
    │     runc, privileged, the Docker socket; network sandbox-runner_default (172.18.0.0/16)
    │     creates and destroys everything below
    └── one pod (6 at most), created by the runner at warm-up
          network pod-<id>: a /28 of 172.29.0.0/16, internal, no address on the VM
          ├── container proxy-<id>     SERVER: nginx on :8080, for its own pod only      runc
          │     .2 on the pod network, and on sandbox-egress (its only way to the gateway)
          ├── container mounter-<id>   CLIENT: rclone (FUSE) → http://.2:8080/dav/     runc, SYS_ADMIN, /dev/fuse
          │     .4
          └── container sandbox-<id>   CLIENT: agent-runtime, Codex its child process  runsc (gVisor), no capabilities
                .3                    listens on nothing; calls only http://.2:8080/…
```

**The containers:**

| Container | Image | Built by | Configured by |
|---|---|---|---|
| `proxy-<id>` | `nginxinc/nginx-unprivileged:stable-alpine` (stock, `PROXY_IMAGE`) | NGINX | `nginx.conf` from `egress.py`, bound read-only over the image's own |
| `mounter-<id>` | `rclone/rclone:1.69.1` (stock, `MOUNTER_IMAGE`) | rclone | its command line (`mounts.py` `mounter_command`) and the session folder |
| `sandbox-<id>` | `agent-runtime`, by digest (`templates.yaml`) | our CI (`python/libs/agent-runtime/Dockerfile`, R8) | the session file it waits for (R7) |
| `sandbox-runner` | ours, by commit | our CI (`python/services/sandbox-runner/Dockerfile`) | `config.env`, `templates.yaml` |

The runner pulls the stock images itself at start. Every container gets a read-only root, no
capabilities, `no-new-privileges`, limits and capped logs; the mounter alone adds back `SYS_ADMIN`,
`/dev/fuse` and AppArmor unconfined (what FUSE needs, R10).

**The host folders:** directories on the VM, made by the runner and bind-mounted into the pod's
containers at fixed paths. They are how the runner talks to a container (it writes a file; it never
opens a connection into a pod or execs into one), and how containers share (the mounter's mount
reaches the sandbox only because both are given part of the same folder).

| On the VM | Into | As | Written by | Read by |
|---|---|---|---|---|
| `/srv/workspaces/<id>/` (XFS, a quota) | sandbox | `/workspace` (rw) | the agent | the agent (`work/`, `outputs/`; R13) |
| `/run/sandbox-runner/sessions/<id>/` | sandbox, mounter | `/run/session` (ro) | the runner: `capability` empty at warm-up; at the claim `capability`, then `session.json` | agent-runtime, rclone (R7, R10) |
| `/run/sandbox-runner/proxies/<id>.conf` | proxy | `/etc/nginx/nginx.conf` (ro) | the runner, at warm-up | nginx |
| `/run/sandbox-runner/mounts/<id>/` | mounter | `/mnt` (rw, rshared) | rclone's FUSE mount | the VM, the runner (bound rshared too) |
| `…/mounts/<id>/inputs` | sandbox | `/workspace/inputs` (ro) | rclone | the agent |

`/run` is a tmpfs: a reboot leaves none of these behind; the workspace is deleted with its pod.

**Who wires it together: the runner, at warm-up** (`pool.py` `_create`), in order: the workspace;
the network; the proxy's config (the **real** gateway URL, the runner's `GATEWAY_URL`, and the pod's
id) and the proxy on both networks; the mounter, pointed at the proxy; the sandbox. At the claim it
writes `session.json`, whose `gateway_url` is **the proxy's address** (`http://<.2>:8080`,
`Pod.gateway_url`): the sandbox and Codex call it "the gateway" but never learn the real one, which
only the proxy's config holds. And they can't go around it: the pod network is `internal` (no route,
no NAT), so from `.3` only `.2` (and `.4`) answer. The proxy forwards every request, whatever its URL
or Host, to the one upstream, replacing `X-Sandbox-Id` with its pod's id, verifying the gateway's
TLS certificate and streaming both ways (R12); the gateway pairs the capability with that stamp (C7).

**The networks on VM 200:**

| Network | Kind | On it | Reaches |
|---|---|---|---|
| the VM's interface | 192.168.50.95 | the VM | the LAN, behind the Proxmox firewall (R5) |
| `sandbox-runner_default` | Docker bridge | the runner | the LAN (NAT); its `:8443` published as the VM's `:443` |
| `sandbox-egress` | Docker bridge | **only the proxies** | the LAN (NAT): the gateway |
| `pod-<id>`, one per pod | Docker bridge, `internal`, a /28 | its proxy `.2`, sandbox `.3`, mounter `.4` | **only each other** |

- **No address on the VM** for a pod's bridge (`inhibit_ipv4`; checked: no `172.29.x` address on
  the VM): a sandbox can't reach the VM's own ports, its SSH included (CP2 found that hole).
- **One bridge per pod:** pods can't see each other or each other's mounters.
- **The proxy is the only container on two networks:** the pod's one door. It resolves the
  gateway's name through Docker's DNS (`127.0.0.11`).
- **A request's path** (Codex asking the model): sandbox `.3` → `http://.2:8080/model/responses` →
  proxy (+ `X-Sandbox-Id`) → `sandbox-egress`, NAT out of 192.168.50.95 →
  `https://agent.lan.liustev6.ca:8443` (CT 107) → the gateway checks the capability and the stamp →
  OpenAI with your login. rclone's `/dav` takes the same road from `.4`; the gateway, not the pod,
  then reads file-management (CT 103) with a token exchanged for the session's caller.

## Measured through the deployed runner (CP2 2.8, 2026-11-13)
The dev gateway (agent-service's stand-in) claimed from `https://sandbox-runner.lan.liustev6.ca`
over TLS with its auth-service token:

| | |
|---|---|
| a claim of a warm `chat` pod (token arrived → response) | **26 ms** |
| claim → `runtime.ready` inside the sandbox (Codex starting under gVisor) | **523 ms** |
| a claim of `dev`, none warm (a pod built from scratch, 20 GB quota) | **834 ms** |
| a real turn in the claimed pod | works (first text 2.6–3.3 s with the `general` profile; `voice` measured 1.6–2.6 s) |
| theft test: the capability from another pod, with the victim's id forged | **403**; from its own pod, 200 |
| the gateway stops | its pod is released; the pool refills |
| an `agent-runtime` change | rolls out by itself: CI builds it, the runner is redeployed (recreated) and its pool rebuilt from it; the old image removed |

## The runner, measured (CP2 2.4, 2026-11-12)
`python/services/sandbox-runner/` (`app/pool.py`, `backend.py`, `workspaces.py`, `api.py`; 28 tests
against a fake Docker), run live on VM 200 with the dev gateway as its gateway:

| | |
|---|---|
| warm-up of one pod (workspace + network + proxy + sandbox, runtime waiting) | **~1 s** |
| claim (the API call: writes the session file) | **10 ms** |
| claim to `runtime.ready` at the gateway (Codex starting under gVisor) | **647 ms** |
| a real turn through a claimed pod | Codex under gVisor → the pod's proxy → the gateway → the model: works |
| release | nothing left (containers, network, workspace, session file); the pool refills |
| `kill -9` mid-warm-up, then restart (R9) | 5 containers, 3 networks, 3 workspaces found and removed; a fresh pool |
| graceful stop | every pod destroyed |
| API auth (2.5) | auth-service tokens (audience `sandbox-runner`, a scope per route): no or forged token 401; the dev gateway claims and releases with its own token |
| theft test (2.5) | this session's capability used from another pod, even with the victim's `X-Sandbox-Id` sent: **403** (that pod's proxy re-stamps its own id); from its own pod: 200 |

A pod counts as warm only when its sandbox waits for its session file **and** its proxy runs; a
warm pod whose proxy stops is replaced.

## The pod, checked (CP2, 2026-11-12)
`python/services/sandbox-runner/scripts/pod_check.py` builds two pods by hand on the worker VM
(network, nginx proxy from `app/egress.py`, a sandbox under gVisor) and a stand-in gateway that
echoes headers. From inside each sandbox, all pass:
- the gateway receives **exactly one** `X-Sandbox-Id`, the pod's own, though the sandbox sent forged
  ones (`X-Sandbox-Id`, `x-sandbox-id`);
- a request for another host (`GET http://1.1.1.1/…`) still reaches only the gateway;
- replies stream (first chunk in under 0.5 s of a 1.5 s reply);
- unreachable: the gateway directly, the other pod's proxy, the internet; and no DNS.

## gVisor, measured (CP2, 2026-11-12)
On the worker VM (8 vCPU, amd64), the same session under each runtime: the `voice` profile, two
turns, an interrupt, then a benchmark inside the sandbox.

| | runc (no gVisor) | **runsc, systrap** (chosen) | runsc, KVM |
|---|---|---|---|
| startup to `ready` | 310 ms | **590 ms** | 1,727 ms |
| first text (cold / warm) | 2.0 / 2.6 s | **2.4 / 2.1 s** | 2.1 / 1.9 s |
| interrupt | 3 ms | **5 ms** | 7 ms |
| memory idle / after turns | 84 / 96 MiB | **108 / 117 MiB** | 110 / 119 MiB |
| 3,000 small files / Python imports / CPU loop | 0.15 / 0.13 / 0.21 s | **0.55 / 0.16 / 0.22 s** | 1.44 / 0.38 / 0.25 s |

- **gVisor's cost is small where agents spend their time:** first text is set by the model, and
  memory adds ~20 MiB. It shows on **syscall-heavy work** (3.7× on many small files), which matters
  for builds and big checkouts, not for chat or home control.
- **systrap, not KVM:** in a VM, the KVM platform runs on nested virtualization and is slower on
  every measure here (startup 3×, files 2.6× systrap). The open question is closed.
- **Codex's own tools work under gVisor:** a shell command reports the `4.19.0-gvisor` kernel.

## Not chosen
| Option | Why not (now) |
|---|---|
| Sandboxes as LXCs created by Proxmox | slow to start, hard to keep warm, and they share pve01's kernel |
| One microVM per sandbox (Firecracker, Kata) | the strongest isolation, but much more to build and run. **Upgrade path:** only the runner changes; the API, agent-service and `agent-runtime` stay as they are. |
| The runner as a systemd service on the VM | the same power on the VM, but outside the "everything deploys through the pipeline" pattern |
| agent-service starting containers itself | it would hold credentials and control Docker at once |

## Open questions
- **Pin the stock images by digest:** the sandbox image is pinned by digest, but `PROXY_IMAGE`
  (`stable-alpine`, a moving tag) and `MOUNTER_IMAGE` (`1.69.1`, a version tag that could be
  re-pushed) only by tag; `image@sha256:…` would make every pod's proxy and mounter reproducible
  (R12 says the proxy is "pinned by digest"; the code doesn't, yet).
- VM size and pool sizes, after measuring what a sandbox needs (Codex and its tools).
- **Network for tools** (decided in outline, built later): web search is Codex's server-side tool
  (no sandbox network); screenshots of an app inside the sandbox need a browser template, not a
  network; fetching real sites needs an allowlisting forward proxy with private-range (SSRF)
  checks after DNS, or the browser as a tool outside the sandbox. Either needs the Proxmox
  firewall (R5's second layer) first. Where the allowlist lives (the pod proxy, the gateway, or a
  dedicated egress proxy) is decided then.
