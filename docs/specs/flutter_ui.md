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
| D2 | **All data fetching lives in home-hub** (home lab): weather, Home Assistant, assistant. **pi-agent is a hardware layer + thin relay.** The UI declares what it needs via `subscribe` (derived from the dashboard layout) and renders what is pushed. |
| D3 | **Riverpod 3** (`flutter_riverpod`), **no code generation** (no `riverpod_generator`/`build_runner`). Zustand-style: one store (state + setters), **events** describe what happened, **handlers** decide the new state and call the setters. |
| D4 | **Caching:** a full `snapshot` (from home-hub, via pi-agent) arrives on every (re)connect; the UI also keeps a debounced JSON cache file for cold boot. No database. |
| D5 | **One transport, one router:** a single WebSocket client plus a typed message router (sealed classes, exhaustive `switch`). No HTTP clients in the app. |
| D6 | Device pixel ratio ≈ **1.0**; the logical canvas is 1024×600 on both Pi and Mac. |
| D7 | Pure-Dart packages only, no plugins with native code. |
| D8 | **Protocol is split** into a *client protocol* (spoken by home-hub; pi-agent passes it through) and a *device protocol* (pi-agent only). The data layer (`protocol/`, `connection/`, `feeds/`, `cache/`, non-display state) is **portable**: it must not import panel-only code, so it can later move to `dart/libs/home_client/` and be shared with a phone app that connects to home-hub directly. |

## Hardware / display
- Raspberry Pi 5, Raspberry Pi OS 64-bit (Bookworm). The current Pi runs the **desktop** image
  (hostname `raspberrypi`, user `liustev6`, SSH alias `panel`); the desktop (`lightdm`) must be
  stopped while flutter-pi runs. All flutter-pi runtime libraries ship with the desktop image.
- Screen: **1024×600 px over HDMI** (HDMI-A-2, native mode 1024×600), landscape, active area
  **152.4 × 83.3 mm**. Touch only, no keyboard/mouse.
- Run flutter-pi with `--dimensions "269,158"` (D6). flutter-pi derives the pixel ratio as
  `10·width_px / (width_mm·38)`, so a nominal 269 mm width gives ≈ 1.0. (The real 152 mm would give
  ≈ 1.77 and shrink the canvas to ~578×338.) The bundled flutter-pi binary does **not** support
  `--pixel-ratio` yet; that flag only exists on flutter-pi's GitHub `master`.
- Tap targets ≥ 12 mm physical ≈ **81 logical px**, defined once in `ui/sizing.dart`.
- Viewed from ~0.5–1.5 m: large type, high contrast, dark theme. **Inter** (OFL) is bundled in `assets/fonts/inter/` (weights 200–700) and set app-wide, so the Mac and
  the Pi render identically (the Pi has no SF; it would fall back to DejaVu).
- **Visual style: lightweight glass** (`ui/glass.dart`), built from plain Flutter: translucent
  gradient panels with a hairline border over a static background with soft colour fields. The
  sleep view has its own calmer background: a plain vignette. One shared
  backdrop blur (`BackdropGroup`) that can be switched off with `GlassTokens.enableBlur` if the Pi
  can't hold frame rate. No glass-effect packages (e.g. `liquid_glass_widgets`: its iOS-style
  refraction needs Impeller, which flutter-pi doesn't use). The sleep view has no glass.
- **Design system** (`ui/theme.dart`):
  - Tokens (`PanelColors`): **primary** = shaded white (gradient `#F4F4F7 → #B4B6C1`, the
    clock's colour), **secondary** = subtitle grey `#9497A3`, plus **primaryInverse** (shaded
    near-black `#1C1F2A → #4A4F5E`) and **secondaryInverse** (`#5B6070`) for light mode.
  - `PanelPalette` (a `ThemeExtension`, read with `context.palette`) maps tokens per mode:
    `primary` (gradient, applied with a `ShaderMask`), `primaryColor` (flat), `secondary`,
    dashboard background (`background` + `glows`), sleep vignette (`sleepGlow` → `sleepBackground`),
    glass fill/border (contrasting: white tint in dark mode, near-black tint in light mode). `PanelPalette.dark` uses primary/secondary;
    `PanelPalette.light` uses the inverses. Widgets never hard-code colours.
  - **Light/dark mode**: a 1×1 tile in the bottom row toggles it; dark is the default. It applies
    to both the dashboard and the sleep view (each palette has its own sleep vignette colours).

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
- Each `PanelWidget` declares the feeds it needs (later: a `feeds` getter, e.g. the Home widget
  built with a list of entity ids). Feed ids: `weather`, `ha:<entity_id>`.
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
- **Store** (`state/app_state.dart`): one `AppState` plus plain setters on `AppStore`; no logic.
  Widgets watch slices with `ref.watch(appStoreProvider.select(...))`.
- **Events** (`state/events/`): every event is a `UserEvent` or a `SystemEvent` (source) and may
  also be a `DisplayEvent` (effect). Widgets only ever **dispatch events**.
- **Handlers** (`state/handler/user_handler.dart`, `system_handler.dart`): one function per event;
  read the state, call the store's setters. The dispatcher routes events to handlers (exhaustive
  over the sealed `AppEvent`), restarts the idle timer for every `UserEvent`, and owns side effects
  (timers; later, network sends).
- Tests run events through the dispatcher and check the store (e.g. the display table in
  `test/state/handler/display_handlers_test.dart`).
- Providers:

| Provider | Kind | Holds | Layer (D8) |
|---|---|---|---|
| `agentClientProvider` | `Provider` | the `AgentClient` instance | portable |
| `connectionProvider` | `Notifier` | socket status, last message time, protocol mismatch flag | portable |
| `relayProvider` | `Notifier` | relay-to-home-hub `online/offline` (from `connection`) | panel |
| `assistantProvider` | `Notifier` | assistant state (below) | portable |
| `haProvider` | `Notifier` | entity states + pending optimistic actions | portable |
| `feedsProvider` | `Notifier` | feed registry (weather + staleness) | portable |
| `appStoreProvider` | `Notifier` | the app store (Zustand-style), **state + setters only**: `AppState` = display state (`active / sleep`) + settings (`themeMode`); setters `setDisplay`, `setThemeMode`. Widgets watch slices with `.select` | panel |
| `eventDispatcherProvider` | `Provider` | `dispatch(AppEvent)`: routes each event to its handler, which works out the new state and calls the store's setters; owns side effects (the idle timer). Widgets send events here, never call setters directly | panel |
| `screenProvider` | `Provider` (derived) | what to render (below); never stored | panel |

- Portable providers live in `state/client_providers.dart`; panel state lives in the app store,
  `state/app_state.dart` (plus `state/app_time.dart` for the clock tick).
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
- **No wake-tap swallowing:** we assume a single tap. The tap that wakes the panel lands on the
  sleep view (no controls), so it can't press anything on the dashboard.
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
A grid of **square cells**: always **12 columns**; the cell size is the width
divided by 12 (minus gaps), and the number of rows is however many full rows fit the height. At
1024×600, with 16 px side and 8 px top/bottom padding, that's **68 px cells, 12 × 7 = 84 cells**
(16 px gaps; the leftover 12 px is split above and below the grid). There is no top bar: the
dashboard is the grid only. A 1×1 cell (68 px ≈ 10 mm) is below the 12 mm tap target, so tappable widgets should
span ≥ 2×2.

**Widget model** (`lib/layout/`):
- **`PanelWidget`** (`panel_widget.dart`): abstract base class (a Riverpod `ConsumerWidget`) for every
  dashboard widget. Each subclass declares its own **size** (`columns`, `rows`, each 1–4 cells,
  `PanelWidget.maxSpan`) and whether it is **`expandable`** (has a detail page, opened via the
  router later). Widget-specific settings (e.g. the Home widget's entity list) are constructor
  parameters.
- **Layout** (`layout_config.dart`): `dashboardLayout`, a `const` Dart list of
  `PanelPlacement(column, row, widget)`: which widgets appear and the grid cell of each one's
  top-left corner. Sizes come from the widgets. **No YAML and no registry**: adding a widget means a
  `PanelWidget` subclass plus one placement line. Changing the layout means a rebuild + deploy
  (`scripts/run_deploy.sh`, ~30 s); there is no on-Pi layout file.
- **Validation** (`panel_grid.dart`, `validPlacements`): spans outside 1–4, negative positions,
  anything past the last column or row, and overlaps are skipped with a log message. One bad
  placement never takes the dashboard down; a test checks the real layout has no skips.

1. **Clock + date.** No feeds.
2. **Weather** (3×2, `expandable`): glass card like the other widgets. Inside, 6 equal sub-rows:
   rows 1–2 **location arrow + city** (regular body text); rows 3–5 **current temperature**
   ("18°C", medium weight, left); rows 3–6 **condition icon** (square, right; coloured Material
   Icons, sun/moon layered behind a cloud for partly cloudy, cloud over drops for rain); row 6
   **today's low / high** ("12°C / 23°C", left). Feed `weather` (contract below). Uses a random
   mock (`MockWeather`, new values every 5 s) until the feed exists.
3. **HA quick controls**: lights/scenes as toggle tiles. Constructor `entities: [...]` → feeds `ha:<id>`.
4. **Assistant panel**: phase indicator, live transcript, streamed reply, push-to-talk button
   (press = start, release = stop), text input via the in-app on-screen keyboard (flutter-pi has no
   system IME). Rendered as the assistant overlay (see Screen derivation); an optional dashboard
   tile opens it.
5. **Status** (connection, relay, last update, protocol mismatch): the fixed top status strip was
   **removed**; it will come back as a grid widget.
6. **Sleep tile** (1×1): puts the panel to sleep immediately.
7. **`PlaceholderTile(title, icon, columns, rows)`**: stand-in of any size for widgets that don't
   exist yet (icon only at 1×1, icon + title when one cell wide or tall, full card otherwise).

Current `dashboardLayout` (fills all 84 cells; a test checks this):
```
     0  1  2  3 | 4  5  6  7 | 8  9  10 | 11
r0   Clock 4×2  | Home 4×4   | Weather 3×2  | Temp 1×1
r1              |            |              | Humid 1×1
r2   Media 4×2  |            | Calendar 3×3 | Door 1×1
r3              |            |              | Volume
r4   Forecast 4×1 | Assistant |             |  1×2
r5   Hourly 4×1 |   4×2      | Camera       | Energy
r6   Sleep Lock Fan Theme|Sun 2×1|Air 2×1| 2×2 | 2×2
```

## Weather feed contract
home-hub fetches weather and pushes it; the app only renders it. Model: `lib/weather/weather_data.dart`
(pure Dart, moves into `protocol/messages.dart` later). Provider: `weatherProvider`
(`lib/weather/weather_provider.dart`); the mock is swapped for a feed-backed notifier later, widgets
don't change.

Message (also used for the `weather` entry in `snapshot.widgets`):
```json
{"type": "widget_update", "widget": "weather", "ts": 1762290000000,
 "data": {"location": {"name": "San Francisco"},
          "current": {"temperature_c": 18.4, "condition": "partly_cloudy", "is_day": true},
          "today": {"low_c": 7.2, "high_c": 23.8}}}
```
- Celsius always; the UI rounds for display. snake_case keys (Python backend).
- `condition` is a provider-independent enum: `clear`, `partly_cloudy`, `cloudy`, `fog`, `drizzle`,
  `rain`, `snow`, `sleet`, `thunderstorm`, `windy`. Unknown values render as `unknown` (never crash).
- `is_day` picks sun vs moon icons and the night background.
- `today` (optional): forecast low/high for the location's current day; the UI hides the row if
  missing. Open-Meteo: `&daily=temperature_2m_min,temperature_2m_max&forecast_days=1`.
- Later fields (additive, optional): `hourly: [...]`, `daily: [...]`.

Backend (home-hub) suggestion: **Open-Meteo** (free, no API key), polled every ~10–15 min:
`GET https://api.open-meteo.com/v1/forecast?latitude=…&longitude=…&current=temperature_2m,weather_code,is_day,wind_speed_10m&timezone=auto`.
The city name comes from home-hub config (or Open-Meteo's geocoding API). WMO `weather_code` →
`condition`:

| WMO code | condition |
|---|---|
| 0, 1 | `clear` |
| 2 | `partly_cloudy` |
| 3 | `cloudy` |
| 45, 48 | `fog` |
| 51, 53, 55 | `drizzle` |
| 56, 57, 66, 67 | `sleet` (freezing drizzle/rain) |
| 61, 63, 65, 80, 81, 82 | `rain` |
| 71, 73, 75, 77, 85, 86 | `snow` |
| 95, 96, 99 | `thunderstorm` |
| (any non-precipitating code with `wind_speed_10m` ≥ 40 km/h) | `windy` |

## Flutter project layout
`[P]` = portable data layer (D8): pure Dart or Riverpod only, no imports from `[panel]` code.
Everything else is panel-only.
```
dart/apps/home-panel/
├── README.md                    # toolchain, dev, build, deploy, protocol
├── pubspec.yaml                 # name: home_panel
├── analysis_options.yaml
├── assets/
│   └── fonts/inter/             # Inter (OFL), weights 200–700
├── lib/
│   ├── main.dart                # ProviderScope, env config, runApp
│   ├── app.dart                 # MaterialApp, theme, screenProvider switch
│   ├── protocol/                # [P]
│   │   └── messages.dart        # ALL protocol models + JSON, tagged client/device
│   ├── connection/              # [P]
│   │   ├── agent_client.dart    # WebSocket, ping, reconnect/backoff; endpoint is config
│   │   └── message_router.dart  # decode → dispatch to notifiers
│   ├── weather/                 # [P] weather_data.dart (model + JSON), weather_provider.dart (mock for now)
│   ├── feeds/                   # [P]
│   │   └── feed_registry.dart   # feed ids, fetchedAt, staleness
│   ├── cache/                   # [P]
│   │   └── snapshot_cache.dart  # debounced atomic JSON file
│   ├── state/
│   │   ├── app_state.dart          # global app store: AppState (display + settings) + setters
│   │   ├── app_time.dart           # global clock: per-second / per-minute time
│   │   ├── types/                  # state data types
│   │   │   └── display_state.dart     # DisplayMode, DisplayState
│   │   ├── events/                 # what can happen (definitions only)
│   │   │   ├── app_event.dart         # sealed AppEvent; source: UserEvent | SystemEvent (extends);
│   │   │   │                          # effect: DisplayEvent (implements); the files below are parts
│   │   │   ├── user_events.dart       # UserUncategorizedClick, SleepClick, ThemeModeClick
│   │   │   └── system_events.dart     # IdleTimeout
│   │   ├── handler/                # what each event does
│   │   │   ├── event_dispatcher.dart  # dispatch(event) → handler; owns the idle timer (UserEvents
│   │   │   │                          # restart it); HandlerContext (state, setters, dispatch)
│   │   │   ├── user_handler.dart      # handlers for every UserEvent
│   │   │   └── system_handler.dart    # handlers for every SystemEvent
│   │   ├── assistant_machine.dart  # [P] pure reducer (planned)
│   │   ├── ha_machine.dart         # [P] pure reducer (optimistic/pending, planned)
│   │   └── client_providers.dart   # [P] connection / assistant / ha / feeds notifiers (planned)
│   ├── display/
│   │   ├── dashboard_view.dart  # grid from layout
│   │   ├── sleep_view.dart      # ambient clock, 1/min redraw, pixel shift
│   │   ├── assistant_overlay.dart
│   │   └── touch_guard.dart     # every touch → UserUncategorizedClick (wake + idle reset)
│   ├── layout/
│   │   ├── panel_widget.dart    # abstract PanelWidget: columns, rows (≤4), expandable
│   │   ├── panel_grid.dart      # 12-column square-cell grid, PanelPlacement, validPlacements
│   │   └── layout_config.dart   # dashboardLayout: const list of PanelPlacement
│   ├── widgets/
│   │   ├── clock_widget.dart
│   │   ├── weather_widget.dart
│   │   ├── ha_controls_widget.dart
│   │   ├── assistant_panel.dart
│   │   ├── sleep_tile.dart      # 1×1 sleep button
│   │   └── placeholder_tile.dart # stand-in of any size
│   └── ui/
│       ├── theme.dart
│       ├── sizing.dart          # 12 mm tap target, spacing scale
│       └── keyboard/            # on-screen keyboard
├── tool/
│   └── mock_agent.dart          # `dart run tool/mock_agent.dart`
├── test/
│   ├── protocol/                # JSON round-trip for every message
│   ├── state/                   # store, events, handler tables (display, …)
│   ├── connection/              # router + fake socket reconnect
│   └── widgets/                 # dashboard at 1024×600, sleep view, overlay
├── macos/                       # generated; window size fixed in MainFlutterWindow.swift
├── deploy/                      # files that get INSTALLED ON THE PI
│   ├── start-panel.sh           # desktop → app switch (transient unit home-panel-app)
│   ├── home-panel.desktop       # desktop/app-menu launcher (@HOME@ filled in on install)
│   ├── home-panel.svg           # launcher icon
│   └── home-panel.service       # (later) systemd unit for boot-to-app
└── scripts/                     # scripts RUN ON THE MAC
    └── run_deploy.sh            # build → stop app → rsync app + deploy/ files [--run] [--skip-build]
```

Dependencies: `flutter_riverpod` (3.x), `intl`. Everything else from the SDK (`dart:io`
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
- flutterpi_tool: the pub.dev release (0.12.0) does **not** compile against Flutter 3.47. Install
  from GitHub instead: `flutter pub global activate --source git https://github.com/ardera/flutterpi_tool`
  (working commit `248d799`, "add support for flutter 3.47"). Switch back to pub.dev once a release
  supports the installed Flutter.
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
└── cache.json    # snapshot cache; survives deploys
```

## Build & deploy (`scripts/run_deploy.sh`, run on the Mac)
Current mode: **the Pi boots to the desktop**; the app runs only when started from the
"Home Panel" launcher (or `--run`), which stops the desktop and starts the app as the transient
unit `home-panel-app`. Reboot (or `sudo systemctl stop home-panel-app; sudo systemctl start lightdm`)
returns to the desktop.
```sh
scripts/run_deploy.sh            # build, stop app, copy app + launcher files
scripts/run_deploy.sh --run      # ...and switch the Pi screen to the app
```
Under the hood:
```sh
flutterpi_tool build --arch=arm64 --cpu=pi5 --release   # → build/flutter-pi/pi5-64/
ssh panel 'sudo systemctl stop home-panel-app'
rsync -a --delete build/flutter-pi/pi5-64/ panel:panel/app/
rsync deploy/{start-panel.sh,home-panel.svg,home-panel.desktop} panel:panel/   # + install launcher
```
- Output goes to `build/flutter-pi/<target>/` (`pi5-64` with `--cpu=pi5`), **not**
  `build/flutter_assets/`. It contains the `flutter-pi` binary, `libflutter_engine.so`, `app.so`
  and assets. The script globs for the single target directory rather than hard-coding it.
- `--cpu=pi5` uses the Pi 5–tuned engine (available in the git build of flutterpi_tool).
- `PI_HOST` comes from the environment, default `panel` (the `~/.ssh/config` alias).
- `sudo` over ssh relies on passwordless sudo (Pi OS default for the first user).

## systemd unit (`deploy/home-panel.service`), later, for boot-to-app
```ini
[Unit]
Description=Home panel Flutter UI
After=systemd-user-sessions.service pi-agent.service
Conflicts=getty@tty1.service

[Service]
User=<user>
SupplementaryGroups=render video input
WorkingDirectory=/home/<user>/panel
Environment=HOME_PANEL_CACHE=/home/<user>/panel/cache.json
ExecStart=/home/<user>/panel/app/flutter-pi --release --dimensions 269,158 /home/<user>/panel/app
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
- `active` ↔ `sleep` switch on touch, on 2 min idle, and on `display_state`.
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
- Weather location and the HA entity list for the Home widget.
- Keep the on-screen keyboard in v1, or ship PTT-only first?
