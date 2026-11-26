# Tracker: building agent-service

Part of [agent-service](README.md). The build order, as checkpoints.

**How this file works:**
- **A checkpoint (CP)** is one end-to-end slice that works, retires a named risk, and usually
  replaces a temporary stand-in with the real piece. It's done when its "done when" is
  **measured**, not when the code exists.
- **Sub-checkpoints are defined only for the CP being worked on.** Later CPs stay one line until
  they start: they'll be planned with what the earlier ones taught.
- **When a CP is done:** record its date and commit, collapse its sub-checkpoints into one line, and
  move its measured results into the spec it belongs to.
- **Status:** ✅ done · 🟡 in progress · ⏳ not started.

**Rules for every CP:**
1. **The security invariants are tested again, not assumed:**
   - no real credential in a sandbox;
   - sandbox egress reaches only the gateway;
   - every hop is authenticated (README, "Security notes").
2. **The spec is updated with measured results,** and its decisions become locked.
3. **From CP3 on, everything ships through the deployer;** only the roots of trust deploy by hand.
4. **Each CP ends with a demo you run yourself,** not just passing tests.

## Checkpoints
| CP | Status | Built | Risk it retires | Done when |
|---|---|---|---|---|
| 0 | ✅ 2026-11-11 | the design: decisions 1–4 | building the wrong thing | the specs are agreed |
| 1 | ✅ 2026-11-12 (`37d1b61`) | **the runtime** on the Codex SDK, with the dev gateway (milestone 1, [agent-runtime.md](agent-runtime.md)) | can the agent run sandboxed, paid by your plan, with no secret in the sandbox? | replies paid by your Pro plan through the gateway (C1, route A); the sandbox never held the token (Q1, Q7); ready ~350 ms, first text 1.6–2.6 s, interrupt ~10 ms (Q2–Q5). **Deferred:** gVisor (Q6, C2) to CP2; the login refresh (C5) to CP3 |
| 2 | ✅ 2026-11-13 (`3221624`) | **sandbox worker:** VM 200, sandbox-runner, pods (R12), templates (R14), gVisor ([sandbox-runner.md](sandbox-runner.md)) | isolation and speed at the infrastructure level | Codex runs under gVisor (C2; ~0.3 s, ~20 MiB, 3.7× on small files; systrap); a warm claim **26 ms**, claim to ready **523 ms**, on demand **834 ms**; pods destroyed after use, the pool refills, crash recovery; sandboxes reach only their own proxy (no DNS, internet, LAN, VM or other pods; pod check 16/16); the proxy's stamp can't be forged (theft test 403); the runner on auth-service tokens, deployed by the pipeline, sandbox-image updates rolling out by themselves; the Proxmox firewall on VM 200 (21/21) |
| 3 | ✅ 2026-11-17 | **agent-service** on CT 107 ([control-plane.md](control-plane.md)), deployed by the pipeline: sessions API (protocol B v0 as Server-Sent Events, RFC 9457 errors), the gateway on its own port (pairing by capability and the pod's stamp, budgets counted from OpenAI's usage), the auth broker (your login on CT 107, saved to OpenBao), PostgreSQL history with carry-over, the `agent` CLI, api-service's relay (grant 3). The dev gateway is deleted; backups dropped (C10) | the control plane, real auth, operability, continuity | **Live (3.10):** a turn streamed (first text 2–4 s, nearly all of it the model; agent-service ~40 ms); another caller gets 404; across a restart the session lived on, same pod, nothing lost; a new session carried the conversation over; the login restored from OpenBao on a "new disk", identical; the kill switch held across a restart. **To confirm after the GPU reboot:** the first scheduled login refresh saved to OpenBao (`agent-service` logs: "login refreshed", then "login saved") |
| 4 | 🟡 paused after 4.13 (2026-11-25) for CP5 | **voice:** first **speech-service** on pve01's GPU (an LXC, CT 108; [speech-service.md](../speech-service.md)), then speech-to-text and pi-server → agent-service → speech-service, protocol B in shared-schemas, Hey Pi speaks | the first thing you actually use | you ask Hey Pi and hear the answer; first audio within the budget set at the start of this CP; barge-in interrupts; a week of daily use |
| 5 | 🟡 **live:** the core ✅ 2026-11-26 (5.1–5.6); Home Assistant and confirmations later | **tools** ([tools.md](tools.md), live): tools-service on CT 109 (one registry, a REST API for apps and MCP for agents, one filter; weather, `pi/end_conversation`), the gateway's `/mcp`, each session's tools = its caller's grant (a profile may narrow it), by token exchange (nothing in the sandbox), fixed at the start and listed in the instructions, tool budgets (G14). Also on: web search, Codex's own (run at OpenAI), for `pi` since 2026-11-18. **Later:** Home Assistant and confirmations (G12) | an agent that acts, safely | ✅ the Pi answers the weather with our tool; ✅ a session sees only its own tools; ✅ an injected instruction can't call a tool outside them; ✅ a session over budget is refused; ✅ the agent ends the conversation (all live, tools.md "Measured", "Safety"). With Home Assistant (later): "turn off the kitchen lights" works; locks and the garage ask first |
| 6 | ⏳ | **files** (milestone 2, [files-and-mounts.md](files-and-mounts.md)): WebDAV read view, mounter, publish, token exchange | storage access without credentials in the sandbox | that spec's "done when" |
| 7 | ⏳ | **summaries and memory** (model-driven): older turns summarized, memory extracted ("I like the lights at 40%"). History itself (PostgreSQL, context carry-over, backups) moved into CP3 | cost and continuity over long histories | long conversations carry a summary instead of every turn; memory is recalled in new sessions |
| 8 | ⏳ | **knowledge:** corpus in a bucket, index, `search_knowledge`; stevenai-service retired | RAG, filtered by grants | answers cite your documents; search never returns text the session couldn't read; stevenai-service is off |
| 9 | ⏳ | **outside and operations:** the phone through api-service; alerts (login refresh failed, pool empty, plan limit near); load test | running it for real | the phone works off the LAN; every alert fires in a drill; N parallel sessions measured against your plan's windows (C4) |

```
CP1 ─▶ CP2 ─▶ CP3 ─▶ CP4 ─▶ CP5 ─┬─▶ CP7 ─▶ CP9
                                  └─▶ CP6 ─▶ CP8      (6 and 7 can swap or run in parallel)
```

## The order from here (2026-11-26)
CP4 stopped after barge-in, because its next steps need tools: ending a conversation is a tool
(`end_conversation`), and Hey Pi's assistant should show tool activity.

1. **CP4 up to 4.13** ✅ 2026-11-25: the voice turn, the wake word (小派小派), the session, barge-in.
2. **CP5, 5.1–5.6** ✅ 2026-11-26: tools-service with weather, the gateway's `/mcp`, filtering and
   delegation, the safety checks, `end_conversation`.
3. **Back to CP4 (next):** 4.14 (Hey Pi shows the assistant), 4.15 (protocol B in shared-schemas, the
   agent's latency levers: Codex's ~2 s tool lookup, its own tool context, the cache), then its "done
   when" (the first-audio budget, a week of daily use): CP4 ✅.
4. **CP5's later part:** Home Assistant's tools and confirmations (G12): CP5 ✅.
5. **CP6 / CP7**, as the diagram.

Anything skipped on the way is in "Skipped for now" below, with where it comes back.

## CP5, tools: the core live (2026-11-26)
**5.1–5.6 ✅**, each checked live: auth-service's `tools-service` audience and relay (5.1), tools-service
on CT 109 through the pipeline (5.2), the gateway's `/mcp` (5.3), Codex on it with the tool list in its
instructions and the time per turn (5.4), the safety checks with the sandbox assumed compromised
(5.5), `pi/end_conversation` ending the voice session (5.6, by voice on the Pi). Decisions TL1–TL15,
how it works, what was measured and found, and each step's commits: [tools.md](tools.md). **Left for
CP5:** Home Assistant's tools and confirmations (G12), after CP4 ("The order from here").

## Ongoing: S, the lab's status
**One public page for the whole lab:** `GET https://api.liustev6.ca/v1/status`, no token. api-service
reads every service in parallel and returns a public-safe summary (cached 5 s, rate-limited): each
service ok or not, its own health details, and the sandbox runner's pods. Never in it: session or
sandbox ids, prompts, tokens, secrets, internal addresses, who deployed.
- **A service's `/health`** stays minimal and unauthenticated (Docker, deploys); api-service reads it
  when it says enough (auth-service, file-management, the deployer; OpenBao's `/v1/sys/health`).
- **A token-protected `/v1/status`** (scope `status:read`, granted to api-service only) when a service
  has details beyond liveness: sandbox-runner's pods now, agent-service's sessions in CP3.
- **Rule:** every checkpoint that adds or changes a service adds it to api-service's list (and its
  `/v1/status` if it has details), and checks it in a smoke test. Later: a `status` tool for agents,
  through the gateway.

| Sub-CP | Status | What | Done when |
|---|---|---|---|
| S.1 | ✅ 2026-11-13 | `status:read` on `sandbox-runner` for api-service; the runner's `/v1/status`; api-service's public `/v1/status` (service-auth and a client secret for api-service); VM 200's firewall lets CT 106 reach the runner | `curl https://api.liustev6.ca/v1/status` shows every service and the runner's pods; a stopped service shows as down without failing the page. **Live:** through Cloudflare, all 6 services ok (13–24 ms each), OpenBao unsealed, the runner's templates and pods, no ids or addresses; deployed by the pipeline with its smoke test. (api-service's never-delivered client secret from 2026-11-07 was rotated out.) |

## In progress: CP4, voice
**Decided (2026-11-17):** the GPU (an RTX 5060, 8 GB) serves only voice for now, through an **LXC
container** (CT 108), not a VM; the old gpu-server is archived for a multi-GPU redesign. **First goal:**
speech-service up, streaming audio packets from one long-running process that keeps the model loaded:
no cold load per request.

**Sub-checkpoints:** speech-service's build (4.1–4.7), recorded here; the decisions and the spikes behind
them: [speech-service.md](../speech-service.md) (live since 2026-11-18). 4.1 ✅ (2026-11-17): agreed; pi-server orchestrates the voice pipeline, speech-service is an engine (T10). 4.2 ✅ the driver on pve01; 4.3 ✅ CT 108 with the GPU, CUDA working in a container ([lxc-gpu-passthrough-setup.md](../../lxc-gpu-passthrough-setup.md)). 4.4 ✅ identity. 4.5a ✅ the spike: our own streaming engine 169 ms first audio, CUDA graphs 112 ms; decided: our own engine on Qwen's official code, with our own CUDA graphs (T11). 4.5b ✅ the plain engine (Qwen's exact codes, streamed). 4.5c ✅ our own CUDA graphs and Triton kernels: first audio 20 ms, RTF 0.17 (from 57 ms and 0.48). 4.5d ✅ speech-to-text: Qwen3-ASR-0.6B in the same process (T12), streamed, 3.9% / 4.2% / 12.5% error (English / Chinese / mixed), ~60 ms after the speaker stops. 4.5e ✅ batches of 2 for both models (continuous batching for the TTS, tick batching for the STT); 4.5f ✅ the service (WebSocket sessions, 2 running + 1 waiting per kind); 4.6 ✅ deployed on CT 108 (https://speech.lan.liustev6.ca), its smoke test speaking a sentence and hearing it back. 4.7 skipped (covered by the e2e runs and the test client); speech-service on the pipeline. The rest of CP4, the voice path
(4.8–4.15: pi-server's identity, the Pi's audio, the voice turn, the wake word, barge-in), is in
[voice-path.md](../voice-path.md) (draft, 2026-11-17). **Paused after 4.13** (barge-in, 2026-11-25) for
CP5; 4.14 and 4.15 come back after it ("The order from here").

## Skipped for now: to come back to
Kept here so nothing is lost while CP5 runs. Each row says where it comes back; when it's done,
strike it through with the date, as below.

| Item | Skipped from | Comes back |
|---|---|---|
| **4.14 Hey Pi shows the assistant:** in progress (2026-11-26): the protocol reduced to one message, `speech` (idle, listening, speaking), on both sides; the app connects and shows a speech view over its own mode. No push-to-talk ([flutter_ui.md](../flutter_ui.md) "Protocol") | CP4 | **next** (CP5's core is live) |
| **4.15 protocol B in shared-schemas; the agent's latency levers** (~5,800 tokens per call of Codex's own context, below) | CP4 | after 4.14 |
| **CP4's "done when":** the first-audio budget measured against what was set; a week of daily use | CP4 | after 4.15 |
| **Home Assistant's tools** (`home.*`, each with its class) **and confirmations** (G12, `confirmation.requested`, asked out loud on the Pi) | CP5 ([tools.md](tools.md) "Later") | after CP4 is done |
| **Deploys of the Pi on push** (pi-server, Hey Pi): deployer phase 4; until then by hand from the Mac, on the home LAN | [deployer.md](../deployer.md) | when deploying by hand gets in the way; the Pi on Tailscale first, to deploy from away |
| **Hey Pi boots to the app** (the `hey-pi.service` unit; `pi-setup.sh` from a fresh Pi OS) | [flutter_ui.md](../flutter_ui.md) | with 4.14 |
| **The wake word's next round:** 30–60 min of your ordinary talk on the Pi's mic, pi-server saving each wake's audio, test set v2 and a retrain (quiet takes 68%, speech over media) | [wake-word.md](../wake-word.md) | when false wakes or misses show up in daily use |
| ~~**pi-server's unused grant at api-service**~~ removed in 5.1 (2026-11-26) | the auth policy | 5.1 ✅ |
| **The website's Steven AI chat** calls web-gateway's `/chat/stream` on api.liustev6.ca: 404 since api-service replaced it | the website | CP8 (stevenai-service retired); hide it until then |
| **Open bugs** (2026-11-24): #1 pi-server can loop on empty turns; #2 the TTS's second slot can fail to allocate memory (worked around); #3 speech-service's GPU memory creeps up; #5 the Pi's journal is in memory, lost on reboot; #6 the agent's search queries aren't logged; #7 the agent sometimes replies in Spanish; #8 speech-service's test client sends no location; #9 a deploy's smoke test once heard garbled audio; #10 tokens go stale across the Mac's sleep; #11 (proposed) speech-service logs the speech-to-text context, sent in the URL | CP4 | #5 first (cheap, and it makes the rest diagnosable); the others between CP5's steps or with 4.15 |

## Found along the way (not yet scheduled)
| Item | Found in | Likely CP |
|---|---|---|
| ~5,800 tokens per call are still Codex's own tool and environment context, even with no tools: trim for cheaper voice calls | CP1 (Q3) | 4 |
| ~~An interrupted turn's `usage` repeats the previous call's figures~~ protocol B reports it per turn, and `null` when Codex sent none (an interrupt) | CP1 (Q4) | 3 ✅ |
| The sandbox image is 840 MB, mostly the Codex binaries; it affects the pool's pull and disk size | CP1 (Q5) | 2 |
| ~~A gateway holding long polls must cap its graceful shutdown~~ agent-service: 5 s (`app/main.py`) | CP2 (2.5) | 3 ✅ |
| ~~Remove the temporary client `agent-dev`~~ removed from the policy (3.9) | CP2 (2.5) | 3 ✅ |
| VM 200's internet egress (443/80) is broad: image pulls, packages, certificates. The sandboxes never use it (their only way out is the gateway); narrowing the VM's own needs an allowlisting proxy | CP2 (2.7) | 5 (with the web tools' egress) |
| The runner's container needs the Docker socket, the XFS disk and `/run/sandbox-runner` at the same paths, and `CAP_SYS_ADMIN` for `xfs_quota` (or it runs privileged: it's root on the VM anyway, R3) | CP2 (2.4) | 2 (2.6) |
| ~~The auth map, the OpenBao exception, trusting the proxy's stamp: into the README~~ done (README "Who authenticates how") | the auth review (2026-11-11) | 3 ✅ |
