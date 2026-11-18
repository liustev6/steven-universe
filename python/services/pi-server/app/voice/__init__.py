"""The voice turn (docs/specs/voice-path.md): the Pi's mic → speech-service (speech to text) →
agent-service (profile voice) → speech-service (text to speech) → the Pi's speaker.

    audio.py    the mic and the speaker: arecord and aplay on ALSA (V2)
    speech.py   speech-service's sessions: an utterance heard, a reply spoken
    agent.py    agent-service: the conversation, its turns streamed
    turn.py     one turn, end to end, and the phases Hey Pi sees (V3-V8)
"""

import logging

logger = logging.getLogger(__name__)


def create(settings, ui):
    """The voice assistant from pi-server's settings, as client `pi-server` (its secret on the Pi)."""
    from service_auth import TokenClient

    from app.voice.agent import AgentClient
    from app.voice.audio import Mic, Speaker
    from app.voice.speech import SpeechClient
    from app.voice.turn import VoiceAssistant, VoiceOptions

    tokens = TokenClient(settings.AUTH_ISSUER, settings.CLIENT_ID, settings.CLIENT_SECRET_FILE.read_text().strip())
    speech = SpeechClient(settings.SPEECH_URL, lambda: tokens.token("speech-service"))
    agent = AgentClient(settings.AGENT_URL, lambda: tokens.token("agent-service"), settings.AGENT_PROFILE,
                        settings.CONVERSATION_IDLE_SECONDS)
    options = VoiceOptions(voice=settings.VOICE_NAME, tts_language=settings.TTS_LANGUAGE,
                           stt_language=settings.STT_LANGUAGE, silence_ms=settings.SILENCE_MS)
    logger.info("voice: speech %s, agent %s (profile %s), mic %s, speaker %s", settings.SPEECH_URL,
                settings.AGENT_URL, settings.AGENT_PROFILE, settings.MIC_DEVICE, settings.SPEAKER_DEVICE)
    return VoiceAssistant(speech, agent, Mic(settings.MIC_DEVICE), lambda: Speaker(settings.SPEAKER_DEVICE),
                          ui, options)
