"""What speech-service would do before the TTS: wetext (WeTextProcessing's runtime, Apache-2.0), its
language picked per sentence ("auto": Chinese grammar when the sentence has Chinese, else English)."""

from wetext import Normalizer

_normalizer = Normalizer(lang="auto", operator="tn")


def normalize(text: str) -> str:
    return _normalizer.normalize(text)
