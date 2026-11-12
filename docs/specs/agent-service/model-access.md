# Spec: model access (how a session calls the model, paid by your ChatGPT subscription)

**Status: milestone 1, route A chosen** (2026-11-11; 2026-11-12: C1 passed, your Pro plan answers
through the gateway). The rules below are agreed. C5 (watching a refresh) is deferred to CP3, when
the broker saves the login to OpenBao ([tracker.md](tracker.md)). Part of
[agent-service](README.md). Related: [agent-runtime.md](agent-runtime.md),
[secrets-manager.md](../secrets-manager.md).

## What it is
Every session's model calls are paid by **your ChatGPT Pro subscription**, not an API key, and your
login **never enters a sandbox**. One trusted process in agent-service, the **auth broker**, holds the
login and keeps it fresh; the gateway uses it on the way out.

```
one time:   you ── `codex login --device-auth` (approve on your phone) ──▶ auth broker
                                                                              │
 ┌─ agent-service (trusted) ──────────────────────────────────────────────────┼─────────┐
 │  auth broker: CODEX_HOME/auth.json, the only copy that refreshes            ▼         │
 │    · refreshes through Codex itself, never by calling the token endpoint by hand      │
 │    · after each refresh, saves the new bundle to OpenBao ─────────────▶ OpenBao       │
 │                         │ current access token                     (durable copy)     │
 │  gateway, model route   ▼                                                             │
 │    capability ok? ──▶ swap it for your token ──HTTPS──▶ OpenAI                        │
 └──────────▲────────────────────────────────────────────────────────────────────────────┘
            │ Responses API, Bearer <capability>   (the only outbound request a sandbox makes)
 sandbox: Codex SDK, custom model provider = the gateway; no auth.json, no API key
```

**Who makes outbound calls:** only the gateway. The sandbox reaches the gateway and nothing else
(sandbox-runner R5); Codex in the sandbox thinks it's calling OpenAI, but its provider's `base_url` is
the gateway.

## Decisions
| ID | Decision | Why |
|---|---|---|
| MA1 | **No subscription token in a sandbox,** neither the access token nor the refresh token. A sandbox holds only its session capability (README G3). | Code in the sandbox may be compromised; a stolen refresh token is your whole account for 30 days. |
| MA2 | **One trusted owner refreshes the login:** the auth broker in agent-service. Codex refreshes it (the SDK's account refresh), never our own call to the token endpoint. | OpenAI: "Use one `auth.json` per runner or per serialized workflow stream" and "Do not share the same file across concurrent jobs or multiple machines". Each refresh issues a new refresh token, so a second copy would break the first. |
| MA3 | **OpenBao keeps the durable copy,** written by agent-service after every refresh (`secret/agent-service/codex-auth`, a new policy allowing agent-service to write it). It is **not** injected at deploy time. | The bundle changes on every refresh, so a deploy-time copy would go stale; a rebuilt host restores the latest bundle instead of needing a new login. |
| MA4 | **The login is one-time and by device code:** `codex login --device-auth`, approved on your phone. If a refresh ever fails (revoked, or unused for 30 days), the model route answers `503`, agent-service alerts you, and a re-login prints a new device code. | OAuth needs your consent once by design; everything after that is automatic. |
| MA5 | **Personal use only.** The subscription is used only for you: your own services and apps, running as your account. If agent-service ever serves another person, it switches to an API key (route D) or to each user's own login (route C). | ChatGPT plans are personal; sharing one across people is account sharing. |
| MA6 | **The gateway caps parallel sessions and usage** (G14). The plan's allowance is shared by every session **and** by your own ChatGPT and Codex use. A turn that hits the plan's limit fails with `model.rate_limited`. | Agents shouldn't silently use up your own allowance. |

## Routes (milestone 1 picks one)
The sandbox side is the same for A, C and D; only what the gateway sends upstream differs.

| Route | How | Status |
|---|---|---|
| **A. Gateway injection** (**chosen**) | Codex in the sandbox sends Responses API calls to the gateway with the capability; the gateway swaps in your access token and forwards to OpenAI. | **Works (C1, 2026-11-12).** Upstream: `https://chatgpt.com/backend-api/codex/responses` with `Authorization: Bearer <access token>` and `chatgpt-account-id`, as Codex itself calls it when logged in. |
| **B. Central trusted worker** (fallback) | Codex runs in agent-service with the cached login, the documented path; the sandbox only executes tools. | **Documented.** A tool-less message needs no remote tools, so day 1 works either way. With tools, Codex's built-in tools must be off on the trusted side and replaced by remote tools in the sandbox: the biggest change of the four. |
| **C. Sign in with ChatGPT** | OpenAI's official OAuth program for apps using ChatGPT plans. | Covers "open-source and locally hosted apps"; remotely hosted apps need an interest form. Check whether a personal, self-hosted setup qualifies. |
| **D. OpenAI API key** | The gateway adds an API key instead. | **Documented**, the route OpenAI recommends for automation. Always works; costs per token. |

**The sandbox side** (any route but B): a custom model provider, no login, no key.
```toml
model_provider = "gateway"

[model_providers.gateway]
name = "agent gateway"
base_url = "http://gateway:8080/model/v1"   # through the egress proxy
env_key = "AGENT_CAPABILITY"                # sent as Authorization: Bearer
wire_api = "responses"
requires_openai_auth = false
```

## What milestone 1 must answer
| # | Question | Decides |
|---|---|---|
| C1 | Does OpenAI accept your `codex login` access token from the gateway, and bill it to your plan? | **Yes** (2026-11-12): route A |
| C4 | How do parallel sessions share your Pro limits? What does a limit look like? | **Partly answered.** Codex reports the plan's usage after each call (`account/rateLimits/updated`, now forwarded as `model.usage_updated`): a **5-hour window** and a **weekly window**, each with `used_percent` and a reset time; a hit sets `rate_limit_reached_type`, which becomes `model.rate_limited`. A short voice turn moved the 5-hour window by about 1% or less. How parallel sessions share it: under load, later. |
| C5 | What does a refresh look like from the broker: how often, and does the SDK's account refresh write the new bundle to `auth.json`? | how the broker saves to OpenBao (MA3). The broker refreshes every 6 hours and on a `401`; **deferred to CP3** |

(C2 and C3 are the runtime's questions: [agent-runtime.md](agent-runtime.md).)

## Not chosen
| Option | Why not |
|---|---|
| A copy of `auth.json` in each sandbox | OpenAI says not to share one across concurrent jobs or machines; refresh rotation breaks the copies; and it puts your refresh token in untrusted code |
| The login as a deploy-time secret (the deployer's usual path) | it changes on every refresh, so the deployed copy goes stale |
| Our own call to the token refresh endpoint | OpenAI: let Codex refresh; a second refresher races it |

## Sources
- Codex authentication: <https://learn.chatgpt.com/docs/auth>
- Codex account auth in CI/CD: <https://learn.chatgpt.com/docs/auth/ci-cd-auth>
- Config reference (`model_providers`, `env_key`, `requires_openai_auth`): <https://learn.chatgpt.com/docs/config-file/config-reference>
- Sign in with ChatGPT, Codex app-server: <https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server>
- Sign in with ChatGPT, tokens (one-hour access tokens; each refresh returns a new 30-day refresh token): <https://developers.openai.com/siwc/token-sharing-open-source/token-reference>
