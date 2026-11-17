# Spec: the control plane (agent-service on CT 107)

**Status: agreed** (2026-11-16; proposed 2026-11-13). CP3's design (3.1, [tracker.md](tracker.md)). Part of
[agent-service](README.md). Related: [agent-runtime.md](agent-runtime.md) (protocol A),
[model-access.md](model-access.md) (the auth broker), [sandbox-runner.md](sandbox-runner.md) (pods),
[files-and-mounts.md](files-and-mounts.md) (later: files), [auth-service.md](../auth-service.md),
[secrets-manager.md](../secrets-manager.md).

## What it is
agent-service is the **trusted control plane**: it decides who may do what, holds every real
credential, keeps the history, and gives each session a sandbox from the runner. It replaces the
dev gateway (CP1, CP2).

```
callers ──(token, aud=agent-service)──▶ :443  sessions API
  smoke-test · the agent CLI · api-service (relaying)   │
                                                         ├─ sessions: claim a pod at start, end on DELETE or 5 idle min
                                                         ├─ the event log: PostgreSQL first, then to listeners (G10)
                                                         ├─ history: conversations, turns; carry-over into new sessions
VM 200: pods ──proxy──▶ :8443 gateway (sandboxes only)   │
                         ├─ runtime: turns in, events out (protocol A), capability + X-Sandbox-Id
                         └─ model: + your token (auth broker) ──▶ OpenAI
agent-service ──(token, aud=sandbox-runner)──▶ sandbox-runner (claim/release)
agent-service ──(token, aud=secrets-manager)──▶ OpenBao (its login's durable copy)
```

**It runs on CT 107** (`agent.lan.liustev6.ca`), deployed by the pipeline (wave 1), as one compose
project: the service and its own **PostgreSQL** (`python/services/agent-service/compose.yaml`).

| | |
|---|---|
| **one process, two listeners** | `app/main.py`: uvicorn twice in one event loop, so both share the sessions core's memory. Container 8443 (host **443**) the sessions API; container 9443 (host **8443**) the gateway. Each serves only its own routes. One certificate (`agent.lan.liustev6.ca`) for both. |
| **start-up** | profiles (a bad file: no start), migrations to the newest, the login restored (the broker), live sessions reloaded (C5), then both listeners, the sweeper and the login's refresh loop |
| **shutdown** | SIGTERM stops both listeners together; open streams get 5 s. Sessions are **not** ended: they survive (C5) |
| **PostgreSQL** | `postgres:17.12-alpine`, pinned by digest; uid 70; data in `/var/lib/agent-service/postgres`; on an `internal` network with agent-service only (no ports, no route out). The password is one OpenBao secret delivered twice, to each user's own file |
| **hardening** | both containers read-only, all capabilities dropped, `no-new-privileges`; agent-service as uid 10001 |
| **image** | `python:3.11-slim` with the Codex SDK and its binary (the broker): about 1 GB |
| **the deploy** | `infra/services/agent-service/deploy.yaml`: its pre-start hook makes the data folders and pulls PostgreSQL's image (the deploy starts containers with `--pull never`); the smoke test, as `smoke-test`, checks TLS, tokens, problem details, ownership and scopes without starting a session |

## Decisions
| ID | Decision | Why |
|---|---|---|
| C1 | **Two listeners:** the sessions API on 443 (callers), the **gateway on 8443** (sandboxes). VM 200's firewall reaches only 8443; nothing else is exposed on it. | A compromised sandbox can't even reach the sessions API. |
| C2 | **A session belongs to its caller:** the token's client, or, when a trusted relay (api-service) passes a caller on, that caller (`on_behalf_of`). Only the owner (or `chat:admin`) can use, read or end it; anyone else gets 404, not 403 (no probing for ids). **Scopes are verbs, not profiles:** `chat:read`, `chat:write`, `chat:admin`; the request names the profile, and any caller with `chat:write` may start any profile. Identity is only the client id: no "acting as a person" until there's more than one. | One client can't read another's sessions (CP3's "done when"). A fixed set of scopes, not one per profile; a profile's cost is bounded by its own limits (C13). Limiting callers to some profiles, skills or tools later is an `authorization_details` type (RFC 9396, like file-management's `file_access`), not more scopes. |
| C3 | **Callers see protocol B v0** (below): protocol A translated at full fidelity, plus agent-service's own events, one `seq` per session. **Every caller gets every event;** a client shows what it needs (a voice client reads only `message.*`). `seq` only increases and **may skip numbers**, so a server-side filter can be added later without breaking callers. | A stable, caller-shaped contract. Every caller today is trusted and holds a token; filtering on the server matters once a reader is trusted less than the data. |
| C4 | **Every event is written to PostgreSQL before anyone sees it** (G10). Listeners get it from memory; a reconnect (`?after=`) reads the rest from the table. | Nothing lost to a dropped connection or a restart. |
| C5 | **Sessions survive an agent-service restart.** Their state (sandbox, capability hash, owner, timers) is in PostgreSQL; on start, agent-service reloads live sessions, and their runtimes' long polls reconnect to the new process. Memory holds only what can be rebuilt: each session's queue to its runtime and its translator. A running turn the runtime never picked up (`turns.delivered_at` empty) is sent again; one it did pick up isn't. A session we died while claiming for (no pod recorded) ends `failed`. | Deploys of agent-service don't end conversations. |
| C6 | **A session claims its pod at start** (the profile's template), and releases it on `DELETE`, after **5 idle minutes**, or at the profile's wall time. **A lost sandbox ends the session** (`session.status` `ended`, reason `sandbox_lost`): the caller starts a new session in the same conversation, and agent-service rebuilds its history from `turns` (C9). The CLI and api-service's relay do this by themselves. **One live session per conversation:** continuing a conversation that has a live session returns that session (no new pod); a new pod is claimed only once it has ended. **A conversation keeps its profile:** continuing it with another is `409`. | Ready by the first turn (~0.5 s); single-use pods (R6). One way to recover, the same as any new session; a runner deploy (which ends its pods) costs a reconnect, not the conversation. One live session keeps a conversation one thread: two sandboxes would each see only half of it. The database enforces it (a partial unique index), so racing starts can't both claim. |
| C7 | **The capability** is 256 random bits, given only to the runner (the session file); agent-service stores only its SHA-256. A request needs it **and** the pod's `X-Sandbox-Id`. It dies with the session. | A stolen capability is useless from another pod (the CP2 theft test) and after the session. |
| C8 | **History lives in PostgreSQL** (decision 2): conversations, sessions, turns, events. | Continuity, and the cost policy (C9). |
| C9 | **A new session in an existing conversation gets its last N turns as context** (the profile's `carry_turns`, default 6), as text after its instructions: agent-service appends it to the profile's `instructions` in the session file, so protocol A and the runtime need nothing new. Within a session, the conversation only grows (decision 2: caching, reasoning state). | Continuity at a bounded, chosen cost. Summaries of older turns come in CP7. |
| C10 | **No backups for now** (decided 2026-11-17): chat history isn't important enough yet to back up. PostgreSQL's data lives on CT 107's disk (`/var/lib/agent-service/postgres`), covered only by whatever protects that disk. | Revisit when history matters (CP7's summaries and memory make it more valuable): the plan was a nightly `pg_dump` through file-management, kept 14 days, with a scripted restore check. |
| C11 | **The auth broker (model-access.md MA1–MA6) lives here:** your ChatGPT login in `/var/lib/agent-service/codex-home`, refreshed by Codex in this one process, each refreshed bundle written to OpenBao (`kv/services/agent-service/codex_auth`); restored from there if the disk is new. | One owner of the login (MA2); a rebuilt host comes back without a new login (MA3). |
| C12 | **Profiles are a reviewed file:** `infra/services/agent-service/profiles.yaml`, installed by every deploy (the deployer's `files`). | Changing what an agent may do is a reviewed diff (G5). |
| C13 | **Budgets are enforced at the gateway** (G14): turns per session, tokens per turn (counted from the model responses the gateway relays, never from what the sandbox reports), and the profile's wall time; past one, the model route refuses and the turn fails with a clear `error`. | The sandbox is assumed compromised; only the gateway's limits are real. |
| C14 | **The kill switch:** `DELETE` a session; `POST /v1/admin/stop` (`chat:admin`) ends every session and refuses new ones until `…/start`; or stop the runner. The switch is a row in `flags`, so it stays on across restarts and deploys. | One call stops everything, without touching hosts. |

## The sessions API (443, callers)
Auth: an auth-service token, `aud=agent-service`. Scopes: `chat:write` (start, talk in and end
your own sessions), `chat:read` (read them and their history), `chat:admin` (everyone's, and the
kill switch), `status:read` (api-service's status page). The scope each route needs is in its row.
Code: `app/api/`.

| Route | What |
|---|---|
| `POST /v1/sessions` `{profile, conversation_id?}` | `chat:write`: a session for a new conversation, or for one you own (C9). **`201`** a new session (a pod claimed); **`200`** the conversation's live session, already running (C6). Returns the session (below). |
| `POST /v1/sessions/{id}/turns` `{text}` | `chat:write`: one turn, **streamed** (below) until its `turn.completed`, or the session's end; the turn's id in `X-Turn-Id`. A turn may be sent while the session is `starting`: it waits for the runtime. |
| `GET /v1/sessions/{id}/events?after=<seq>` | `chat:read`: everything after `seq` (or `Last-Event-ID`), then live, until the session ends (reconnect, G10) |
| `GET /v1/sessions/{id}` | `chat:read`: `session_id`, `conversation_id`, `state` (`starting`, `ready`, `busy`, `ended`), `end_reason`, `turns_used`, `started_at`, `ready_at`, `ended_at` |
| `POST /v1/sessions/{id}/cancel` | `chat:write`: interrupt the running turn (`{"interrupted": bool}`) |
| `DELETE /v1/sessions/{id}` | `chat:write`: end it: the pod is released (`{"ended": bool}`) |
| `GET /v1/conversations` | `chat:read`: yours, most recently active first, each with its live session if any (`?limit=`) |
| `GET /v1/conversations/{id}/turns` | `chat:read`: its turns across all its sessions, oldest first (text, status, timings, usage; `?limit=`); each turn's protocol B events too with `?events=true` |
| `POST /v1/admin/stop`, `POST /v1/admin/start` | `chat:admin`: the kill switch |
| `GET /v1/status` | `status:read`, for the lab's status page: `ok`, `stopped`, `database`, live sessions by state, `login` (signed in, its age, saved to OpenBao); counts only, never ids or secrets. (The last backup comes with 3.5.) |
| `GET /health` | liveness only, no token |

**Streams are Server-Sent Events** (`text/event-stream`, the W3C EventSource format), as OpenAI's
and Anthropic's APIs stream: each event is `id: <seq>`, `event: <type>`, `data: <the protocol B
event as JSON>`. A client that reconnects sends the last `id` back as `Last-Event-ID` (a browser's
EventSource does this by itself), or uses `?after=`. A comment line (`: keep-alive`) every 15 s
without events keeps proxies from closing the stream. (Chosen over NDJSON, the first plan: the
reconnect and the keep-alive are part of the standard instead of ours.)

**Errors are RFC 9457 problem details** (`application/problem+json`: `type`, `title`, `status`,
`detail`) with a `code` to branch on:

| Status | `code` | When |
|---|---|---|
| 400 | `unknown_profile` | no such profile |
| 404 | `not_found` | no such session or conversation, **or not yours** (C2) |
| 409 | `profile_mismatch` · `busy` · `ended` | another profile than the conversation's · a turn is running · the session is over (start a new one in the conversation) |
| 429 | `budget` | the session has used its `max_turns` |
| 502 | `runner` | the sandbox runner couldn't be reached |
| 503 | `no_sandbox` (with `Retry-After`) · `stopped` | no pod free right now · the kill switch is on |

**Callers in CP3:**
- `smoke-test`: deploy smoke tests, directly (agent-service's) and through the relay (api-service's);
  no model call (a turn would spend your plan on every deploy).
- **the `agent` CLI** on CT 107 (client `agent-cli`, secret root-only there): `ssh -t agent-host agent
  chat`, `agent ask [--last] …`, `agent conversations`, `agent history <id>`, `agent stop|start`. It
  runs in agent-service's running image (`bin/agent`, installed by the deploy hook), so it always
  matches the service. `app/cli.py`.
- **api-service, relaying** an authenticated machine client at `/v1/agent/…` (the public edge; mode
  `client`, grant 3: the caller's own grants and identity reach agent-service; api-service.md "Agents").

## Protocol B v0 (to callers)
**Envelope:** `v` (1), `session_id`, `conversation_id`, `turn_id` (none for session-level events),
`seq` (per session, only increases, may skip numbers), `ts`, `type`.

| `type` | Fields | From |
|---|---|---|
| `session.status` | `status`: `ready`, `ended`; `reason` (`deleted`, `idle`, `wall_time`, `sandbox_lost`, `stopped`) | agent-service |
| `turn.started` | — | protocol A `turn.started` |
| `message.delta` | `text` | A `text.delta` |
| `message.completed` | `text` | A `text.completed` |
| `tool.started` | `tool_id`, `name`, `input`, `server` | A `tool.started` |
| `tool.completed` | `tool_id`, `is_error`, `output`, `truncated` | A `tool.completed` |
| `task.started` / `task.progress` / `task.completed` | `task_id`, `description`, `status`, `summary` | A `task.*` |
| `turn.completed` | `status`, `reason`, `first_text_ms`, `duration_ms`, `usage` (**this turn's** tokens: `input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_tokens`, `total_tokens`; null if unknown) | A `turn.completed`: Codex reports the thread's running total, so agent-service subtracts the total at the turn's start; the engine's other figures stay internal |
| `error` | `code` (`no_sandbox`, `rate_limited`, `budget`, `runtime`, `internal`), `message` | agent-service, A `runtime.error`, A `model.rate_limited` |

**From the sandbox, checked, not trusted:** each protocol A event is parsed on its own (a bad one is
dropped and logged); its `session_id` must be the session's and its `turn_id` a turn agent-service
started; tool output and error messages are capped again. Code: `app/protocol/translate.py`; B's models:
`app/protocol/events.py`.

**Never sent:** the capability, sandbox ids, internal addresses, other sessions,
your plan's account details (A's `model.usage_updated` stays internal: the gateway uses it for C13).

## The sessions core (`app/sessions/`)
```
starting ──runtime.ready──▶ ready ◀──turn.completed── busy
   │  └──────────turn (queued until the runtime is ready)──▶ │
   │                          │ ──turn──────────────▶ │
   └──────────────┬───────────┴───────────────────────┘
                  ▼
                ended    deleted · idle · wall_time · sandbox_lost · stopped · failed
```
- **Start:** the session row is inserted (`starting`) before the pod is claimed, so the
  one-live-session index decides a race and only the winner claims. No pod (`503`): the rows are
  removed, nothing is left behind.
- **A state change and the events announcing it commit together** (the event log writes inside the
  caller's transaction): a turn's row is `completed` exactly when its `turn.completed` is stored.
- **The sweep** (every 15 s) reads the table and ends what has to end: never ready within 60 s
  (`failed`), the profile's wall time, 5 idle minutes (only when `ready`: a long turn is never
  idle), or a pod the runner no longer lists as claimed (`sandbox_lost`; nothing is judged lost
  while the runner can't be reached). It reads the sessions before the runner's list, so a pod
  claimed in between can't look lost.
- **A profile's wall time is at most 3600 s**, the runner's own hard limit, past which it destroys
  the pod.

## The gateway (8443, sandboxes)
Reached only through a pod's proxy (R12), which stamps `X-Sandbox-Id`. Code: `app/gateway/` (`pairing.py`, `runtime.py`, `model.py`).

| Route | What |
|---|---|
| `GET /v1/runtime/turns?wait=` | the runtime's long poll: turns, interrupts, end (at most 30 s) |
| `POST /v1/runtime/events` | protocol A events (a batch, at most 1 MB): each parsed alone (a bad one is dropped), translated to B, written (C4), sent on |
| `/model/{path}` | to OpenAI with your token (C11); the profile's model enforced; C13's budgets (a request at most 16 MB) |

**Every request:** capability hash → its live session (from memory, rebuilt at start; else 401);
`X-Sandbox-Id` must be that session's pod (else 403, logged). `X-Sandbox-Id`, `X-Forwarded-For`,
`X-Real-IP` and the capability are never forwarded. Shutdown waits at most 5 s for open long polls
(the CP2 lesson), so its own shutdown work runs.

**The model route** is a pass-through with four jobs:
- **the credential:** the capability is swapped for your login's headers; an upstream `401` refreshes
  the login once and retries;
- **the model:** a profile that pins `model` refuses any other (`403 model_not_allowed`), and fails
  closed on a body it can't read (compressed, not JSON); `model: null` takes Codex's choice;
- **the token budget:** it reads the `usage` of each response's final server-sent event
  (`response.completed`, `.incomplete`, `.failed`) as the bytes pass, untouched, and adds it to the
  running turn. Once the turn reaches `max_turn_tokens`, further calls get `403 budget_exceeded` and
  the caller one `error {code: budget}`; the runtime's turn then fails. The response that crosses
  the line completes (a turn can overshoot by one response). It counts OpenAI's figures, never the
  sandbox's;
- **refusals in OpenAI's error shape**, so Codex shows the message, and `403`, not `429`: Codex
  retries a `429`.

## The auth broker (`app/broker/`)
Your ChatGPT login, in `/var/lib/agent-service/codex-home/auth.json` on CT 107, and nowhere else
the sandboxes can reach (MA1).
- **Sign in once,** on CT 107: `docker compose exec agent-service python -m app.broker login` prints
  a URL and a one-time code to approve on your phone; the login is saved to OpenBao at once.
  `… python -m app.broker status` shows it (never a token).
- **Codex refreshes it** (MA2), in this process only: every 6 hours (so the refresh token stays in
  use, MA4), and on an upstream `401`. Several sessions hitting an expired token together cause one
  refresh: the others reuse it (refresh tokens rotate; two refreshes would race).
- **Every refresh that changes the login is saved to OpenBao** (`kv/services/agent-service/codex_auth`,
  role `agent-service`, one revoked OpenBao token per operation). A failed save is logged and shown
  on the status page, never fatal: the model keeps working from the disk's copy.
- **At start, the newer copy wins** (by Codex's `last_refresh`; the disk's on a tie): a new disk is
  restored from OpenBao (MA3), and a stale OpenBao copy never replaces a newer disk one; OpenBao is
  then brought up to date.
- **The status page** shows `login`: `signed_in`, `refreshed_hours_ago`, `saved_to_openbao`; not
  signed in makes agent-service's `ok` false.

## Profiles (`profiles.yaml`)
```yaml
profiles:
  steven-ai:                       # the evidence chat
    template: chat                 # the runner's template (R14)
    model: null                    # Codex's default for your plan
    effort: medium
    instructions: |
      …
    codex_instructions: true
    tools: []                      # CP5 adds tools
    limits: {wall_seconds: 3600, max_turns: 100, max_turn_tokens: 200000}
    history: {carry_turns: 6}
  voice:
    template: chat
    effort: low
    codex_instructions: false      # 42% fewer tokens per call (agent-runtime.md Q3)
    limits: {wall_seconds: 1800, max_turns: 60, max_turn_tokens: 50000}
    history: {carry_turns: 4}
  smoke:                           # deploy smoke tests: one short turn, as cheap as possible
    template: chat
    effort: low
    codex_instructions: false
    limits: {wall_seconds: 120, max_turns: 2, max_turn_tokens: 20000}
    history: {carry_turns: 0}
```
What reaches the runtime (protocol A's `Profile` in the session file) is the subset it needs
(model, effort, instructions with the carried-over turns appended (C9), `codex_instructions`, tools, wall
time): `AgentProfile.runtime()` in `app/profiles.py`.

## History (PostgreSQL)
**Tables** (Alembic migrations, reviewed in git; SQLAlchemy 2 with psycopg 3):

| Table | Holds | Key |
|---|---|---|
| `conversations` | owner, profile (fixed for its life), created, last active | `id` (UUID) |
| `sessions` | conversation, owner, sandbox id, capability hash, state, `next_seq`, turns used, started, ready (runtime.ready arrived), last activity, ended, end reason | `id`; at most one not `ended` per conversation |
| `turns` | conversation, session, user text, reply text, status, timings, usage, delivered (to the runtime) | `id`; ordered per conversation |
| `events` | session, `seq`, `ts`, `type`, turn, protocol A's seq (a retried batch is stored once), payload (the protocol B event, full fidelity) | (`session_id`, `seq`) |
| `flags` | name, value, changed by, changed at: the kill switch (C14) | `name` |

- **Events are stored as sent:** the protocol B event, full fidelity.
- **Retention: everything is kept,** turns and events. A few GB a year at home scale; agent-service's
  `/v1/status` reports the database's size, and a retention policy can come later if it matters.
- **Carry-over (C9):** at session start, the conversation's last `carry_turns` turns become
  `context`: "Earlier in this conversation:\nYou: …\nAssistant: …", added to the instructions.
- **The database:** `postgres:17`, a container in agent-service's compose project, on an internal
  network only, data in `/var/lib/agent-service/postgres`; its password a managed secret
  (`kv/services/agent-service/postgres_password`), delivered by the deployer.
- **Backups (C10):** none for now; the data is on CT 107's disk only.

## Identity and secrets
| What | Where | Delivered |
|---|---|---|
| agent-service's client secret (its "secret zero") | `/etc/agent-service/credentials/client_secret` | `add-client.sh` (host-only) |
| PostgreSQL's password | `/etc/agent-service/credentials/postgres_password` | the deployer, from OpenBao |
| your ChatGPT login | `/var/lib/agent-service/codex-home/auth.json`, and OpenBao | the auth broker itself (C11): device code, once |

**auth-service policy** (`infra/services/auth-service/policy.yaml`):

| Client | Gets | Why |
|---|---|---|
| `agent-service` | sandbox-runner: claim, release, read; secrets-manager: `secrets:read`; file-management: all on `private/agent-service/backups/` | pods; its login in OpenBao; backups |
| `agent-cli` | agent-service: `chat:read`, `chat:write`, `chat:admin` | your operator tool on CT 107, kill switch included |
| `smoke-test` | agent-service and api-service: `chat:read`, `chat:write` | deploy checks (the `smoke` profile), directly and through the relay |
| `api-service` | agent-service: `status:read` | the status page. It relays `/v1/agent/…` with `can_relay_clients` (grant 3): the relayed token carries the **caller's own** agent-service grants, never api-service's |

The audience `api-service` gains `chat:read` and `chat:write`: the scopes its `/v1/agent/…` routes require.

**OpenBao** (`infra/secrets-manager/tofu/main.tf`): role `agent-service` (`sub=service:agent-service`,
10-minute tokens) with the policy `agent-service-login`: **read, create and update only**
`kv/services/agent-service/codex_auth`. The PostgreSQL password (`kv/services/agent-service/postgres_password`)
is written by hand with `bao kv put` and delivered by the deployer; its read policies are generated
once agent-service's manifest declares it (3.7).

**The firewall (VM 200):** in, 443 from CT 107 (claims); out, 8443 to CT 107 (the gateway). VM 200's
internet egress narrows in 3.9: model calls now leave from CT 107.

## Not chosen
| Option | Why not |
|---|---|
| Protocol A to callers | internal fields, no agent-service events, a `seq` that restarts with each sandbox ([the CP3 discussion](tracker.md)) |
| One listener for callers and sandboxes | a sandbox could reach the sessions API; the firewall can't tell routes apart |
| Event log in JSONL files | history needs PostgreSQL anyway; one store for both |
| A database host of its own | one more host; at home scale, a container next to its only user, backed up off-host, is enough |
| Rebuilding context on every turn | caching and reasoning state lost (decision 2) |
| Visibility per profile (`full`, `summary`, `text`), filtered on the server | every caller is trusted today; a client filters for display. Comes back as a server filter when untrusted readers exist (C3's `seq` gaps allow it) |
| Events deleted after 30 days | small at home scale; one rule fewer |
| A scope per profile (`profile:<name>`) | scopes growing with every profile; which profile is a resource choice, not a permission. If callers must be limited to some profiles, skills or tools, an `authorization_details` type does it |
| Two live sessions in one conversation | each sandbox would see half the conversation; continuing returns the live session instead |
| Switching a conversation's profile | clients keep to their profile; a fixed one keeps history and budgets consistent |
| Callers acting as a person (a user identity) | one person; the client id is enough for ownership |
| Replacing a lost sandbox inside the same session | a second way to recover; a new session in the same conversation already rebuilds the history (C9) |

## Resolved in review (2026-11-16)
1. **Visibility:** none for now; callers get every event and filter for display (C3).
2. **Events retention:** kept forever (History).
3. **Identity:** the client id owns sessions; scopes are `chat:read`, `chat:write`, `chat:admin`, any profile (C2).
4. **A lost sandbox:** the session ends; a new session in the same conversation rebuilds the history (C6).
