"""
The hey-pi <-> pi-server protocol (docs/specs/flutter_ui.md "Protocol"): JSON over WebSocket, one
object per message, tagged by "type". Mirrors hey-pi's lib/protocol/messages.dart: keep both in sync.

One message, pi-server -> hey-pi, on connect and on every change:

    {"type": "speech", "state": "idle" | "listening" | "speaking"}

    idle       no voice session
    listening  the session waits for you, or hears you
    speaking   its turn: from the end of what you said until its reply has played

hey-pi sends nothing: what it shows when speech is idle (the dashboard, or idle after no touch) is
its own state, never sent.
"""

from typing import Any, Literal

from pydantic import BaseModel

SpeechState = Literal["idle", "listening", "speaking"]


class Speech(BaseModel):
    type: Literal["speech"] = "speech"
    state: SpeechState


def encode(message: BaseModel) -> dict[str, Any]:
    """A message as a JSON-ready dict."""
    return message.model_dump(mode="json")
