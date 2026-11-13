# Spec: the control plane (agent-service on CT 107)

**Status: proposed, for review** (2026-11-13). CP3's design (3.1, [tracker.md](tracker.md)). Part of
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
agent-service ──(token, aud=file-management)──▶ file-management (nightly backups)
```

**It runs on CT 107** (`agent.lan.liustev6.ca`), deployed by the pipeline (wave 1), as one compose
project: the service and its own **PostgreSQL**.

## Decisions
| ID | Decision | Why |
|---|---|---|
| C1 | **Two listeners:** the sessions API on 443 (callers), the **gateway on 8443** (sandboxes). VM 200's firewall reaches only 8443; nothing else is exposed on it. | A compromised sandbox can't even reach the sessions API. |
| C2 | **A session belongs to its caller:** the token's client, or, when a trusted relay (api-service) passes a caller on, that caller (`on_behalf_of`). Only the owner (or `sessions:admin`) can use, read or end it; anyone else gets 404, not 403 (no probing for ids). | One client can't read another's sessions (CP3's "done when"). |
| C3 | **Callers see protocol B v0** (below): protocol A translated, plus agent-service's own events, one `seq` per session, and what the **profile's visibility** allows. | A stable, caller-shaped contract; full evidence where the profile says so. |
| C4 | **Every event is written to PostgreSQL before anyone sees it** (G10). Listeners get it from memory; a reconnect (`?after=`) reads the rest from the table. | Nothing lost to a dropped connection or a restart. |
| C5 | **Sessions survive an agent-service restart.** Their state (sandbox, capability hash, owner) is in PostgreSQL; on start, agent-service reloads active sessions, and their runtimes' long polls reconnect to the new process. | Deploys of agent-service don't end conversations. |
| C6 | **A session claims its pod at start** (the profile's template), and releases it on `DELETE`, after **5 idle minutes**, or at the profile's wall time. A lost sandbox is replaced once, **continuing the conversation** (C9); a second loss ends the session. | Ready by the first turn (~0.5 s); single-use pods (R6). |
| C7 | **The capability** is 256 random bits, given only to the runner (the session file); agent-service stores only its SHA-256. A request needs it **and** the pod's `X-Sandbox-Id`. It dies with the session. | A stolen capability is useless from another pod (the CP2 theft test) and after the session. |
| C8 | **History lives in PostgreSQL** (decision 2): conversations, sessions, turns, events. | Continuity, and the cost policy (C9). |
| C9 | **A new session in an existing conversation gets its last N turns as context** (the profile's `carry_turns`, default 6), as text after its instructions (the session file's `context`, read by the runtime). Within a session, the conversation only grows (decision 2: caching, reasoning state). | Continuity at a bounded, chosen cost. Summaries of older turns come in CP7. |
| C10 | **Nightly backup:** `pg_dump` saved **through file-management** to `private/agent-service/backups/` (it stays the only S3 client, F3), kept 14 days; a restore is tested. | History is the lab's first data that can't be rebuilt. |
| C11 | **The auth broker (model-access.md MA1–MA6) lives here:** your ChatGPT login in `/var/lib/agent-service/codex-home`, refreshed by Codex in this one process, each refreshed bundle written to OpenBao (`kv/services/agent-service/codex_auth`); restored from there if the disk is new. | One owner of the login (MA2); a rebuilt host comes back without a new login (MA3). |
| C12 | **Profiles are a reviewed file:** `infra/services/agent-service/profiles.yaml`, installed by every deploy (the deployer's `files`). | Changing what an agent may do is a reviewed diff (G5). |
| C13 | **Budgets are enforced at the gateway** (G14): turns per session, tokens per turn (from the runtime's usage events), and the profile's wall time; past one, the model route refuses and the turn fails with a clear `error`. | The sandbox is assumed compromised; only the gateway's limits are real. |
| C14 | **The kill switch:** `DELETE` a session; `POST /v1/admin/stop` (`sessions:admin`) ends every session and refuses new ones until `…/start`; or stop the runner. | One call stops everything, without touching hosts. |

## The sessions API (443, callers)
Auth: an auth-service token, `aud=agent-service`. Scopes: `sessions:use` (your own sessions),
`sessions:admin` (all, and the kill switch), `status:read` (api-service's status page).

| Route | What |
|---|---|
| `POST /v1/sessions` `{profile, conversation_id?}` | start a session: a new conversation, or continue one you own (C9). Claims a pod; returns `{session_id, conversation_id, state}`. `503` (`Retry-After`) if no sandbox. |
| `POST /v1/sessions/{id}/turns` `{text}` | one turn: streams protocol B (NDJSON) until its `turn.completed`. `409` if a turn is running. |
| `GET /v1/sessions/{id}/events?after=<seq>` | everything after `seq`, then live until the session ends (reconnect, G10) |
| `GET /v1/sessions/{id}` | its state: `starting`, `ready`, `busy`, `ended` (with the reason) |
| `POST /v1/sessions/{id}/cancel` | interrupt the running turn |
| `DELETE /v1/sessions/{id}` | end it: the pod is released |
| `GET /v1/conversations/{id}/turns` | the conversation's history (text, timings; tools at `full` visibility) |
| `POST /v1/admin/stop`, `POST /v1/admin/start` | the kill switch (`sessions:admin`) |
| `GET /v1/status` | for the lab's status page (`status:read`): sessions by state, pods held, PostgreSQL, the login's age, the last backup |
| `GET /health` | liveness only |

**Callers in CP3:** `smoke-test` (deploy smoke tests); the `agent` CLI on CT 107 (client `agent-cli`,
secret root-only there: `ssh agent-host agent ask "…"`); **api-service** relaying an authenticated
caller at `/v1/agent/…` (it's the public edge: a token is required; grant 3, `can_relay_clients`).

## Protocol B v0 (to callers)
**Envelope:** `v` (1), `session_id`, `conversation_id`, `turn_id` (none for session-level events),
`seq` (per session, only increases, across sandboxes), `ts`, `type`.

| `type` | Fields | From |
|---|---|---|
| `session.status` | `status`: `ready`, `sandbox_lost`, `retrying`, `ended`; `reason` | agent-service |
| `turn.started` | — | protocol A `turn.started` |
| `message.delta` | `text` | A `text.delta` |
| `message.completed` | `text` | A `text.completed` |
| `tool.started` | `tool_id`, `name`, `input`, `server` | A `tool.started` |
| `tool.completed` | `tool_id`, `is_error`, `output`, `truncated` | A `tool.completed` |
| `task.started` / `task.progress` / `task.completed` | `task_id`, `description`, `status`, `summary` | A `task.*` |
| `turn.completed` | `status`, `reason`, `first_text_ms`, `duration_ms`, `usage` | A `turn.completed` (agent-service adds `seq`, drops the engine's figures it doesn't own) |
| `error` | `code` (`no_sandbox`, `rate_limited`, `budget`, `runtime`, `internal`), `message` | agent-service, A `runtime.error`, A `model.rate_limited` |

**Visibility (the profile's `visibility`; a caller may ask for less with `?visibility=`, never more):**

| | `full` (Steven AI) | `summary` | `text` (voice) |
|---|---|---|---|
| messages, `turn.completed`, `session.status`, `error` | ✓ | ✓ | ✓ |
| `tool.*` | full input and output | name and status only | — |
| `task.*` | ✓ | description and status | — |
| `usage` in `turn.completed` | ✓ | — | — |

**Never sent, at any visibility:** the capability, sandbox ids, internal addresses, other sessions,
your plan's account details (A's `model.usage_updated` stays internal: the gateway uses it for C13).

## The gateway (8443, sandboxes)
Reached only through a pod's proxy (R12), which stamps `X-Sandbox-Id`.

| Route | What |
|---|---|
| `GET /v1/runtime/turns?wait=` | the runtime's long poll: turns, interrupts, end |
| `POST /v1/runtime/events` | protocol A events: each parsed alone (a bad one is dropped), translated to B, written (C4), sent on |
| `/model/{path}` | to OpenAI with your token (C11); the profile's model enforced; C13's budgets |

**Every request:** capability hash → its session; `X-Sandbox-Id` must be that session's pod (else 403,
logged); the session must be active. `X-Sandbox-Id` and `X-Forwarded-For` are never forwarded.
Shutdown waits at most 5 s for open long polls (the CP2 lesson), so its own shutdown work runs.

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
    visibility: full
    limits: {wall_seconds: 3600, max_turns: 100, max_turn_tokens: 200000}
    history: {carry_turns: 6}
  voice:
    template: chat
    effort: low
    codex_instructions: false      # 42% fewer tokens per call (agent-runtime.md Q3)
    visibility: text
    limits: {wall_seconds: 1800, max_turns: 60, max_turn_tokens: 50000}
    history: {carry_turns: 4}
```
What reaches the runtime (protocol A's `Profile` in the session file) is the subset it needs
(model, effort, instructions, `codex_instructions`, tools, wall time), plus `context` (C9).

## History (PostgreSQL)
**Tables** (Alembic migrations, reviewed in git; SQLAlchemy 2 with psycopg 3):

| Table | Holds | Key |
|---|---|---|
| `conversations` | owner, profile, created, last active | `id` (UUID) |
| `sessions` | conversation, owner, profile, sandbox id, capability hash, state, started, ended, end reason | `id` |
| `turns` | conversation, session, user text, reply text, status, timings, usage | `id`; ordered per conversation |
| `events` | session, `seq`, `ts`, `type`, turn, payload (the protocol B event, full fidelity) | (`session_id`, `seq`) |

- **Events are stored at full fidelity;** visibility is applied when they're sent. A caller's
  history shows what their visibility allows.
- **Retention:** turns kept; events 30 days (they're the detail; turns are the record).
- **Carry-over (C9):** at session start, the conversation's last `carry_turns` turns become
  `context`: "Earlier in this conversation:\nYou: …\nAssistant: …", added to the instructions.
- **The database:** `postgres:17`, a container in agent-service's compose project, on an internal
  network only, data in `/var/lib/agent-service/postgres`; its password a managed secret
  (`kv/services/agent-service/postgres_password`), delivered by the deployer.
- **Backups (C10):** agent-service runs `pg_dump --format=custom` nightly and uploads it through
  file-management with its own token (`private/agent-service/backups/<date>.dump`); deletes those older
  than 14 days. **Restore:** `pg_restore` into an empty database (a runbook step); tested in 3.10.

## Identity and secrets
| What | Where | Delivered |
|---|---|---|
| agent-service's client secret (its "secret zero") | `/etc/agent-service/credentials/client_secret` | `add-client.sh` (host-only) |
| PostgreSQL's password | `/etc/agent-service/credentials/postgres_password` | the deployer, from OpenBao |
| your ChatGPT login | `/var/lib/agent-service/codex-home/auth.json`, and OpenBao | the auth broker itself (C11): device code, once |

**auth-service policy:** audience `agent-service` (`sessions:use`, `sessions:admin`, `status:read`);
client `agent-service` granted `sandbox-runner` (`sandboxes:claim`, `:release`, `:read`),
`secrets-manager` (`secrets:read`), `file-management` (read, write, delete, list on
`private/agent-service/`); callers `smoke-test` and `agent-cli` granted `sessions:use`; api-service
granted `status:read` here, and relaying for `/v1/agent/…`.

**OpenBao:** role `agent-service` (auth-service tokens), a policy that may **read and write only**
`kv/services/agent-service/codex_auth` (OpenTofu; the generated `service-agent-service` policy covers
reading the PostgreSQL password).

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

## Open, for review
1. **Visibility for relayed callers:** should api-service's public `/v1/agent/…` route cap visibility
   (say at `summary`), whatever the profile says, until real user accounts exist?
2. **Events retention:** 30 days, then only turns? Or keep events forever (small at home scale)?
3. **The CLI's caller identity:** one shared `agent-cli` client, or should it act as you, once users
   exist?
4. **A lost sandbox:** replace it once and continue (C6), or end the session and let the caller start a
   new one in the same conversation?
