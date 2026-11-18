"""Text normalization before the TTS (spikes/13): numbers, units, dates and symbols rewritten as the
words a speaker would say, so the model reads words instead of guessing at "5,959", "138-0013-8000",
"-5.5°C" or "2026年" (it read them right 56% of the time; 90% after this).

    今天是2026年11月17日。            → 今天是二零二六年十一月十七日。
    Mount Logan is 5,959 metres tall. → Mount Logan is five thousand nine hundred and fifty nine metres tall.

wetext (WeTextProcessing's grammars compiled to FSTs, Apache-2.0), one grammar per sentence, picked by
its script (wetext's own rule: kana → Japanese, hangul → Korean, any Chinese → Chinese, else English).
Words it doesn't know ("London", "iPhone") are left alone: the model speaks mixed Chinese and English
well (spikes/13). A language without a grammar in the image (base-image keeps zh, en, ja, ko), or any
failure, passes the sentence through unchanged: normalization never stops a sentence being spoken.
Runs in the TTS's work thread, as each sentence's prompt is made (~1-2 ms); `warm()` at start-up loads
the grammars (16-90 ms each, the first time).
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

LANGUAGES = ("zh", "en", "ja", "ko")             # the grammars the base image keeps
WARM = ("今天是2026年11月17日，气温-5.5°C。", "It's 3:30 PM, and 5,959 metres.",
        "今日は2026年11月17日です。", "오늘은 2026년 11월 17일입니다.")


class TextNormalizer:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._normalizers: dict[str, object] = {}
        self._lock = threading.Lock()
        self._failed_once = False
        if enabled:
            from wetext import Normalizer                  # noqa: F401  (fail at start-up, not mid-reply)
            from wetext.utils import get_lang
            self._get_lang = get_lang

    def __call__(self, text: str) -> str:
        if not self.enabled or not any(ch.isdigit() or not ch.isalnum() for ch in text):
            return text
        try:
            lang = self._get_lang(text)
            if lang not in LANGUAGES:
                return text
            out = self._normalizer(lang).normalize(text)
            return out if out.strip() else text
        except Exception:                                   # never stop the sentence: say it as written
            if not self._failed_once:
                logger.exception("text normalization failed; the sentence is spoken as written")
                self._failed_once = True
            return text

    def _normalizer(self, lang: str):
        with self._lock:
            if lang not in self._normalizers:
                from wetext import Normalizer
                self._normalizers[lang] = Normalizer(lang=lang, operator="tn")
            return self._normalizers[lang]

    def warm(self) -> None:
        for text in WARM:
            self(text)
