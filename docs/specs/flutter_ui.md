# Spec: Home panel Flutter UI (Raspberry Pi 5 + flutter-pi)

## Scope
A full-screen Flutter touch UI for a wall/desk home control panel. It runs on a Raspberry Pi 5 via
**flutter-pi** (no desktop; draws straight to the display through KMS/DRM).

The UI's only peer is **pi-agent**, a local service on the Pi (out of scope, doesn't exist yet)
reached over `ws://127.0.0.1:8765`. The UI never calls Home Assistant, the LLM, a weather API, or any
lab service directly, holds no secrets, and has no hardware code. This spec covers the app, its dev
loop, the Pi install, and deployment.

### System context
```
Panel:  Flutter UI ──ws (localhost)──▶ pi-agent ──wss (LAN)──────────┐
                                       ├ mic / speaker / wake word    │
                                       └ backlight / wake triggers    │
                                                                      ▼
Phone (future):  Flutter app ──wss via VPN──▶ web-gateway ──▶ home-hub (home lab)
                                                                      ├ HA proxy ─▶ Home Assistant
                                                                      ├ weather fetcher
                                                                      ├ feeds: subscribe / snapshot / push
                                                                      └ assistant: STT → LLM → TTS
```

| Component | Owns | Does not do |
|---|---|---|
| **Flutter UI** (this spec) | Rendering, touch, local UI state | Hardware, fetching, holding secrets |
| **pi-agent** (out of scope) | Wake word, mic streaming / TTS playback, backlight, external wake/sleep triggers (e.g. wake word, night schedule) → `display_state`, the device's lab credential, **relaying client-protocol messages** between UI and home-hub, reporting relay health | Weather, HA calls, LLM, any business logic |
| **home-hub** (lab, out of scope) | The client API for every client (panel and phone): feeds, snapshots, HA actions, assistant turns; calls to HA / weather / LLM; device identity | Anything specific to one client's hardware |
| **Home Assistant** | Devices and automations | — |

Neither client registers with Home Assistant. Both register with home-hub as devices: the panel's
device token is held by pi-agent; the phone authenticates as you over the VPN. home-hub is the only
HA client, and the HA token never leaves the lab.

Enclosure: `models/3d-print/home-control-frame/`.

## Locked decisions
| # | Decision |
|---|---|
| D1 | Two display states: **`active`** (dashboard) and **`sleep`** (ambient clock view). No separate black state; if pi-agent turns the backlight off, the UI keeps rendering the sleep view. **The UI owns the idle timer** (2 min without a touch → `sleep`; a touch → `active`); pi-agent can force a state with `display_state`. |
| D2 | **All data fetching lives in home-hub** (home lab): weather, Home Assistant, assistant. **pi-agent is a hardware layer + thin relay.** The UI declares what it needs via `subscribe` (derived from the layout file) and renders what is pushed. |
| D3 | **Riverpod 3** (`flutter_riverpod`), **no code generation** (no `riverpod_generator`/`build_runner`). State machines are pure-Dart reducers hosted in Riverpod `Notifier`s. |
| D4 | **Caching:** a full `snapshot` (from home-hub, via pi-agent) arrives on every (re)connect; the UI also keeps a debounced JSON cache file for cold boot. No database. |
| D5 | **One transport, one router:** a single WebSocket client plus a typed message router (sealed classes, exhaustive `switch`). No HTTP clients in the app. |
| D6 | Device pixel ratio pinned to **1.0**; the logical canvas is 1024×600 on both Pi and Mac. |
| D7 | Pure-Dart packages only, no plugins with native code. |
| D8 | **Protocol is split** into a *client protocol* (spoken by home-hub; pi-agent passes it through) and a *device protocol* (pi-agent only). The data layer (`protocol/`, `connection/`, `feeds/`, `cache/`, non-display state) is **portable**: it must not import panel-only code, so it can later move to `dart/libs/home_client/` and be shared with a phone app that connects to home-hub directly. |

## Hardware / display
- Raspberry Pi 5, Raspberry Pi OS Lite 64-bit (Bookworm), booted to console.
- Screen: **1024×600 px**, landscape, active area **152.4 × 83.3 mm**. Touch only, no keyboard/mouse.
- Run flutter-pi with `--pixel-ratio 1.0` (D6). Without it, flutter-pi derives
  `10·1024 / (152·38) ≈ 1.77` from `--dimensions` and the logical canvas shrinks to ~578×338.
- Tap targets ≥ 12 mm physical ≈ **81 logical px**, defined once in `ui/sizing.dart`.
- Viewed from ~0.5–1.5 m: large type, high contrast, dark theme. Fonts bundled in `assets/fonts/`.
- **Visual style: lightweight glass** (`ui/glass.dart`), built from plain Flutter: translucent
  gradient panels with a hairline border over a static, soft-colored background. One shared
  backdrop blur (`BackdropGroup`) that can be switched off with `GlassTokens.enableBlur` if the Pi
  can't hold frame rate. No glass-effect packages (e.g. `liquid_glass_widgets`: its iOS-style
  refraction needs Impeller, which flutter-pi doesn't use). The sleep view has no glass.

## Architecture
```
pi-agent ⇄ AgentClient ──▶ MessageRouter ──▶ Notifiers (Riverpod) ──▶ Widgets
           (transport)      (decode/route)    display / assistant /      │
               ▲                              ha / feeds / connection    │ actions
               │                                     │                   │
               └──────────── send(UiMessage) ◀───────┼───────────────────┘
                                                     ▼
                                              SnapshotCache (JSON file)
```

### AgentClient (transport): `lib/connection/agent_client.dart`
- `dart:io` `WebSocket` (no package), `pingInterval` 5 s to detect a hung pi-agent.
- Endpoint and enabled protocols are constructor config, not hard-coded: the panel uses
  `ws://127.0.0.1:8765` with client + device protocol; a future phone app would use home-hub's URL
  with the client protocol only.
- Reconnect with exponential backoff: 0.5 s → 10 s cap, reset after a successful connect.
- On every connect: send `hello`, then `subscribe` (feeds from the layout), then wait for `snapshot`.
- **No outbound queue.** While disconnected, actions fail immediately with visible feedback
  (toggle rolls back, assistant shows "offline"); `user_activity` is dropped.
- Exposes connection status (`connecting | connected | disconnected`) to `connectionProvider`.

### MessageRouter: `lib/connection/message_router.dart`
- Decodes JSON into sealed `AgentMessage` subclasses (from `protocol/messages.dart`) and dispatches
  each to its Notifier via one exhaustive `switch`; a new message type that isn't handled is a compile error.
- Unknown `type`s and unknown fields are logged and ignored, never crash.
- Malformed JSON is logged and dropped; it does not drop the connection.

### Feeds: `lib/feeds/feed_registry.dart`
- Each widget type declares the feeds it needs from its layout options (registry entry
  `feedsFor(options)`). Feed ids: `weather`, `ha:<entity_id>`.
- The union of all widgets' feeds is sent in `subscribe` on every connect (and again if the layout changes).
- Tracks per feed: last data, `fetchedAt` (from the message `ts` set by home-hub, else arrival time), and a
  staleness threshold (weather 30 min, HA 5 min; overridable per widget in the layout).
  Widgets show their own "stale" state, separate from the global offline badge.

### SnapshotCache: `lib/cache/snapshot_cache.dart`
- Contents: feed data + `fetchedAt` (weather, HA states). **Not** display state or assistant state.
- Path: `$HOME_PANEL_CACHE`, default `~/.cache/home-panel/cache.json`.
- Write debounced (at most once per 30 s, SD-card wear), atomically (write temp file → rename).
- Cold start: load cache → render everything as stale → replace on `snapshot`.
- Corrupt/missing cache → start empty, log, overwrite on next write.

## State management (Riverpod 3, D3)
- Pure-Dart reducers `(State, Event) → State` live in `lib/state/*_machine.dart`, with no Flutter or
  Riverpod imports, and are unit-tested directly.
- Each reducer is hosted by a Riverpod `Notifier`; side effects (timers, sends) live in the
  Notifier, not the reducer.
- Providers:

| Provider | Kind | Holds | Layer (D8) |
|---|---|---|---|
| `agentClientProvider` | `Provider` | the `AgentClient` instance | portable |
| `connectionProvider` | `Notifier` | socket status, last message time, protocol mismatch flag | portable |
| `relayProvider` | `Notifier` | relay-to-home-hub `online/offline` (from `connection`) | panel |
| `assistantProvider` | `Notifier` | assistant state (below) | portable |
| `haProvider` | `Notifier` | entity states + pending optimistic actions | portable |
| `feedsProvider` | `Notifier` | feed registry (weather + staleness) | portable |
| `displayProvider` | `Notifier` | `active / sleep` + wake-guard timestamp | panel |
| `layoutProvider` | `FutureProvider` | parsed layout config | panel |
| `screenProvider` | `Provider` (derived) | what to render (below); never stored | panel |

- Portable providers live in `state/client_providers.dart`, panel ones in `state/panel_providers.dart`.
  Portable code never imports panel code; panel code may import portable code.
- Tests override `agentClientProvider` with a fake client.

### Display state (D1)
| State | Renders |
|---|---|
| `active` | Widget dashboard. |
| `sleep` | Ambient view: big time (h:mm) with AM/PM stacked over the seconds on its right, date below, small current weather; dark navy radial background, soft white→grey time gradient, grey details. **No animations.** Everything redraws at most once per minute **except the seconds digits**, which redraw every second in their own repaint boundary. Shift the clock a few px every few minutes (LCD image-retention mitigation). |

- Cold start (no connection yet): `active`.
- **Idle timer (UI-owned):** 2 min without a touch → `sleep`; any touch → `active` and restarts the
  timer. Works the same with or without pi-agent.
- **pi-agent overrides:** a `display_state` message (e.g. wake word → `active`, night schedule →
  `sleep`) is applied immediately; the idle timer keeps running from the last touch.
- **Wake-tap swallowing:** while `sleep`, and for 300 ms after switching to `active`, touches send
  `user_activity` but don't reach controls.
- Every touch anywhere sends `user_activity`, throttled to ≤ 1/s.

### Assistant state
State: `phase` (`idle | listening | thinking | speaking`), `turnId`, `transcript`, `reply`, `error`.

```
idle ──wake word / PTT──▶ listening ──end of speech──▶ thinking ──first reply──▶ speaking ──done──▶ idle
  ▲                           │                          │                         │
  └──── cancel / watchdog / disconnect ────────────────────┴─────────────────────────┘
```

| Event | Source | Effect |
|---|---|---|
| `assistant` message | home-hub via pi-agent (pi-agent may emit `listening` itself on wake word) | **Authoritative:** apply `phase` as sent. New `turn_id` → reset `transcript`/`reply`. `transcript` replaces; `reply_delta` appends. |
| PTT press / release | UI | Send `assistant_ptt start/stop`. Phase changes only when pi-agent says so. |
| Cancel tap (overlay ✕ or tap during `speaking`) | UI | Send `assistant_cancel`; go `idle` locally. |
| Watchdog: no `assistant` message for 30 s while not `idle` | UI | `idle` + error "assistant didn't respond". |
| Socket disconnect while not `idle` | UI | `idle` + error "offline". |

### Screen derivation (`screenProvider`)
1. `assistant.phase != idle` → **assistant overlay** on top of the dashboard (pi-agent is expected
   to also send `display_state: active` so the backlight is on).
2. else `display == active` → **dashboard**.
3. else → **sleep view**.

The overlay stays up for ~5 s after returning to `idle` so the final reply can be read, unless dismissed.

### HA optimistic toggles (`haProvider`)
- Tap → flip the tile immediately, send `ha_action` with a fresh `id`, mark pending.
- Confirmed by `ha_action_result{ok:true}` **or** a matching `ha_state`, whichever comes first.
- `ok:false`, or neither within 3 s → roll back + brief error on the tile.
- Disconnected → don't send; brief "offline" on the tile.

## Protocol (JSON over WebSocket; panel endpoint `ws://127.0.0.1:8765`)
All models live in **one file**, `lib/protocol/messages.dart`, **pure Dart** (no `package:flutter`),
so the mock agent, a future phone app, and a future cross-language schema (JSON Schema alongside
`js/libs/shared-schemas` / `python/libs/shared-schemas`, so home-hub's Python can't drift) can reuse
it. Each message class is tagged as client or device protocol (D8). `protocol_version: 1`.
`ts` = unix epoch milliseconds when home-hub fetched the data (optional).

### Client protocol: home-hub's API (pi-agent passes through mostly unchanged)
home-hub → client
```json
{"type":"snapshot","protocol_version":1,
 "assistant":{"phase":"idle","turn_id":null},
 "widgets":{"weather":{"data":{...},"ts":1762290000000}},
 "ha_states":[{"entity_id":"light.living_room","state":"on","attributes":{...},"ts":1762290000000}]}
{"type":"widget_update","widget":"weather","data":{...},"ts":1762290000000}
{"type":"ha_state","entity_id":"light.living_room","state":"on","attributes":{...},"ts":1762290000000}
{"type":"ha_action_result","id":"a1b2","ok":true,"error":null}
{"type":"assistant","turn_id":"t42","phase":"idle|listening|thinking|speaking","transcript":"...","reply_delta":"..."}
```

client → home-hub
```json
{"type":"hello","protocol_version":1,"client":"home-panel"}
{"type":"subscribe","feeds":["weather","ha:light.living_room","ha:scene.movie"]}
{"type":"ha_action","id":"a1b2","entity_id":"light.living_room","service":"light.toggle","data":{}}
{"type":"assistant_text","text":"turn off the kitchen lights"}
{"type":"assistant_cancel"}
```

### Device protocol: pi-agent only (panel)
pi-agent → UI
```json
{"type":"display_state","state":"active|sleep"}
{"type":"connection","lab":"online|offline"}
```
`connection.lab` = whether pi-agent's relay to home-hub is up (not the UI↔pi-agent socket, which
the UI tracks itself).

UI → pi-agent
```json
{"type":"user_activity"}
{"type":"assistant_ptt","action":"start|stop"}
```

### Sequencing
- Connect: UI `hello` → UI `subscribe` → `snapshot` → updates. Right after the UI connects,
  pi-agent also sends `display_state` and `connection`.
- A `snapshot` is re-sent after any later `subscribe`.
- `snapshot.protocol_version` ≠ UI's → status strip shows "protocol mismatch"; the UI continues
  best-effort (unknown fields/types ignored).

### What the UI expects of pi-agent as a relay
- Forwards client-protocol messages both ways, attaching the device identity on its upstream connection.
- While the relay is down: sends `connection{lab:"offline"}` and answers `ha_action` /
  `assistant_text` with failure (`ha_action_result{ok:false}`, assistant `idle`) rather than
  queuing them. The UI keeps showing its cached data, marked stale.
- When the relay comes back: sends `connection{lab:"online"}` and replays the UI's last `subscribe`
  upstream so a fresh `snapshot` arrives.

## Widgets (v1)
A config-driven grid. The layout file sets the grid size (default 4 columns × 3 rows below the
status strip) and each widget's type, position, size, and options. Widgets register in a single
`type → (builder, feedsFor)` registry.

1. **Clock + date.** No feeds.
2. **Weather**: current conditions and the next few hours. Feed `weather`.
3. **HA quick controls**: lights/scenes as toggle tiles. Option `entities: [...]` → feeds `ha:<id>`.
4. **Assistant panel**: phase indicator, live transcript, streamed reply, push-to-talk button
   (press = start, release = stop), text input via the in-app on-screen keyboard (flutter-pi has no
   system IME). Rendered as the assistant overlay (see Screen derivation); an optional dashboard
   tile opens it.
5. **Status strip** (fixed, not a grid widget): pi-agent connected/disconnected, relay to home-hub online/offline,
   time of last update, protocol mismatch.

Example `layout.yaml`:
```yaml
grid: {columns: 4, rows: 3}
widgets:
  - {type: clock,       pos: [0, 0], size: [2, 1]}
  - {type: weather,     pos: [2, 0], size: [2, 2]}
  - {type: ha_controls, pos: [0, 1], size: [2, 2],
     options: {entities: [light.living_room, light.kitchen, scene.movie]}}
  - {type: assistant,   pos: [2, 2], size: [2, 1]}
```

Layout file resolution: `$HOME_PANEL_LAYOUT` if set and readable, else the bundled
`assets/layout.default.yaml`. Invalid file → log and fall back to the bundled default.

## Flutter project layout
`[P]` = portable data layer (D8): pure Dart or Riverpod only, no imports from `[panel]` code.
Everything else is panel-only.
```
dart/apps/home-panel/
├── README.md                    # toolchain, dev, build, deploy, protocol
├── pubspec.yaml                 # name: home_panel
├── analysis_options.yaml
├── assets/
│   ├── layout.default.yaml
│   └── fonts/
├── lib/
│   ├── main.dart                # ProviderScope, env config, runApp
│   ├── app.dart                 # MaterialApp, theme, screenProvider switch
│   ├── protocol/                # [P]
│   │   └── messages.dart        # ALL protocol models + JSON, tagged client/device
│   ├── connection/              # [P]
│   │   ├── agent_client.dart    # WebSocket, ping, reconnect/backoff; endpoint is config
│   │   └── message_router.dart  # decode → dispatch to notifiers
│   ├── feeds/                   # [P]
│   │   └── feed_registry.dart   # feed ids, fetchedAt, staleness
│   ├── cache/                   # [P]
│   │   └── snapshot_cache.dart  # debounced atomic JSON file
│   ├── state/
│   │   ├── assistant_machine.dart  # [P] pure reducer
│   │   ├── ha_machine.dart         # [P] pure reducer (optimistic/pending)
│   │   ├── client_providers.dart   # [P] connection / assistant / ha / feeds notifiers
│   │   ├── display_machine.dart    # pure reducer (panel)
│   │   └── panel_providers.dart    # relay / display / layout / screen providers
│   ├── display/
│   │   ├── dashboard_view.dart  # grid from layout
│   │   ├── sleep_view.dart      # ambient clock, 1/min redraw, pixel shift
│   │   ├── assistant_overlay.dart
│   │   └── touch_guard.dart     # wake-tap swallowing + user_activity throttle
│   ├── layout/
│   │   ├── layout_config.dart   # YAML → typed config
│   │   └── widget_registry.dart # type → builder + feedsFor
│   ├── widgets/
│   │   ├── clock_widget.dart
│   │   ├── weather_widget.dart
│   │   ├── ha_controls_widget.dart
│   │   ├── assistant_panel.dart
│   │   └── status_strip.dart
│   └── ui/
│       ├── theme.dart
│       ├── sizing.dart          # 12 mm tap target, spacing scale
│       └── keyboard/            # on-screen keyboard
├── tool/
│   └── mock_agent.dart          # `dart run tool/mock_agent.dart`
├── test/
│   ├── protocol/                # JSON round-trip for every message
│   ├── state/                   # reducer tables (display, assistant, ha)
│   ├── connection/              # router + fake socket reconnect
│   └── widgets/                 # dashboard at 1024×600, sleep view, overlay
├── macos/                       # generated; window size fixed in MainFlutterWindow.swift
└── deploy/
    ├── pi-setup.sh              # one-time Pi provisioning (run on the Pi)
    ├── deploy.sh                # build → rsync → restart (run on the Mac)
    └── home-panel.service       # systemd unit
```

Dependencies: `flutter_riverpod` (3.x), `yaml`, `intl`. Everything else from the SDK (`dart:io`
WebSocket and files). Only the `macos` platform folder exists; flutter-pi builds don't need `linux/`.

## Mock pi-agent (`tool/mock_agent.dart`)
Pure Dart, reuses `protocol/messages.dart`. Plays **both pi-agent and home-hub** in one process
(device protocol + client protocol), implements the full connect sequence (`hello` → `subscribe` →
`snapshot`, plus `display_state` / `connection` on connect), follows the relay expectations above
when "lab offline" is toggled, and provides:
- fake weather updates; HA entities that answer `ha_action` with `ha_action_result` + `ha_state`;
- an assistant turn script (listening → thinking → speaking with streamed `reply_delta` → idle);
- terminal keys to: toggle `display_state`, toggle lab online/offline, start an assistant turn,
  drop the next HA confirmation (rollback test), send a malformed message, and kill/restart the
  socket (reconnect test).

## Toolchain (Mac)
- Flutter **3.47.9** stable at `~/flutter` (on `PATH` via `~/.zshrc`); Xcode 27.2 for the macOS dev build.
  CocoaPods not needed (no plugins; Flutter uses Swift Package Manager).
- Apple Silicon: Rosetta (flutterpi_tool uses the x64 macOS build tools); already installed.
- `flutter pub global activate flutterpi_tool`. **Compatibility check pending:** the latest
  release (0.12.0) supports Flutter 3.44; 3.47 support is only on its `main` branch. If the release
  fails, install from git or pin Flutter to 3.44.
- Record the working Flutter + flutterpi_tool versions in the README.

## Dev loop
- **Mac (default):** `dart run tool/mock_agent.dart` in one terminal; `flutter run -d macos` (or F5
  with the `home-panel (macOS)` launch config) in another. The macOS window is fixed at 1024×600 in
  `macos/Runner/MainFlutterWindow.swift`.
- **On the Pi with hot reload:** register once with
  `flutterpi_tool devices add <user>@panel.local --id=panel --display-size=269x158`
  (269 mm gives flutter-pi's pixel-ratio formula ≈ 1.0, matching release), then
  `flutterpi_tool run -d panel`. Stop the `home-panel` service first; only one process can own the display.

## Pi setup (one-time, `deploy/pi-setup.sh`, run on the Pi)
1. Boot to console: `sudo raspi-config nonint do_boot_behaviour B1`.
2. Runtime packages: `sudo apt install libgl1-mesa-dri libegl1 libgles2 libdrm2 libgbm1 libsystemd0
   libinput10 libudev1 libxkbcommon0 fontconfig ttf-mscorefonts-installer && sudo fc-cache`.
   (flutterpi_tool bundles the `flutter-pi` binary and engine into the app, so flutter-pi does
   **not** need to be compiled on the Pi.)
3. Groups: `sudo usermod -aG render,video,input <user>` (GPU, DRM, touch input).
4. Create `~/panel/` and install the unit:
   `sudo cp home-panel.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable home-panel`.
5. Reboot.

On-Pi directory layout:
```
~/panel/
├── app/          # build output; replaced wholesale on every deploy (rsync --delete)
├── layout.yaml   # optional local layout override; survives deploys
└── cache.json    # snapshot cache; survives deploys
```

## Build & deploy (`deploy/deploy.sh`, run on the Mac)
```sh
flutterpi_tool build --arch=arm64 --release          # → build/flutter-pi/<target>/
rsync -a --delete build/flutter-pi/<target>/ <user>@panel.local:~/panel/app/
ssh <user>@panel.local sudo systemctl restart home-panel
```
- Output goes to `build/flutter-pi/<target>/` (e.g. `genericAArch64`), **not**
  `build/flutter_assets/`. It contains the `flutter-pi` binary, `libflutter_engine.so`, `app.so`
  and assets. The script globs for the single target directory rather than hard-coding it.
- `--cpu=generic` (default). Switch to `--cpu=pi5` once a released flutterpi_tool ships it.
- `PI_HOST` / `PI_USER` come from environment variables with defaults `panel.local` / `$USER`.
- `sudo systemctl restart` over ssh relies on passwordless sudo (Pi OS default for the first user).
- Optional: `deploy.sh --layout <file>` also copies a layout file to `~/panel/layout.yaml`.

## systemd unit (`deploy/home-panel.service`)
```ini
[Unit]
Description=Home panel Flutter UI
After=systemd-user-sessions.service pi-agent.service
Conflicts=getty@tty1.service

[Service]
User=<user>
SupplementaryGroups=render video input
WorkingDirectory=/home/<user>/panel
Environment=HOME_PANEL_LAYOUT=/home/<user>/panel/layout.yaml
Environment=HOME_PANEL_CACHE=/home/<user>/panel/cache.json
ExecStart=/home/<user>/panel/app/flutter-pi --release --pixel-ratio 1.0 /home/<user>/panel/app
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```
- `After=pi-agent.service` is ordering only (no `Requires=`), so the UI starts and shows
  "offline" even before pi-agent exists.
- If the panel is mounted rotated, add `--rotation 90|180|270` to `ExecStart`.

## Non-goals
No network calls besides the pi-agent socket; no data fetching; no secrets; no
backlight/GPIO/audio code; no app-store packaging; no `linux/` desktop target; no database.

## Done when
- Dashboard renders cleanly at 1024×600 on the Mac against the mock.
- `active` ↔ `sleep` switch on touch, on 2 min idle, and on `display_state`; wake-tap swallowing works.
- Connect sequence works; snapshot replaces cached data; stale indicators appear when feeds age out.
- Cold start with pi-agent down shows cached data marked stale.
- HA toggles round-trip; rollback works on `ok:false`, on a dropped confirmation, and when offline.
- Assistant overlay follows the phase machine: streamed reply, PTT, cancel, watchdog, and disconnect.
- Survives mock kill/restart and malformed messages without crashing.
- Reducer tables and protocol round-trips are unit-tested.
- A test fails if any `[P]` file imports panel-only code (D8).
- `pi-setup.sh` + `deploy.sh` take a fresh Pi OS Lite install to the app running under systemd.
- README covers toolchain, dev loop, Pi setup, build/deploy, and the protocol.

## Open questions
- **Pixels may not be square:** 1024/152.4 ≈ 6.7 px/mm across but 600/83.3 ≈ 7.2 px/mm down.
  Confirm the active area from the panel datasheet; if the pixels really aren't square, round/square
  shapes will look ~7% squashed (Flutter can't compensate).
- DSI or HDMI? (Affects `--rotation` / `--videomode` only.)
- Weather location and the HA entity list for `layout.default.yaml`.
- Keep the on-screen keyboard in v1, or ship PTT-only first?
