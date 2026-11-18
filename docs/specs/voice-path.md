# The voice path (CP4, after speech-service)

**Status:** agreed 2026-11-17; 4.9 and 4.10 done, 4.11 in progress. The rest of CP4: you talk to the Pi, it answers out loud.

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
| V3 | **Triggers, in order:** first `POST /debug/voice/listen` (curl; proves the whole path with no UI); then **a wake word on the Pi** (4.12), the real trigger; Hey Pi's push-to-talk when the app talks to pi-server (4.14). **The turn ends** on 700 ms of silence (speech-service's server turn detection) or a commit (push-to-talk released) | The path is testable now, without waiting on the Flutter app. A wake word makes it hands-free, which is the point of a panel |
| V4 | **One agent conversation, kept while you talk to it:** pi-server keeps one session (profile `voice`), started as listening starts (so it's ready by the end of the sentence). A conversation continues until 10 minutes pass with no turn; then the next turn starts a new one. A session that ended (its limits) is replaced in the same conversation | A smart speaker's follow-up context ("and tomorrow?") without one endless conversation. Claiming at listen time hides the session's start (a lever in the speech spec) |
| V5 | **The reply streams end to end:** the agent's text deltas go into one TTS session as they arrive (speech-service cuts them into clauses: first audio after the first clause, not the whole reply); its audio goes to aplay as it arrives | You hear the first words while the agent still writes. Measured per turn: heard → agent's first text → first audio |
| V6 | **Half duplex until barge-in** (4.13): the mic isn't sent while the reply plays | The speaker would hear itself. Barge-in needs the wake word listening while speaking, with the speakerphone's echo cancellation (to be measured) |
| V7 | **Hey Pi's protocol unchanged:** pi-server sends the `assistant` messages already specified (phase `listening` → `thinking` with the `transcript` → `speaking` with each `reply_delta` → `idle` when the speaker is done) | The app needs no protocol change when it starts listening (4.14) |
| V8 | **Failures end the turn, never pi-server:** a hop refused or down → phase `idle`, logged with which hop; the next trigger starts clean. No retry inside a turn, except the session replacement (V4) | A late answer is worse than asking again (speech-service's admission rule) |

## Build plan
| # | What | Done when |
|---|---|---|
| 4.8 ✅ | **this design** | reviewed and agreed (2026-11-17) |
| 4.9 ✅ | **pi-server's identity:** grants in auth-service's policy (`speech-service`: `speech:create`, `speech:transcribe`; `agent-service`: `chat:read`, `chat:write`); its secret rotated onto the Pi (`scripts/add-client.sh pi-server pi /etc/pi-server/credentials/client_secret liustev6 --rotate`, then `promote-secret`) | on the Pi, a token for both audiences. **Done 2026-11-17:** the secret rotated onto the Pi and promoted; tokens for `speech-service` (`speech:create speech:transcribe`) and `agent-service` (`chat:read chat:write`) |
| 4.10 ✅ | **the Pi's audio:** the speaker's level; `app/voice/audio.py` (the mic and the speaker, V2); a check on the Pi: speech-service speaks a sentence out of the speaker, the mic hears it, speech-to-text reads it back | the sentence comes back right; the speaker's latency known. **Done 2026-11-17:** the mic's own format is 16 kHz mono (no conversion); the speaker takes 48 kHz stereo (plughw converts our 24 kHz); the speaker at 60%; aplay adds ~120 ms at its start. **The speakerphone cleans the mic itself:** what isn't speech comes out as digital zero, and its own playback was removed entirely (echo cancellation: good for barge-in, 4.13); a voice is −30 dB, heard right |
| 4.11 🔨 | **the voice turn in pi-server** (V1, V3–V8): `app/voice/` (the agent client, the speech sessions, the turn), `POST /debug/voice/listen`; the mock assistant only when `MOCK`; tests with fake services; deployed with `pi-server.sh` (with `service-auth`) | on the Pi: curl, ask, hear the answer; timings in the log. **So far (2026-11-17):** `app/voice/` and 9 tests with fakes; deployed (`deploy/pi-server.env` turns it on); a typed turn spoken on the Pi: first audio 3.6 s (the agent's first text 3.4 s, the first turn's tokens and session included) |
| 4.12 | **the wake word:** openWakeWord on the Pi (a pre-trained word first, a custom "hey pi" later), listening locally; audio leaves the Pi only after it | say the word, ask, hear the answer; CPU and temperature measured |
| 4.13 | **barge-in:** the wake word heard while the reply plays (the speakerphone's echo cancellation) cancels the agent's turn and stops the speaker | interrupting works without the reply triggering itself |
| 4.14 | **Hey Pi shows it** (Flutter): connects to pi-server, the assistant overlay's phases, push-to-talk | later; the app's own work |
| 4.15 | **protocol B in shared-schemas; the agent's latency levers** | defined when reached |

## Found along the way
- **The TTS misreads digits** (2026-11-17): "5,959 metres, or 19,551 feet", spoken 4 times, came back 4
  ways ("nineteen point one five hundred…", "fifty desimples") and took 11–14 s for ~5 s of words.
  The standard fix is text normalization before the TTS (numbers, units, dates as words: Riva's TTS
  pipeline does it); a "say numbers in words" line in the `voice` profile as a cheap second guard.
- **The agent dominates the wait:** heard → its first text ~2 s, against ~0.4 s for everything else
  (the test client's e2e, 2026-11-17). The latency levers (4.15).
