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
| 3 | 🟡 | **agent-service** on CT 107, through the deployer: sessions API (protocol B v0), gateway (runtime and model routes, its own port), auth broker with OpenBao, auth-service tokens; **with history (CP7's core): its own PostgreSQL**. **The dev gateway is deleted.** | the control plane, real auth, operability, continuity | a caller's token starts a session and streams a turn; another caller can't read it (403); reconnecting with `?after=` loses nothing, even across a restart; a new session continues an earlier conversation; the login survives a host rebuild (restored from OpenBao) and a refresh is saved there (C5); a backup restores; the kill switch works |
| 4 | ⏳ | **voice:** pi-server → agent-service, protocol B in shared-schemas, Hey Pi speaks | the first thing you actually use | you ask Hey Pi and hear the answer; first audio within the budget set at the start of this CP; barge-in interrupts; a week of daily use |
| 5 | ⏳ | **tools:** the gateway's MCP route, weather, Home Assistant, confirmations (G12), budgets (G14) | an agent that acts, safely | "turn off the kitchen lights" works; locks and the garage ask first; an injected instruction can't unlock the door; a session over budget is refused |
| 6 | ⏳ | **files** (milestone 2, [files-and-mounts.md](files-and-mounts.md)): WebDAV read view, mounter, publish, token exchange | storage access without credentials in the sandbox | that spec's "done when" |
| 7 | ⏳ | **summaries and memory** (model-driven): older turns summarized, memory extracted ("I like the lights at 40%"). History itself (PostgreSQL, context carry-over, backups) moved into CP3 | cost and continuity over long histories | long conversations carry a summary instead of every turn; memory is recalled in new sessions |
| 8 | ⏳ | **knowledge:** corpus in a bucket, index, `search_knowledge`; stevenai-service retired | RAG, filtered by grants | answers cite your documents; search never returns text the session couldn't read; stevenai-service is off |
| 9 | ⏳ | **outside and operations:** the phone through api-service; alerts (login refresh failed, pool empty, plan limit near); load test | running it for real | the phone works off the LAN; every alert fires in a drill; N parallel sessions measured against your plan's windows (C4) |

```
CP1 ─▶ CP2 ─▶ CP3 ─▶ CP4 ─▶ CP5 ─┬─▶ CP7 ─▶ CP9
                                  └─▶ CP6 ─▶ CP8      (6 and 7 can swap or run in parallel)
```

## In progress: CP3, agent-service (with history)
**Decided (2026-11-13):**
- **Callers see protocol B v0,** agent-service's own stream: full-fidelity events translated from
  protocol A (text, tools with their full input and output, tasks, turn results) plus the events
  agent-service originates (sandbox ready, lost, retrying; session ended; errors), with **one
  `seq` per session** across sandboxes (G10). **What a caller sees is set per profile:**
  `visibility: full` (Steven AI: the full evidence), `summary`, or `text` (voice). Never sent: the
  capability, sandbox ids, other sessions. Defined in agent-service now; moves to shared-schemas in CP4.
- **Two listeners on CT 107:** the sessions API for callers (443), the **gateway on its own port**
  (8443) for sandboxes; the firewall lets VM 200 reach only the gateway's.
- **Callers in CP3:** `smoke-test` (deploy smoke tests); an `agent` CLI on CT 107 with its own
  client (`agent-cli`; no client secret on the Mac); and **api-service, relaying** an
  authenticated caller (`/v1/agent/…`, token required: it's the public edge).
- **History (CP7's core) is part of CP3:** agent-service's own **PostgreSQL**, a container in its
  compose project on CT 107; SQLAlchemy 2 (psycopg 3) and Alembic migrations; the event log is a
  table; a new session continues a conversation with its last N turns as context; a nightly
  backup through file-management and a tested restore. **Summaries and memory extraction stay in CP7.**
- **A session claims its pod at start;** released on end or after 5 idle minutes (G2).
- **Names:** CT 107 `agent-service`, `agent.lan.liustev6.ca`, SSH alias `agent-host`; profiles in
  `infra/services/agent-service/profiles.yaml` (the deployer's `files`, reviewed like templates).

| Sub-CP | Status | What | Done when |
|---|---|---|---|
| 3.1 | ⏳ | **the design:** `control-plane.md`: sessions and ownership, protocol B v0 and visibility, the gateway, the auth broker, the PostgreSQL schema, context carry-over, backups; README's API and G5 updated | reviewed and agreed |
| 3.2 | ⏳ | **identity and secrets:** auth-service policy (audience `agent-service`; client `agent-service` with `sandbox-runner`, `secrets-manager`, `file-management` (backups); callers `smoke-test`, `agent-cli`; api-service relaying); OpenBao: role `agent-service`, a policy that may write only its login, the PostgreSQL password (OpenTofu) | `explain` shows the grants; `tofu plan` shows only these changes; deployed and applied |
| 3.3 | ⏳ | **CT 107:** the container (lxc-setup part 1), DNS name, certificate; client secrets delivered (`add-client.sh`) | host ready, like CT 103 |
| 3.4 | ⏳ | **the service:** sessions API with ownership and protocol B v0; the runner client (claim at start, release, idle timeout); the gateway on 8443 (runtime routes, pairing by `X-Sandbox-Id`, the model route); the auth broker (login, refresh, save and restore with OpenBao); profiles from `profiles.yaml`; tested against fakes | tests: ownership, pairing, translation and visibility, resume, broker |
| 3.5 | ⏳ | **history:** PostgreSQL in the compose project, Alembic migrations; conversations, sessions, turns, the event log; context carry-over (the session file's `context`, read by the runtime); a cost policy per profile (turns carried); nightly `pg_dump` saved through file-management | a second session continues the first's conversation; a backup appears in `private/agent-service/backups/` |
| 3.6 | ⏳ | **callers:** api-service's `/v1/agent/…` relay (token required); the `agent` CLI on CT 107; smoke tests for agent-service's and api-service's deploys | a session through api-service works with a token and is refused without |
| 3.7 | ⏳ | **deploy:** by hand, then the pipeline (wave 1); the firewall (VM 200 → CT 107:8443, CT 107 → VM 200:443); the runner's `GATEWAY_URL` → agent-service | deployed by the pipeline; a pod's proxy reaches only the gateway port |
| 3.8 | ⏳ | **your login moves:** a device-code login on CT 107, saved to OpenBao; VM 200's dev login deleted (one owner, MA2); a refresh watched (C5) | the model route answers from CT 107; a refresh is saved to OpenBao |
| 3.9 | ⏳ | **close-out:** the dev gateway and the `agent-dev` client removed; VM 200's internet narrowed (model calls now leave from CT 107); the gateway's capped graceful shutdown; usage after an interrupt | nothing of the dev setup left; `check.sh` updated and passing |
| 3.10 | ⏳ | **close CP3:** every "done when" above, live, including history across sessions, a restore from backup, and the login restored on a rebuilt host | recorded in the specs; CP3 collapsed to one line |

## Found along the way (not yet scheduled)
| Item | Found in | Likely CP |
|---|---|---|
| ~5,800 tokens per call are still Codex's own tool and environment context, even with no tools: trim for cheaper voice calls | CP1 (Q3) | 4 |
| An interrupted turn's `usage` repeats the previous call's figures | CP1 (Q4) | 3 |
| The sandbox image is 840 MB, mostly the Codex binaries; it affects the pool's pull and disk size | CP1 (Q5) | 2 |
| A gateway holding long polls must cap its graceful shutdown (uvicorn `--timeout-graceful-shutdown`) so its shutdown work runs: releasing pods, flushing events | CP2 (2.5) | 3 |
| Remove the temporary client `agent-dev` (policy, `disable-client`) when agent-service's own client takes over | CP2 (2.5) | 3 |
| VM 200's internet egress (443/80) is broad: image pulls, packages, certificates, and the dev gateway's model calls. When the gateway leaves the VM (CP3), narrow it (registries and repositories, e.g. through an allowlisting proxy) | CP2 (2.7) | 3 |
| The runner's container needs the Docker socket, the XFS disk and `/run/sandbox-runner` at the same paths, and `CAP_SYS_ADMIN` for `xfs_quota` (or it runs privileged: it's root on the VM anyway, R3) | CP2 (2.4) | 2 (2.6) |
| The auth map (which hop authenticates how), the OpenBao exception for the login, and trusting the proxy's sandbox stamp: into the README | the auth review (2026-11-11) | 3 |
