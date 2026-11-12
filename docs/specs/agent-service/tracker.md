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
| 2 | ⏳ | **sandbox worker:** the VM, sandbox-runner, warm pool, egress proxy, gVisor ([sandbox-runner.md](sandbox-runner.md)); the dev gateway stays | isolation and speed at the infrastructure level | Codex runs under gVisor (C2) and gVisor's cost is measured (Q6, from CP1); a claim gives a ready sandbox in under 1 s; the pool refills; each sandbox is destroyed after use; a sandbox reaches only the gateway (escape tests fail); the proxy overwrites the sandbox-id header |
| 3 | ⏳ | **agent-service skeleton** on CT 107, through the deployer: sessions API, gateway (runtime and model routes), auth broker with OpenBao, auth-service tokens. **The dev gateway is deleted.** | the control plane, real auth, operability | a caller's token starts a session and streams a turn; a login refresh is watched and its new bundle saved to OpenBao (C5, from CP1); another caller can't read it; reconnecting with `?after=` loses nothing, even across a restart (G10); the login survives a host rebuild; the kill switch works |
| 4 | ⏳ | **voice:** pi-server → agent-service, protocol B in shared-schemas, Hey Pi speaks | the first thing you actually use | you ask Hey Pi and hear the answer; first audio within the budget set at the start of this CP; barge-in interrupts; a week of daily use |
| 5 | ⏳ | **tools:** the gateway's MCP route, weather, Home Assistant, confirmations (G12), budgets (G14) | an agent that acts, safely | "turn off the kitchen lights" works; locks and the garage ask first; an injected instruction can't unlock the door; a session over budget is refused |
| 6 | ⏳ | **files** (milestone 2, [files-and-mounts.md](files-and-mounts.md)): WebDAV read view, mounter, publish, token exchange | storage access without credentials in the sandbox | that spec's "done when" |
| 7 | ⏳ | **history and memory:** PostgreSQL, context curated at session start, backups (decision 2) | continuity and cost control | a new session continues yesterday's conversation with selected context; cost per session is bounded by policy; a backup restore is tested |
| 8 | ⏳ | **knowledge:** corpus in a bucket, index, `search_knowledge`; stevenai-service retired | RAG, filtered by grants | answers cite your documents; search never returns text the session couldn't read; stevenai-service is off |
| 9 | ⏳ | **outside and operations:** the phone through api-service; alerts (login refresh failed, pool empty, plan limit near); load test | running it for real | the phone works off the LAN; every alert fires in a drill; N parallel sessions measured against your plan's windows (C4) |

```
CP1 ─▶ CP2 ─▶ CP3 ─▶ CP4 ─▶ CP5 ─┬─▶ CP7 ─▶ CP9
                                  └─▶ CP6 ─▶ CP8      (6 and 7 can swap or run in parallel)
```

## In progress
None. **CP2 is next;** its sub-checkpoints are defined when it starts.

## Found along the way (not yet scheduled)
| Item | Found in | Likely CP |
|---|---|---|
| ~5,800 tokens per call are still Codex's own tool and environment context, even with no tools: trim for cheaper voice calls | CP1 (Q3) | 4 |
| An interrupted turn's `usage` repeats the previous call's figures | CP1 (Q4) | 3 |
| The sandbox image is 840 MB, mostly the Codex binaries; it affects the pool's pull and disk size | CP1 (Q5) | 2 |
| The auth map (which hop authenticates how), the OpenBao exception for the login, and trusting the proxy's sandbox stamp: into the README | the auth review (2026-11-11) | 3 |
