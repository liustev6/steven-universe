"""The agent's text → what Hey Pi can say. Web search answers carry inline citations, ChatGPT's
markers `citeturn0search0` (private-use characters around a source id), which a
chat app turns into links and a voice must drop; and a model sometimes slips in markdown emphasis.
Fed the reply as it streams: a marker split across two pieces is held until it closes."""

from __future__ import annotations

START, END = "", ""
DROP = {"*", "`"}                                 # markdown emphasis and code, never meant to be heard


def _private(ch: str) -> bool:
    return "" <= ch <= ""             # the private-use area: markers, never speech


class SpokenText:
    def __init__(self) -> None:
        self._in_marker = False

    def feed(self, text: str) -> str:
        """The part of `text` to speak now (possibly empty)."""
        out = []
        for ch in text:
            if self._in_marker:
                self._in_marker = ch != END
            elif ch == START:
                self._in_marker = True
            elif ch not in DROP and not _private(ch):
                out.append(ch)
        return "".join(out)

    def end(self) -> None:
        """A message ended: a marker left open is dropped, not carried into the next message."""
        self._in_marker = False
