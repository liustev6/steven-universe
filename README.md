# Steven Universe - Monorepo for Steven

My personal monorepo: a home lab on one Proxmox host (pve01) and a Raspberry Pi, built as a place to
learn how real systems are designed. It holds an **agent platform** (sandboxed agent sessions paid by
my ChatGPT plan), **Hey Pi** (a voice and touch home panel on the Pi: say "Hey Pi", ask, hear the
answer), the services they stand on (tokens, secrets, object storage, deploys, speech on the GPU),
and my personal website. Where there is an industry-standard design (OAuth 2.0, RFC 9457 errors,
Server-Sent Events, S3), the lab uses it rather than a home-grown shortcut, and every decision is
written down in a spec under [docs/specs](docs/specs/).

Status as of 2026-11-18.

## Repository layout

```
steven-universe/
├── python/
│   ├── services/        # the lab's services (one folder each; table below)
│   ├── libs/            # agent-runtime (inside every sandbox), service-auth (tokens), shared-schemas
│   └── workers/         # gpu-server's worker images (archived with gpu-server)
├── dart/apps/hey-pi/    # Hey Pi, the Flutter panel app (Raspberry Pi 5, flutter-pi)
├── js/
│   ├── apps/personal-website-new/   # personal website (Next.js, on Vercel)
│   └── libs/shared-schemas/         # the website's shared types
├── infra/
│   ├── services/        # one deploy manifest per service (deploy.yaml, config.env, hooks)
│   ├── deployment/      # deploy scripts run from the Mac (manifest-driven, and the older per-service ones)
│   ├── secrets-manager/ # OpenBao: container, config, OpenTofu policies, runbook
│   ├── hosts/pve01/     # host setup (setup.sh) and VM 200's Proxmox firewall
│   └── images/          # shared base images (agent-runtime, speech-base)
├── experiments/python/ # the playground's (CT 110) work: wake-word training, speech-service's measuring tools;
│                       # each with results/ (spikes' write-ups, raw numbers, logs)
├── docs/
│   ├── specs/           # one spec per service or feature: design, decisions, core results, status
│   └── *.md             # host setup guides (LXC, GPU passthrough, sandbox worker VM)
├── models/3d-print/     # 3D-printable frame for the panel's screen
└── .github/workflows/   # build.yml (images to GHCR), deploy.yml (to the lab through the deployer)
```

## Services

**Live** = running and in use. **In progress** = being built now. **Legacy** = old code not yet
retired. **Archived** = kept for reference only, not deployed. "Pipeline" = deployed by the deployer
on a push to `main`; "by hand" = from the Mac with `infra/deployment/`.

| Service | What it does | Runs on | Status | Spec |
|---|---|---|---|---|
| [auth-service](python/services/auth-service/) | OAuth 2.0 token issuer for every service-to-service call; with the [service-auth](python/libs/service-auth/) library | CT 102 | Live since 2026-11-07, by hand (root of trust) | [auth-service.md](docs/specs/auth-service.md) |
| [secrets-manager](infra/secrets-manager/) (OpenBao) | Secrets for every deploy; policies as code (OpenTofu) | CT 104 | Live since 2026-11-08, by hand | [secrets-manager.md](docs/specs/secrets-manager.md) |
| RustFS | S3-compatible object storage; file-management is its only user | CT 101 | Live since 2026-11-06 | [rustfs.md](docs/specs/rustfs.md) |
| [file-management-service](python/services/file-management-service/) | The only service that talks to object storage: one `/objects/{bucket}/{key}` API, access from the caller's token | CT 103 | Live since 2026-11-08, pipeline (wave 1) | [file-management.md](docs/specs/file-management.md) |
| [deployer](python/services/deployer/) | CI/CD: takes GitHub Actions deploy requests and deploys `pipeline: true` services to their hosts | CT 105 | Live since 2026-11-08, by hand | [deployer.md](docs/specs/deployer.md) |
| [api-service](python/services/api-service/) | The public edge (Cloudflare tunnel, `api.liustev6.ca`): health, deploy routes, the lab's status page `/v1/status`, the relay to agent-service | CT 106 | Live since 2026-11-08, minimal; pipeline (wave 2) | [api-service.md](docs/specs/api-service.md) |
| [agent-service](python/services/agent-service/) | The control plane: sessions API (SSE), the sandboxes' gateway, the auth broker for the ChatGPT login, PostgreSQL history | CT 107 | Live since 2026-11-17 (CP3), pipeline | [agent-service](docs/specs/agent-service/README.md), [control-plane.md](docs/specs/agent-service/control-plane.md) |
| [sandbox-runner](python/services/sandbox-runner/) | Runs agent sandboxes as pods under gVisor, behind the Proxmox firewall | VM 200 | Live since 2026-11-13 (CP2), pipeline | [sandbox-runner.md](docs/specs/agent-service/sandbox-runner.md) |
| [agent-runtime](python/libs/agent-runtime/) (library) | What runs inside every sandbox pod: the agent on the Codex SDK | sandbox pods on VM 200 | Live since 2026-11-12 (CP1) | [agent-runtime.md](docs/specs/agent-service/agent-runtime.md) |
| [speech-service](python/services/speech-service/) | Streamed text-to-speech (Qwen3-TTS 1.7B) and speech-to-text (Qwen3-ASR 0.6B), both kept loaded on the GPU | CT 108 (RTX 5060, 8 GB) | Live since 2026-11-18, pipeline | [speech-service.md](docs/specs/speech-service.md) |
| [tools-service](python/services/tools-service/) | The agents' tools (weather; Hey Pi's own): a REST API for apps and MCP for agents over one registry, each caller seeing only the tools its token grants | CT 109 | Live since 2026-11-26, pipeline | [tools.md](docs/specs/agent-service/tools.md) |
| [pi-server](python/services/pi-server/) | The Pi's local service: Hey Pi's WebSocket, the voice turn (mic → speech-to-text → agent → speech), the wake word (小派小派) | Raspberry Pi 5 | Live (voice turn and wake word since 2026-11-18); tells Hey Pi the speech state (since 2026-11-26, its only message); by hand (`infra/deployment/pi-server.sh`) | [voice-path.md](docs/specs/voice-path.md) |
| [Hey Pi](dart/apps/hey-pi/) (Flutter) | The panel's touch UI; talks only to pi-server | Raspberry Pi 5 | Live (CP4 4.14, 2026-11-26): follows pi-server's speech state with the assistant's character, and a voice button; the dashboard's data is the app's own mock until the protocol carries it | [flutter_ui.md](docs/specs/flutter_ui.md) |
| [wake-word](experiments/python/wake-word/) | Trains and tests the Pi's microWakeWord model: 小派小派 (live), "Hey Pi" (retired) | the playground (CT 110) | Live since 2026-11-19: model 04f at 0.96; a fixed test set for every model | [wake-word.md](docs/specs/wake-word.md) |
| [web-gateway](python/services/web-gateway/) | The old API gateway | not running | Legacy: its routes are ported to api-service one at a time, then it's deleted | [api-service.md](docs/specs/api-service.md) "Migration" |
| [stevenai-service](python/services/stevenai-service/) | RAG chatbot about me (FAISS, BGE embeddings) | not among pve01's guests in [boot.md](docs/specs/boot.md) | Legacy: retired by CP8 (knowledge in agent-service) | [tracker.md](docs/specs/agent-service/tracker.md) |
| [gpu-server](python/services/gpu-server/) (+ [workers](python/workers/gpu-server/)) | Session-based GPU task service | not deployed | Archived 2026-11-17: the GPU serves only speech; a GPU service will be redesigned when the lab has several GPUs | its [README](python/services/gpu-server/README.md) |
| [personal website](js/apps/personal-website-new/) | Portfolio site (Next.js) | Vercel, https://liustev6.ca | Live | none |

Also on pve01: CT 100 `cloudflared` (the Cloudflare tunnel in front of api-service). Boot order and
recovery for every guest: [boot.md](docs/specs/boot.md).

## Roadmap

The build order is the agent-service checkpoints in [tracker.md](docs/specs/agent-service/tracker.md);
the voice part of CP4 is planned step by step in [voice-path.md](docs/specs/voice-path.md).

| CP | What | Status |
|---|---|---|
| 1–3 | the runtime, the sandbox worker, the control plane | Done (2026-11-12, 11-13, 11-17) |
| 4 | **voice:** speech-service, then pi-server → agent-service → speech-service on the Pi | **In progress** |
| 5 | tools ([tools.md](docs/specs/agent-service/tools.md)): tools-service (one MCP server, weather first), the gateway's `/mcp`, each session's tools = its owner's grant (a profile may narrow it), budgets, `end_conversation`; Home Assistant and confirmations later | **Live** (the core, 2026-11-26: weather and ending the conversation, on the Pi); Home Assistant and confirmations later |
| 6 | files and mounts ([files-and-mounts.md](docs/specs/agent-service/files-and-mounts.md)) | Planned |
| 7 | summaries and memory | Planned |
| 8 | knowledge (RAG over my documents); replaces stevenai-service | Planned |
| 9 | outside access (the phone through api-service) and operations (alerts, load test) | Planned |

**Where CP4 is now:** speech-service is live (4.1–4.6); on the Pi, the voice turn, the wake word and
the voice session are done (4.8–4.12). 4.12b's wake word is 小派小派 since 2026-11-19
([wake-word.md](docs/specs/wake-word.md)). 4.13 barge-in (the wake word over the reply) is done since 2026-11-25.
CP5's core went live in between (2026-11-26, [tools.md](docs/specs/agent-service/tools.md)): the Pi answers the
weather with our own tool and ends the conversation when you say goodbye. 4.14 Hey Pi shows the assistant is done (2026-11-26).
Next: 4.15 protocol B in shared-schemas and the agent's latency levers.

## Docs and deploys

- **Specs:** [docs/specs/](docs/specs/), one per service, each starting with a `**Status:**` line;
  agent-service's parts are in [docs/specs/agent-service/](docs/specs/agent-service/).
- **Experiments and their results:** [experiments/](experiments/): each one's code, and in its `results/` the
  spikes' write-ups, raw numbers and logs (speech-service's engine, the wake word); the specs keep the decisions
  and core numbers.
- **Host setup:** [lxc-setup.md](docs/lxc-setup.md) (a new service's container and its deployer
  onboarding), [lxc-gpu-passthrough-setup.md](docs/lxc-gpu-passthrough-setup.md),
  [sandbox-worker-setup.md](docs/sandbox-worker-setup.md), [boot.md](docs/specs/boot.md).
- **Deploying:** [infra/deployment/README.md](infra/deployment/README.md).

**How deploys work:** a push to `main` builds images to GHCR, and the deployer deploys every changed
service whose `infra/services/<service>/deploy.yaml` says `pipeline: true` (file-management,
sandbox-runner, agent-service, speech-service, then api-service). The roots of trust (auth-service,
OpenBao, the deployer) deploy by hand with `infra/deployment/deploy.sh <service>`. The Pi is by hand
too: `infra/deployment/pi-server.sh` and `dart/apps/hey-pi/scripts/run_deploy.sh`.
