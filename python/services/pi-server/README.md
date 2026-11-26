# pi-server

The local service Hey Pi talks to. It runs on the Raspberry Pi next to the Hey Pi
Flutter app (`dart/apps/hey-pi`).

**Path**: `python/services/pi-server/`

```
Hey Pi (Flutter UI) ◀──ws://127.0.0.1:8765/ws── pi-server ──▶ speech-service, agent-service (lab)
                          the speech state
```

- **Runs the voice assistant:** owns the Pi's mic and speaker; the wake word (小派小派, `models/`), the
  voice session, the turn (speech-service → agent-service → speech), barge-in by the wake word.
- **Tells Hey Pi one thing, the speech state** (since 2026-11-26): its only message. Later: relaying
  data for the dashboard, as new messages; the backlight.

Protocol and design: [flutter_ui.md](../../../docs/specs/flutter_ui.md) ("Protocol"); the voice
assistant: [voice-path.md](../../../docs/specs/voice-path.md); the wake word: [wake-word.md](../../../docs/specs/wake-word.md).

## Protocol
One message, pi-server → Hey Pi, on connect and on every change (`app/protocol.py`):
```json
{"type": "speech", "state": "idle | listening | speaking"}
```
`idle`: no voice session. `listening`: the session waits for you, or hears you. `speaking`: its turn,
from the end of what you said until its reply has played. The voice assistant's phases map onto it
in `app/server.py` (thinking and speaking are both `speaking`). Hey Pi sends nothing; anything it
sends is dropped, and never closes the connection.

## Endpoints

| Endpoint | What |
|---|---|
| `ws://127.0.0.1:8765/ws` | Hey Pi's connection |
| `GET /health` | status, version, connected screens, voice on or off, the speech state |
| `POST /debug/speech/{idle\|listening\|speaking}` | tell the screens a speech state (no voice needed: the UI's dev loop) |
| `POST /debug/say?text=...` | a typed turn, spoken (voice on) |
| `POST /debug/voice/{listen\|commit\|cancel}` | start a spoken turn, end it, end the session (voice on) |

`/debug/*` exists only when `PI_SERVER_DEBUG_ENDPOINTS=true` (the default for now).

## Data
pi-server keeps nothing on disk for the UI; the voice session's history is agent-service's.

## Local development

```sh
cd python/services/pi-server
uv venv .venv --python 3.11          # the Pi runs Python 3.11
uv pip install -p .venv -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload   # voice off: speech stays idle
.venv/bin/python -m pytest
```

Settings come from `PI_SERVER_*` environment variables or a `.env` file; see
`.env.example`. Everything has a default.

## Deploy to the Pi

```sh
infra/deployment/pi-server.sh   # from the repo root
```

This copies the code to `~/pi-server`, installs packages into `~/pi-server/.venv`,
installs the `pi-server` systemd service (starts at boot, restarts on failure,
localhost only) and checks `/health`.

```sh
ssh pi 'curl -s localhost:8765/health'
ssh pi 'journalctl -u pi-server -f'
```
