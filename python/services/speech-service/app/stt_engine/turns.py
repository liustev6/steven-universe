"""Server-side turn detection (speech-service.md, the STT session; OpenAI Realtime's `server_vad`,
simplified): the server notices the speaker stopped, so a client that streams its microphone without
its own voice detection doesn't have to send `commit`.

    speech    a 20 ms window louder than `threshold_db` (dBFS: 0 is full scale; a quiet room's
              microphone noise is ~-60 to -50, speech ~-30 to -15)
    a turn    starts once speech has lasted `speech_ms` (a click or a bump doesn't count), and ends
              after `silence_ms` of quiet that follows it

Pure code (numpy on 20 ms windows), cheap enough for the session's event loop.
"""

from __future__ import annotations

import numpy as np

WINDOW = 320                                         # 20 ms at 16 kHz


class SilenceEndpoint:
    def __init__(self, *, threshold_db: float = -45.0, speech_ms: int = 150, silence_ms: int = 700, rate: int = 16000):
        self.threshold = 10 ** (threshold_db / 20)   # as an RMS amplitude
        self.window = rate * 20 // 1000
        self.speech_needed, self.silence_needed = speech_ms // 20, silence_ms // 20
        self.rest = np.zeros(0, dtype=np.float32)
        self.speech_run = self.silence_run = 0
        self.in_turn = self.ended = False

    def feed(self, pcm: np.ndarray) -> bool:
        """More audio (float32 [-1, 1]); True once the turn has ended (and from then on)."""
        audio = np.concatenate([self.rest, np.asarray(pcm, dtype=np.float32).reshape(-1)])
        n = len(audio) // self.window
        self.rest = audio[n * self.window:]
        if n and not self.ended:
            rms = np.sqrt(np.mean(audio[:n * self.window].reshape(n, self.window) ** 2, axis=1))
            for loud in rms > self.threshold:
                if loud:
                    self.speech_run, self.silence_run = self.speech_run + 1, 0
                    if self.speech_run >= self.speech_needed:
                        self.in_turn = True
                else:
                    self.speech_run, self.silence_run = 0, self.silence_run + 1
                    if self.in_turn and self.silence_run >= self.silence_needed:
                        self.ended = True
                        break
        return self.ended
