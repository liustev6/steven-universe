"""Admission and sentences (app/work), without a model."""

import asyncio

import pytest

from app.work.admission import Admission, QueueTimeout, Refused
from app.work.sentences import Sentences


# ---- admission -------------------------------------------------------------------------------------
def test_two_run_one_waits_the_fourth_is_refused():
    async def go():
        a = Admission(running=2, waiting=1)
        t1, t2, t3 = a.enter(), a.enter(), a.enter()
        assert (t1.running, t2.running, t3.running, t3.position) == (True, True, False, 1)
        with pytest.raises(Refused):
            a.enter()
        t1.release()                                        # a place frees: the waiting one starts
        await t3.started(timeout=1)
        assert t3.running and a.counts()["running"] == 2 and a.counts()["waiting"] == 0
        t4 = a.enter()                                      # the line has room again
        assert not t4.running
    asyncio.run(go())


def test_waiting_too_long_gives_up_its_place():
    async def go():
        a = Admission(running=1, waiting=1)
        a.enter()
        waiting = a.enter()
        with pytest.raises(QueueTimeout):
            await waiting.started(timeout=0.05)
        assert a.counts()["waiting"] == 0 and not a.enter().running   # its place is free again
    asyncio.run(go())


def test_release_twice_counts_once():
    a = Admission(running=1, waiting=0)
    t = a.enter()
    t.release(); t.release()
    assert a.counts()["running"] == 0
    a.enter()
    with pytest.raises(Refused):
        a.enter()


def test_a_waiting_ticket_that_leaves_doesnt_hold_the_line():
    a = Admission(running=1, waiting=2)
    a.enter()
    w1, w2 = a.enter(), a.enter()
    w1.release()
    assert w2.position == 1


# ---- sentences -------------------------------------------------------------------------------------
def feed(pieces, **kw):
    s, out = Sentences(**kw), []
    for p in pieces:
        out += s.add(p)
    return out, s.flush()


def test_sentences_come_out_as_soon_as_complete():
    out, rest = feed(["Sure. Tomor", "row looks cloudy", " in the morning. And", " then rain"])
    assert out == ["Sure.", "Tomorrow looks cloudy in the morning."] and rest == ["And then rain"]


def test_numbers_and_names_dont_cut():
    out, rest = feed(["It's 3.5 degrees at example.com today. Ok"])
    assert out == ["It's 3.5 degrees at example.com today."] and rest == ["Ok"]


def test_chinese_cuts_at_once_with_its_closing_quote():
    out, rest = feed(["好的。我现在帮你", "把灯关掉！「好吗？」然后"])
    assert out == ["好的。", "我现在帮你把灯关掉！", "「好吗？」"] and rest == ["然后"]


def test_end_punctuation_waits_for_what_follows():
    out, rest = feed(["Really?", "!", " Yes."])
    assert out == ["Really?!"] and rest == ["Yes."]


def test_a_long_stretch_without_punctuation_cuts_at_a_comma_or_space():
    out, rest = feed(["word " * 60], max_chars=50)
    assert all(len(s) <= 50 for s in out) and len(out) >= 5


def test_line_breaks_cut():
    out, rest = feed(["first line\nsecond"])
    assert out == ["first line"] and rest == ["second"]



def test_the_first_clause_starts_at_a_comma():
    out, rest = feed(["好的，", "我现在帮你，", "把灯关掉。"], first_clause=True)
    assert out == ["好的，", "我现在帮你，把灯关掉。"]                   # only the first segment cuts at a comma
    out, _ = feed(["Sure, ", "tomorrow looks cloudy, with rain. Then sun."], first_clause=True)
    assert out == ["Sure,", "tomorrow looks cloudy, with rain."]
    out, _ = feed(["3,500 people came. ", "Yes."], first_clause=True)
    assert out == ["3,500 people came."]                               # "," not before a space: no cut
    out, _ = feed(["好的，我现在帮你。"], first_clause=False)
    assert out == ["好的，我现在帮你。"]
