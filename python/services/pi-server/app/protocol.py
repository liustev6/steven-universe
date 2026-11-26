"""
The hey-pi <-> pi-server protocol (docs/specs/flutter_ui.md "Protocol"): JSON over WebSocket, one
object per message, tagged by "type". Mirrors hey-pi's lib/protocol/messages.dart: keep both in sync.

pi-server -> hey-pi, on connect and on every change:

    {"type": "speech", "state": "idle" | "listening" | "speaking"}

    idle       no voice session
    listening  the session waits for you, hears you, or works out its reply
    speaking   its voice: from the reply's first sound until it has played

hey-pi -> pi-server, its voice button:

    {"type": "wake"}

    what hearing the wake word does: a voice session if there's none, a barge-in over a reply

What hey-pi shows when speech is idle (the dashboard, or idle after no touch) is its own state,
never sent.
"""

import json
from typing import Any, Literal, Optional

from pydantic import BaseModel, TypeAdapter, ValidationError

SpeechState = Literal["idle", "listening", "speaking"]


class Speech(BaseModel):
    type: Literal["speech"] = "speech"
    state: SpeechState


class Wake(BaseModel):
    """hey-pi's voice button: as if the wake word were heard."""
    type: Literal["wake"] = "wake"


ScreenMessage = Wake                    # more: Annotated[Union[...], Field(discriminator="type")]
_screen_message: TypeAdapter[ScreenMessage] = TypeAdapter(ScreenMessage)


def decode(raw: str) -> Optional[ScreenMessage]:
    """A message from a screen, or None if it isn't one (never raises)."""
    try:
        return _screen_message.validate_python(json.loads(raw))
    except (ValueError, ValidationError):
        return None


def encode(message: BaseModel) -> dict[str, Any]:
    """A message as a JSON-ready dict."""
    return message.model_dump(mode="json")
