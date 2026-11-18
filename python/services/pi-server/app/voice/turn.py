"""One voice turn, end to end (voice-path.md V3-V8):

    listening ──the turn ends (silence, or commit)──▶ thinking ──first audio──▶ speaking ──played──▶ idle

  - listening: the mic's frames go to speech-to-text; the agent's session is claimed meanwhile (V4)
  - thinking: the transcript goes to the agent; its text, as it's written, into one TTS session (V5)
  - speaking: the audio plays as it comes; idle once the speaker has played it all
A typed turn (`reply`) starts at thinking. Half duplex (V6): the mic is closed from thinking on.
One turn at a time: a new one cancels the last. A hop that fails ends the turn, logged (V8).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable, Optional, Protocol

from app.voice.agent import AgentClient
from app.voice.audio import Mic, Speaker
from app.voice.speech import SpeechClient

logger = logging.getLogger(__name__)


class Phases(Protocol):
    """What Hey Pi is told (its `assistant` messages, V7)."""

    async def phase(self, turn_id: str, phase: str, transcript: Optional[str] = None) -> None: ...

    async def reply_delta(self, turn_id: str, text: str) -> None: ...


@dataclass
class VoiceOptions:
    voice: str = "ryan"
    tts_language: Optional[str] = None
    stt_language: Optional[str] = None
    silence_ms: int = 700


class VoiceAssistant:
    def __init__(self, speech: SpeechClient, agent: AgentClient, mic: Mic,
                 speaker: Callable[[], Speaker], ui: Phases, options: VoiceOptions,
                 clock: Callable[[], float] = time.monotonic):
        self.speech, self.agent, self.mic, self.new_speaker = speech, agent, mic, speaker
        self.ui, self.options, self.clock = ui, options, clock
        self._task: Optional[asyncio.Task] = None
        self._commit = asyncio.Event()

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    async def listen(self, turn_id: str) -> None:
        """Start a spoken turn: listening now."""
        await self._start(self._listen_then_reply(turn_id))

    async def reply(self, turn_id: str, text: str) -> None:
        """Start a typed turn: the text goes straight to the agent."""
        await self._start(self._turn(turn_id, lambda: self._reply(turn_id, text, self.clock())))

    def commit(self) -> None:
        """The user is done talking (push-to-talk released): don't wait for the silence."""
        self._commit.set()

    async def cancel(self) -> None:
        """Stop the turn wherever it is: the listening, the agent's turn, the speech. Hey Pi goes idle."""
        if self.busy:
            self._task.cancel()
            await asyncio.wait([self._task])
        self._task = None

    async def _start(self, coro) -> None:
        await self.cancel()
        self._commit = asyncio.Event()
        self._task = asyncio.create_task(coro)

    async def _turn(self, turn_id: str, body: Callable[[], Awaitable[None]]) -> None:
        try:
            await body()
        except asyncio.CancelledError:
            logger.info("turn %s: cancelled", turn_id)
            raise
        except Exception as e:                                         # V8: the turn ends, pi-server doesn't
            logger.error("turn %s failed: %s: %s", turn_id, type(e).__name__, e)
        finally:
            await self.ui.phase(turn_id, "idle")

    async def _listen_then_reply(self, turn_id: str) -> None:
        async def body() -> None:
            await self.ui.phase(turn_id, "listening")
            claim = asyncio.create_task(self.agent.prepare())         # ready by the end of the sentence (V4)
            started = self.clock()
            frames = self.mic.frames()
            try:
                heard = await self.speech.transcribe(
                    frames, commit=self._commit, silence_ms=self.options.silence_ms,
                    language=self.options.stt_language,
                    on_partial=lambda text: self.ui.phase(turn_id, "listening", transcript=text))
            finally:
                await frames.aclose()                                 # the mic closes: half duplex (V6)
                if not claim.done():
                    await asyncio.wait([claim])
                if not claim.cancelled() and claim.exception():       # the turn will try again
                    logger.warning("claiming the agent's session: %s", claim.exception())
            heard_at = self.clock()
            logger.info("turn %s: heard %r (%s, %s, %.1f s listening)", turn_id, heard.text,
                        heard.reason, heard.language or "?", heard_at - started)
            if heard.text.strip():
                await self._reply(turn_id, heard.text, heard_at)

        await self._turn(turn_id, body)

    async def _reply(self, turn_id: str, text: str, heard_at: float) -> None:
        """The agent's reply, spoken while it's written."""
        timings: dict[str, int] = {}

        def mark(name: str) -> None:
            timings.setdefault(name, round((self.clock() - heard_at) * 1000))

        await self.ui.phase(turn_id, "thinking", transcript=text)
        texts: asyncio.Queue[Optional[str]] = asyncio.Queue()
        speaker = self.new_speaker()
        await speaker.open()                                           # aplay's start, hidden in the thinking

        async def text_in() -> AsyncIterator[str]:
            while (piece := await texts.get()) is not None:
                yield piece

        async def play(pcm: bytes) -> None:
            if "first_audio" not in timings:
                mark("first_audio")
                await self.ui.phase(turn_id, "speaking")
            await speaker.write(pcm)

        speaking = asyncio.create_task(self.speech.speak(
            text_in(), voice=self.options.voice, language=self.options.tts_language, on_audio=play))
        agent_done = False

        async def write() -> None:
            """The agent's turn, its text into the speech as it comes."""
            nonlocal agent_done
            async with contextlib.aclosing(self.agent.turn(text)) as events:
                async for event in events:
                    kind = event.get("type")
                    if kind == "message.delta" and event.get("text"):
                        mark("first_text")
                        texts.put_nowait(event["text"])
                        await self.ui.reply_delta(turn_id, event["text"])
                    elif kind == "message.completed":
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
            mark("audio_done")
            await speaker.drain()
            mark("played")
            logger.info("turn %s: timings (ms from heard) %s", turn_id, timings)
        except BaseException:
            writing.cancel()
            speaking.cancel()
            await asyncio.wait([writing, speaking])
            await speaker.stop()
            if not agent_done:                                         # don't leave it writing
                await self.agent.cancel()
            raise
