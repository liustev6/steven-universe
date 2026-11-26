# Spec: tools (CP5: tools-service, the gateway's `/mcp`, filtering and delegation)

**Status: live** (2026-11-26): CP5's core, 5.1–5.6, built and checked live ([tracker.md](tracker.md)).
**Later:** Home Assistant's tools and confirmations (G12), which close CP5. Part of
[agent-service](README.md). Related: [control-plane.md](control-plane.md) (the gateway, profiles),
[agent-runtime.md](agent-runtime.md) (Codex in the sandbox), [auth-service.md](../auth-service.md)
(grants, token exchange), [voice-path.md](../voice-path.md) (Hey Pi), tools-service's
[README](../../../python/services/tools-service/README.md).

## What it is
The agents' own tools, served by **tools-service** (CT 109, `tools.lan.liustev6.ca`): one service, one
tool registry, **two interfaces**: MCP for agents (port 8443) and a REST API for apps (port 443), with
the same filter on both. **Codex, in the sandbox, is the MCP client**: it reaches the tools only
through agent-service's gateway (`/mcp`), which turns the session into a token delegated by the
session's caller. Codex's own tools (web search, run at OpenAI) stay separate.

Live today: `weather/current`, `weather/forecast` (Open-Meteo) and `pi/end_conversation` (the agent
ends Hey Pi's voice session). On the Pi, "明天天气怎么样" is answered by `weather_forecast`, and
"好了，谢谢" ends the conversation.

```
pi-server ──turn (its token)──▶ agent-service ──claim──▶ sandbox: Codex (MCP client)
                                     │                         │ tools/list, tools/call
                                     │                         ▼ capability + X-Sandbox-Id (C7)
                                     │                  gateway /mcp ── token exchange (grant 3) ──▶ auth-service
                                     │                         │ the exchanged token (sub = the caller, act = agent-service)
                                     │                         ▼
an app (Hey Pi's dashboard) ── its own token ──▶ tools-service: REST API (443) │ MCP (8443) ──▶ Open-Meteo
                                     └── the model: gateway /model ──▶ OpenAI
```

## Decisions
| ID | Decision | Why |
|---|---|---|
| TL1 | **tools-service: one standalone service** on its own LXC (CT 109), deployed by the pipeline, holding every tool under a namespace (`weather`, `pi`; later `home`, `files`), with its own credentials (later Home Assistant's token, from OpenBao). Its MCP side: Streamable HTTP, the official MCP Python SDK (2.3). Split only when a namespace needs its own deploy or credentials; the gateway then routes by namespace | One server any MCP client can use, each seeing what its token allows. The tools' credentials aren't next to the control plane |
| TL2 | **The sandbox reaches tools only through the gateway's one `/mcp`**, as it reaches the model only through `/model` (G4) | One door to guard: the session's policy (its list, budget, later confirmations) applies in one place |
| TL3 | **No delegation in the sandbox:** it holds only its session capability (G3, C7). The gateway resolves capability → session → caller, and the delegated token is made and used on the trusted side only | The sandbox is assumed compromised: nothing in it is worth stealing |
| TL4 | **The tools token is a token exchange** (RFC 8693, auth-service's grant 3, the relay): the subject is the caller's own token (`aud=agent-service`), the audience `tools-service`. The result: `sub` = the caller (`service:pi-server`), `act = {sub: service:agent-service}`, at most 10 minutes. Exchanged at the session's start (to learn its tools) and at each turn's first tool call (the turn's caller's token, kept in memory for that turn only). **Never a passthrough** | MCP's authorization rules forbid passing a token on and require its audience checked. Every call is audited under who asked (G8) |
| TL5 | **A session's tools = the caller's grant at tools-service, narrowed by its profile only if the profile says so.** The grant is required and an allowlist (`requires_details`: a grant without `tool_access` is refused by the policy check, and no token is minted without details). By default the exchange asks for nothing and gets the whole grant; a profile's optional **`mcp_tools`** (`[weather/]`) asks for those and gets the overlap (`grant_overlap`, RFC 6749 §3.3; nothing in common is refused); `mcp_tools: []` = none. Scopes stay verbs (`tools:call`); tools are `authorization_details` of type `tool_access` (RFC 9396) | The grant is the security ceiling, reviewed once in the policy; a profile narrows for the agent's sake (fewer tools, fewer wrong choices) and is never a second copy of it |
| TL6 | **tools-service decides by its token alone:** `tools/list` and `GET /v1/tools` show the tools the token's `tool_access` covers; every call checks again. A tool not granted is answered as unknown (MCP) or 403 (API) and audited | The same rule for every client; the gateway's check isn't the only one |
| TL7 | **A session's list is fixed when it starts:** looked up before the claim (at most 3 s; a start never fails or waits longer on tools), stored with the session (`sessions.tools`, so a restart keeps it), answered from there to the sandbox's `tools/list`. A change of grants applies to the next session. A call outside the list is refused at the gateway, logged, never forwarded | The model never sees a tool it can't call; a stable list keeps the prompt cache. A refused call means a bug or an injection, never normal traffic |
| TL8 | **Skills are filtered by the same set:** a skill declares `requires_tools`; a session gets a profile's skill only if all of them are in its tools. **Not built yet:** there are no skills | No skill tells the model to use a tool it doesn't have |
| TL9 | **Each tool declares a class:** `read`, `reversible` or `sensitive` (G12), MCP's `readOnlyHint` and `destructiveHint` set to match. **Confirmations come with the first sensitive tool** (Home Assistant) | The class is the tool's own fact; confirming needs something worth confirming |
| TL10 | **Tool calls are budgeted at the gateway** (G14): `limits.max_tool_calls` per session (default 20), counted in PostgreSQL before each forward | The sandbox's own limits are a courtesy (C13); a counter in the table survives a restart |
| TL11 | **A client's own tools are tools-service tools in its namespace:** `pi/end_conversation`, granted to pi-server alone. tools-service only acknowledges it; pi-server, which owns the voice session, sees the call in the turn's events and ends the session once the reply has played. The gateway has no tools of its own | One kind of tool and one rule for all of them; the client that owns an action carries it out |
| TL12 | **Audit:** tools-service logs `sub`, `act`, `client`, the token's `jti`, the tool, the outcome and duration; the gateway logs the session, the tool, the outcome and the same `jti` (the join). Never the arguments or the result. Each call also lands in the session's history as protocol B `tool.started`/`tool.completed` (what the sandbox reports) | Every call traced to its session and requester, without copying what was said |
| TL13 | **Two interfaces, two listeners, one filter.** One process and one registry on two ports, as agent-service's two listeners (C1): **443, the REST API** for apps (`GET /v1/tools`, `GET /v1/weather/current?place=…`, `GET /v1/weather/forecast?place=…&days=…`), **8443, MCP** for agents. Each tool is registered once (location, MCP name, arguments, function, its API route if it has one); both sides take the same call path. A tool's logic is its own module (`app/weather/`) | One grant per tool for every way of using it. Domain logic stays out of api-service (E6); weather moves to a home-hub later, if one is built, with its routes and grants unchanged |
| TL14 | **The session's tools are listed in its instructions** when its profile says `tool_instructions: append`: agent-service writes one line per tool (the name the model sees, `mcp__tools__weather_forecast`, and its description's first sentence), sorted, under "prefer them over a web search whenever one fits" | Codex keeps MCP tools behind its tool search while built-in tools are always in hand: told nothing, the model took a web search (live, 3 of 3) |
| TL15 | **The instructions, from what never changes to what changes most:** the profile's own text, the tool list (the same for the same grants), the caller's (pi-server: its city and time zone), the carried-over turns. The time goes with each turn (`[now: …]` before the turn's text), not in the instructions | The prompt cache shares the longest identical start of a prompt, across sessions too: the first turn of a fresh session went from 5.6–17.2k cached tokens to a steady 17.4k of ~27k |

## Auth on each hop
| Hop | Credential | Checked by |
|---|---|---|
| Hey Pi → pi-server | none (WebSocket on the Pi's loopback) | loopback only |
| pi-server → agent-service | pi-server's token: `aud=agent-service`, `sub=service:pi-server`, `chat:write`, fresh per request | agent-service: the session's owner is `sub` (C2) |
| agent-service → sandbox | the **capability** (256 bits, this session; the runner's session file) | — |
| sandbox → gateway `/mcp` | capability + `X-Sandbox-Id` (the pod's proxy stamps it) | gateway: the session, its list, its budget |
| gateway → auth-service | agent-service's client credential; `subject_token` = the caller's token; `audience=tools-service`; `scope=tools:call`; `authorization_details` only for a profile's `mcp_tools` | auth-service: subject `aud` = agent-service, `can_relay_clients` with `relay_audiences: [tools-service]`; the caller's grant, or its overlap |
| gateway → tools-service | the exchanged token: `aud=tools-service`, `sub=service:pi-server`, `act={sub: service:agent-service}`, `tool_access` | tools-service: signature, audience, details |
| an app → tools-service's API | the app's own token (`aud=tools-service`, `tools:call`, its grant); the phone later through api-service's relay | tools-service: the same check as MCP's |
| tools-service → an outside API | that API's own credential (Open-Meteo: none; later Home Assistant's token) | the API |

## A tool call
1. **Session start** (agent-service): the caller's token is exchanged and tools-service's `tools/list`
   asked, **before the claim** (TL7); the list is stored with the session, and written into its
   instructions when the profile says so (TL14). Then the claim writes the session file.
2. **Codex starts** with one MCP server, the gateway's `/mcp`, and lists the session's tools.
3. **The model looks a tool up** (Codex's tool search, the first time in a session) **and calls it**;
   Codex sends `tools/call` to `/mcp`.
4. **The gateway:** the pod's pairing, the name in the session's list, the budget (counted in the
   table), the turn's caller's token exchanged; the call forwarded to tools-service as an MCP client.
5. **tools-service** checks the token, runs the tool, returns its result (text and structured content);
   it goes back unchanged to Codex, then to the model.
6. **History:** the runtime reports the call as protocol A `tool.started`/`tool.completed`
   (`tools.weather_forecast`), stored as protocol B in the session's events; pi-server sees them in the
   turn's stream (5.6 acts on `pi_end_conversation`).

## The pieces
| Where | What |
|---|---|
| auth-service: `app/permissions.py`, `app/issuer.py`; `infra/services/auth-service/policy.yaml` | the `tools-service` audience (`tools:call`, `tool_access` with prefix locations: `weather/` = the namespace), `grant_overlap`, `requires_details`; `relay_audiences`. Grants: pi-server `weather/`, `pi/`; smoke-test `weather/forecast` |
| tools-service: `python/services/tools-service/` | `app/registry.py` (each tool once; the one call path: check, arguments, run, audit), `app/access.py` (the decision), `app/api.py` and `app/mcp_server.py` (the two interfaces; MCP with the SDK's own resource-server auth, stateless), `app/weather/` (Open-Meteo in the dashboard's format, cached), `app/pi/` (`end_conversation`); `infra/services/tools-service/` |
| agent-service: `app/tools_service.py` | the exchange, `list`, `call` (an MCP client per call), the tool list for the instructions, a token's `jti` |
| agent-service: `app/sessions/manager.py`, `app/gateway/mcp.py` | the start's lookup (before the claim, 3 s), `sessions.tools` and `tool_calls_used` (migration 0003), `call_tool` (list, caller token, budget); `/mcp`: one stateless MCP server for every session, each request paired, the session carried in a context variable |
| agent-service: `app/profiles.py`, `infra/services/agent-service/profiles.yaml` | `tools` (Codex's built-ins only), `mcp_tools`, `tool_instructions`, `limits.max_tool_calls`; `pi`: the tool list on, "when one of your tools fits, use it, never a web search", "when the user wants you to stop or is done, call `pi_end_conversation`, then one short goodbye", the per-turn time for reference only. `smoke`: no tools |
| agent-runtime: `core/agent.py` (`codex_overrides`) | Codex's MCP server, keys checked on the pinned Codex 0.167.2: `mcp_servers.tools.url = <gateway>/mcp`, `bearer_token_env_var = AGENT_CAPABILITY`, `default_tools_approval_mode = "approve"` (allowed and refused at the gateway, never declined by Codex's own approvals under `approval_policy = never`), `startup_timeout_sec = 10`, `tool_timeout_sec = 30` |
| pi-server: `app/voice/agent.py`, `app/voice/assistant.py` | client info without the time, `now_line` before each turn; `pi_end_conversation` in a turn's events ends the session after the reply has played |

**Tool names:** in MCP, `<namespace>_<tool>` (`weather_forecast`; the model's function names allow
only `[A-Za-z0-9_-]`), seen by the model as `mcp__tools__weather_forecast`, reported in events as
`tools.weather_forecast`; in grants and profiles, `<namespace>/<tool>`.

## Measured (2026-11-26)
- **The tools are used:** on fresh `pi` sessions, as pi-server sends them: "明天天气怎么样" →
  `weather_forecast` (days 2), "现在外面冷吗" → `weather_current`, "this weekend" → `weather_forecast`
  (days 7), each follow-up too; none a web search. Each starts with its spoken line ("稍等，我查一下…").
- **Ending:** "停吧", "Stop, that is all.", "休息一下吧", "别说了", "好了，谢谢", "没事了" (after a
  question: 4 of 4) → `pi_end_conversation` and one goodbye; "谢谢，那后天呢？" → the forecast, not the
  end. By voice on the Pi: "谢谢。好了，休息一下吧。" → the goodbye played whole, the session ended the
  same millisecond, released.
- **Time:** a weather answer ~6–8 s from what was heard (the first tool call of a session pays
  Codex's tool lookup, ~2 s); a goodbye with the end tool ~5 s to its first word.
- **The prompt cache:** a first turn's cached input steady at ~17.4k of ~27k (~64%), from 5.6–17.2k
  with the time in the instructions; later turns mostly cached (20–27k).
- **Safety:** below; every check live.

## Safety (checked live, 5.5)
**The threat model:** the sandbox is compromised. A prompt injection (in what the user says, a web page,
a tool's result) or the model's own code (Codex's `functions.exec` runs in the sandbox) can send the
gateway anything. So the checks don't depend on the model behaving: they're the gateway's and
tools-service's, on every request, whoever wrote it.

| Attack | Stopped by | Live result |
|---|---|---|
| an injected instruction to call a tool the session doesn't have | the session's list (TL7): the model has no such tool, and the gateway refuses the name anyway | the model refused; `home_unlock`, `admin_shell` sent straight from the pod: "Unknown tool", logged, never forwarded |
| a real tool, called by code between turns | no caller token outside a turn (TL4) | refused, nothing forwarded |
| a caller with no grant, using a profile that has tools | the exchange (no grant at tools-service) | 0 tools; its pod's call: "Unknown tool" |
| a loop of calls | `max_tool_calls`, counted in PostgreSQL (TL10) | 20 answered, then refused; tools-service saw exactly 20 |
| the capability used from another pod | pairing (C7) | 403, logged |
| a token in the sandbox | none ever enters it (TL3) | the sandbox holds only its capability |

Not staged: an injection inside a real tool result (tools-service's are our own) or a web page. The
checks above don't depend on where an instruction came from.

## Found along the way
- **Codex 0.167.2 keeps MCP tools behind its tool search** (`tool_search_always_defer_mcp_tools`, not
  configurable for an MCP server): the model looked a tool up only when told to. Answered by TL14; the
  lookup costs ~2 s before a session's first tool call (4.15's speed work).
- **The start's race:** the runtime starts the moment the claim writes the session file; with the tools
  looked up alongside the claim, Codex could list them before they were known. Answered by looking them
  up before the claim (TL7).
- **The model echoed the per-turn time** ("[now: …] 好的…"), the pattern every message showed it:
  answered by the profile ("for your reference; never include it in your response").
- **Two goodbyes:** "say a line before any tool call" applied to the end tool too ("Goodbye! Goodbye!"):
  the end tool takes no line before it.
- **Codex exposes its own tools** beyond a profile's `tools` (`functions.exec`, sub-agents, goals): they
  act only inside the sandbox (G3, G4, G13), but they're prompt tokens on every call (the ~5,800 of
  CP1's Q3): 4.15.

## History
Built and checked live on 2026-11-26:
- **5.1** auth-service: the audience, the relay to tools-service only, the overlap, and "no details =
  no grant" (`a69d648`, `e8da8fc`): a real pi-server token exchanged for `weather/` + `home/` came back
  `weather/` only, `sub=service:pi-server`, `act=agent-service`.
- **5.2** tools-service on CT 109 through the pipeline (`41cfc58`, `2daf6e6`): its smoke test 9/9; a
  pi-server token sees its 3 tools on both interfaces, smoke-test's only `weather_forecast`.
- **5.3** the gateway's `/mcp` (`7451d79`): from a session's own pod, its list, a call during a turn
  (audited `sub=service:pi-server`, `act=agent-service`), refusals; another pod's capability 403.
- **5.4** Codex on `/mcp` (`0aa703d`), the tool list in the instructions and the time per turn
  (`0a8bbf7`): the Pi's weather questions answered by our tools; the first turn's cache steady.
- **5.5** the safety checks, live (above).
- **5.6** `pi/end_conversation` (`3335398`), the stop rule (`a23100f`, `21e5599`): by voice on the Pi.

**Later (closes CP5):** Home Assistant's tools (`home/`, each with its class; grants to exact tools
for anything that acts) and confirmations (G12): "turn off the kitchen lights" works, "unlock the door"
asks you first on Hey Pi.

## Open questions
- **A turn longer than its token** (10 minutes, `exp` ≤ the caller's token's): tools stop working
  mid-turn. Voice turns are seconds; a long task profile will need a fresh exchange with a newer caller
  token, or a longer-lived narrowed token (grant 4, after its known issue is fixed).
- **A per-turn tool cap** beside the per-session budget, against one turn looping.
- **Trimming Codex's own tools and context** (4.15).

## Not chosen
- **The same tool list for every session, refused per call:** the model would see tools it can't
  use and collect refusals.
- **A delegated token in the sandbox:** against G3; it would be stolen with the sandbox.
- **One scope per tool:** scopes are verbs (C2); resources go in `authorization_details`.
- **tools-service filtering by client id in its own code:** the token's grants already say it, and a
  second list would drift from the policy.
- **A profile listing its tools as a second copy of the grant:** the grant decides; a profile only
  narrows (TL5).
- **Weather's API in api-service:** the edge holds no business logic, and tools-service would call it
  from the LAN (E6).
- **home-hub as its own service now:** a second LXC with one caller; TL13 keeps the shape.
- **One gateway route per MCP server** (`/mcp/{server}`), or **one MCP session per agent session**:
  one stateless server with each request paired survives a restart and needs nothing more.
- **A bracket filter in pi-server** against the echoed time: the instruction alone is enough.
