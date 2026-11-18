"""On the Pi, always listening, nothing sent anywhere (voice-path.md V9): the wake word, and whether
someone is speaking. Both are microWakeWord's (pymicro-wakeword, pymicro-vad: Apache-2.0, the
engines of Home Assistant's voice satellites), run on 10 ms of 16 kHz audio at a time; pi-server
hands them the mic's 100 ms frames."""

from __future__ import annotations

from pathlib import Path

STEP = 320                       # 10 ms of 16 kHz 16-bit audio, in bytes


class WakeWord:
    """`feed(frame)` → True when the wake word ends in it. `model`: a built-in name (okay_nabu,
    hey_jarvis, hey_mycroft, alexa) or a trained model's .json (a custom "hey pi")."""

    def __init__(self, model: str):
        from pymicro_wakeword import MicroWakeWord, MicroWakeWordFeatures, Model

        self._load = (lambda: MicroWakeWord.from_config(Path(model))) if model.endswith(".json") \
            else (lambda: MicroWakeWord.from_builtin(Model(model)))
        self._features_type = MicroWakeWordFeatures
        self.model = self._load()
        self.name = getattr(self.model, "wake_word", model)
        self._features = MicroWakeWordFeatures()

    def feed(self, frame: bytes) -> bool:
        heard = False
        for i in range(0, len(frame) - STEP + 1, STEP):
            for features in self._features.process_streaming(frame[i:i + STEP]):
                if self.model.process_streaming(features):
                    heard = True
        return heard

    def reset(self) -> None:
        """Forget what it heard (after a detection, so the same word doesn't fire twice)."""
        self.model.reset()
        self._features = self._features_type()


class Voice:
    """`feed(frame)` → True when the frame is speech: at least `share` of its 10 ms steps above
    `threshold`. Fed every frame, so its context is always warm (it misses ~1 s from cold)."""

    def __init__(self, threshold: float = 0.5, share: float = 0.3):
        from pymicro_vad import MicroVad

        self._vad = MicroVad()
        self.threshold, self.share = threshold, share

    def feed(self, frame: bytes) -> bool:
        steps = [self._vad.process_10ms(frame[i:i + STEP]) for i in range(0, len(frame) - STEP + 1, STEP)]
        speech = sum(1 for p in steps if p is not None and p >= self.threshold)
        return bool(steps) and speech / len(steps) >= self.share
