# Spec: Hey Pi, the home panel Flutter UI (Raspberry Pi 5 + flutter-pi)

## Scope
A full-screen Flutter touch UI for a wall/desk home control panel. It runs on a Raspberry Pi 5 via
**flutter-pi** (no desktop; draws straight to the display through KMS/DRM).

The UI's only peer is **pi-server**, a local Python service on the Pi (`python/services/pi-server`,
FastAPI; it runs the voice assistant) reached over `ws://127.0.0.1:8765/ws`. **The protocol has one
message today** (since 2026-11-26): pi-server tells the UI its speech state (see "Protocol"). The UI never calls Home Assistant, the LLM, a weather API, or any
lab service directly, holds no secrets, and has no hardware code. This spec covers the app, its dev
loop, the Pi install, and deployment.

### System context
```
Panel:  Flutter UI ──ws (localhost)──▶ pi-server ──wss (LAN)──────────┐
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
| **pi-server** (`python/services/pi-server`) | The voice assistant: wake word, mic, speaker, the voice turn ([voice-path.md](voice-path.md)), the device's lab credential; **tells the UI the speech state**. Later: relaying data for the dashboard | Weather, HA calls, LLM, any business logic |
| **home-hub** (lab, future; not in the protocol yet) | The client API for every client (panel and phone): feeds, snapshots, HA actions, assistant turns; calls to HA / weather / LLM; device identity | Anything specific to one client's hardware |
| **Home Assistant** | Devices and automations | — |

Neither client registers with Home Assistant. Both register with home-hub as devices: the panel's
device token is held by pi-server; the phone authenticates as you over the VPN. home-hub is the only
HA client, and the HA token never leaves the lab.

Enclosure: `models/3d-print/home-control-frame/`.

## Locked decisions
| # | Decision |
|---|---|
| D1 | **The app's own mode, app-only (never in the protocol):** **`dashboard`** (the widgets) and **`idle`** (the ambient clock view). **The UI owns the idle timer:** 2 min without a touch → `idle`; a touch → `dashboard`. No separate black state (2026-11-26: renamed from active/sleep; pi-server no longer forces it). |
| D2 | **All data fetching lives in the lab** (home-hub, or tools-service's REST API for weather), never in the UI. **Future:** the dashboard's data reaches the UI through pi-server as new protocol messages; none exist yet. |
| D3 | **Riverpod 3** (`flutter_riverpod`), **no code generation** (no `riverpod_generator`/`build_runner`). Zustand-style stores (state + setters), **one store per owner** (`lib/state/store/`): the **app store** is the app's own slice; the **server store** is what pi-server tells the app (today its speech state; one field per protocol message). **Every change goes through an event:** events describe what happened (a touch, a timer, a pi-server message), handlers decide the new state and call the setters; nothing else changes a store. The two meet only in derived providers (`uiModeProvider`) (2026-11-26). |
| D4 | **No database on the Pi, no files in the UI** (its app dir is replaced on every deploy). The theme is app-only and not kept across a restart yet (2026-11-26: pi-server's settings file was removed with the old protocol). History and anything shared with other devices belongs in the lab. |
| D5 | **One transport, one router:** a single WebSocket client plus a typed message router (sealed classes, exhaustive `switch`). No HTTP clients in the app. |
| D6 | Device pixel ratio ≈ **1.0**; the logical canvas is 1024×600 on both Pi and Mac. |
| D7 | Pure-Dart packages only, no plugins with native code. |
| D8 | **The protocol starts minimal and grows by adding message types** (2026-11-26: the first design's client/device split, mock feeds, settings and assistant messages were removed). Today: `speech`, pi-server → UI; `wake` (the voice button), UI → pi-server. `protocol/` and `connection/server_client.dart` stay **pure Dart**, so they can be shared later. |
| D9 | **What the screen shows is derived, never stored:** pi-server's speech state when it isn't idle (`listening`, `thinking`, `speaking`), else the app's own mode (`dashboard`, `idle`) (2026-11-26). |

## Hardware / display
- Raspberry Pi 5, Raspberry Pi OS 64-bit (Bookworm). The current Pi runs the **desktop** image
  (hostname `raspberrypi`, user `liustev6`, SSH alias `pi`); the desktop (`lightdm`) must be
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
pi-server ──ws──▶ ServerClient ──▶ message_router ──┐ system events
            (transport, pure Dart)                   ▼
touches, timers ───────────────────────────────▶ dispatcher ──▶ handlers ──▶ app store ──┐
                              user / system events                     └──▶ server store ─┴─▶ uiModeProvider ──▶ PanelRoot
```

### ServerClient (transport): `lib/connection/server_client.dart`
- `dart:io` `WebSocket` (no package), `pingInterval` 5 s to detect a hung pi-server. Pure Dart.
- `send(message)` while connected; **not connected, it's dropped, not queued** (a tap from a minute
  ago mustn't wake the assistant later).
- Reconnect with exponential backoff: 0.5 s → 10 s cap, reset after a successful connect.
- Exposes the link status (`connecting | connected | disconnected`) and the decoded messages.
- The address is `ws://127.0.0.1:8765/ws` (the Pi's loopback), or `--dart-define=PI_SERVER_URL=…`.

### Message router: `lib/connection/message_router.dart`
- `serverClientProvider` (the one client; not started under `flutter test`, where tests override it)
  and `serverLinkProvider` (alive from boot: `PanelRoot` watches it).
- **It changes no store:** each message becomes a `SystemEvent` (`speech` → `ServerSpeech(state)`),
  dispatched like any other event; its handler changes the server store. One exhaustive `switch` over
  the sealed `ServerMessage`: a new message type nobody handles is a compile error. Unknown types and
  malformed JSON are logged by the client and dropped; they never close the connection.
- **Not connected → `ServerDisconnected`**, whose handler sets speech `idle`: with pi-server gone,
  there's no voice session the UI can know of.

## App state (Riverpod 3, D3)
Stores live in `lib/state/store/`. Widgets watch slices (`ref.watch(provider.select(...))`) and only
ever **dispatch events**; the router turns pi-server's messages into system events too. **Only
handlers change a store.**

| Store | Holds | Changed by |
|---|---|---|
| `appStoreProvider` (`store/app_store.dart`) | `AppMode` (`dashboard`, `idle`; D1) + theme mode: **app-only**, never sent | handlers, for user events (touches, the Sleep tile, the theme tile) and the idle timer |
| `serverStoreProvider` (`store/server_store.dart`) | `ServerState`: what pi-server tells the app, one field per protocol message; today `speech` (`idle`, `listening`, `thinking`, `speaking`) | handlers, for the router's system events: `ServerSpeech` (each `speech` message), `ServerDisconnected` (`speech` → `idle`) |
| `uiModeProvider` (`store/ui_mode.dart`) | `UiMode` (`dashboard`, `idle`, `listening`, `thinking`, `speaking`), **derived, never stored** (D9) | — |

- **Events** (`state/events/`): each is a `UserEvent` or a `SystemEvent` (source) and may also have
  effect types: `AppModeEvent` (changes the app store), `ServerStateEvent` (changes the server store). **Handlers** (`state/handler/`): one function per event; through their
  `HandlerContext` (`store/handler_context.dart`) they read the state, call the store's setters,
  raise further events and **send pi-server a message** (`send`: the voice tile's `VoiceClick` sends
  `wake`). The dispatcher routes events to handlers (exhaustive over the sealed `AppEvent`) and owns
  the idle timer (every `UserEvent` restarts it).
- **The idle timer keeps running during speech.** When speech goes back to `idle`, the screen shows
  the app's mode as it is by then (`dashboard`, or `idle` if nobody touched it for 2 min).
- **The voice tile changes no store:** it sends `wake`, and the screen follows pi-server's `speech`
  messages, as for the wake word.
- `PanelRoot` renders `uiModeProvider`: `dashboard` → the dashboard, `idle` → the ambient clock,
  `listening` / `speaking` → the speech view: the assistant's character, animated, on the ambient
  clock's background (Widgets, 4).
- Tests: events through the dispatcher and the store (`test/state/handler/`), the derivation table
  (`test/state/store/ui_mode_test.dart`), the voice tile's `wake` (`test/state/handler/voice_click_test.dart`), the speech view over both app modes
  (`test/display/panel_root_test.dart`), the figure in both speech states and themes
  (`test/display/assistant_figure_test.dart`).

### The ambient clock (`idle`)
Big time (h:mm) with AM/PM stacked over the seconds on its right, date below, small current weather;
dark navy radial background, soft white→grey time gradient, grey details. **No animations.**
Everything redraws at most once per minute **except the seconds digits**, which redraw every second in
their own repaint boundary. Shift the clock a few px every few minutes (LCD image-retention
mitigation). The tap that wakes the panel lands on the clock view (no controls), so it can't press
anything on the dashboard.

## Protocol (JSON over WebSocket; panel endpoint `ws://127.0.0.1:8765/ws`)
Models: `lib/protocol/messages.dart` (pure Dart) and pi-server's `app/protocol.py`, kept in sync.

**pi-server → UI**, on connect and on every change:
```json
{"type": "speech", "state": "idle | listening | thinking | speaking"}
```

| State | Means | pi-server's voice assistant |
|---|---|---|
| `idle` | no voice session | `IDLE` |
| `listening` | the session waits for you, or hears you | `WAITING`, `LISTENING` |
| `thinking` | you've finished: it works out its reply (the agent, its tools); again between its messages, once what it said has played ("let me check", then its searches and the answer being written) | `REPLYING`, until the reply's first audio, and between messages with the speaker past what was said (+0.3 s) (its `thinking` phase) |
| `speaking` | its voice: from the reply's first sound until it has played | `REPLYING`, from its first audio, and again at the next message's audio (its `speaking` phase) |

**UI → pi-server, the voice button** (2026-11-26):
```json
{"type": "wake"}
```
What hearing the wake word does (pi-server's `VoiceAssistant.woken`): no session, one opens (its
agent session and pod claimed now, the 7 s window, a voice starts the turn); over a reply, a
barge-in; while the session waits or listens, nothing. The same rules after it as after the word
(follow-ups, the timeouts, the wake word to barge in). The button and the word at once act once.
pi-server drops anything else a screen sends, without closing the socket.

What the UI shows when speech is `idle` is its own mode (D1, D9).
Unknown message types and states are ignored, so pi-server can add messages before the UI knows
them. **Removed on 2026-11-26** (mock, or a first design never used for real): `hello`, `subscribe`,
`snapshot`, `widget_update`, `ha_*`, `assistant*`, `display_state`, `connection`, `settings*`,
`user_activity`.

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
   **today's low / high** ("12°C / 23°C", left). Uses a random mock in the app (`MockWeather`, new
   values every 5 s) until a weather message exists in the protocol (contract below).
3. **HA quick controls**: lights/scenes as toggle tiles (later, with Home Assistant).
4. **Assistant**: not a tile: while speech isn't `idle`, the speech view replaces the screen (App
   state): the assistant's character (`AssistantFigure`, 480 px tall of the 600), listening (head tilted to a
   cupped ear, incoming waves), thinking (head tilted, a hand under the chin, eyes glancing up,
   three thought dots) or speaking (mouth moving, outgoing waves), on the sleep view's
   background (`AmbientBackground`: same vignette, same 15% dim over everything, figure included, as over
   the sleep clock). Design source: `assets/svgs/assistant-{listening,thinking,speaking}.svg`. They animate with CSS
   (`@keyframes`, `var(--outline-color)`, `d: path()`), which flutter_svg doesn't run, so the
   figure is a `CustomPainter` with the same paths (parsed by `path_parsing`) and the same
   keyframes (2.4 s loops; the thinking head's 4.8 s); an SVG change is mirrored there. Coloured like the clock's time digits: the palette's
   `assistant` gradient (the SVGs' `--outline-color`; today the same shaded white / near-black as
   `primary`), stretched over the drawing's own height (antennae to chin) as the clock's spans its
   digits. Reduced motion (`disableAnimations`): drawn still, as the SVGs'
   `prefers-reduced-motion`.
5. **Status** (connection): will come back as a grid widget.
6. **Sleep tile** (1×1): puts the panel to sleep immediately.
7. **Voice tile** (1×1, `VoiceTile`, a mic): the wake word pressed: sends `wake` (Protocol).
8. **`PlaceholderTile(title, icon, columns, rows)`**: stand-in of any size for widgets that don't
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
r6   Sleep Voice Fan Theme|Sun 2×1|Air 2×1| 2×2 | 2×2
```

## Weather feed contract
**Not in the protocol yet** (2026-11-26): the shape a future weather message will carry; the app
renders it from its own mock until then. The lab fetches weather (tools-service has it) and pushes
it; the app only renders it. Model: `lib/weather/weather_data.dart`
(pure Dart, moves into `protocol/messages.dart` later). Provider: `weatherProvider`
(`lib/weather/weather_provider.dart`); the mock is swapped for a feed-backed notifier later, widgets
don't change.

The data (as the old mock protocol sent it):
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
dart/apps/hey-pi/
├── README.md                    # toolchain, dev, build, deploy, protocol
├── pubspec.yaml                 # name: hey_pi
├── analysis_options.yaml
├── assets/
│   └── fonts/inter/             # Inter (OFL), weights 200–700
├── lib/
│   ├── main.dart                # ProviderScope, env config, runApp
│   ├── app.dart                 # MaterialApp, theme; PanelRoot: uiModeProvider → the screen
│   ├── protocol/
│   │   └── messages.dart        # the protocol (pure Dart): `speech` in, `wake` out; mirrors protocol.py
│   ├── connection/
│   │   ├── server_client.dart   # WebSocket, ping, reconnect/backoff, send (pure Dart)
│   │   └── message_router.dart  # serverClientProvider, serverLinkProvider; messages → system events
│   ├── weather/                 # weather_data.dart (model + JSON), weather_provider.dart (mock for now)
│   ├── state/
│   │   ├── app_time.dart           # global clock: per-second / per-minute time
│   │   ├── store/                  # the stores
│   │   │   ├── app_store.dart         # AppState (AppMode + theme mode) + setters: app-only
│   │   │   ├── server_store.dart      # ServerState: what pi-server says (set by handlers)
│   │   │   ├── ui_mode.dart           # UiMode, derived: speech over the app's mode
│   │   │   └── handler_context.dart   # what a handler may use: state, setters, dispatch, send
│   │   ├── types/
│   │   │   └── app_mode.dart          # AppMode: dashboard | idle
│   │   ├── events/                 # what can happen (definitions only)
│   │   │   ├── app_event.dart         # sealed AppEvent; source: UserEvent | SystemEvent (extends);
│   │   │   │                          # effects: AppModeEvent, ServerStateEvent (implements)
│   │   │   ├── user_events.dart       # UserUncategorizedClick, SleepClick, ThemeModeClick, VoiceClick
│   │   │   └── system_events.dart     # IdleTimeout; ServerSpeech, ServerDisconnected (the router's)
│   │   └── handler/                # what each event does
│   │       ├── event_dispatcher.dart  # dispatch(event) → handler; owns the idle timer
│   │       ├── user_handler.dart      # handlers for every UserEvent
│   │       └── system_handler.dart    # handlers for every SystemEvent
│   ├── display/
│   │   ├── dashboard_view.dart  # grid from layout
│   │   ├── sleep_view.dart      # the ambient clock (`idle`), 1/min redraw, pixel shift
│   │   ├── speech_view.dart     # listening / thinking / speaking: the figure on the ambient background
│   │   ├── assistant_figure.dart # the character, painted from assets/svgs/assistant-*.svg
│   │   └── touch_guard.dart     # every touch → UserUncategorizedClick (wake + idle reset)
│   ├── layout/
│   │   ├── panel_widget.dart    # abstract PanelWidget: columns, rows (≤4), expandable
│   │   ├── panel_grid.dart      # 12-column square-cell grid, PanelPlacement, validPlacements
│   │   └── layout_config.dart   # dashboardLayout: const list of PanelPlacement
│   ├── widgets/
│   │   ├── clock_widget.dart
│   │   ├── weather_widget.dart
│   │   ├── sleep_tile.dart      # 1×1 sleep button
│   │   ├── voice_tile.dart      # 1×1 voice button: sends `wake`
│   │   └── placeholder_tile.dart # stand-in of any size
│   └── ui/
│       ├── theme.dart
│       ├── ambient_background.dart # the idle screens' vignette + dim (sleep clock, speech view)
│       ├── sizing.dart          # 12 mm tap target, spacing scale
│       └── keyboard/            # on-screen keyboard
├── test/
│   ├── protocol/                # decoding every message, never throwing
│   ├── state/                   # stores, events, handler tables, the UI-mode derivation
│   ├── connection/              # the client on a fake socket (reconnect), the router
│   └── widgets/                 # dashboard at 1024×600, sleep view, overlay
├── macos/                       # generated; window size fixed in MainFlutterWindow.swift
├── deploy/                      # files that get INSTALLED ON THE PI
│   ├── start-hey-pi.sh           # desktop → app switch (transient unit hey-pi)
│   ├── hey-pi.desktop       # desktop/app-menu launcher (@HOME@ filled in on install)
│   ├── hey-pi.svg           # launcher icon
│   └── hey-pi.service       # (later) systemd unit for boot-to-app
└── scripts/                     # scripts RUN ON THE MAC
    └── run_deploy.sh            # build → stop app → rsync app + deploy/ files [--run] [--skip-build]
```

Dependencies: `flutter_riverpod` (3.x), `intl`. Everything else from the SDK (`dart:io`
WebSocket and files). Only the `macos` platform folder exists; flutter-pi builds don't need `linux/`.

## pi-server for the UI (`python/services/pi-server`)
- On the Pi: the voice assistant on (`PI_SERVER_VOICE=true`); every change of its state is a
  `speech` message.
- Anywhere else (e.g. the Mac): voice off (the default), speech `idle`, and
  `curl -X POST localhost:8765/debug/speech/listening` (or `speaking`, `idle`) to drive the screen.
- `GET /health` (clients, voice, speech) for systemd and debugging.

Details: `python/services/pi-server/README.md`.

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
- **Mac, local pi-server:** run pi-server (`.venv/bin/uvicorn app.main:app --port 8765` in
  `python/services/pi-server`; voice off) in one terminal, drive the speech state with
  `curl -X POST localhost:8765/debug/speech/<state>`; `flutter run -d macos` (or F5 with the
  `hey-pi (macOS)` launch config) in another. The macOS window is fixed at 1024×600 in
  `macos/Runner/MainFlutterWindow.swift`.
- **Mac, the Pi's real pi-server:** `ssh -N -L 18765:127.0.0.1:8765 pi` (pi-server stays
  loopback-only on the Pi), then `flutter run -d macos --dart-define=PI_SERVER_URL=ws://127.0.0.1:18765/ws`:
  say 小派小派 to the Pi and the Mac's window follows.
- **On the Pi with hot reload:** register once with
  `flutterpi_tool devices add <user>@raspberrypi.local --id=pi --display-size=269x158`
  (269 mm gives flutter-pi's pixel-ratio formula ≈ 1.0, matching release), then
  `flutterpi_tool run -d pi`. Stop the `hey-pi` service first; only one process can own the display.

## Pi setup (one-time, `deploy/pi-setup.sh`, run on the Pi)
1. Boot to console: `sudo raspi-config nonint do_boot_behaviour B1`.
2. Runtime packages: `sudo apt install libgl1-mesa-dri libegl1 libgles2 libdrm2 libgbm1 libsystemd0
   libinput10 libudev1 libxkbcommon0 fontconfig ttf-mscorefonts-installer && sudo fc-cache`.
   (flutterpi_tool bundles the `flutter-pi` binary and engine into the app, so flutter-pi does
   **not** need to be compiled on the Pi.)
3. Groups: `sudo usermod -aG render,video,input <user>` (GPU, DRM, touch input).
4. Create `~/hey-pi/` and install the unit:
   `sudo cp hey-pi.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable hey-pi`.
5. Reboot.

On-Pi directory layout:
```
~/hey-pi/
├── app/          # build output; replaced wholesale on every deploy (rsync --delete)
```

## Build & deploy (`scripts/run_deploy.sh`, run on the Mac)
Current mode: **the Pi boots to the desktop**; the app runs only when started from the
"Hey Pi" launcher (or `--run`), which stops the desktop and starts the app as the transient
unit `hey-pi`. Reboot (or `sudo systemctl stop hey-pi; sudo systemctl start lightdm`)
returns to the desktop.
```sh
scripts/run_deploy.sh            # build, stop app, copy app + launcher files
scripts/run_deploy.sh --run      # ...and switch the Pi screen to the app
```
Under the hood:
```sh
flutterpi_tool build --arch=arm64 --cpu=pi5 --release   # → build/flutter-pi/pi5-64/
ssh pi 'sudo systemctl stop hey-pi'
rsync -a --delete build/flutter-pi/pi5-64/ panel:hey-pi/app/
rsync deploy/{start-hey-pi.sh,hey-pi.svg,hey-pi.desktop} panel:hey-pi/   # + install launcher
```
- Output goes to `build/flutter-pi/<target>/` (`pi5-64` with `--cpu=pi5`), **not**
  `build/flutter_assets/`. It contains the `flutter-pi` binary, `libflutter_engine.so`, `app.so`
  and assets. The script globs for the single target directory rather than hard-coding it.
- `--cpu=pi5` uses the Pi 5–tuned engine (available in the git build of flutterpi_tool).
- `PI_HOST` comes from the environment, default `pi` (the `~/.ssh/config` alias).
- `sudo` over ssh relies on passwordless sudo (Pi OS default for the first user).

## systemd unit (`deploy/hey-pi.service`), later, for boot-to-app
```ini
[Unit]
Description=Hey Pi (home panel Flutter UI)
After=systemd-user-sessions.service pi-server.service
Conflicts=getty@tty1.service

[Service]
User=<user>
SupplementaryGroups=render video input
WorkingDirectory=/home/<user>/hey-pi
ExecStart=/home/<user>/hey-pi/app/flutter-pi --release --dimensions 269,158 /home/<user>/hey-pi/app
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```
- `After=pi-server.service` is ordering only (no `Requires=`), so the UI starts and shows
  "offline" even before pi-server exists.
- If the panel is mounted rotated, add `--rotation 90|180|270` to `ExecStart`.

## Non-goals
No network calls besides the pi-server socket; no data fetching; no secrets; no
backlight/GPIO/audio code; no app-store packaging; no `linux/` desktop target; no database.

## Done when
- Dashboard renders cleanly at 1024×600 on the Mac.
- `dashboard` ↔ `idle` switch on touch and on 2 min idle.
- The speech view shows over both app modes while speech isn't `idle`, and the app's mode is back
  after it ✅ (tests, 2026-11-26).
- Cold start with pi-server down shows the app's mode, and the speech state follows once pi-server
  comes up; a pi-server restart mid-reply returns to the app's mode ✅ (tests).
- Malformed or unknown messages never crash it or close the socket ✅ (tests).
- `pi-setup.sh` + `deploy.sh` take a fresh Pi OS Lite install to the app running under systemd.
- README covers toolchain, dev loop, Pi setup, build/deploy, and the protocol.

## Open questions
- **Pixels may not be square:** 1024/152.4 ≈ 6.7 px/mm across but 600/83.3 ≈ 7.2 px/mm down.
  Confirm the active area from the panel datasheet; if the pixels really aren't square, round/square
  shapes will look ~7% squashed (Flutter can't compensate).
- Weather location and the HA entity list for the Home widget.
- Whether the speech view shows what was said and the reply (the protocol would grow
  a message for them).
