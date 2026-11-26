# Spec: tools (CP5: tools-service, the gateway's `/mcp`, filtering and delegation)

**Status: agreed** (2026-11-26); building in CP5 ([tracker.md](tracker.md)). Part of
[agent-service](README.md). Related: [control-plane.md](control-plane.md) (the gateway, profiles),
[agent-runtime.md](agent-runtime.md) (Codex in the sandbox), [auth-service.md](../auth-service.md)
(grants, token exchange), [voice-path.md](../voice-path.md) (`end_conversation`).

## What it is
The agent's own tools (weather first), served by **tools-service**, one standalone MCP server.
**Codex, in the sandbox, is the MCP client**; it reaches the tools only through agent-service's
gateway, which turns the session into a delegated token. Web search stays Codex's own, run at OpenAI
(a hosted tool: no MCP, no sandbox traffic).

```
pi-server ──turn (its token)──▶ agent-service ──▶ sandbox: Codex (MCP client)
                                     │                   │ tools/list, tools/call
                                     │                   ▼ capability + X-Sandbox-Id
                                     │            gateway /mcp ── token exchange (grant 3) ──▶ auth-service
                                     │                   │ the exchanged token
                                     │                   ▼
                                     │            tools-service (MCP server) ──▶ Open-Meteo, later Home Assistant
                                     └── the model: gateway /model ──▶ OpenAI
```

## Decisions
| ID | Decision | Why |
|---|---|---|
| TL1 | **tools-service: one standalone MCP server** (streamable HTTP, the official MCP Python SDK) holding every tool under a namespace (`weather.*`, later `home.*`, `files.*`), with its own credentials (Home Assistant's token from OpenBao), on **its own LXC**, deployed by the pipeline. Split into several servers only when a namespace needs its own deploy or credentials; the gateway then routes by namespace | One server any MCP client can use (Codex in a sandbox, Claude Code on the Mac, the phone later), each seeing what its token allows. The tools' credentials aren't next to the control plane |
| TL2 | **The sandbox reaches tools only through the gateway's one `/mcp`** (replaces the README's `/mcp/{server}`), as it reaches the model only through `/model` (G4) | One door to guard; the MCP gateway pattern: the session's policy (profile, budgets, later confirmations) applies in one place |
| TL3 | **No delegation in the sandbox:** it holds only the session capability (G3, C7). The gateway resolves capability → session → owner and profile, and makes the delegated token on the trusted side; that token never enters the sandbox | The sandbox is assumed compromised (prompt injection, model-written code): nothing in it is worth stealing |
| TL4 | **The tools token is a token exchange** (RFC 8693, auth-service grant 3, the relay): the subject is the caller's own token (at session start, `POST /v1/sessions`; then each turn's), the audience `tools-service`, `authorization_details` the profile's tools. The result: `sub` = the session's owner (`service:pi-server`), `act = {sub: service:agent-service}`, at most 10 minutes. Kept in memory for the turn, never stored. **Never a passthrough:** the gateway doesn't forward the capability or the caller's token | MCP's authorization rules forbid token passthrough and require the audience checked. Every tool call is audited under who asked (G8) |
| TL5 | **A session's tools = the profile's tools ∩ the owner's grants** (∩ a user's, when there are users). The profile says what the agent is for; the grants, what the requester may do. **Scopes stay verbs** (`tools:call`); tool names are `authorization_details` of type `tool_access` (RFC 9396, the way C2 planned per-tool limits). **auth-service grants the overlap and says what it granted** (RFC 6749 §3.3), where today asking for more than the policy is refused | Fewer tools: a smaller prompt and fewer wrong choices; the grants are the security ceiling. No scope per tool |
| TL6 | **tools-service filters by its token alone:** `tools/list` returns the tools the token's details allow; `tools/call` checks again | The same rule for every client; the gateway's check isn't the only one |
| TL7 | **The list is fixed when the session starts:** agent-service exchanges the `POST /v1/sessions` token, stores the session's tool names, and keeps tools-service's `tools/list` answer; the gateway answers the sandbox's `tools/list` from it. No `notifications/tools/list_changed`; a change of grants applies to the next session. A call outside the list is refused at the gateway (and at tools-service) and logged as a security event | The model never sees a tool it can't call, so no rounds of 403s; a stable list keeps the prompt cache. A refused call means a bug or an injection, never normal traffic |
| TL8 | **Skills are filtered by the same set:** a skill declares `requires_tools` in its frontmatter; a session gets a profile's skill only if all of them are in its tools | No skill tells the model to use a tool it doesn't have |
| TL9 | **Each tool declares a class:** `read`, `reversible` or `sensitive` (G12), with MCP's `readOnlyHint` and `destructiveHint` set to match (trusted here: tools-service is our code). **Confirmations come with the first sensitive tool** (Home Assistant), not in CP5 | The class is the tool's own fact; confirming needs something worth confirming |
| TL10 | **Tool calls are budgeted at the gateway** (G14): `limits.max_tool_calls` per session; past it, `/mcp` refuses | The sandbox's own limits are a courtesy (C13) |
| TL11 | **Tools that act on the session are the gateway's own**, served by the same `/mcp` beside tools-service's: `end_conversation` (the agent ends the voice session: voice-path.md 4.13). The profile lists them like any tool | They change agent-service's state, not an outside service's: no token exchange, nothing to call |
| TL12 | **Audit:** tools-service logs `sub`, `act`, the token's `jti`, the tool, the outcome and duration (never the arguments or the result: they can be personal); the gateway logs the session with each `jti` | Every call traced to its session and requester, without copying what was said |

## Auth on each hop
| Hop | Credential | Checked by |
|---|---|---|
| Hey Pi → pi-server | none (WebSocket on the Pi's loopback) | loopback only |
| pi-server → agent-service | pi-server's token: `aud=agent-service`, `sub=service:pi-server`, `chat:write` (grant 1), a fresh one each request | agent-service: the session's owner is `sub` (C2) |
| agent-service → sandbox | the **capability** (256 bits, this session; the runner's session file) | — |
| sandbox → gateway `/mcp` | capability + `X-Sandbox-Id` (the egress proxy's stamp) | gateway: the session, its owner, profile and tools |
| gateway → auth-service | agent-service's client credential; `subject_token` = the caller's token; `audience=tools-service`; `authorization_details=[{type: tool_access, actions: [call], locations: <the profile's tools>}]` | auth-service: subject `aud` = agent-service, `can_relay_clients`, the overlap with the owner's grants at tools-service |
| gateway → tools-service | the exchanged token: `aud=tools-service`, `sub=service:pi-server`, `act={sub: service:agent-service}`, `scope=tools:call`, the granted `tool_access` | tools-service: signature, audience, details |
| tools-service → an outside API | that API's own credential (Open-Meteo: none; Home Assistant: its token, from OpenBao) | the API |
| tools-service → file-management (CP6) | grant 3 again: `sub=service:pi-server`, `act` chain tools-service → agent-service (2 deep; the limit is 3) | file-management |

## A tool call
1. **Session start:** agent-service exchanges the caller's token (TL4), stores the session's tools, and
   keeps tools-service's `tools/list` for them (TL7).
2. **Codex starts** and connects to its one MCP server, the gateway's `/mcp`; `tools/list` returns the
   session's tools.
3. **Each model call** (`/model`) carries them as function definitions, with Codex's own.
4. **The model asks for a call** (`weather.forecast(…)`); Codex sends MCP `tools/call` to `/mcp`.
5. **The gateway:** the session (capability + pod), the tool in its list, the budget (TL10); this
   turn's exchanged token; the call forwarded to tools-service.
6. **tools-service** checks the token, runs the tool, returns the result; Codex sends it to the
   model in the next `/model` call. The runtime already reports `mcpToolCall` items as protocol A
   events, so each call shows in the session's events.

## The pieces
- **Codex's configuration** (`agent-runtime`, `codex_config`), beside the model provider it has:
  `mcp_servers.tools.url = <gateway>/mcp` and the capability as its bearer token
  (`bearer_token_env_var = AGENT_CAPABILITY`). The exact keys are checked against the pinned Codex
  in 5.4. No tool list in the image or the configuration.
- **Profiles** (`profiles.yaml`): `tools` lists Codex's own (`web_search`) and MCP tools by name or
  namespace (`weather.*`, `end_conversation`); `limits.max_tool_calls`; skills with `requires_tools`.
- **auth-service** (`policy.yaml`): the audience `tools-service` (`scopes: [tools:call]`, the detail type
  `tool_access`: `actions: [call]`, locations = tool names, prefix match on `.`); agent-service gets
  `can_relay_clients`; each client's tools are its grants there (pi-server: `weather.*`).

## Steps
| Step | What | Done when |
|---|---|---|
| 5.1 | **auth-service:** the `tools-service` audience and `tool_access`; agent-service relays; the overlap granted (TL5) | an exchange returns profile ∩ grants; its audit line shows `sub` and `act` |
| 5.2 | **tools-service** on its LXC, through the pipeline: `weather.*` on Open-Meteo; tokens checked; the list filtered by token | Claude Code on the Mac, with a test client's token, sees only that client's tools |
| 5.3 | **the gateway's `/mcp`:** the session's list (TL7), the exchange per turn, the budget, `end_conversation` (TL11) | a sandbox lists and calls weather; another pod's capability gets 403; a session over its tool budget is refused |
| 5.4 | **the runtime and profiles:** Codex's MCP configuration; `tools`, `max_tool_calls`, skills' `requires_tools` | "明天天气怎么样" on the Pi is answered by `weather.*`, not a web search |
| 5.5 | **the safety checks:** an instruction injected through a web page or a tool result can't call a tool outside the session's list; the budget holds | both refused and logged |
| 5.6 | **`end_conversation`** in the voice path: pi-server ends the session when the agent calls it | "好了，谢谢" ends the session |

**Later (not CP5):** Home Assistant's tools (`home.*`) with their classes, and confirmations (G12):
"turn off the kitchen lights" works, "unlock the door" asks you first on Hey Pi.

## Open questions
- **A turn longer than the token** (10 minutes, `exp` ≤ the caller's token's): tools stop working
  mid-turn. Voice turns are seconds; a long task profile will need agent-service to exchange again
  with a newer caller token, or a longer-lived narrowed token (grant 4, after its known issue is fixed).
- tools-service's LXC number and its place in the boot order ([boot.md](../boot.md)).

## Not chosen
- **The same tool list for every session, refused per call:** the model would see tools it can't
  use and the SDK would collect 403s.
- **A delegated token in the sandbox:** against G3; it would be stolen with the sandbox.
- **One scope per tool:** scopes are verbs here (C2); resources go in `authorization_details`.
- **tools-service filtering by client id in its own code:** the token's grants already say it, and a
  second list would drift from the policy.
- **One gateway route per MCP server** (`/mcp/{server}`): one server today; the gateway can route by
  namespace behind one `/mcp` when there are more.
