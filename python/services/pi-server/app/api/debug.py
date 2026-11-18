"""
Debug endpoints for pushing test messages to connected hey-pi screens:

    curl -X POST localhost:8765/debug/display/sleep
    curl -X POST localhost:8765/debug/lab/offline
    curl -X POST "localhost:8765/debug/assistant?text=turn+on+the+lights"
    curl -X POST localhost:8765/debug/voice/listen        # a spoken turn: listening now
    curl -X POST localhost:8765/debug/voice/commit        # done talking (else the silence ends it)
    curl -X POST localhost:8765/debug/voice/cancel

Only mounted when PI_SERVER_DEBUG_ENDPOINTS is true.
"""

from typing import Literal

from fastapi import APIRouter, Request

router = APIRouter(prefix="/debug", tags=["debug"])


@router.post("/display/{state}")
async def push_display_state(state: Literal["active", "sleep"], request: Request):
    await request.app.state.pi.set_display(state)
    return {"sent": {"type": "display_state", "state": state}}


@router.post("/lab/{status}")
async def push_lab_status(status: Literal["online", "offline"], request: Request):
    await request.app.state.pi.set_lab_online(status == "online")
    return {"sent": {"type": "connection", "lab": status}}


@router.post("/assistant")
async def start_assistant_turn(request: Request, text: str = "what's the weather"):
    await request.app.state.pi.say(text)
    return {"started": text}


@router.post("/voice/{action}")
async def voice(action: Literal["listen", "commit", "cancel"], request: Request):
    """As push-to-talk would (assistant_ptt start/stop, assistant_cancel), until the wake word."""
    pi = request.app.state.pi
    if action == "listen":
        await pi.listen()
    elif action == "commit":
        await pi.commit()
    else:
        await pi._cancel_turn()
    return {"voice": action, "real": pi.voice is not None, "turn_id": pi.turn_id}
