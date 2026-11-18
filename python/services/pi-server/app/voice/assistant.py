"""The voice assistant: always listening on the Pi for the wake word, then a voice session of turns
(voice-path.md V3-V9):

    idle ──wake word──▶ waiting ──someone speaks──▶ listening ──the turn ends──▶ replying ──played──┐
     ▲   (claim the       │ ▲   (open an STT       (speech to text)       (agent → speech →      │
     │    agent session)  │ └─────────────────────────────────────────────────── speaker) ◀────────┘
     └──10 s and nobody speaks: release the agent session──┘         back to waiting, for 10 s more

  - idle: only the wake word listens, on the Pi; nothing leaves it
  - waiting: the session is open (its agent session claimed at the wake word); the first voice
    within `window` seconds starts a turn, with the half second before it (so its first syllable
    isn't lost); none: the session ends and its agent session is released
  - listening: the mic's frames go to speech-to-text until the turn ends (silence, or commit)
  - replying: the agent's text, as it's written, into speech, to the speaker; the mic is ignored
    (half duplex, V6), then waiting again
Hey Pi sees `listening` for the whole session between turns, `thinking` and `speaking` in a turn,
`idle` when it ends (V7), and the screen wakes at the wake word. Push-to-talk and the debug endpoints
use the same session (`listen`, `commit`, `say`, `cancel`). A hop that fails ends that turn, logged;
the session goes on (V8).
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import enum
import itertools
import logging
import re
import time
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Optional, Protocol

from app.voice.agent import AgentClient
from app.voice.audio import AudioError, Mic, Speaker
from app.voice.spoken import SpokenText
from app.voice.speech import SpeechClient

logger = logging.getLogger(__name__)

PREROLL_FRAMES = 5                    # 500 ms kept before a voice starts a turn
CONTEXT_TURNS = 2                     # the last turns given to speech-to-text as context
CONTEXT_TURN_CHARS = 300              # each side of a turn, at most
HOLDOFF_SECONDS = 0.3                 # after the wake word and after a reply: not yet a new voice


class UI(Protocol):
    """What Hey Pi is told (its `assistant` and `display_state` messages, V7)."""

    async def phase(self, turn_id: str, phase: str, transcript: Optional[str] = None) -> None: ...

    async def reply_delta(self, turn_id: str, text: str) -> None: ...

    async def awake(self) -> None: ...


CHINESE = re.compile(r"[\u4e00-\u9fff]")
ENGLISH_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


def main_language(text: str, heard: str = "") -> str:
    """The language most of `text` is in: Chinese characters against English words (a character and a
    word are each about one spoken unit). A tie goes to what speech-to-text heard, else English."""
    zh, en = len(CHINESE.findall(text)), len(ENGLISH_WORD.findall(text))
    if zh != en:
        return "Chinese" if zh > en else "English"
    return "Chinese" if heard.lower().startswith("chinese") else "English"


@dataclass
class VoiceOptions:
    voice_en: str = "ryan"             # English replies
    voice_zh: str = "dylan"            # Chinese replies (spikes 14-15: steadiest on Chinese, natural pace)
    tts_language: Optional[str] = None
    stt_language: Optional[str] = None
    stt_context: str = ""              # who speaks to Hey Pi (their languages): every context starts with it
    silence_ms: int = 700
    window: float = 10.0               # the session waits this long for someone to speak

    def voice_for(self, text: str, heard: str = "") -> str:
        """The voice for a turn's main language (mixed Chinese and English: whichever most of it is).
        The session's first turn decides it for the whole session (VoiceAssistant._voice)."""
        return self.voice_zh if main_language(text, heard) == "Chinese" else self.voice_en


class State(enum.Enum):
    IDLE = "idle"
    WAITING = "waiting"
    LISTENING = "listening"
    REPLYING = "replying"


class VoiceAssistant:
    def __init__(self, speech: SpeechClient, agent: AgentClient, mic: Mic, speaker: Callable[[], Speaker],
                 ui: UI, options: VoiceOptions, wake, voice, clock: Callable[[], float] = time.monotonic):
        self.speech, self.agent, self.mic, self.new_speaker = speech, agent, mic, speaker
        self.ui, self.options, self.wake, self.voice, self.clock = ui, options, wake, voice, clock
        self.state = State.IDLE
        self._turn_ids = itertools.count(1)
        self.turn_id: Optional[str] = None
        self._deadline = 0.0                 # waiting: when the session ends if nobody speaks
        self._holdoff = 0.0                  # waiting: voices before this don't start a turn
        self._preroll: collections.deque[bytes] = collections.deque(maxlen=PREROLL_FRAMES)
        self._frames: Optional[asyncio.Queue] = None
        self._commit = asyncio.Event()
        self._claim: Optional[asyncio.Task] = None
        self._turn: Optional[asyncio.Task] = None
        self._ending: Optional[asyncio.Task] = None
        self._recent: collections.deque[tuple[str, str]] = collections.deque(maxlen=CONTEXT_TURNS)
        self._session_voice: Optional[str] = None

    # ---- the mic loop ------------------------------------------------------------------------------
    async def run(self) -> None:
        """The always-on loop (from pi-server's start): every frame from the mic, for good."""
        while True:
            try:
                async for frame in self.mic.frames():
                    await self.on_frame(frame)
            except AudioError as e:
                logger.error("%s; the mic restarts in 2 s", e)
                await asyncio.sleep(2)

    async def on_frame(self, frame: bytes) -> None:
        speaking = self.voice.feed(frame)            # always fed: warm when it's needed
        self._preroll.append(frame)
        if self.state is State.IDLE:
            if self.wake.feed(frame):
                logger.info("wake word heard")
                self.wake.reset()
                await self._open()
        elif self.state is State.WAITING:
            now = self.clock()
            if speaking and now >= self._holdoff:
                self._listen(list(self._preroll))
            elif now >= self._deadline:
                self._ending = self._end("idle")        # released in the background: the mic reads on
        elif self.state is State.LISTENING and self._frames is not None:
            self._frames.put_nowait(frame)
        # REPLYING: the mic is ignored (half duplex)

    # ---- the session -------------------------------------------------------------------------------
    async def _open(self) -> None:
        """A session: its agent session claimed now, while the user starts talking."""
        self._claim = asyncio.create_task(self.agent.start())
        await self.ui.awake()
        await self._wait(holdoff=HOLDOFF_SECONDS)

    async def _wait(self, holdoff: float) -> None:
        self.state = State.WAITING
        now = self.clock()
        self._deadline, self._holdoff = now + self.options.window, now + holdoff
        self._preroll.clear()
        self.turn_id = f"v{next(self._turn_ids)}"
        await self.ui.phase(self.turn_id, "listening")

    async def close(self, reason: str) -> None:
        """End the session: any turn stopped, the agent session released, Hey Pi idle."""
        if self.state is not State.IDLE:
            await self._end(reason)

    def _end(self, reason: str) -> asyncio.Task:
        """Idle now (the next frame already listens for the wake word); the rest in a task."""
        logger.info("voice session ends (%s)", reason)
        self.state = State.IDLE
        turn, claim, turn_id = self._turn, self._claim, self.turn_id or "v0"
        self._turn = self._claim = None
        self.wake.reset()
        self._recent.clear()                         # a new session is a new conversation (V4)
        self._session_voice = None                   # ... and picks its voice again

        async def release() -> None:
            if turn is not None and not turn.done():
                turn.cancel()
                await asyncio.wait([turn])
            if claim is not None and not claim.done():
                await asyncio.wait([claim])
            await self.agent.end()
            await self.ui.phase(turn_id, "idle")

        return asyncio.create_task(release())

    # ---- a turn ------------------------------------------------------------------------------------
    def _listen(self, preroll: list[bytes]) -> None:
        self.state = State.LISTENING
        self._commit = asyncio.Event()
        self._frames = asyncio.Queue()
        for frame in preroll:
            self._frames.put_nowait(frame)
        self._turn = asyncio.create_task(self._spoken_turn(self.turn_id, self._frames))

    async def _mic_frames(self, frames: asyncio.Queue) -> AsyncIterator[bytes]:
        while True:
            yield await frames.get()

    async def _spoken_turn(self, turn_id: str, frames: asyncio.Queue) -> None:
        started = self.clock()
        try:
            heard = await self.speech.transcribe(
                self._mic_frames(frames), commit=self._commit, silence_ms=self.options.silence_ms,
                language=self.options.stt_language, context=self.context(),
                on_partial=lambda text: self.ui.phase(turn_id, "listening", transcript=text))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("turn %s: listening failed: %s: %s", turn_id, type(e).__name__, e)
            await self._wait(holdoff=0)
            return
        finally:
            self._frames = None
        heard_at = self.clock()
        logger.info("turn %s: heard %r (%s, %s, %.1f s)", turn_id, heard.text, heard.reason,
                    heard.language or "?", heard_at - started)
        if not heard.text.strip():
            await self._wait(holdoff=0)
            return
        await self._replying(turn_id, heard.text, heard_at, self._voice(heard.text, heard.language))

    async def _replying(self, turn_id: str, text: str, heard_at: float, voice: str) -> None:
        self.state = State.REPLYING
        try:
            if self._claim is not None:                  # the session claimed at the wake word: ready by now
                await asyncio.wait([self._claim])
                if not self._claim.cancelled() and self._claim.exception():
                    logger.warning("claiming the agent session: %s (the turn tries again)", self._claim.exception())
                self._claim = None
            await self._reply(turn_id, text, heard_at, voice)
        except asyncio.CancelledError:
            raise
        except Exception as e:                           # V8: this turn ends, the session goes on
            logger.error("turn %s failed: %s: %s", turn_id, type(e).__name__, e)
        await self._wait(holdoff=HOLDOFF_SECONDS)

    def _voice(self, text: str, heard: str = "") -> str:
        """The session's voice: decided by its first turn's main language (Chinese: dylan, English:
        ryan), then kept for every turn of the session, whatever language a later turn is in."""
        if self._session_voice is None:
            self._session_voice = self.options.voice_for(text, heard)
            logger.info("voice session speaks with %s", self._session_voice)
        return self._session_voice

    def context(self) -> str:
        """For speech-to-text: this session's last turns, so a name or a place said again is heard as
        itself ("Seattle downtown", not 死丫头当场), and the language follows the conversation."""
        lines = [self.options.stt_context] if self.options.stt_context else []
        if self._recent:
            lines.append("Recent conversation:")
        for said, replied in self._recent:
            lines += [f"User: {said[:CONTEXT_TURN_CHARS]}", f"Assistant: {replied[:CONTEXT_TURN_CHARS]}"]
        return "\n".join(lines)

    # ---- push-to-talk, typed turns, cancel (Hey Pi, the debug endpoints) ---------------------------
    async def listen(self) -> None:
        """Start a turn now, without the wake word (push-to-talk): a session if there's none."""
        if self.state is State.IDLE:
            await self._open()
        elif self.state in (State.LISTENING, State.REPLYING):
            await self._stop_turn()
            await self._wait(holdoff=0)
        self._listen([])

    def commit(self) -> None:
        """The user is done talking (push-to-talk released): don't wait for the silence."""
        self._commit.set()

    async def say(self, text: str) -> None:
        """A typed turn: the reply spoken, in the session (one is opened if there's none)."""
        if self.state is State.IDLE:
            await self._open()
        else:
            await self._stop_turn()
        self.state = State.REPLYING                      # now, so no voice starts a turn meanwhile
        self._turn = asyncio.create_task(self._replying(self.turn_id, text, self.clock(), self._voice(text)))

    async def cancel(self) -> None:
        await self.close("cancelled")

    async def _stop_turn(self) -> None:
        turn, self._turn = self._turn, None
        if turn is not None and not turn.done():
            turn.cancel()
            await asyncio.wait([turn])

    # ---- the reply ---------------------------------------------------------------------------------
    async def _reply(self, turn_id: str, text: str, heard_at: float, voice: str) -> None:
        """The agent's reply, spoken while it's written."""
        timings: dict[str, int] = {}

        def mark(name: str) -> None:
            timings.setdefault(name, round((self.clock() - heard_at) * 1000))

        await self.ui.phase(turn_id, "thinking", transcript=text)
        texts: asyncio.Queue[Optional[str]] = asyncio.Queue()
        reply: list[str] = []
        speaker = self.new_speaker()
        await speaker.open()                                           # aplay's start, hidden in the thinking

        async def text_in() -> AsyncIterator[str]:
            while (piece := await texts.get()) is not None:
                yield piece

        # The speech is read as fast as it comes and queued for the speaker, which plays it in real
        # time on its own: the TTS session ends (and frees its slot) when the audio is made, not played.
        audio: asyncio.Queue[Optional[bytes]] = asyncio.Queue()

        async def received(pcm: bytes) -> None:
            if "first_audio" not in timings:
                mark("first_audio")
                await self.ui.phase(turn_id, "speaking")
            audio.put_nowait(pcm)

        async def play() -> None:
            while (pcm := await audio.get()) is not None:
                await speaker.write(pcm)

        speaking = asyncio.create_task(self.speech.speak(
            text_in(), voice=voice, language=self.options.tts_language, on_audio=received))
        player = asyncio.create_task(play())
        agent_done = False

        spoken = SpokenText()

        async def write() -> None:
            """The agent's turn, its text into the speech as it comes."""
            nonlocal agent_done
            async with contextlib.aclosing(self.agent.turn(text)) as events:
                async for event in events:
                    kind = event.get("type")
                    if kind == "message.delta" and event.get("text"):
                        mark("first_text")
                        said = spoken.feed(event["text"])             # no citation markers, no markdown
                        if said:
                            reply.append(said)
                            texts.put_nowait(said)
                            await self.ui.reply_delta(turn_id, said)
                    elif kind == "message.completed":
                        spoken.end()
                        texts.put_nowait(" ")                          # a gap between text blocks
                    elif kind == "turn.completed":
                        agent_done = True
                        mark("agent_done")
                        if event.get("status") != "completed":
                            logger.warning("turn %s: the agent's turn %s (%s)", turn_id, event.get("status"), event.get("reason"))
                    elif kind == "error":
                        logger.warning("turn %s: the agent: %s", turn_id, event)
            texts.put_nowait(None)

        writing = asyncio.create_task(write())
        try:
            # Side by side; the first to fail ends the turn (the speech refused: don't wait for the agent).
            await asyncio.wait([writing, speaking], return_when=asyncio.FIRST_EXCEPTION)
            for task in (speaking, writing):
                if task.done() and not task.cancelled() and task.exception():
                    raise task.exception()
            await writing
            await speaking
            mark("audio_done")                                         # all of it made (the session closed)
            audio.put_nowait(None)
            await player
            await speaker.drain()
            mark("played")
            self._recent.append((text, "".join(reply).strip()))
            logger.info("turn %s (%s): timings (ms from heard) %s", turn_id, voice, timings)
        except BaseException:
            writing.cancel()
            speaking.cancel()
            player.cancel()
            await asyncio.wait([writing, speaking, player])
            await speaker.stop()
            if not agent_done:                                         # don't leave it writing
                await self.agent.cancel()
            raise
