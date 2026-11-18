# The voice path (CP4, after speech-service)

**Status:** agreed 2026-11-17; 4.9-4.10 done, 4.11 (the turn) and 4.12 (the wake word, the session) in progress. The rest of CP4: you talk to the Pi, it answers out loud.

```
 the Pi (panel)                                       the lab (LAN)
┌──────────────────────────────────────────┐
│ USB speakerphone ─ mic ─▶ arecord ─┐     │   audio  ┌────────────────────────────┐
│                                    ▼     │ ───────▶ │ speech-service (CT 108)    │
│                           pi-server ─────│ ◀─────── │  speech to text, streamed  │
│                     (the voice turn)     │   text   └────────────────────────────┘
│                                    │     │   text   ┌────────────────────────────┐
│                                    ├─────│ ───────▶ │ agent-service (CT 107)     │
│                                    │     │ ◀─────── │  profile voice, streamed   │
│                                    │     │  deltas  └────────────────────────────┘
│                                    │     │   text   ┌────────────────────────────┐
│                                    ├─────│ ───────▶ │ speech-service             │
│ USB speakerphone ◀─ aplay ◀────────┘     │ ◀─────── │  text to speech, streamed  │
│  (speaker)                               │   audio  └────────────────────────────┘
│ Hey Pi (Flutter) ◀─ ws://127.0.0.1 ──────│ phases, transcript, reply (later: 4.14)
└──────────────────────────────────────────┘
```

## What the Pi has (checked 2026-11-17)
- **A USB "4-mic Speakerphone"** (Actions chip): mic and speaker in one, ALSA card 2. Its speaker
  mixer (`PCM`) is at **0%**; the mic (`Mic`) at 100%.
- **PipeWire** runs only in a logged-in session (`Linger=no`; Hey Pi's service stops the desktop).
- **alsa-utils** (`arecord`, `aplay`) installed; Python 3.11; 8 GB RAM, 4 cores; no fan (it
  throttled at 83 °C in the speech-to-text spike).
- **The lab is reachable** over Wi-Fi: speech, agent and auth answer `/health` in 70–110 ms.
- **pi-server isn't installed** (`~/pi-server` absent); its auth client exists (2026-11-07) but its
  secret never reached the Pi.
- **Hey Pi doesn't talk to pi-server yet:** its assistant tile is a placeholder, so the screen
  can't start a turn (no push-to-talk button yet).

## Decisions
| # | Decision | Why |
|---|---|---|
| V1 | **pi-server runs the voice turn** (speech-service T10): it calls speech-service and agent-service directly on the LAN, as its own client `pi-server`. agent-service stays text-only; speech-service stays an engine. home-hub (flutter_ui.md D2) doesn't exist: when it does, only the text side (the agent turn) could move there; the audio stays on the device | The cascaded voice agent of Pipecat and LiveKit Agents: one place owns the timing (listening, the reply's streaming, playback, barge-in). It's the same loop the test client's `/voice` prototype runs |
| V2 | **Audio through ALSA directly, with `arecord` and `aplay` as child processes** (raw 16-bit mono: the mic at 16 kHz, the speaker at 24 kHz; ALSA's `plughw` converts to what the device takes). Playback is aplay's stdin; stopping at once is ending aplay | How Home Assistant's voice satellite (Wyoming) does it on a Pi. pi-server is a system service with no desktop session, so no PipeWire; nothing to build (no PortAudio, no Python audio bindings); a child that crashes can't take pi-server down |
| V3 | **Triggers:** **the wake word, on the Pi** (V9): the real one; Hey Pi's push-to-talk when the app talks to pi-server (4.14); `POST /debug/voice/listen` (curl). **A turn ends** on 700 ms of silence (speech-service's server turn detection) or a commit (push-to-talk released) | Hands-free, which is the point of a panel; the same session for every trigger |
| V4 | **A voice session is one agent session, in a new conversation** (revised 2026-11-17): claimed at the wake word (its pod with it: agent-service C6), so it's ready by the end of the sentence; **released** (`DELETE`) when the voice session ends (V9). A session that ended mid-way (its limits) is replaced in the same conversation. Continuing an earlier conversation: later | A pod is held only while you're talking to it, not for agent-service's 5 idle minutes. A conversation is agent-service's history (PostgreSQL), apart from its sessions (pods): a new session can continue one (C9), so continuity can come later without changing this |
| V5 | **The reply streams end to end:** the agent's text deltas go into one TTS session as they arrive (speech-service cuts them into clauses: first audio after the first clause, not the whole reply); its audio goes to aplay as it arrives | You hear the first words while the agent still writes. Measured per turn: heard → agent's first text → first audio |
| V6 | **Half duplex until barge-in** (4.13): the mic isn't sent while the reply plays | The speaker would hear itself. Barge-in needs the wake word listening while speaking, with the speakerphone's echo cancellation (to be measured) |
| V7 | **Hey Pi's protocol unchanged:** pi-server sends the `assistant` messages already specified (phase `listening` → `thinking` with the `transcript` → `speaking` with each `reply_delta` → `idle` when the speaker is done) | The app needs no protocol change when it starts listening (4.14) |
| V8 | **Failures end the turn, never pi-server:** a hop refused or down → phase `idle`, logged with which hop; the next trigger starts clean. No retry inside a turn, except the session replacement (V4) | A late answer is worse than asking again (speech-service's admission rule) |
| V9 | **The wake word and the voice session** (2026-11-17): the mic is always on and stays on the Pi: microWakeWord (`pymicro-wakeword`, Home Assistant's satellites' engine; Apache-2.0) listens for the word, and a voice detector (`pymicro-vad`) is fed every frame. **The word → a session:** the screen wakes, Hey Pi shows `listening`, the agent session is claimed (V4). **Waiting:** the first voice within **5 s** starts a turn, with the 500 ms before it (its first syllable); a turn's audio goes to speech-service only then. **After each reply, 5 s more** for a follow-up, no wake word needed. **5 s and nobody:** the session ends, the agent session is released, Hey Pi `idle`. The word: `hey_jarvis` (built in) until a "hey pi" is trained (4.12b) | Audio leaves the Pi only when someone is talking to it. A follow-up window is how smart speakers do conversation; 5 s is short enough that a TV doesn't keep it open. microWakeWord: small (made for an ESP32), maintained (2026), the Pi's wheels; openWakeWord's last release is from 2024 |

## Build plan
| # | What | Done when |
|---|---|---|
| 4.8 ✅ | **this design** | reviewed and agreed (2026-11-17) |
| 4.9 ✅ | **pi-server's identity:** grants in auth-service's policy (`speech-service`: `speech:create`, `speech:transcribe`; `agent-service`: `chat:read`, `chat:write`); its secret rotated onto the Pi (`scripts/add-client.sh pi-server pi /etc/pi-server/credentials/client_secret liustev6 --rotate`, then `promote-secret`) | on the Pi, a token for both audiences. **Done 2026-11-17:** the secret rotated onto the Pi and promoted; tokens for `speech-service` (`speech:create speech:transcribe`) and `agent-service` (`chat:read chat:write`) |
| 4.10 ✅ | **the Pi's audio:** the speaker's level; `app/voice/audio.py` (the mic and the speaker, V2); a check on the Pi: speech-service speaks a sentence out of the speaker, the mic hears it, speech-to-text reads it back | the sentence comes back right; the speaker's latency known. **Done 2026-11-17:** the mic's own format is 16 kHz mono (no conversion); the speaker takes 48 kHz stereo (plughw converts our 24 kHz); the speaker at 60%; aplay adds ~120 ms at its start. **The speakerphone cleans the mic itself:** what isn't speech comes out as digital zero, and its own playback was removed entirely (echo cancellation: good for barge-in, 4.13); a voice is −30 dB, heard right |
| 4.11 🔨 | **the voice turn in pi-server** (V1, V3–V8): `app/voice/` (the agent client, the speech sessions, the turn), `POST /debug/voice/listen`; the mock assistant only when `MOCK`; tests with fake services; deployed with `pi-server.sh` (with `service-auth`) | on the Pi: curl, ask, hear the answer; timings in the log. **So far (2026-11-17):** `app/voice/` and 9 tests with fakes; deployed (`deploy/pi-server.env` turns it on); a typed turn spoken on the Pi: first audio 3.6 s (the agent's first text 3.4 s, the first turn's tokens and session included) |
| 4.12 🔨 | **the wake word and the voice session** (V9, V4): microWakeWord and a voice detector on the Pi, the session's 5 s windows, the agent session claimed and released with it | say the word, ask, hear the answer, ask again without it; 5 s later the pod is released; CPU and temperature measured. **So far (2026-11-17):** `app/voice/wake.py`, `assistant.py` (the session as a state machine), 14 tests with fakes and a fake clock; `hey_jarvis` fires on English voices (1.00), never on "Hey Pi", "Hey, how are you" or Chinese (0.00) |
| 4.12b | **"Hey Pi":** a microWakeWord model trained for it (its training: synthetic samples, here from our own TTS's voices, on the GPU) | the word fires for you and not for the room |
| 4.13 | **barge-in:** the wake word heard while the reply plays (the speakerphone's echo cancellation) cancels the agent's turn and stops the speaker | interrupting works without the reply triggering itself |
| 4.14 | **Hey Pi shows it** (Flutter): connects to pi-server, the assistant overlay's phases, push-to-talk | later; the app's own work |
| 4.15 | **protocol B in shared-schemas; the agent's latency levers** | defined when reached |

## Found along the way
- **The TTS misreads digits** (2026-11-17; **fixed** by text normalization in speech-service, its T14 and spikes/13: 56% → 90% read right): "5,959 metres, or 19,551 feet", spoken 4 times, came back 4
  ways ("nineteen point one five hundred…", "fifty desimples") and took 11–14 s for ~5 s of words.
  The standard fix is text normalization before the TTS (numbers, units, dates as words: Riva's TTS
  pipeline does it); a "say numbers in words" line in the `voice` profile as a cheap second guard.
- **The agent dominates the wait:** heard → its first text ~2 s, against ~0.4 s for everything else
  (the test client's e2e, 2026-11-17). The latency levers (4.15).
