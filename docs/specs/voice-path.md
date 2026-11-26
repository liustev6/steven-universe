# The voice path (CP4, after speech-service)

**Status:** agreed 2026-11-17; 4.8–4.12 done (2026-11-18: the turn, the wake word and the session, live on the Pi); 4.12b (the wake word itself: 小派小派 live since 2026-11-19) in [wake-word.md](wake-word.md); 4.13 (barge-in by the wake word) done 2026-11-25. The rest of CP4: you talk to the Pi, it answers out loud.

```
 the Pi (panel)                                       the lab (LAN)
┌──────────────────────────────────────────┐
│ USB speakerphone ─ mic ─▶ arecord ─┐     │   audio  ┌────────────────────────────┐
│                                    ▼     │ ───────▶ │ speech-service (CT 108)    │
│                           pi-server ─────│ ◀─────── │  speech to text, streamed  │
│                     (the voice turn)     │   text   └────────────────────────────┘
│                                    │     │   text   ┌────────────────────────────┐
│                                    ├─────│ ───────▶ │ agent-service (CT 107)     │
│                                    │     │ ◀─────── │  profile pi, streamed      │
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
| V2 | **Audio through ALSA directly, with `arecord` and `aplay` as child processes** (raw 16-bit mono: the mic at 16 kHz, the speaker at 24 kHz; ALSA's `plughw` converts to what the device takes). Playback is aplay's stdin; stopping at once is ending aplay. **aplay is fed in real time for the whole reply** (2026-11-26, `audio.py` `Speaker`): the reply's audio up to 300 ms ahead, silence up to 60 ms ahead when there's none; aplay with 20 ms periods and a 40 ms start threshold | How Home Assistant's voice satellite (Wyoming) does it on a Pi. pi-server is a system service with no desktop session, so no PipeWire; nothing to build (no PortAudio, no Python audio bindings); a child that crashes can't take pi-server down. **Fed in real time** because aplay reads stdin a period at a time and starts only once its buffer is full (500 ms by default): fed only the reply's audio, a pause (a web search mid-reply) held the end of the message before it in aplay, heard at the start of the next message (measured on the Pi: a 1.3 s message held 50 ms back, a 0.4 s one never started; the stream underran). Silence keeps it running, as a sound card's callback or WebRTC's playout does; measured after: running through a 3 s pause, the 0.4 s message played at once |
| V3 | **Triggers:** **the wake word, on the Pi** (V9): the real one; Hey Pi's push-to-talk when the app talks to pi-server (4.14); `POST /debug/voice/listen` (curl). **A turn ends** on 700 ms of silence (speech-service's server turn detection) or a commit (push-to-talk released) | Hands-free, which is the point of a panel; the same session for every trigger |
| V4 | **A voice session is one agent session, in a new conversation** (revised 2026-11-17): claimed at the wake word (its pod with it: agent-service C6), so it's ready by the end of the sentence; **released** (`DELETE`) when the voice session ends (V9). A session that ended mid-way (its limits) is replaced in the same conversation. Continuing an earlier conversation: later | A pod is held only while you're talking to it, not for agent-service's 5 idle minutes. A conversation is agent-service's history (PostgreSQL), apart from its sessions (pods): a new session can continue one (C9), so continuity can come later without changing this |
| V5 | **The reply streams end to end:** the agent's text deltas go into one TTS session as they arrive (speech-service cuts them into clauses: first audio after the first clause, not the whole reply); its audio goes to aplay as it arrives | You hear the first words while the agent still writes. Measured per turn: heard → agent's first text → first audio |
| V6 | **Half duplex, with barge-in by the wake word** (4.13, 2026-11-23): the mic isn't sent while the reply plays, but the wake word listens; heard over the reply, the reply stops at once (the speaker, the speech) and pi-server sends agent-service a `cancel`, which interrupts the agent's turn if it's still writing and does nothing if it's complete; the session then waits for what the user says next, as after the wake word. History isn't touched: a reply complete but cut off by the user stays whole in agent-service and in Codex (what was heard isn't reported back; Codex keeps nothing of a reply interrupted while being written). Each reply logs the wake word's highest score while the Pi spoke, to measure the speakerphone's echo cancellation | The speaker would hear itself, so other speech can't interrupt; the wake word is specific enough to hear over it (as smart speakers: "Alexa" over Alexa). Reporting what was heard (OpenAI Realtime's `conversation.item.truncate`) isn't worth its complexity yet: a reply the agent finished is still what it said |
| V7 | **Hey Pi's protocol unchanged:** pi-server sends the `assistant` messages already specified (phase `listening` → `thinking` with the `transcript` → `speaking` with each `reply_delta` → `idle` when the speaker is done) | The app needs no protocol change when it starts listening (4.14) |
| V8 | **Failures end the turn, never pi-server:** a hop refused or down → phase `idle`, logged with which hop; the next trigger starts clean. No retry inside a turn, except the session replacement (V4) | A late answer is worse than asking again (speech-service's admission rule) |
| V9 | **The wake word and the voice session** (2026-11-17): the mic is always on and stays on the Pi: microWakeWord (`pymicro-wakeword`, Home Assistant's satellites' engine; Apache-2.0) listens for the word, and a voice detector (`pymicro-vad`) is fed every frame. **The word → a session:** the screen wakes, Hey Pi shows `listening`, the agent session is claimed (V4). **Waiting:** the first voice within **7 s** starts a turn, with the 500 ms before it (its first syllable); a voice is **300 ms of speech in a row** (not a click or a fan's burst). **A turn listens at most 15 s:** at the cap the session's first turn (the wake word's, or push-to-talk) is sent as heard, and a follow-up is dropped and ends the session (2026-11-18: a fan kept the voice detector on, follow-ups ran 29 and 52 s and the session never went idle; requests are short, as smart speakers' utterance caps assume); a turn's audio goes to speech-service only then. **After each reply, 7 s more** for a follow-up, no wake word needed. **7 s and nobody:** the session ends, the agent session is released, Hey Pi `idle`. The word: **小派小派**, our own microWakeWord model (since 2026-11-19; "Hey Pi" 2026-11-18, `hey_jarvis` built in before): the model, its training and its tests are [wake-word.md](wake-word.md) | Audio leaves the Pi only when someone is talking to it. A follow-up window is how smart speakers do conversation; 7 s (2026-11-18; 5, then 10 before) leaves room to think of a follow-up, and is still short enough that a TV doesn't keep it open for long. microWakeWord: small (made for an ESP32), maintained (2026), the Pi's wheels; openWakeWord's last release is from 2024 |

## Build plan
| # | What | Done when |
|---|---|---|
| 4.8 ✅ | **this design** | reviewed and agreed (2026-11-17) |
| 4.9 ✅ | **pi-server's identity:** grants in auth-service's policy (`speech-service`: `speech:create`, `speech:transcribe`; `agent-service`: `chat:read`, `chat:write`); its secret rotated onto the Pi (`scripts/add-client.sh pi-server pi /etc/pi-server/credentials/client_secret liustev6 --rotate`, then `promote-secret`) | on the Pi, a token for both audiences. **Done 2026-11-17:** the secret rotated onto the Pi and promoted; tokens for `speech-service` (`speech:create speech:transcribe`) and `agent-service` (`chat:read chat:write`) |
| 4.10 ✅ | **the Pi's audio:** the speaker's level; `app/voice/audio.py` (the mic and the speaker, V2); a check on the Pi: speech-service speaks a sentence out of the speaker, the mic hears it, speech-to-text reads it back | the sentence comes back right; the speaker's latency known. **Done 2026-11-17:** the mic's own format is 16 kHz mono (no conversion); the speaker takes 48 kHz stereo (plughw converts our 24 kHz); the speaker at 60%; aplay adds ~120 ms at its start. **The speakerphone cleans the mic itself:** what isn't speech comes out as digital zero, and its own playback was removed entirely (echo cancellation: good for barge-in, 4.13); a voice is −30 dB, heard right |
| 4.11 ✅ | **the voice turn in pi-server** (V1, V3–V8): `app/voice/` (the agent client, the speech sessions, the turn), `POST /debug/voice/listen`; the mock assistant only when `MOCK`; tests with fake services; deployed with `pi-server.sh` (with `service-auth`) | on the Pi: curl, ask, hear the answer; timings in the log. **Done 2026-11-18:** spoken turns end to end on the Pi, English and Chinese, in its own profile `pi` (luna, effort low); the session's voice set by its first turn (ryan, or dylan for Chinese), speech-to-text given the language hint and the last two turns. **First audio after you stop speaking** (9 turns, the 1.7B TTS streaming its generation, the first clause off): median 1.9 s, 1.4–2.0 s for short questions, 2.9 s once; 5.9 and 6.6 s when the agent took longer to start (two questions about Nvidia's earnings: no web search yet). The agent's first text is most of it (1.1–1.9 s); speech then adds 0.1–0.6 s |
| 4.12 ✅ | **the wake word and the voice session** (V9, V4): microWakeWord and a voice detector on the Pi, the session's follow-up windows (7 s), the agent session claimed and released with it | say the word, ask, hear the answer, ask again without it; 10 s later the pod is released; CPU and temperature measured. **Done 2026-11-18:** `app/voice/wake.py`, `assistant.py` (the session as a state machine; tests with fakes and a fake clock); follow-ups without the word, the agent session released (`DELETE`) after 10 s of nobody; a follow-up window of 7 s (5, then 10, then 7 on 2026-11-18). **On the Pi:** pi-server 3.7% of one core while listening (25 min), the Pi at 61 °C. The word: `hey_jarvis` at first, "Hey Pi" since 2026-11-18 (4.12b) |
| 4.12b ✅ | **the wake word itself** ([wake-word.md](wake-word.md)): our own microWakeWord model, trained on the playground | the word fires for you and not for the room. **Done 2026-11-19: 小派小派** on the Pi, model 04f at 0.96 (04b at 0.91 first: it woke on the owner's casual talk): in the common test 81% of the owner's held-out takes, none of the owner's ordinary lines, no false wakes in 2.9 h of test-only audio; "Hey Pi" never got under 1.7 an hour |
| 4.13 ✅ | **barge-in:** the wake word heard while the reply plays (the speakerphone's echo cancellation) cancels the agent's turn and stops the speaker | interrupting works without the reply triggering itself. **Done 2026-11-25** (V6; `assistant.py` `_barge_in`, 2 tests): on the Pi, 3 barge-ins out of 3 over two long stories (scores 0.99, 1.00, 0.99; the agent's turn cancelled while it was still writing; the next request answered in the same session); ~2.5 min of the Pi's own speech never woke it, and a reply played to its end peaked at 0.06. A stopped speaker no longer logs asyncio's "Future exception was never retrieved" (`audio.py` `Speaker.stop` awaits aplay's stdin closing). "停" right after a barge-in is heard as a request ("听"): the user can just stay quiet, and the session ends after 7 s. Ending a conversation (by the user or the agent) waits for the tools checkpoint (CP5: an `end_conversation` tool) |
| 4.14 | **Hey Pi shows it** (Flutter): connects to pi-server, the assistant overlay's phases, push-to-talk | later; the app's own work |
| 4.15 | **protocol B in shared-schemas; the agent's latency levers** | defined when reached |

## Found along the way
- **The TTS misreads digits** (2026-11-17; **fixed** by text normalization in speech-service, its T14 and spike 13: 56% → 90% read right): "5,959 metres, or 19,551 feet", spoken 4 times, came back 4
  ways ("nineteen point one five hundred…", "fifty desimples") and took 11–14 s for ~5 s of words.
  The standard fix is text normalization before the TTS (numbers, units, dates as words: Riva's TTS
  pipeline does it); a "say numbers in words" line in the `voice` profile as a cheap second guard.
- **The agent dominates the wait:** heard → its first text ~2 s, against ~0.4 s for everything else
  (the test client's e2e, 2026-11-17); on the Pi, 4.3 and 11.2 s (Codex's default model, `gpt-6.1-sol`).
  **Hey Pi's own profile, `pi`** (2026-11-18): `gpt-6-luna` at effort `low`, the fastest of the plan's
  models for short voice turns (agent-service's first-word time, 18 turns each: luna 1.15 s median, 90%
  under 2.3 s; `gpt-6.1-terra` 1.38, `gpt-6-sol` 1.48, `gpt-6-astra` 2.73, `gpt-6.1-sol` 4.26). It told
  the truth about having no tools (`gpt-5.6-luna` claimed to set a reminder). Caveats: one evening, simple
  questions only. Where the panel is (Seattle, Pacific time: the first turn answered by UTC) was in the
  profile; it kept saying "Seattle", so since 2026-11-18 pi-server sends it as its own instructions
  (agent-service C15), an "Additional Client Info" block built at each session start (`timezone`,
  `City, Country`; from `PI_SERVER_TIMEZONE` and `PI_SERVER_LOCATION`), and the profile's rules say how
  to use it: for place and time questions only, never naming the city. **The time goes with each turn**
  (since 2026-11-26): a `[now: 2026-11-26 10:31 (Wednesday, UTC-0800)]` line before the turn's text,
  not in the instructions, which start every model call's prompt: a time there changed every session's
  prompt from that point on, so the prompt cache could share less of it (tools.md). **Web search on** (`pi`'s `tools: [web_search]`, Codex's own, run at OpenAI: the sandbox
  still reaches only the gateway): a weather question searched and answered in ~9 s on the Mac.
