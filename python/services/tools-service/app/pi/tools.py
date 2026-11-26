"""Hey Pi's own tools, under pi/ (tools.md TL11): granted to pi-server alone, so only its sessions see them."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.registry import Registry, Tool


class EndConversation(BaseModel):
    model_config = ConfigDict(extra="forbid")


def register(registry: Registry) -> None:
    async def end_conversation(args: EndConversation) -> dict:
        # Only acknowledged here: pi-server, which owns the voice session, sees the call in the turn's
        # events and ends the session once the reply has been spoken (tools.md 5.6).
        return {"ending": True}

    registry.add(Tool(
        location="pi/end_conversation", title="End the conversation", arguments=EndConversation,
        run=end_conversation, read_only=False, destructive=False,
        description=("End the voice conversation after this reply, so the panel stops listening for a "
                     "follow-up. Call it when the user says goodbye or is clearly done ('that's all', "
                     "'好了，谢谢'). Say your short goodbye in the reply as usual."),
    ))
