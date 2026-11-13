# Milestone 1: the runtime behind a dev gateway (temporary)

Runs one sandbox (the real `agent-runtime` image, hardened like the runner will run it) behind the
**dev gateway**, which plays agent-service's part: it writes the session file, holds **your ChatGPT
login** (the auth broker) and adds your token to the sandbox's model calls. The sandbox only ever
holds its session capability. Specs:
[agent-runtime.md](../../../../docs/specs/agent-service/agent-runtime.md),
[model-access.md](../../../../docs/specs/agent-service/model-access.md).

## Day 1: one message on your subscription (Mac or test VM, Docker)
```sh
cd python/libs/agent-runtime/dev
mkdir -p secrets && chmod 700 secrets && openssl rand -hex 32 > secrets/dev_token
docker compose build

# Once: your ChatGPT login. Opens nothing; prints a URL and a code to approve on your phone.
# (If it's refused, allow device-code sign-in in ChatGPT's security settings first.)
docker compose run --rm dev-gateway python -m agent_dev.login

docker compose up -d
docker compose exec -e AGENT_DEV_TOKEN_FILE=/secrets/dev_token dev-gateway agent-dev ask "hi"   # Ctrl-C interrupts
```
- Your login lives in `secrets/codex-home/auth.json` (gitignored), on the gateway's side only; the
  gateway refreshes it (and retries once on a `401`).
- Each runtime container serves **one session**. A new session (new capability):
  `docker compose up -d --force-recreate`.
- **An API key instead** (route D): put it in `secrets/openai_api_key`, then
  `MODEL_AUTH=apikey docker compose up -d --force-recreate`.
- What the model route did: `docker compose logs dev-gateway | grep model` (`model POST /responses -> 200`).

## Runner mode (CP2): the sandbox comes from sandbox-runner
On VM 200, with the runner deployed (it keeps `chat` pods warm), the dev gateway claims a pod instead
of running a runtime container of its own, using auth-service's client `agent-dev`
(`secrets/agent_dev_client_secret`, created with `add-client.sh`; never in git):
```sh
docker compose -f compose.yaml -f compose.runner.yaml up -d dev-gateway
docker compose exec -e AGENT_DEV_TOKEN_FILE=/secrets/dev_token dev-gateway agent-dev ask "hi"
docker compose -f compose.yaml -f compose.runner.yaml down       # releases the pod
```
The session's capability then only works from its own pod: requests stamped with another pod's
`X-Sandbox-Id` (by that pod's proxy) are refused (403).

## From another machine (the test VM)
```sh
pip install httpx
export AGENT_DEV_URL=http://<vm>:8080 AGENT_DEV_TOKEN=<contents of secrets/dev_token>
PYTHONPATH=python/libs/agent-runtime/dev python3 -m agent_dev.cli ask "what can you do?"
PYTHONPATH=python/libs/agent-runtime/dev python3 -m agent_dev.cli events                   # every event, with timings
```

## Measuring (the spec's questions)
| | How |
|---|---|
| C1 subscription | a reply at all, and `model POST /responses -> 200` in the gateway's log |
| Q2 startup | `runtime.ready`'s `startup_ms` (`agent-dev events`) |
| Q3 first text | the summary line after `ask` (`first text … ms`), on a short voice-style question, `PROFILE=voice` |
| Q4 interrupt | Ctrl-C during a long answer; `interrupted` in the summary; latency from `events` timestamps |
| Q5 size | `docker images`, `docker stats` while idle and during a turn |
| Q6, C2 gVisor | install gVisor (`runsc`, registered with Docker), then `SANDBOX_RUNTIME=runsc docker compose up -d --force-recreate` and repeat |
| Q7 | covered by the compose settings (read-only root, `/tmp` and `/workspace` the only writable places) |
