# Spec: agent-service (the assistant that answers and acts for you)

**Status: live** (2026-11-17). The runtime (CP1), the sandbox worker (CP2) and the control plane (CP3)
run in the lab: agent-service on CT 107 ([control-plane.md](control-plane.md)), sandbox-runner on VM 200
([sandbox-runner.md](sandbox-runner.md)). What's next is in [tracker.md](tracker.md) (CP4: voice). Parts of
this document are the original design and its reasoning; where it and a component's spec differ,
the component's spec is what's built.

**Locked decisions so far:**

| # | Decision | Document |
|---|---|---|
| 1 | Who runs sandboxes: sandbox-runner, a container on a dedicated VM, starting Docker sandboxes under gVisor | [sandbox-runner.md](sandbox-runner.md) |
| 2 | Conversation history: PostgreSQL in the control plane owns history and memory; context is curated at session start; append-only within a session. **Built in CP3** (history, carry-over; no backups for now, C10); summaries and memory in CP7 | "Conversation history and cost" below |
| 3 | The engine: the **Codex SDK** (OpenAI's `openai-codex`) runs the agent loop in the sandbox, replacing the Claude Agent SDK, so that your ChatGPT Pro subscription pays for the model | [agent-runtime.md](agent-runtime.md) |
| 4 | Model access: your subscription's login lives only in a trusted **auth broker** in agent-service (one owner refreshes it; the durable copy is in OpenBao); sandboxes hold only a capability; personal use only. The gateway adds your token on the way out (route A, proven in milestone 1) | [model-access.md](model-access.md) |

**Milestones** (the build order as checkpoints: [tracker.md](tracker.md)):

| Milestone | What | Document |
|---|---|---|
| M1 | the runtime on the Codex SDK, with a temporary dev gateway (no control plane). **Done** (2026-11-12): replies paid by your subscription, interrupts in 10 ms; gVisor moves to CP2 |
| CP2 | the sandbox worker: VM 200, sandbox-runner (pods, templates), gVisor, the Proxmox firewall. **Done** (2026-11-13): claims in 26 ms, ready in ~0.5 s, deployed by the pipeline | [sandbox-runner.md](sandbox-runner.md) |
| CP3 | the control plane: agent-service on CT 107 (sessions API, gateway, auth broker, PostgreSQL history), the `agent` CLI, api-service's relay. **Done** (2026-11-17): live end to end, through the deployer's pipeline | [control-plane.md](control-plane.md) |
| CP5 | tools: tools-service (one MCP server), the gateway's `/mcp`, each session's tools = its profile ∩ its owner's grants, by token exchange. **Agreed** (2026-11-26) | [tools.md](tools.md) |
| M2 | files as a mounted folder: a WebDAV view in file-management, a mounter sidecar, the gateway's files route, narrowed tokens | [files-and-mounts.md](files-and-mounts.md) |

**Replaces and absorbs:**
- `stevenai-service`: its RAG chat becomes one capability here;
- the **assistant** part of home-hub ([flutter_ui.md](../flutter_ui.md) D2): Hey Pi's assistant turns land here.

**Planned paths:**
- `python/services/agent-service/`: the control plane;
- `python/libs/agent-runtime/`: the package inside each sandbox;
- `python/services/sandbox-runner/`: the daemon on the worker VM that runs sandboxes
  ([sandbox-runner.md](sandbox-runner.md));
- `infra/agents/`: agent profiles and skills.

Related: [auth-service.md](../auth-service.md), [file-management.md](../file-management.md),
[api-service.md](../api-service.md), [deployer.md](../deployer.md), [secrets-manager.md](../secrets-manager.md);
in this folder: [tracker.md](tracker.md), [control-plane.md](control-plane.md), [agent-runtime.md](agent-runtime.md), [model-access.md](model-access.md),
[sandbox-runner.md](sandbox-runner.md), [files-and-mounts.md](files-and-mounts.md).

## What it is
One service you can ask anything about you and your home, by voice on Hey Pi, later from the phone:

| Request | What happens |
|---|---|
| "What's the weather today?" | the agent calls the weather tool |
| "Turn off the kitchen lights." | the agent calls Home Assistant through a tool; risky actions ask you to confirm |
| "What did I write about X?", "Summarize my notes on Y" | the agent searches your documents (RAG over your buckets) and reads files |
| "Rename these photos", "Build a table from these receipts" | the agent works on files in a **workspace**, with code, in its sandbox |
| Just chatting | the model alone |

**The pattern: a control plane and disposable sandboxes.** This is how hosted coding agents work:
- **GitHub's Copilot coding agent** runs each task in an ephemeral GitHub Actions environment,
  prepared by a setup file, behind a firewall, with MCP servers configured per repository. It
  clones the repository, works on a branch and hands back a pull request.
- **Claude Code on the web** runs each session in a cloud sandbox with configurable network
  access, and keeps credentials out of the sandbox behind a proxy.
- **Anthropic's Managed Agents** split it the same way: versioned *agent* configs, *environments*,
  per-session containers, and an event stream.

The common idea: **the control plane decides and holds every credential; the sandbox runs the
agent loop and holds nothing worth stealing.**

```
 ┌─ callers ─────────────────┐
 │ pi-server · apps · you    │
 └─────────────┬─────────────┘
               │ T1 (a token from auth-service)
               ▼                                                 ┌─ auth-service ────────┐
 ┌─ agent-service: trusted control plane (CT 107) ───────┐       │ issues T1             │
 │ sessions · profiles · policy (what a session may use) │◀─────▶│ exchanges T1 → T2     │
 │ PostgreSQL: conversations, turns, memory              │       │ (narrowed, for files) │
 │ event log → protocol B to callers                     │       └───────────────────────┘
 │                                                       │       ┌─ OpenBao ─────────────┐
 │ ┌─ gateway: the only door out of a sandbox ─────────┐ │◀─────▶│ your ChatGPT login    │
 │ │ model:   capability → auth broker ────────────────┼─┼──┐    │ (refreshed copy),     │
 │ │          (your token, refreshed in one place)     │ │  │    │ other keys            │
 │ │ tools:   capability → MCP servers (their keys)    │ │  │    └───────────────────────┘
 │ │ files:   capability → T2 ─────────────────────────┼─┼─┐└──▶ OpenAI
 │ │ runtime: turns in, events out                     │ │ │
 │ └───────────────────────▲───────────────────────────┘ │ └───▶ file-management ──▶ RustFS
 └──────────┬──────────────┼─────────────────────────────┘
            │ claim/release│ capability only
            ▼              │
 ┌─ sandbox VM ────────────┼─────────────────────────────────────────────────┐
 │ sandbox-runner: warm pool · claim · writes session file · teardown        │
 │ ┌─ one session: internal network, no route out ──────────────────────────┐│
 │ │ sandbox (gVisor)                     mounter (rclone, FUSE)            ││
 │ │  agent-runtime: Codex SDK + codex     reads inputs through the gateway ││
 │ │  /workspace/inputs ◀── read-only ─────┘                                ││
 │ │  /workspace/work, /workspace/outputs  (local disk; saved by publish)   ││
 │ │                 └─────── egress-proxy: the only exit, to the gateway   ││
 │ └────────────────────────────────────────────────────────────────────────┘│
 └───────────────────────────────────────────────────────────────────────────┘
   (the deployer ships every image above; it isn't part of a running session)
```

| Concern | Who controls it | What the sandbox holds |
|---|---|---|
| Session lifecycle, profile, policy | **agent-service** | — |
| Who is asking | **auth-service** (T1); files use the narrowed T2 | — |
| Model access (your subscription) | **the gateway + the auth broker**; the login's durable copy in **OpenBao** ([model-access.md](model-access.md)) | the capability |
| Sandbox start, warm pool, teardown | **sandbox-runner** | — |
| Isolation | **gVisor** (the host) and the **egress proxy** (the network) | — |
| File reads | the **mounter** → gateway → **file-management** | a read-only folder |
| File saves | **publish** → the gateway's checks → file-management | local `outputs/` |
| History and memory | **agent-service**, in PostgreSQL | the current session only |

## The same split as industry practice
This design follows the common architecture for agent sandboxes: **the control plane is trusted, the
sandbox is assumed compromised.**
- **The control plane owns policy:** sessions, access, credentials, model routing and budgets,
  lifecycle.
- **The sandbox owns execution:** the agent loop (the Codex SDK), its tools, its local workspace,
  its events.
- **Model, tool and storage gateways** (logically separate, here all inside agent-service) hold every
  real credential and authorize each call. The sandbox connects **out** to them; nothing connects
  into a sandbox.
- **The agent can request access to resources, but can't grant itself access.**

The stronger alternative, running the agent loop outside the sandbox and only tools inside (as
Anthropic's Managed Agents does), was considered. Running the agent SDK inside the sandbox behind
gateways is the simpler start, proven with the Claude Agent SDK in milestone 1's first run. The
alternative stays the fallback for model access (route B in [model-access.md](model-access.md)).

## Concepts
| Term | Meaning | In git or at runtime |
|---|---|---|
| **Profile** (agent) | what an agent is: instructions, model and effort, allowed tools, skills, which files it may use, limits, which actions need confirmation. Examples: `home`, `steven-ai`, `tasks`. | git: `infra/agents/<profile>/` |
| **Skill** | a folder with a `SKILL.md` and optional scripts: know-how loaded when relevant (Agent Skills) | git: `infra/agents/skills/<name>/` |
| **Tool** | an MCP server the gateway exposes to sessions: weather, Home Assistant, knowledge search, files | code (agent-service, or its own container) |
| **Sandbox image** | the container image every sandbox starts from: Python, the Codex SDK and its `codex` binary, `agent-runtime`, common CLIs | built in CI, like every image |
| **Session** | one conversation with one profile, in one sandbox, from first message to idle timeout | runtime |
| **Turn** | one request and the agent's reply inside a session, streamed as events | runtime |
| **Workspace** | the files a session works on: declared bucket prefixes, synced into `/workspace` | runtime, with S3 as the source of truth |

## Decisions
| ID | Decision | Why |
|---|---|---|
| G1 | **Control plane and data plane are separate processes on separate hosts.** agent-service (CT 107) holds profiles, credentials, sessions and history. Sandboxes run on a dedicated worker. | Model-written code and untrusted content (web pages, documents) run where nothing valuable lives. |
| G2 | **Sandboxes are single-use, from a warm pool.** The runner keeps N sandboxes started and idle with the runtime loaded. A session claims one, keeps it for its follow-ups, and on idle timeout (5 min) the workspace is saved and the sandbox **destroyed**, never reused. | Warm keeps "turn off the lights" fast; single-use means no leftovers between sessions. |
| G3 | **No real credential ever enters a sandbox.** A sandbox gets one **session capability**: a random token valid only for that session, and only from that sandbox (the egress proxy stamps each request with the sandbox's id), and only at the gateway. The gateway adds the real credentials on the way out: your subscription's model token ([model-access.md](model-access.md)), Home Assistant's token, file-management tokens. | Prompt injection can make an agent misbehave, but there's no key to steal. This is the same idea as Managed Agents' vault credentials and Claude Code on the web's proxy. |
| G4 | **Sandbox egress is denied by default:** a sandbox reaches the gateway and nothing else, neither the LAN nor the internet. Web access, when a profile allows it, goes through the gateway with an allowlist. | Like Copilot's agent firewall: an agent can't reach your router, Proxmox or the internet unless the profile says so. |
| G5 | **Profiles and skills are files in git** (profiles: `infra/services/agent-service/profiles.yaml`, installed by every deploy; skills later), reviewed like `policy.yaml`. A profile names: `model`, `effort`, `tools` (allowlist), `skills`, `workspace` (bucket prefixes, read or write), `confirm` (actions needing your yes), and limits (turns, tokens, wall time). | Changing what the assistant may do is a reviewed diff, not a setting in a UI. |
| G6 | **agent-service defines the API and ships the sandbox side as a package,** `agent-runtime`, installed in the sandbox image. It: fetches the session config from the gateway; runs the **Codex SDK** (built-in shell and file-edit tools, MCP, skills, subagents) with the gateway as its model provider; registers the gateway's MCP tools; syncs the workspace; streams events back. | One contract between control and data plane; the image is built and pinned by CI like everything else. |
| G7 | **Sandboxes never hold storage credentials; file-management stays the only S3 client** ([file-management.md](../file-management.md) D1, D3). A session's workspace has three areas: `inputs/` (its S3 locations, **mounted read-only** by a mounter sidecar through the egress proxy and gateway), `work/` (local scratch) and `outputs/` (local results). Saving to S3 happens only through an explicit **publish** checked by the gateway ([files-and-mounts.md](files-and-mounts.md)). | Reads are lazy and natural; editing happens on a real disk (S3 isn't POSIX); writes from code that may be compromised are explicit, checked and audited. |
| G8 | **A session acts on behalf of whoever asked.** pi-server's request carries pi-server's identity, and later a user's. The gateway calls file-management with a **downscoped** token (auth-service grant 4) limited to the session's workspace, so a sandbox can't reach beyond it even through the gateway. | Least privilege per session; every file access audited under the real requester. Needs auth-service's downscope gap fixed first ([auth-service.md](../auth-service.md), Known issues). |
| G9 | **Model per profile,** from the models Codex offers under your plan. `effort` per profile: `low` for home control and chat (latency), `high` for research and file tasks. A smaller, faster model is your call per profile if measured latency says so. | One strong default; effort is the first knob. |
| G10 | **Events are streamed, resumable and stored as they arrive:** Server-Sent Events with `id` = `seq`, and `Last-Event-ID` (or `GET …/events?after=<seq>`) to reconnect. agent-service writes each event to PostgreSQL before forwarding it, so a reconnecting client can recover what it missed even after a restart ([control-plane.md](control-plane.md) C4). | Voice clients drop; a long file task must not be lost with a connection or a restart. |
| G11 | **One execution path:** every request, even "what's the weather", runs in a warm sandbox. | One security model, one tool setup. Revisit with a fast path (agent loop in agent-service, no sandbox, tools only) only if measured latency hurts. |
| G12 | **Sensitive actions need confirmation.** Each tool action is `read`, `reversible` (lights, music) or `sensitive` (locks, garage, alarm, deleting files, anything that costs money). A profile's `confirm` list pauses a turn with a `confirmation_required` event until you say yes on Hey Pi. | Text from a web page or a document can't open your door. |
| G13 | **Sandboxes run on a dedicated VM under gVisor, managed by sandbox-runner** (locked: [sandbox-runner.md](sandbox-runner.md)). The runner, a container on the VM, creates, claims and destroys Docker sandboxes (`--runtime=runsc`) from a warm pool. Sandboxes sit on an internal network whose only exit is an egress proxy to the gateway. agent-service never touches Docker; it calls the runner's API with an auth-service token. | Model-written code is the least trusted code in the lab: a VM (its own kernel) plus gVisor is a much stronger wall than Docker in an LXC, and a Docker socket on the network would be root. |
| G14 | **Budgets are enforced at the gateway,** not only in the runtime. Per session: model tokens, tool calls, and published bytes; across sessions, how many run at once (they share your plan's allowance, model-access MA6); when one is exceeded, the gateway refuses further calls. The runtime's own limits (turns, wall time) are a courtesy: code inside the sandbox can call the gateway directly, so only the gateway's limits are real. | The sandbox is assumed compromised; a limit it could bypass isn't a limit. |

## How a turn works
1. **Hey Pi** sends `assistant_text` ("turn off the kitchen lights") to **pi-server**, which calls
   `POST /v1/sessions/{id}/turns` on agent-service with its auth-service token
   (`aud=agent-service`). Off the LAN, the phone comes through api-service, which relays the
   identity (api-service E1, E6).
2. **New session:** agent-service loads the profile (`home`), asks `sandbox-runner` for a warm
   sandbox and hands it a session capability.
3. **In the sandbox,** `agent-runtime`:
   - fetches the session config (instructions, tools, skills, workspace);
   - syncs `/workspace` if the profile has one;
   - runs the Codex SDK with the gateway as its model provider and the gateway's MCP tools.
4. **The model calls tools** (`home_assistant.turn_off(area=kitchen, domain=light)`). The gateway:
   - checks the call against the profile's allowlist and confirmation policy;
   - calls the real MCP server with the real credential;
   - returns the result.
5. **Events stream** back. The sandbox sends protocol A ([agent-runtime.md](agent-runtime.md)), and
   agent-service translates it into protocol B, the client events in shared-schemas (planned):
   `message.delta`, `activity`, `confirmation.requested`, `artifact.created`, `turn.completed`.
   pi-server passes them on; Hey Pi shows them and speaks the reply.
6. **The session stays warm** for follow-ups ("and the hallway"). After the idle timeout:
   - the workspace is pushed back;
   - the transcript is saved;
   - the sandbox is destroyed.

## API
**As built (CP3), in detail:** [control-plane.md](control-plane.md): the sessions API (443), protocol
B v0, the gateway (8443). Outline:

**agent-service**, for callers: an auth-service token with `aud=agent-service` and a `chat:*` scope;
outside clients go through api-service's relay (`https://api.liustev6.ca/v1/agent/…`, grant 3).

| Route | What |
|---|---|
| `POST /v1/sessions` `{profile, conversation_id?}` | a new session (201), or the conversation's live one (200) |
| `POST /v1/sessions/{id}/turns` `{text}` | one turn, streamed as Server-Sent Events until `turn.completed` |
| `GET /v1/sessions/{id}/events` (`Last-Event-ID` or `?after=`) | reconnect: everything after a `seq`, then live |
| `GET /v1/sessions/{id}`, `POST …/cancel`, `DELETE …` | its state; interrupt the turn; end it (the pod is released) |
| `GET /v1/conversations`, `GET /v1/conversations/{id}/turns` | history, across sessions |
| `POST /v1/admin/stop`, `/start` | the kill switch (`chat:admin`) |

**The gateway**, for sandboxes only (VM 200's firewall reaches only its port), authenticated with the
session capability and the pod's `X-Sandbox-Id`:

| Route | What | Status |
|---|---|---|
| `GET /v1/runtime/turns`, `POST /v1/runtime/events` | the runtime's long poll, and its events (protocol A) | built |
| `/model/…` (OpenAI's Responses API) | your subscription's token added (the auth broker); the profile's model and token budget enforced | built |
| `/mcp` (streamable HTTP MCP) | the session's tools, fixed at its start (profile ∩ the owner's grants), forwarded to tools-service with a token exchanged for the owner, plus the gateway's own (`end_conversation`); tool budgets; later confirmations (`POST /v1/sessions/{id}/confirmations/{call_id}` on the callers' side). [tools.md](tools.md) | CP5 |
| `/files/…` (WebDAV reads, for the mounter), `POST /publish` | file-management with the session's narrowed token ([files-and-mounts.md](files-and-mounts.md)) | later |

## Tools and skills
As agreed for CP5 (one MCP server, `tools-service`; filtering and delegation): [tools.md](tools.md).
- **MCP servers run outside the sandbox,** as trusted code with their own credentials:
  - **Weather:** Open-Meteo, which needs no key.
  - **Home Assistant:** HA's MCP server integration, or a small MCP server over HA's API. Each
    action is classified for G12.
  - **Files:** file-management, through the gateway.
  - **Knowledge:** document search (see "Knowledge").
  - Later: calendar, reminders, the Proxmox status a profile may read.
- **Skills** are reviewed folders in `infra/agents/skills/` (for example `home-routines`,
  `receipts-to-table`). The profile lists which ones a session gets. They're mounted read-only into
  the sandbox, so Codex loads them on demand.
- **Codex's built-in tools** (shell, file edits) act only inside the sandbox and its `/workspace`. That's safe
  because of G3, G4 and G13.

## Knowledge (steven-ai, upgraded)
- **Source of truth:** documents in a bucket prefix (for example `private/agent-service/knowledge/`,
  or a `knowledge` bucket). Files go in through file-management like any file.
- **Index:** embeddings (BGE, as stevenai-service uses today, on CPU or gpu-server) in a vector index
  that agent-service owns: LanceDB or sqlite-vec on disk to start, Qdrant if it grows. It is
  **derived** and rebuildable from the bucket. Reindexing runs on change or nightly.
- **Retrieval is a tool:** `search_knowledge(query)`. Every hit carries its object path, and results
  are **filtered by the session's grants**, so search never returns text from a prefix the session
  couldn't read. The agent then reads whole files under `/workspace/inputs` when it needs more than the
  snippet.
- **Migration:** stevenai-service's corpus (`rag_document.json`, its FAISS indexes) moves into the
  bucket, is reindexed, and becomes profile `steven-ai`. Then stevenai-service is retired.

## Data: what is stored where (and the database question)
| State | Where | Database? |
|---|---|---|
| File contents **and file metadata** (key, size, ETag, modified, content type) | RustFS through file-management; listing and HEAD are its API | **no**: S3 already is the catalog |
| Small extra metadata on a file (source, tags, "indexed at") | S3 object metadata or tags, set through file-management | no |
| Workspaces | the same buckets (G7) | no |
| Sessions in flight | agent-service memory | no |
| Conversations and turns (user text, reply, tools, usage, cost) | **PostgreSQL** in the control plane (decision 2) | **yes** |
| Memory ("I like the lights at 40 % in the evening"), summaries | **PostgreSQL** (decision 2) | **yes** |
| The raw event log per session (G10) | **PostgreSQL**, the `events` table: written before anyone sees it (control-plane.md C4) | yes (built) |
| Search index | derived from the bucket, rebuildable | a vector index file, not a database you manage |

**1. Does agent-service need a database for file metadata?** No; I agree with your take. A list of
files and their size, date and type is what S3 already answers through file-management's list
and HEAD. A second copy in a database would need syncing, drift, and add something to back up,
with no route that needs it.

**2. Where would file metadata live if it were needed: file-management or agent-service?**
**file-management.** Metadata about stored objects belongs to the service that owns storage, as
GitHub's repository service owns a repository and the Copilot agent just clones it. agent-service
is one client among several (api-service, pi-server, future services). If it owned file metadata,
every other client would bypass it, and it would go stale. Small per-file facts go into S3 object
metadata or tags, through file-management.

**3. Do only some buckets need a "file repository"?** Yes: add one **per bucket, only when a
question can't be answered by listing prefixes**:
- **Search by meaning or full text:** the knowledge index above.
- **Sharing records:** "user 7 can see user 42's folder" (later, the `users` bucket).
- **Queries across prefixes:** by tag, owner or date range.

Each one is a **derived** catalog: rebuildable from the bucket, with S3 the source of truth, and
owned by the service that needs that query. That's the knowledge component here; sharing would
sit with file-management or an authorization service. The same split exists at GitHub: code search
runs on its own index, separate from where the repositories are stored.

## Conversation history and cost (locked decision 2, a later step)
**PostgreSQL in the control plane owns conversation history and memory, and decides what goes into a
session's context.** That's where cost is controlled: which earlier turns are carried into a new
session, and which are kept, summarized or left out.

```
                 PostgreSQL (agent-service): conversations · turns (user text, reply, tools,
                 usage, cost, keep?) · summaries · memory
                        │                                   ▲
   at session start:    │ agent-service BUILDS the          │ after every turn: agent-service
   the cost policy      │ starting context                  │ STORES it (from protocol-A events)
                        ▼                                   │
           sandbox: the SDK runs the conversation ── append-only ──▶ events
```

| When | Who | What |
|---|---|---|
| **A session starts** (a new sandbox continuing a conversation, after an idle timeout or a crash) | agent-service | **selects** what to carry over, under a policy: the last N turns, turns marked "keep", a summary of older ones, relevant memory |
| **During the session** | the SDK, in the sandbox | appends each turn, never edits; the session's budget (G14) and the SDK's own compaction bound its growth |
| **After every turn** | agent-service | stores it from protocol-A events: user text, the `text.completed` reply, `tool.*`, `turn.completed` usage and cost |

**Why curate at session start, and never edit during a session:**
1. **Prompt caching works on prefixes.** Repeated history is cheap only while it stays
   byte-identical from the start; dropping a turn mid-conversation makes everything after it a cache
   miss, at full price.
2. **Reasoning state.** The model's reasoning from earlier turns is carried with the exact
   conversation; editing earlier turns discards it.

**How the context is seeded:** the session file gains an optional `context`: the selected earlier
turns and a summary, as text after the profile's instructions. It's simple, independent of the SDK
version, and you see exactly what goes in. Resuming an SDK transcript instead (exact continuation)
stays possible, but is tied to an internal file format.

**Not chosen:** rebuilding the context on every turn (a fresh SDK call per turn). It gives the most
control, but re-pays the SDK's startup each turn, caches badly and conflicts with preserved thinking:
slower and more expensive.

**What it adds:** the lab's first database (`flutter_ui.md` D4 anticipated PostgreSQL for home-hub),
which needs **backups**; `context` in protocol A's session file; tables for conversations, turns,
summaries and memory.

## Where it fits
| Piece | Change |
|---|---|
| pi-server | assistant turns go to agent-service (today: a mock); it stays the hardware layer and relay (flutter_ui.md) |
| home-hub's other half (feeds: weather widget, HA states, snapshots) | stays a separate concern; it can live in agent-service at first and split out if it grows |
| auth-service | a new audience `agent-service`; a client `agent-service` (it calls file-management and the knowledge store); pi-server granted `agent-service`; downscoping fixed and enabled for the gateway |
| file-management | unchanged; agent-service is a client, with its own folder `private/agent-service/` |
| api-service | later, a relay route for the phone (`client` mode) |
| deployer | agent-service and sandbox-runner deploy like any service (wave 1); the sandbox image is built in CI and pulled by the runner |
| hosts | CT 107 `agent-service` (small); the **sandbox-worker VM** ([sandbox-runner.md](sandbox-runner.md), "The worker VM") |

## Security notes
- **Prompt injection** (from web pages, documents, even file names) is assumed. The defenses are
  structural, not prompt-based:
  - no credentials in the sandbox (G3);
  - no network beyond the gateway (G4);
  - the tool allowlist per profile (G5);
  - confirmation for sensitive actions (G12);
  - files limited by a downscoped token (G8).
- **Isolation:** gVisor in a VM (G13); sandboxes are single-use (G2).
- **Limits per session:** tokens, tool calls, wall time and workspace size.
  Exceeding them ends the turn with a clear result.
- **Audit:** every tool call and file access is logged with session, profile and requester.
  file-management and auth-service audit their side too.
- **Kill switch:** `DELETE` a session, drain the pool, or stop the runner. Nothing keeps running
  without the control plane.

### Who authenticates how (the auth map, as built in CP3)
| Hop | Credential | Checked by | Notes |
|---|---|---|---|
| a machine client → api-service (the public edge) | its auth-service token, `aud=api-service`, with `chat:*` | api-service (service-auth: auth-service's public keys) | mode `client`: machine clients calling as themselves |
| api-service → agent-service (the relay) | a **relayed** token (RFC 8693, grant 3), `aud=agent-service`: the **caller's** identity and grants | agent-service | the caller's own token never leaves api-service (E4) |
| `agent-cli`, `smoke-test` → agent-service | their own token, `aud=agent-service` | agent-service | each route needs its `chat:*` scope; sessions are their owner's (C2) |
| api-service → agent-service, sandbox-runner `/v1/status` | api-service's own token, `status:read` | the service | for the public status page |
| agent-service → sandbox-runner | agent-service's token, `aud=sandbox-runner` | the runner | claim, release, list |
| a runtime → agent-service's gateway | the session's **capability** (256 bits, in the session file) **and** the pod's `X-Sandbox-Id` | the gateway: the capability's hash, then the pod | the capability is useless from another pod (C7) |
| agent-service's gateway → OpenAI | **your ChatGPT login** (the auth broker) | OpenAI | swapped in at the gateway; no sandbox ever holds it (MA1) |
| agent-service → OpenBao | its token, `aud=secrets-manager` → OpenBao role `agent-service` | OpenBao | **the one exception:** the only service that **writes** to OpenBao, and only one path, its login (`kv/services/agent-service/codex_auth`, C11). Every other service's secrets reach it only through the deployer |

**Why the gateway trusts the proxy's `X-Sandbox-Id`:** a sandbox can't reach the gateway except
through its own pod's proxy. Its network is internal (`inhibit_ipv4`, no route out), and VM 200's
firewall lets only port 8443 of CT 107 out. The proxy **overwrites** the header with its own pod's
id, whatever the sandbox sent, and the sandbox can't change the proxy's config (read-only, outside
the sandbox). So the stamp names the pod the request really came from (sandbox-runner.md R12).

## Build order
The build order is a sequence of checkpoints, tracked in [tracker.md](tracker.md): what each one
proves, when it's done, and the sub-checkpoints of the one in progress.

## Open questions
- **Latency budget for voice:** if a warm sandbox plus the SDK's startup adds too much to "turn off
  the lights", add the fast path (G11)?
- **Local models:** route some profiles or embeddings to gpu-server (Qwen, Llama, BGE) through the
  gateway?
- **Where the Hey Pi feeds live:** in agent-service at first, or a separate home-hub from the start?
- **Confirmation by voice:** what counts as a "yes", and how long a confirmation stays valid.

## Non-goals
- Multiple users and accounts (until an identity provider exists). The subscription is for you only
  ([model-access.md](model-access.md) MA5).
- Running agents on the Pi itself (it stays a client).
- A general-purpose code-hosting agent; this is a personal and home assistant that can work on
  files.
- Exposing sandboxes or the gateway beyond the LAN.
