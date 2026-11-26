"""The agent's text → what Hey Pi can say. Web search answers carry inline citations, ChatGPT's
markers `citeturn0search0` (private-use characters around a source id), which a
chat app turns into links and a voice must drop; and a model sometimes slips in markdown emphasis.
Square brackets are never spoken either: each turn's text starts with `[now: …]` (the time, agent.py
`now_line`), and a model sometimes echoes it at the start of its reply.
Fed the reply as it streams: a marker or bracket split across two pieces is held until it closes."""

from __future__ import annotations

START, END = "", ""
DROP = {"*", "`"}                                 # markdown emphasis and code, never meant to be heard
BRACKET_MAX = 80                                  # a "[" not closed within this is text after all: spoken


def _private(ch: str) -> bool:
    return "" <= ch <= ""             # the private-use area: markers, never speech


class SpokenText:
    def __init__(self) -> None:
        self._in_marker = False
        self._bracket: list[str] | None = None        # inside "[…": what came after the "[", held

    def feed(self, text: str) -> str:
        """The part of `text` to speak now (possibly empty)."""
        out = []
        for ch in text:
            if self._in_marker:
                self._in_marker = ch != END
            elif ch == START:
                self._in_marker = True
            elif self._bracket is not None:
                if ch == "]":
                    self._bracket = None                 # "[…]": dropped
                else:
                    self._bracket.append(ch)
                    if len(self._bracket) > BRACKET_MAX:  # never closed: it was text, say it
                        out.extend(c for c in ["["] + self._bracket if c not in DROP and not _private(c))
                        self._bracket = None
            elif ch == "[":
                self._bracket = []
            elif ch not in DROP and not _private(ch):
                out.append(ch)
        return "".join(out)

    def end(self) -> None:
        """A message ended: a marker or bracket left open is dropped, not carried into the next message."""
        self._in_marker = False
        self._bracket = None
