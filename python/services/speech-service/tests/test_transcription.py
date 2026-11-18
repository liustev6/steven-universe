"""Streaming speech-to-text's logic (transcription.py), without a model: a fake decode records each
pass's audio length and prefix; a fake tokenizer has one character ("é") that takes two tokens, so a
cut between them leaves a broken character (U+FFFD), as a real tokenizer's would."""

import numpy as np
import pytest

from app.stt_engine.base import TooLong
from app.stt_engine.transcription import SAMPLE_RATE as SR
from app.stt_engine.transcription import StreamingTranscription, pass_interval, settled_prefix


class Tokenizer:
    def encode(self, text):
        return [t for c in text for t in (["é1", "é2"] if c == "é" else [c])]

    def decode(self, ids):
        out, i = "", 0
        while i < len(ids):
            if ids[i] == "é1":
                out += "é" if i + 1 < len(ids) and ids[i + 1] == "é2" else "�"
                i += 2 if out.endswith("é") else 1
            else:
                out += ids[i]; i += 1
        return out


def session(texts=None):
    calls = []

    def decode(audio, prefix):
        calls.append((len(audio), prefix))
        if texts:                                  # each pass's whole text (a consistent model's
            return texts[len(calls) - 1]           # starts with the prefix it was given)
        return prefix + f"[{len(audio) // SR}s]"

    s = StreamingTranscription(decode, Tokenizer(), lambda raw: ("English", raw), max_seconds=60)
    return s, calls


def feed_seconds(s, seconds, piece=0.1):
    out = []
    for _ in range(round(seconds / piece)):
        if (t := s.feed(np.zeros(int(piece * SR), dtype=np.float32))) is not None:
            out.append(t)
    return out


def test_pass_interval_grows_with_the_utterance():
    assert [pass_interval(t) for t in (0, 9.9, 10, 19.9, 20, 59)] == [1, 1, 2, 2, 4, 4]


def test_passes_every_second_then_every_two_then_four():
    s, calls = session()
    feed_seconds(s, 30)
    assert [n / SR for n, _ in calls] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14, 16, 18, 20, 24, 28]


def test_a_burst_gets_one_pass_not_many():
    s, calls = session()
    assert s.feed(np.zeros(5 * SR, dtype=np.float32)) is not None
    assert [n / SR for n, _ in calls] == [5]               # not 1, 2, 3, 4, 5
    s.feed(np.zeros(SR // 2, dtype=np.float32))
    assert len(calls) == 1                                  # the next pass at 6 s


def test_no_prefix_for_two_passes_then_the_text_less_its_last_five_tokens():
    s, calls = session(texts=["hello", "hello wor", "hello world, how", "x"])
    feed_seconds(s, 4)
    prefixes = [p for _, p in calls]
    assert prefixes[:2] == ["", ""]
    assert prefixes[2] == "hell"                            # "hello wor" less 5 characters
    assert prefixes[3] == "hello world"                     # "hello world, how" (16) less 5


def test_a_cut_through_a_character_cuts_one_more_token():
    tok = Tokenizer()
    assert settled_prefix("abcdéfghij", passes=2, tokenizer=tok, unfixed_tokens=5) == "abcdé"
    assert settled_prefix("abcdéfghi", passes=2, tokenizer=tok, unfixed_tokens=5) == "abcd"   # not "abcd�"
    assert settled_prefix("abc", passes=5, tokenizer=tok) == ""
    assert settled_prefix("abcdefgh", passes=1, tokenizer=tok) == ""


def test_finish_decodes_the_audio_since_the_last_pass():
    s, calls = session()
    feed_seconds(s, 2.5)
    final = s.finish()
    assert final.final and calls[-1][0] == int(2.5 * SR)


def test_finish_reuses_the_last_pass_when_nothing_new_arrived():
    s, calls = session()
    feed_seconds(s, 2)
    n = len(calls)
    final = s.finish()
    assert final.final and len(calls) == n and final.text == "[2s]"


def test_a_short_utterance_gets_one_pass_at_its_end():
    s, calls = session()
    assert feed_seconds(s, 0.6) == []
    assert s.finish().text == "[0s]" and len(calls) == 1


def test_int16_audio_is_scaled():
    s, calls = session()
    seen = []
    s._decode = lambda audio, prefix: (seen.append(audio), "x")[1]
    s.feed(np.full(SR, 16384, dtype=np.int16))
    assert np.allclose(seen[0], 0.5)


def test_over_the_limit():
    s, _ = session()
    feed_seconds(s, 60, piece=1.0)
    with pytest.raises(TooLong):
        s.feed(np.zeros(1, dtype=np.float32))


def test_partials_say_which_start_is_stable():
    """`stable`: the settled prefix the next pass will keep, as text; nothing for the first 2 passes."""
    s, _ = session(texts=["hello", "hello wor", "hello world, how", "hello world, how are you"])
    out = [t for t in (s.feed(np.zeros(SR, dtype=np.float32)) for _ in range(4)) if t]
    assert [t.stable for t in out] == ["", "hell", "hello world", "hello world, how ar"]   # each less 5
    assert all(t.text.startswith(t.stable) for t in out)


def test_no_audio_at_all_is_an_empty_final():
    s, calls = session()
    final = s.finish()
    assert final.final and final.text == "" and calls == []
