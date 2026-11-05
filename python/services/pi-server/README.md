# pi-server

The local service Hey Pi talks to. It runs on the Raspberry Pi next to the Hey Pi
Flutter app (`dart/apps/hey-pi`).

**Path**: `python/services/pi-server/`

```
Hey Pi (Flutter UI) ──ws://127.0.0.1:8765/ws──▶ pi-server ──(later)──▶ home-hub (lab) ──▶ Home Assistant
```

- **Now:** serves Hey Pi over a WebSocket with **mock data** (weather, Home Assistant
  entities, assistant replies) and saves **device settings** (light/dark mode).
- **Later:** relays to home-hub in the lab instead of mocking, and owns the Pi's
  hardware (wake word, mic and speaker, backlight).

Protocol and design: `docs/specs/flutter_ui.md` ("Protocol", "pi-server").

## Endpoints

| Endpoint | What |
|---|---|
| `ws://127.0.0.1:8765/ws` | Hey Pi's connection. JSON messages tagged by `"type"`. |
| `GET /health` | Status, version, connected clients, lab link. For systemd and `curl`. |
| `POST /debug/display/{active\|sleep}` | Push a `display_state` to connected screens. |
| `POST /debug/lab/{online\|offline}` | Push a `connection` (lab link) change. |
| `POST /debug/assistant?text=...` | Start a mock assistant turn. |

`/debug/*` exists only when `PI_SERVER_DEBUG_ENDPOINTS=true` (the default for now).

## Protocol summary

On connect, pi-server sends `connection` and `settings`. Hey Pi then sends `hello`
and `subscribe`, and gets a `snapshot`; after that, updates are pushed.

| Hey Pi → pi-server | pi-server → Hey Pi |
|---|---|
| `hello`, `subscribe` | `snapshot` (assistant, weather, Home Assistant states) |
| `ha_action` (e.g. `light.toggle`) | `ha_action_result` + `ha_state` |
| `assistant_text`, `assistant_ptt`, `assistant_cancel` | `assistant` (phase, transcript, streamed `reply_delta`) |
| `settings_update` (`theme_mode`) | `settings` (saved; sent to every screen) |
| `user_activity` | `widget_update` (weather), `display_state`, `connection` |

Unknown or malformed messages are logged and dropped; they never close the connection.

## Data

pi-server holds no database. Device settings live in one JSON file,
`~/.local/share/pi-server/settings.json`, written atomically. Live data (weather,
Home Assistant states) is kept in memory. History and anything shared with other
devices belongs in home-hub in the lab.

## Local development

```sh
cd python/services/pi-server
uv venv .venv --python 3.11          # the Pi runs Python 3.11
uv pip install -p .venv -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
.venv/bin/python -m pytest
```

Settings come from `PI_SERVER_*` environment variables or a `.env` file; see
`.env.example`. Everything has a default.

## Deploy to the Pi

```sh
scripts/run_deploy.sh
```

This copies the code to `~/pi-server`, installs packages into `~/pi-server/.venv`,
installs the `pi-server` systemd service (starts at boot, restarts on failure,
localhost only) and checks `/health`.

```sh
ssh panel 'curl -s localhost:8765/health'
ssh panel 'journalctl -u pi-server -f'
```
