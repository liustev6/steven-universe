"""Gateway -> runtime: the commands a long poll returns (GET /v1/runtime/turns).

Strictly typed, unlike events: a runtime can't act on a command it doesn't understand. The gateway
and the runtime deploy together, so a new command type is never sent to a runtime that lacks it.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class TurnMessage(BaseModel):
    """The user said `text`: run it as turn `turn_id`."""
    type: Literal["turn"]
    turn_id: str
    text: str


class InterruptMessage(BaseModel):
    """Stop turn `turn_id`; it ends with turn.completed, status interrupted."""
    type: Literal["interrupt"]
    turn_id: str


class EndMessage(BaseModel):
    """The session is over: stop the agent, flush events, exit."""
    type: Literal["end"]


InboundMessage = Annotated[Union[TurnMessage, InterruptMessage, EndMessage], Field(discriminator="type")]


class InboundBatch(BaseModel):
    messages: list[InboundMessage] = Field(default_factory=list)
