# Spec: agent-runtime (what runs inside every sandbox)

**Status: milestone 1 done** (2026-11-09; 2026-11-11: the engine is the Codex SDK; 2026-11-12:
replies paid by your ChatGPT subscription through the gateway, results below). The results lock the
design described here. **Deferred:** gVisor (Q6, C2) to the sandbox worker ([tracker.md](tracker.md)
CP2); the login refresh (C5) to agent-service with OpenBao (CP3). Build the runtime and test it with a temporary
**dev gateway**: no agent-service, no sandbox-runner, no public route yet. Details get locked as
milestone 1 answers its questions (below). Part of [agent-service](README.md); model access is in
[model-access.md](model-access.md).

**Planned paths:**
- `python/libs/agent-runtime/`: the package `core` (protocol A in `core/types/`), the sandbox
  image's `Dockerfile`, tests;
- `python/libs/agent-runtime/dev/`: the dev gateway and a small CLI. Temporary: deleted when
  agent-service's gateway exists.

## What it is
The one program inside every sandbox: one image for every profile (`voice`, `general`, `local`).
It:
1. waits for its **session file** (the runner writes it at claim; in milestone 1, the dev gateway does);
2. starts the **Codex SDK** (OpenAI's `openai-codex`, which drives a local `codex app-server`) with
   that session's profile: instructions, model, effort, tools;
3. receives **turns** and **interrupts** from the gateway, runs them, and streams **events** back
   (text as it is generated, tool calls, the result);
4. stops when the session ends.

**It does not:**
- hold a credential beyond its session capability: no `auth.json`, no API key ([model-access.md](model-access.md) MA1);
- listen on any port (outbound only, to the gateway);
- do text-to-speech (that happens outside the sandbox);
- decide policy (the gateway enforces tool and file rules).

## The contract (identical in milestone 1 and in production)
```
             session file (written at claim)
                     │
                     ▼
sandbox: agent-runtime ──outbound only──▶ gateway (dev gateway now, agent-service later)
          │                                │  GET  /v1/runtime/turns       turns and interrupts (long poll)
          │                                │  POST /v1/runtime/events      events (batches)
          └── Codex SDK ─────────────────▶ │  /model/v1/responses          the model (the gateway adds the real credential)
```
**1. Session file:** `/run/session/session.json`, mode 400, read once.
```json
{"session_id": "s_…", "capability": "<random>", "gateway_url": "http://gateway:8080",
 "profile": {"name": "voice", "model": "<a model Codex offers>", "effort": "low",
             "instructions": "…", "tools": [], "limits": {"wall_seconds": 600}}}
```

**2. Model calls through the gateway.** Codex runs with a **custom model provider** whose
`base_url` is `<gateway>/model/v1` and whose key is the **capability** (no Codex login,
`requires_openai_auth = false`; the config is in [model-access.md](model-access.md)). The gateway
checks the capability, swaps in the real credential (your subscription's token) and forwards to
OpenAI. The sandbox's only outbound request is to the gateway.

**3. Turns and events: protocol A.** Protocol A is internal. Only the runtime and agent-service
speak it, and they deploy together. Clients never see it: agent-service translates it into
**protocol B**, the client events in shared-schemas. It never exposes SDK classes, which is why
changing the engine from the Claude Agent SDK to Codex left it unchanged (plus one additive event).
It is defined in `core/types/` (`session.py`, `inbound.py`, `events.py`), as one Pydantic model per
event type in a discriminated union; that package is the source of truth, and agent-service imports
it.

| Direction | Messages |
|---|---|
| gateway → runtime | `turn` {`turn_id`, `text`} · `interrupt` {`turn_id`} · `end` |
| runtime → gateway | the events below, in batches (`POST /v1/runtime/events`) |

**Envelope, on every event:** `v` (protocol version, now 1) · `session_id` · `turn_id` (none for
session-level events) · `seq` (increasing per session; reconnects resume after it) · `ts` (unix
seconds) · `type`.

| `type` | Comes from (Codex notifications) | Fields |
|---|---|---|
| `runtime.ready` | startup, once | `startup_ms`, `profile`, `model`, `tools` |
| `runtime.error` | the runtime itself (busy, crash, wall time) | `message` |
| `turn.started` | a `turn` received | none |
| `text.delta` | `item/agentMessage/delta` | `text` |
| `text.completed` | `item/completed` of an agent message | `text` (the whole message): the cue for speech to flush, and for clients that don't stream |
| `tool.started` | `item/started` of a command, file change, MCP tool call or web search | `tool_use_id` (the item id), `name`, `input` (in full: the trusted gateway decides what clients may see), `server` (true for web search, which runs at OpenAI) |
| `tool.completed` | `item/completed` of the same | `tool_use_id`, `is_error`, `output` (text, up to 2,000 characters), `truncated` |
| `task.started` / `task.progress` / `task.completed` | subagent items (Codex's multi-agent feature; later) | `task_id`, `description`, `status`, … |
| `model.usage_updated` (**new**) | `account/rateLimits/updated`, `thread/tokenUsage/updated`: a usage snapshot, not a limit | `info` |
| `model.rate_limited` | a limit actually hit (the plan's usage limit, or the gateway's budget) | `info` |
| `turn.completed` | `turn/completed`, or the runtime ending the turn | `status` (`completed`, `interrupted` or `failed`: Codex's own three), `reason`, `first_text_ms`, `duration_ms`, `usage`; `stop_reason`, `num_steps`, `total_cost_usd` stay optional and are empty with Codex |

**Not reported:** the model's reasoning (not for clients), and a subagent's own text and tools (they
appear as `task.*` events).

**Rules:**
- **Every turn ends with exactly one `turn.completed`**, whatever happened: interrupted, failed,
  refused as busy, or a crash in the runtime.
- **Senders validate:** the runtime builds every event through the models, so a malformed one fails
  at once, as a bug.
- **Receivers parse one event at a time:** an unknown type or a malformed event is dropped and
  logged, never the whole batch. A newer runtime may briefly talk to an older gateway while the pool
  refills.
- **Changes are additive:** new types and new optional fields. Anything else bumps `v`.
- **The JSON Schema** of all events: `event_json_schema()`, for documentation, or for another
  language if ever needed.

**4. Behaviour.**
- **One Codex thread per session,** `ephemeral` (nothing kept on disk; history belongs to the
  control plane, README decision 2). Follow-up turns run on the same thread.
- **Text streams at once:** no buffering of the reply. That is what lets speech start on the first
  sentence later.
- **Interrupts are honoured within a second:** the turn handle's `interrupt()`; then
  `turn.completed` with status `interrupted`.
- **One turn at a time per session.**
- **Codex's own sandbox is off** (`full_access`, no approval prompts): our container is the boundary
  (gVisor, no credentials, no network but the gateway), as `bypassPermissions` was with the Claude
  SDK. Codex's Linux sandbox may not run under gVisor anyway (C2).
- **No background traffic:** Codex's update checks and telemetry are turned off; they could only fail,
  since the gateway is the only reachable host.
- **Limits:** wall time from the profile; when exceeded, the turn fails with a clear `error`. It's a
  courtesy only: code in the sandbox could ignore it, so the real budgets are enforced at the gateway
  ([README](README.md) G14). Codex has no per-turn step limit like the Claude SDK's `max_turns`.

**5. Profile fields for Codex.**

| Field | Codex setting |
|---|---|
| `model` | a model Codex offers under your plan (the SDK's model list) |
| `effort` | `model_reasoning_effort` |
| `instructions` | added to Codex's own instructions (`developer_instructions`), or, with `codex_instructions: false`, **replacing** them (`base_instructions`) |
| `codex_instructions` | keep Codex's built-in coding-agent instructions (default `true`). `voice` sets `false`: 42% fewer tokens per call (Q3) |
| `tools` | Codex's feature switches: shell commands (`features.shell_tool`), web search (`web_search`), image viewing; `[]` = all off |

## Milestone 1: the test setup
**Day 1: one message, paid by your subscription.** No tools, on your Mac; then the same on the test
VM with gVisor.

```
your Mac (day 1), then the test VM
  agent-dev ask "hi" ──▶ dev gateway (plays agent-service)
                           ├─ session file + capability
                           ├─ auth broker: CODEX_HOME with auth.json from `codex login --device-auth`
                           │   (dev/secrets/, gitignored)
                           └─ model route ──▶ OpenAI (your token added here)
                                ▲
  runtime container ────────────┘ capability only
   (Codex SDK, no tools, read-only, network reaches only the gateway)
```
If OpenAI refuses the token from the gateway (C1), the fallback for a tool-less message is route B:
Codex runs in the dev gateway itself with the cached login, and the runtime relays
([model-access.md](model-access.md), "Routes").

| Piece | Milestone 1 | Becomes |
|---|---|---|
| Who starts the sandbox | you: `docker compose` (dev/) | sandbox-runner (warm pool, gVisor) |
| Gateway | the dev gateway: one session, one profile file, `POST /dev/turns` for you, streaming the events back | agent-service's gateway (profiles from git, sessions, tools, files) |
| Model credential | **your ChatGPT Pro login**, made once by device code, in `dev/secrets/` on the dev gateway's side, never in the container | agent-service's auth broker, with the durable copy in OpenBao ([model-access.md](model-access.md)) |
| How you call it | `agent-dev ask "…"` (LAN or Tailscale; a dev token on the dev gateway) | pi-server, then the phone through api-service |
| Network | the container on a Docker network that reaches only the dev gateway | the egress proxy (sandbox-runner R5) |

**Why not a temporary api-service route:**
- it would be public;
- api-service can't authenticate callers yet (only the deploy proxy exists);
- behind it would be an agent that runs code and spends your subscription.

**The test VM:** Debian 13 with Docker. Install gVisor too, so every measurement can be repeated
with `--runtime=runsc` to see its cost. It's throwaway: it can become the real sandbox worker, or
be deleted.

## What milestone 1 must answer
| # | Question | How it's measured | Decides |
|---|---|---|---|
| Q1 | Does Codex work through our gateway (custom provider, capability as the key, streaming)? | a turn through the dev gateway; the container never had a real token | G3 as designed |
| C1 | Does OpenAI accept your subscription token from the gateway? | the same turn, upstream | route A, or route B ([model-access.md](model-access.md)) |
| C2 | Does Codex run under gVisor with its own sandbox off? | the same turn with `--runtime=runsc` | the runtime's sandbox settings |
| Q2 | Cold start: from container start to `ready` | timestamps in events | warm-pool size, and whether "ready" must be prepared before claim |
| Q3 | Time to first text on a warm sandbox, for a short voice-style question | `turn.started` → first `text.delta` | voice feasibility; the fast-path question (G11) |
| Q4 | Interrupt latency | `interrupt` → `turn.completed` (`interrupted`) | barge-in on Hey Pi |
| Q5 | Image size, memory and CPU of one idle and one busy sandbox | `docker stats`, `docker images` | VM size, pool size |
| Q6 | gVisor's cost | Q2–Q5 again with `--runtime=runsc` | the gVisor platform choice (sandbox-runner open question) |
| Q7 | What Codex needs at startup (writable `CODEX_HOME`, settings to turn off update checks and telemetry) | running read-only, with `/tmp` and `/workspace` as the only writable places | the image layout and the read-only root (R4) |

C4 and C5 (plan limits, refresh) are in [model-access.md](model-access.md).

### Results (2026-11-12: Mac, Docker with runc, your ChatGPT Pro plan)
| # | Result |
|---|---|
| Q1 | **Yes.** Codex sends exactly one kind of request, `POST <gateway>/model/responses` (Responses API, `store: false`, encrypted reasoning), with only the capability. The gateway adds your token and account id. The sandbox's session file, environment, logs and `CODEX_HOME` never contained them (no `auth.json`). |
| C1 | **Yes: route A works.** OpenAI accepts your `codex login` token from the gateway (`chatgpt.com/backend-api/codex/responses`, `Authorization: Bearer` + `chatgpt-account-id`) and counts it against your plan. |
| Q2 | **338–357 ms** from container start to `ready` |
| Q3 | First text at effort `low`: **1.6–2.6 s** (n = 6; first call or not makes little difference). The first test, at effort `medium` with nothing cached, took **10.1 s**. Codex adds **~10,000 tokens** of its own instructions to every call; `codex_instructions: false` cuts a `voice` call to **~5,800** (−42%). The rest is still Codex's (tool and environment context): worth trimming later. |
| Q4 | **9–10 ms** from interrupt to `turn.completed` (`interrupted`), after one word; the next turn continues the conversation |
| Q5 | image **840 MB** (arm64; the Codex binaries are ~400 MB); **84 MiB** idle, **139 MiB** after a few turns |
| Q6, C2 | deferred to CP2: measured on the sandbox worker, under gVisor |
| Q7 | **Works read-only:** non-root (10001), read-only root, `CODEX_HOME` on `/tmp`; on the internal network it reaches only the gateway |

### Baseline: the Claude Agent SDK engine (2026-11-09; replaced)
The same runtime with the Claude Agent SDK, on the Mac against a fake Claude API, for comparison:
the gateway swap worked (Q1); **~0.8 s** to `ready` (Q2); **20 ms** interrupt (Q4); image **777 MB**,
about 300 MB of it the bundled CLI, and **152 MiB** idle (Q5); it ran read-only, non-root, reaching
only the gateway (Q7).

**Done when:** from your Mac, `agent-dev ask "hi"` streams a reply **paid by your subscription**,
from a container that never saw your token; an interrupt stops it within a second; and the questions
above have answers recorded here. Their answers become this spec's locked decisions.

## Not in milestone 1
Tools (weather, Home Assistant), files (milestone 2: [files-and-mounts.md](files-and-mounts.md); the
runtime sees `/workspace/inputs` as a read-only folder and saves through `publish`), skills, the
local model, speech, agent-service, sandbox-runner, auth-service integration, CI builds of the
sandbox image. Each is added once the runtime's numbers are known.
