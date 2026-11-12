# Spec: sandbox-runner (who runs the sandboxes)

**Status: decision locked** (2026-11-09, agent-service decision 1). Being built: CP2 ([tracker.md](tracker.md)).
The worker VM exists (VM 200, [sandbox-worker-setup.md](../../sandbox-worker-setup.md)); gVisor
measured (below).
**Planned paths:**
- `python/services/sandbox-runner/`: the runner;
- `python/services/egress-proxy/`, or a stock proxy with config: the sandboxes' only way out;
- `infra/services/sandbox-runner/`: manifest and config.

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
        └── mounter-N       milestone 2: rclone mount of the session's files, bind-mounted
                            read-only into sandbox-N at /workspace/inputs
```

## Decisions
| ID | Decision | Why |
|---|---|---|
| R1 | **The runner carries out, agent-service decides.** agent-service picks the profile and session, and when a session ends; the runner creates, claims, releases and destroys sandboxes and keeps the warm pool. agent-service never talks to Docker. | Policy and mechanism apart, as with a Kubernetes control plane and the kubelet, or GitHub and an Actions runner. The service holding credentials never also controls Docker. |
| R2 | **The worker is a VM** (Proxmox QEMU), not an LXC. | An LXC shares pve01's kernel: a sandbox escape would be one step from the whole host. A VM has its own kernel, so an escape from gVisor *and* Docker lands in a machine that holds nothing valuable. gVisor also works poorly nested in an unprivileged LXC. |
| R3 | **The runner is a container on the VM,** with the VM's Docker socket mounted. That makes it **root on the VM**; the container is for packaging, not isolation. | It deploys like every service: manifest, pipeline, `deploy-target`, `compose.yaml`. Root on a VM that exists only for sandboxes is acceptable. The socket is never exposed on the network: agent-service reaches only the runner's API. |
| R4 | **Sandboxes are Docker containers under gVisor** (`--runtime=runsc`, the systrap platform, which needs no nested virtualization): read-only root, non-root uid, no capabilities, `no-new-privileges`, CPU, memory and process limits, size-limited `/tmp` and `/workspace`. | gVisor puts its own user-space kernel between model-written code and the VM's kernel; the container settings remove the rest of the attack surface. |
| R5 | **Sandboxes reach only agent-service's gateway.** Each sits on **its own** `--internal` Docker network, with no route out; its pod's **egress-proxy** is the only other member and forwards only to the gateway, stamping the sandbox's id on each request (R12). A second layer: the Proxmox firewall on the VM's interface allows only the gateway, GHCR (the VM's image pulls) and the runner's API from agent-service. | A sandbox can't reach the LAN, the internet, pve01 or the router, whatever runs in it. The gateway can check that a session capability is used from its own sandbox. |
| R6 | **Single-use, from a warm pool.** The runner keeps N sandboxes started, with `agent-runtime` loaded and waiting. A claim hands one over in milliseconds; a released sandbox is destroyed and replaced, never reused. | Warm keeps voice responses fast; single-use means nothing carries over between sessions. |
| R7 | **The session is handed over by file, not network.** At claim, the runner writes the session config (session id, capability, gateway URL) into that sandbox's bind-mounted `/run/session/` (on the VM's tmpfs); `agent-runtime` waits for that file, then starts. | A warm sandbox was started before its session existed; this delivers the session without opening any port into the sandbox. |
| R8 | **Sandbox images are built by CI and rolled out automatically.** CI builds `agent-runtime` like a service image (named by the last commit touching it); the runner's manifest lists it as an input, so a change to it redeploys the runner, whose templates (R14) name the new image, pinned by digest at deploy. The redeploy rebuilds the whole pool from the new image (pods are never updated in place); old images are pruned, keeping the last two. The VM pulls from GHCR; sandboxes never pull anything. | The same image-by-commit and by-digest rules as every service (deployer D1, D1a), with no hand-edited digests. |
| R9 | **The runner's state is disposable.** On start, it destroys every sandbox it finds and rebuilds the pool; sessions that had a sandbox fail, and agent-service reports it. | No unknown leftovers. Recovering an in-flight sandbox isn't worth the risk at home scale. |
| R10 | **One mounter per sandbox** (milestone 2, [files-and-mounts.md](files-and-mounts.md) F2): at claim, the runner starts a small rclone container in the sandbox's pod (its network, through its proxy), with the session's locations, and bind-mounts its FUSE mounts **read-only** into the sandbox at `/workspace/inputs` (`work/` and `outputs/` are local). It is destroyed with the sandbox. | The sandbox gets files without FUSE privileges. The mounter holds only the session capability. |
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
- VM size and pool sizes, after measuring what a sandbox needs (Codex and its tools).
- **Network for tools** (decided in outline, built later): web search is Codex's server-side tool
  (no sandbox network); screenshots of an app inside the sandbox need a browser template, not a
  network; fetching real sites needs an allowlisting forward proxy with private-range (SSRF)
  checks after DNS, or the browser as a tool outside the sandbox. Either needs the Proxmox
  firewall (R5's second layer) first. Where the allowlist lives (the pod proxy, the gateway, or a
  dedicated egress proxy) is decided then.
