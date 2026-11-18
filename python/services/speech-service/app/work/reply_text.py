"""A reply's text as Qwen's talker reads it, piece by piece (tts_worker.ReplyText; spike 16): each
piece normalized (T14: numbers and symbols as words), joined to what came before as continuous text
(a space between words, none around Chinese, Japanese or Korean), then tokenized and embedded by the
engine (QwenTTSEngine.stream_prompt, .text_hidden, .end_hidden)."""

from __future__ import annotations

import re

from app.tts_engine.engine import max_frames

WIDE = re.compile(r"[　-ヿ㐀-鿿가-힯＀-￯]")   # CJK text and punctuation


def joined(after: str, text: str) -> str:
    """`text` as it continues `after`: with a leading space, unless either side is CJK."""
    if not after or not text or WIDE.match(text[0]) or WIDE.match(after[-1]):
        return text
    return " " + text


class QwenReplyText:
    def __init__(self, engine, normalize, *, sample: bool = True):
        """`sample`: Qwen's sampling (the service); False: greedy (tests that compare runs bit for bit)."""
        self.engine, self.normalize, self.sample = engine, normalize, sample
        self._end = None

    def prompt(self, text: str, voice: str, language: str | None):
        return self.engine.stream_prompt(self.normalize(text), voice, language, sample=self.sample)

    def more(self, text: str, after: str):
        return self.engine.text_hidden(joined(after, self.normalize(text)))

    def end(self):
        if self._end is None:
            self._end = self.engine.end_hidden()
        return self._end

    def limit(self, text: str) -> int:
        return max_frames(text)
