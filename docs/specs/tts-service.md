# Spec: tts-service (speech, streamed, from a model that stays loaded)

**Status: agreed** (2026-11-17). The first service on pve01's GPU, and the start of
[agent-service](agent-service/README.md)'s CP4 (voice): its build plan below **is** CP4's first
sub-checkpoints ([tracker.md](agent-service/tracker.md)). Related: [lxc-setup.md](../lxc-setup.md),
[boot.md](boot.md), [auth-service.md](auth-service.md), [deployer.md](deployer.md).

## What it is
A small service that turns text into speech and **streams the audio as it's made**, from **one
long-running process that keeps the model loaded** on the GPU. There's no cold load per request: the
model is loaded and warmed once at start, before the service reports healthy, and stays in VRAM until
the process stops (a deploy, a reboot).

```
caller (pi-server, a smoke test, later the voice path)
  └─(token, aud=tts-service)─▶ CT 108 :443  POST /v1/audio/speech {input, voice}
                                  │  one queue ─▶ one GPU worker ─▶ Qwen3-TTS 0.6B (held in VRAM)
                                  ◀═ audio chunks, streamed as each one is generated (PCM, 24 kHz)
pve01: NVIDIA driver (open kernel modules) ── /dev/nvidia* ──▶ CT 108 (unprivileged LXC) ──▶ Docker
```

**Goal (the first checkpoint's "done when"):** the service is up; a request's audio streams back in
packets, the first one quickly; consecutive requests never pay a model load; a restart is warm again
before it's healthy.

## Decisions
| ID | Decision | Why |
|---|---|---|
| T1 | **The GPU goes to an LXC container, CT 108**, not a VM: the NVIDIA driver on pve01, the device nodes given to the container with Proxmox's own `devN` entries (no raw `lxc.*` lines), the same driver's user-space libraries inside, Docker with the NVIDIA Container Toolkit (`no-cgroups = true`, as unprivileged containers need). | Lighter (no second kernel, no reserved VM memory), and the card can serve more than one container (speech-to-text next). The cost is weaker isolation than a VM: acceptable for our own trusted code, which is why **agent sandboxes never get the GPU**. |
| T2 | **The GPU serves only voice for now.** The old gpu-server is archived; a general GPU service is redesigned when there are several GPUs. | One GPU, one purpose: no scheduling between unrelated workloads. |
| T3 | **One process holds the model, loaded and warmed at start.** It loads the weights, runs a short warm-up synthesis (and captures CUDA graphs if the runtime uses them), and only then answers `/health`. Docker's health check gives it a long start period. It never unloads. | No cold load on any request: the goal. A restart costs one load, at deploy or boot time, not at a caller's. |
| T4 | **The model:** `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice` (preset voices; Chinese and English), **pinned to a Hugging Face revision**. Its weights live on CT 108's disk (`/var/lib/tts-service/models`), fetched once by the deploy (the pre-start hook, running the new image), checked against their hashes; at run time the service is offline (`HF_HUB_OFFLINE=1`). | About 2 GB of VRAM at FP16, of the card's 8 GB: room for speech-to-text later. Pinned weights are a reviewed input like any image; not baking them into the image keeps CI's images small. |
| T5 | **The API is OpenAI's speech API shape:** `POST /v1/audio/speech` `{input, voice, response_format, …}`, the audio streamed back in chunks (`Transfer-Encoding: chunked`). Default format **`pcm`**: raw 16-bit little-endian mono at the model's rate (24 kHz; confirmed in 4.7), the lowest-latency format; also `wav` (a streaming header, length unknown). | A known contract: clients and tools that speak OpenAI's TTS API work as they are. Raw PCM needs no encoder in the loop, so the first packet leaves as soon as it's generated. |
| T6 | **One GPU worker, one queue.** Requests are generated one at a time, in order; a short queue (4) beyond that is refused with `503` and `Retry-After`. **A caller that disconnects cancels its generation** (that's how the voice path stops speech when you interrupt). | One model on one GPU: parallel generation would only slow each other's first packet. Cancelling on disconnect frees the GPU at once (barge-in). |
| T7 | **Auth like the other services:** auth-service tokens, audience `tts-service`, scope `speech:create`; `status:read` for api-service's status page. Callers in CP4: `smoke-test` (deploy checks, which synthesize for real: it costs nothing but GPU time), then pi-server. | The same identity model as everything else (auth-service.md); no shared keys. |
| T8 | **Limits:** `input` at most 4,096 characters (OpenAI's); callers send **a sentence at a time**, so speech starts while the rest is still being written. | Short inputs keep the first packet early and the queue short. |
| T10 | **tts-service is an engine; pi-server orchestrates the voice pipeline; agent-service stays text-only.** pi-server (Hey Pi's backend) turns speech into text, sends the turn to agent-service, collects its streamed reply into sentences, and sends each sentence to tts-service as soon as it's complete, playing the audio in order while the agent is still writing (a chained, overlapping pipeline, the cascaded voice-agent design of Pipecat or LiveKit Agents). On barge-in it cancels the agent's turn and closes the audio stream. pi-server calls both directly on the LAN, with its own grants (`chat:*` at agent-service, `speech:create` here). | Each piece does one job: tts-service knows nothing of chat (any client can use it: the phone, a notification); agent-service carries no audio its text clients don't need; the conversation's timing (sentences, playback, barge-in) lives in one place. You hear the first word after the first sentence and its first audio packet, not after the whole reply. |
| T9 | **Deployed like every service:** its manifest, by hand first, then the pipeline; on the lab's status page (S rule): model and revision, warm or loading, VRAM used, queue length, recent first-packet times. | Nothing new to learn or operate. |

## API (443)
| Route | Scope | What |
|---|---|---|
| `POST /v1/audio/speech` `{input, voice, language?, instructions?, response_format?: "pcm" \| "wav", speed?}` | `speech:create` | audio, streamed as it's generated. `X-Sample-Rate`, `X-Request-Id` headers. `400` unknown voice or too long; `503` (`Retry-After`) queue full or still warming |
| `GET /v1/voices` | `speech:create` | the model's preset voices, with their languages |
| `GET /v1/status` | `status:read` | for the status page: `ok`, `warm`, model and revision, GPU name, VRAM used and total, queue length, generations so far, last first-packet times (ms) |
| `GET /health` | none | `200` once warm; `503` while loading (Docker's health check waits for it) |

## The container (CT 108)
| | |
|---|---|
| name, DNS, alias | `tts-service`, `tts.lan.liustev6.ca`, `tts-host` |
| size | 4 cores, 8 GB memory (weights pass through memory on load), 64 GB disk (the CUDA image, the weights) |
| boot | `onboot`, order 2 (it needs only auth-service, for token keys) |
| GPU | `dev0` … `devN`: `/dev/nvidia0`, `/dev/nvidiactl`, `/dev/nvidia-uvm`, `/dev/nvidia-uvm-tools`, `/dev/nvidia-caps/*` |
| inside | Debian 13, the NVIDIA user-space libraries (the host driver's exact version), Docker, the NVIDIA Container Toolkit; the service's image has PyTorch built for CUDA 12.8 or newer (the RTX 50 series needs it) |

**pve01's kernel and the driver:** the driver is a kernel module, built for each Proxmox kernel by
DKMS. pve01 **follows Proxmox's kernel updates** (decided in 4.2, not pinned): `proxmox-default-headers`
brings each new kernel's headers, DKMS builds the module for it at install, and `./setup.sh reboot`
refuses to reboot into a kernel the module isn't built for. The steps: [lxc-gpu-passthrough-setup.md](../lxc-gpu-passthrough-setup.md).

## Build plan (CP4's first sub-checkpoints)
| Sub-CP | What | Done when |
|---|---|---|
| 4.1 ✅ | **the design:** this document | reviewed and agreed (2026-11-17) |
| 4.2 ✅ | **the GPU on pve01:** NVIDIA's open driver 620.70.06 with DKMS, following Proxmox's kernel updates (not pinned) | `nvidia-smi` on pve01 shows the card; the module rebuilds for a new kernel. **Done 2026-11-17:** built for 7.0.2 and 7.0.14, booted 7.0.14 with the RTX 5060 working ([lxc-gpu-passthrough-setup.md](../lxc-gpu-passthrough-setup.md) Part 1) |
| 4.3 | **CT 108:** the container (lxc-setup part 1), the `devN` entries, the user-space libraries, Docker and the toolkit | `nvidia-smi` inside the container, and inside a CUDA container on its Docker |
| 4.4 | **identity:** auth-service's policy (audience `tts-service`; `smoke-test` and api-service's grants); its client secret | `explain` shows the grants; deployed |
| 4.5 | **the service:** the model holder (load, warm-up, one GPU worker, the queue, cancel on disconnect), the streaming endpoint, voices, status; tests against a fake model | tests: streaming order, the queue limit, cancel frees the worker, not healthy until warm |
| 4.6 | **deploy:** the manifest, the hook (weights fetched and checked), by hand, then the pipeline; the status page; a smoke test that synthesizes a sentence | deployed by the pipeline; the smoke test hears audio (non-empty, the right rate) |
| 4.7 | **measured:** first packet (median of 20 short sentences), the real-time factor, VRAM, the time from start to warm; voices for Chinese and English chosen; recorded here | **the goal:** packets stream; the first within the 300 ms target (or a revised one, with the reason); no load time between requests; a restart warm before healthy |

**Then, later in CP4** (sub-checkpoints defined when reached): speech-to-text on the same GPU; the
voice path (Hey Pi → pi-server → agent-service, its reply's sentences → tts-service → the speaker);
protocol B in shared-schemas; the agent's latency levers (a faster model for `voice`, the session
started as you begin talking); barge-in end to end.

## Not chosen
| Option | Why not |
|---|---|
| A VM with the card passed through | heavier, and the card would belong to that one VM; LXC is enough for trusted code |
| Loading the model per request (or per session) | seconds of load each time: the opposite of the goal |
| Weights baked into the image | GBs more per image, rebuilt with every code change; pinned weights on disk are as reviewable |
| A cloud TTS (OpenAI, others) | the GPU is here, local speech costs nothing per use, and audio stays home |
| Encoding to Opus or MP3 on the server | an encoder adds latency to the first packet; PCM over the LAN is cheap. A compressed format can come with the phone (over the internet) |

## Resolved in review (2026-11-17)
1. **Speech-to-text:** its own container, sharing the GPU (one service per container).
2. **Voices:** the preset voices (`CustomVoice`) first; cloning from a reference clip (`Base`) later.
3. **The first-packet budget:** 300 ms (median, short sentences) as the target; confirmed or changed
   by 4.7's measurement.
