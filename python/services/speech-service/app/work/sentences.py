"""A reply's text arrives in pieces (a word, a few words) as the agent writes it; the TTS speaks a
sentence at a time (each sentence is one prompt: tts_engine). This cuts the stream into sentences as
soon as each is complete, so speech starts while the rest is still being written (T8).

A sentence ends:
  - after Chinese/Japanese end punctuation (。！？；…), at once, with any closing quotes after it
  - after . ! ? ; when followed by a space or a line break (so "3.5" and "example.com" don't cut)
  - at a line break
  - if it runs past `max_chars` without one: at its last comma or space before the limit (a long
    unpunctuated stretch shouldn't hold back the speech)
  - `first_clause`: the reply's FIRST segment also ends at a comma (，、 at once; "," before a space)
    once it has 2 characters or more: "好的，", "Sure, ", "OK, " are spoken at once instead of
    waiting for the whole first sentence (e2e: a Chinese reply's first audio 325 ms → ~20 ms; such
    openings are natural pauses, so the phrasing hardly suffers)
`flush()` at the end of the reply: whatever is left.
"""

from __future__ import annotations

CJK_END = "。！？；…"
ASCII_END = ".!?;"
CLOSERS = "\"'”’」』）)]"
SOFT = "，、,:： "
CJK_COMMA = "，、"


class Sentences:
    def __init__(self, max_chars: int = 200, *, first_clause: bool = False):
        self.max_chars, self.first_clause = max_chars, first_clause
        self.buffer = ""
        self.cut_any = False                         # has a segment come out yet

    def add(self, text: str) -> list[str]:
        self.buffer += text
        out = []
        while (cut := self._cut()) is not None:
            sentence, self.buffer = self.buffer[:cut], self.buffer[cut:]
            if sentence.strip():
                out.append(sentence.strip())
                self.cut_any = True
        return out

    def flush(self) -> list[str]:
        rest, self.buffer = self.buffer.strip(), ""
        return [rest] if rest else []

    def _cut(self) -> int | None:
        b = self.buffer
        clause = self.first_clause and not self.cut_any
        for i, ch in enumerate(b):
            if clause and len(b[:i].strip()) >= 2 and (ch in CJK_COMMA or (ch == "," and i + 1 < len(b) and b[i + 1] in " \n\t")):
                return i + 1
            if ch == "\n":
                return i + 1
            if ch in CJK_END or (ch in ASCII_END and i + 1 < len(b) and b[i + 1] in " \n\t"):
                j = i + 1
                while j < len(b) and (b[j] in CLOSERS or b[j] in CJK_END or b[j] in ASCII_END):
                    j += 1
                if ch in ASCII_END and j == len(b):
                    return None                       # "Hello!" then maybe "?" or a quote: wait
                return j
        if len(b) > self.max_chars:
            soft = max((b.rfind(c, 0, self.max_chars) for c in SOFT), default=-1)
            return soft + 1 if soft > 0 else self.max_chars
        return None
