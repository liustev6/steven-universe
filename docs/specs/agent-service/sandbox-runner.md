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
        ├── sandbox-N       agent-runtime under runsc; reaches only egress-proxy-N, by IP (R11)
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
| R8 | **The sandbox image is pinned by digest** in the runner's `config.env`. Updating it is a config-only deploy of the runner: the pool drains and refills with the new image. The VM pulls it from GHCR; sandboxes never pull anything. | The same image-by-digest rule as every service (deployer D1). |
| R9 | **The runner's state is disposable.** On start, it destroys every sandbox it finds and rebuilds the pool; sessions that had a sandbox fail, and agent-service reports it. | No unknown leftovers. Recovering an in-flight sandbox isn't worth the risk at home scale. |
| R10 | **One mounter per sandbox** (milestone 2, [files-and-mounts.md](files-and-mounts.md) F2): at claim, the runner starts a small rclone container in the sandbox's pod (its network, through its proxy), with the session's locations, and bind-mounts its FUSE mounts **read-only** into the sandbox at `/workspace/inputs` (`work/` and `outputs/` are local). It is destroyed with the sandbox. | The sandbox gets files without FUSE privileges. The mounter holds only the session capability. |
| R11 | **Sandboxes have no DNS: they get the gateway (later, egress-proxy) by IP address** in the session file. | Measured in CP2: under gVisor, Docker's embedded DNS (`127.0.0.11`) doesn't work, because gVisor's own network stack bypasses the firewall rules it relies on. No name resolution is also one less way out (2.7 checks it). |
| R12 | **One pod per sandbox: its own network and its own egress proxy** (the sidecar pattern, as in a Kubernetes pod). The runner creates the network (a /28 from a pool; the proxy at `.2`), starts the proxy from a stock nginx image (pinned by digest) with a config it generates (this pod's sandbox id; the gateway as the only upstream, TLS-verified; any `X-Sandbox-Id` from the sandbox replaced; no buffering, so replies stream), then the sandbox. All of it during warm-up, so a claim stays instant; all of it destroyed together. | **Identity is configuration, not a lookup:** the proxy was started for one sandbox, so the stamp can't be wrong or forged, with no table of IPs to trust. **Sandboxes can't reach each other** (or each other's mounters). A failed proxy affects one sandbox. Chosen in CP2 over one shared proxy, with sandboxes identified by source IP, or one proxy joined to every sandbox's network. |

## API (for agent-service only)
HTTPS on the VM. It's authenticated with an auth-service token: audience `sandbox-runner`, client
`agent-service`.

| Route | What |
|---|---|
| `POST /v1/sandboxes/claim` `{session_id, capability, profile_limits}` | hand over a warm sandbox (R7); returns `sandbox_id`; `503` if the pool is empty and the cap is reached |
| `POST /v1/sandboxes/{id}/release` | end of session: stop and destroy it, then start a replacement |
| `GET /v1/sandboxes` | the pool: warm, claimed, starting; image digest |
| `GET /health` | Docker and gVisor available; the pool at its target |

There's no route to run a command in a sandbox, read its files or change its network. The runner
can only create and destroy.

## Lifecycle
```
starting ──(agent-runtime ready)──▶ warm ──claim──▶ claimed ──release or timeout──▶ destroyed
    └────────────── failed to start ─────────────────────────────────────────────▶ destroyed
```
- **Pool size:** `RUNNER_WARM` sandboxes ready (start: 2) and `RUNNER_MAX` in total (start: 4). A
  claim beyond the cap gets `503`, and agent-service tells the user to try again shortly.
- **Hard timeout:** a claimed sandbox is destroyed after the session's wall-time limit even if
  agent-service never releases it.
- **Deploying the runner:** it stops taking claims, waits for claimed sandboxes up to a timeout,
  then restarts (R9).

## The worker VM
| Item | Plan |
|---|---|
| VM | Debian 13, for example 4 vCPU, 8 GB RAM, 40 GB disk; starts after agent-service ([boot.md](../boot.md)) |
| Software | Docker CE with the Compose plugin; gVisor's `runsc` registered as a Docker runtime |
| Network | DHCP reservation; `sandbox-runner.lan.liustev6.ca` for the API; Proxmox firewall per R5 |
| Deploys | like every service: the deploy wrapper, `infra/services/sandbox-runner/deploy.yaml`, joins the pipeline in wave 1 |

The setup steps follow [lxc-setup.md](../../lxc-setup.md), adapted for a VM: `qm` instead of `pct`,
plus the gVisor install. A runbook gets written when it's built.

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
- Should egress-proxy also enforce per-profile web allowlists (for profiles allowed to browse), or
  does that stay entirely in the gateway?
