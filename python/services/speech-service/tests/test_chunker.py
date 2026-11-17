"""The chunked decode's arithmetic: the streamed audio is exactly the whole-sentence audio, chunk by
chunk, with no gap and no repeat at the joins. A fake decoder stands in for the codec: frame i sounds
as `i` for its 1,920 samples, and `tail` padding samples follow the last frame (like the real codec's)."""

import numpy as np
import pytest

from app.engine.chunker import SAMPLES_PER_FRAME as SPF
from app.engine.chunker import ChunkDecoder


def fake_decode(tail=0, calls=None):
    def decode(frames):
        if calls is not None:
            calls.append(list(frames))
        audio = np.repeat(np.asarray(frames, dtype=np.float32), SPF)
        return np.concatenate([audio, np.full(tail, -1.0, dtype=np.float32)])
    return decode


def run(n_frames, **kw):
    tail = kw.pop("tail", 0)
    calls = []
    d = ChunkDecoder(fake_decode(tail, calls), **kw)
    chunks = [c for f in range(n_frames) if (c := d.add(f)) is not None]
    if (last := d.flush()) is not None:
        chunks.append(last)
    return chunks, calls


@pytest.mark.parametrize("n_frames, sizes, context", [(1, 4, 25), (4, 4, 25), (37, 4, 25), (37, 12, 25),
                                                      (50, 4, 0), (9, 1, 3), (1, (1, 2, 4), 25),
                                                      (2, (1, 2, 4), 25), (37, (1, 2, 4), 25), (40, (1, 2, 4), 6)])
def test_streamed_audio_equals_whole_audio(n_frames, sizes, context):
    chunks, _ = run(n_frames, chunks=sizes, context=context, tail=7)
    whole = fake_decode(tail=7)(list(range(n_frames)))
    assert np.array_equal(np.concatenate(chunks), whole)          # no gap, no repeat, the tail once


def test_chunks_arrive_every_chunk_frames():
    chunks, _ = run(10, chunks=4, context=25)
    assert [len(c) // SPF for c in chunks] == [4, 4, 2]           # 4, 4, then the rest at flush


def test_the_schedule_starts_small_then_repeats_its_last_size():
    chunks, _ = run(20, chunks=(1, 2, 4), context=25)
    assert [len(c) // SPF for c in chunks] == [1, 2, 4, 4, 4, 4, 1]   # 1, 2, then 4s, the rest at flush


def test_the_default_schedule_is_1_1_2_4():
    d = ChunkDecoder(fake_decode())
    sizes = [len(c) // SPF for f in range(28) if (c := d.add(f)) is not None]
    assert sizes == [1, 1, 2, 4, 4, 4, 4, 4, 4]


def test_mid_sentence_padding_is_never_sent():
    chunks, _ = run(8, chunks=4, context=25, tail=50)
    assert not np.any(chunks[0] == -1.0)                          # the first chunk: no padding
    assert np.count_nonzero(np.concatenate(chunks) == -1.0) == 50  # only the end's, once


def test_each_decode_sees_at_most_context_earlier_frames():
    _, calls = run(40, chunks=4, context=6)
    for frames in calls:
        assert len(frames) <= 6 + 4                               # context + the new chunk
    assert calls[-2][0] == 40 - 4 - 6 and calls[-2][-1] == 39     # the last chunk: 6 of context + 4 new
    assert calls[-1][0] == 40 - 6 and calls[-1][-1] == 39         # then the tail: the last 6 frames again


def test_nothing_to_flush():
    d = ChunkDecoder(fake_decode(), chunks=4)         # no tail, and the last chunk already sent
    for f in range(4):
        d.add(f)
    assert d.flush() is None


def test_a_multiple_of_the_chunk_still_ends_with_its_tail():
    """The bug the first run of these tests found: 8 frames, chunks of 4: the last chunk goes out
    mid-sentence with its tail cut, and the end of the sentence must still send that tail."""
    chunks, _ = run(8, chunks=4, context=25, tail=50)
    assert np.count_nonzero(np.concatenate(chunks) == -1.0) == 50


def test_bad_settings():
    for bad in (0, (), (1, 0)):
        with pytest.raises(ValueError):
            ChunkDecoder(fake_decode(), chunks=bad)
