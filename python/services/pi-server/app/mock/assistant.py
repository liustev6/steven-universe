"""
Mock assistant replies until home-hub runs the real assistant (STT -> LLM ->
TTS). A turn goes thinking -> speaking (reply streamed word by word) -> idle.
"""


def reply_for(text: str) -> str:
    """A canned reply that echoes the request."""
    request = text.strip() or "that"
    return (
        f"You asked: {request}. I'm the mock assistant on pi-server, so I can't "
        "do that yet, but the real one will."
    )
