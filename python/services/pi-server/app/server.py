"""
pi-server's core: the voice assistant (when PI_SERVER_VOICE is on), what hey-pi is told (the speech
state) and what it asks (its voice button) (app/protocol.py).

The voice assistant reports its phases (app/voice/assistant.py: idle, listening, thinking, speaking);
here they become the speech state, one for one.
"""

import asyncio
import logging
from typing import Optional

from app.core.config import Settings
from app.hub import Client, Hub
from app.protocol import Speech, SpeechState, Wake, decode
from app.voice.assistant import VoiceAssistant

logger = logging.getLogger(__name__)

SPEECH_OF: dict[str, SpeechState] = {
    "idle": "idle", "listening": "listening", "thinking": "thinking", "speaking": "speaking",
}


class PiServer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.hub = Hub()
        self.speech: SpeechState = "idle"
        self.voice: Optional[VoiceAssistant] = None
        self._tasks: list[asyncio.Task] = []

    # --- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        if self.settings.VOICE:
            from app import voice
            self.voice = voice.create(self.settings, self)
            self._tasks.append(asyncio.create_task(self.voice.run()))     # the mic, always on

    async def stop(self) -> None:
        if self.voice:
            await self.voice.cancel()
            await self.voice.agent.close()
        for task in self._tasks:
            task.cancel()

    # --- hey-pi -------------------------------------------------------------

    async def on_connect(self, client: Client) -> None:
        """A screen connected: the speech state now; then each change."""
        await self.hub.send(client, Speech(state=self.speech))

    async def handle(self, client: Client, raw: str) -> None:
        """A message from a screen; one that isn't in the protocol is dropped (the socket stays)."""
        match decode(raw):
            case Wake():
                if self.voice is None:
                    logger.info("the voice button: the voice assistant is off")
                else:
                    await self.voice.woken("the voice button")
            case None:
                logger.info("dropped a message from a screen: %.80s", raw)

    async def set_speech(self, state: SpeechState) -> None:
        if state != self.speech:
            self.speech = state
            await self.hub.broadcast(Speech(state=state))

    # --- the voice assistant's UI (app/voice/assistant.py) ------------------

    async def phase(self, turn_id: str, phase: str, transcript: Optional[str] = None) -> None:
        await self.set_speech(SPEECH_OF[phase])

    async def reply_delta(self, turn_id: str, text: str) -> None:
        pass                                    # the reply is heard, not shown

    async def awake(self) -> None:
        pass                                    # the wake word: `listening` follows at once
