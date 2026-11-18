"""What the API talks to (speech-service.md T13): the two work threads, the two admissions, what each
model offers, and the numbers for /v1/status. Built by main.py with the real engines, by the tests
with fakes."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from app.config import Settings
from app.work.admission import Admission


@dataclass
class Service:
    tts: object                                   # a TTSWorker
    stt: object                                   # an STTWorker
    voices: dict[str, str]                        # name → its native language
    languages: list[str]                          # what the STT recognizes
    settings: Settings
    models: dict[str, str] = field(default_factory=dict)        # for /v1/status: kind → model and revision
    gpu: object = None                            # () → {"name", "vram_used_gb", "vram_total_gb"}, or None
    warm: bool = False
    started_at: float = field(default_factory=time.time)

    def __post_init__(self):
        s = self.settings
        self.tts_admission = Admission(s.TTS_SLOTS, s.WAITING)
        self.stt_admission = Admission(s.STT_SLOTS, s.WAITING)
        self.first_audio_ms: deque[float] = deque(maxlen=20)     # the TTS: a reply's first audio
        self.final_ms: deque[float] = deque(maxlen=20)           # the STT: end of speech → final
        self.sessions = {"tts": 0, "stt": 0}                     # opened since start

    def status(self) -> dict:
        def recent(values):
            v = sorted(values)
            return {"last": list(values)[-5:], "median": v[len(v) // 2] if v else None}
        return {"ok": True, "warm": self.warm, "models": self.models,
                "gpu": self.gpu() if self.gpu else None,
                "tts": {**self.tts_admission.counts(), "sessions": self.sessions["tts"],
                        "sentences": getattr(self.tts, "sentences_done", 0), "first_audio_ms": recent(self.first_audio_ms)},
                "stt": {**self.stt_admission.counts(), "sessions": self.sessions["stt"],
                        "finals": getattr(self.stt, "finals", 0), "final_ms": recent(self.final_ms)},
                "uptime_seconds": round(time.time() - self.started_at)}
