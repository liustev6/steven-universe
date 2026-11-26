"""
Debug endpoints:

    curl -X POST localhost:8765/debug/speech/listening    # tell the screens a speech state (no voice needed)
    curl -X POST "localhost:8765/debug/say?text=明天天气怎么样"   # a typed turn, spoken (voice on)
    curl -X POST localhost:8765/debug/voice/listen        # a spoken turn: listening now (voice on)
    curl -X POST localhost:8765/debug/voice/commit        # done talking (else the silence ends it)
    curl -X POST localhost:8765/debug/voice/cancel        # end the voice session

Only mounted when PI_SERVER_DEBUG_ENDPOINTS is true.
"""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request

from app.protocol import SpeechState

router = APIRouter(prefix="/debug", tags=["debug"])


def _voice(request: Request):
    voice = request.app.state.pi.voice
    if voice is None:
        raise HTTPException(409, "the voice assistant is off (PI_SERVER_VOICE)")
    return voice


@router.post("/speech/{state}")
async def push_speech(state: SpeechState, request: Request):
    """For the screen alone: the voice assistant's next change overrides it."""
    await request.app.state.pi.set_speech(state)
    return {"sent": {"type": "speech", "state": state}}


@router.post("/say")
async def say(request: Request, text: str = "what's the weather"):
    await _voice(request).say(text)
    return {"said": text}


@router.post("/voice/{action}")
async def voice(action: Literal["listen", "commit", "cancel"], request: Request):
    v = _voice(request)
    if action == "listen":
        await v.listen()
    elif action == "commit":
        v.commit()
    else:
        await v.cancel()
    return {"voice": action}
