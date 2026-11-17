"""The plain engine on the real model and GPU (marked `gpu`: run on CT 108, skipped elsewhere):

    TTS_MODEL=/models/Qwen3-TTS-12Hz-0.6B-CustomVoice python -m pytest -m gpu

The key test: without sampling, our streaming engine produces exactly Qwen's own codes, frame for
frame. Every bit-exact engine (4.5c: our loop, our fixed cache, the CUDA graph) must pass it.

Our fused kernels (kernels.py) can't be bit-exact: they add numbers in another order than PyTorch, so,
rarely, a result differs by one bfloat16 step, and greedy decoding diverges after the first near-tie
it flips. They pass the serving engines' standard instead (vLLM's `check_logprobs_close`): the same
inputs, pass by pass, against the exact predictor; a different pick only where the exact one's top two
scores were nearly tied, and rarely (spikes/05)."""

import os
import threading
import time

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytestmark = [pytest.mark.gpu,
              pytest.mark.skipif(not (os.environ.get("TTS_MODEL") and torch.cuda.is_available()),
                                 reason="needs the model (TTS_MODEL) and a GPU")]

SENTENCES = [("ryan", "Hi, how can I help you today?"),
             ("vivian", "好的，我现在帮你把客厅的灯关掉。"),
             ("ryan", "Sure. Tomorrow looks cloudy in the morning, with a little rain in the afternoon.")]


@pytest.fixture(scope="module")
def engine():
    from app.engine.qwen import QwenEngine

    e = QwenEngine(os.environ["TTS_MODEL"])
    e.load()
    e.warm_up()
    return e


@pytest.fixture
def predictor(engine, request):
    engine.predictor = request.param
    yield request.param
    engine.predictor = "qwen"


@pytest.fixture
def talker(engine, request):
    engine.talker = request.param
    yield request.param
    engine.talker = "qwen"


@pytest.mark.parametrize("predictor", ["qwen", "ours", "ours-static", "ours-graph"], indirect=True)
@pytest.mark.parametrize("voice, text", SENTENCES)
def test_streamed_codes_are_qwens_own(engine, predictor, voice, text):
    ours = torch.stack(list(engine.frames(text, voice, sample=False)))
    official = engine.official_codes(text, voice)
    assert ours.shape == official.shape, (ours.shape, official.shape)
    assert torch.equal(ours.cpu(), official.cpu())


@pytest.mark.parametrize("talker", ["ours"], indirect=True)
@pytest.mark.parametrize("predictor", ["qwen", "ours-graph"], indirect=True)
@pytest.mark.parametrize("voice, text", SENTENCES)
def test_our_talker_gives_qwens_codes(engine, talker, predictor, voice, text):
    """Our talker loop (bit-exact layers), with Qwen's predictor and with our graph: Qwen's codes."""
    ours = torch.stack(list(engine.frames(text, voice, sample=False)))
    official = engine.official_codes(text, voice)
    assert ours.shape == official.shape, (ours.shape, official.shape)
    assert torch.equal(ours.cpu(), official.cpu())


@pytest.mark.parametrize("talker", ["ours"], indirect=True)
@pytest.mark.parametrize("predictor", ["ours-fused"], indirect=True)
def test_our_talker_samples_and_stops(engine, talker, predictor):
    """Sampled, our talker's speech ends by itself (the end code), about as long as Qwen's."""
    seconds = len(np.concatenate(list(engine.stream("Sure. Tomorrow looks cloudy in the morning.", "ryan")))) / 24000
    assert 1.5 < seconds < 6, seconds
    n = sum(1 for _ in engine.frames("Warming up.", "ryan", sample=False))      # and the limit holds
    from app.engine.qwen import max_frames
    assert n <= max_frames("Warming up.")


@pytest.mark.parametrize("predictor", ["ours", "ours-static", "ours-graph", "ours-fused"], indirect=True)
def test_our_predictor_samples(engine, predictor):
    """With sampling on (the default), our loop's speech still ends: about as long as Qwen's."""
    text = "Sure. Tomorrow looks cloudy in the morning."
    seconds = len(np.concatenate(list(engine.stream(text, "ryan")))) / 24000
    assert 1.5 < seconds < 6, seconds


def bf16_steps(x):
    """The size of one bfloat16 step at each value's magnitude (7 bits after the leading one)."""
    return torch.exp2(torch.floor(torch.log2(x.abs().clamp(min=2.0 ** -126))) - 7)


def test_fused_predictor_is_close_to_the_exact_one(engine):
    """Every frame's predictor inputs from real sentences; the fused predictor fed the exact one's codes."""
    from app.engine.predictor import StaticCodePredictor

    cp = engine._talker.code_predictor
    exact, fused, inputs = StaticCodePredictor(cp), StaticCodePredictor(cp, fused=True), []
    saved = engine._predictors["ours-static"]                        # a spy in its place: records inputs
    engine._predictors["ours-static"] = lambda inputs_embeds, **kw: (inputs.append(inputs_embeds.clone()),
                                                                     exact(inputs_embeds, **kw))[1]
    engine.predictor = "ours-static"
    try:
        with torch.inference_mode():
            for voice, text in SENTENCES:
                for _ in engine.frames(text, voice, sample=False):
                    pass
    finally:
        engine.predictor, engine._predictors["ours-static"] = "qwen", saved
    steps, flips, worst = 0, 0, 0.0
    with torch.inference_mode():
        for x in inputs:
            codes, want = exact.teacher_forced(x)
            _, got = fused.teacher_forced(x, codes)
            top2 = want.topk(2, dim=-1).values                       # (15, batch, 2)
            differs = want.argmax(-1) != got.argmax(-1)
            near_tie = (top2[..., 0] - top2[..., 1]) <= 2 * bf16_steps(top2[..., 0])
            assert not (differs & ~near_tie).any(), "a different pick where the exact one wasn't nearly tied"
            steps, flips = steps + differs.numel(), flips + int(differs.sum())
            # in steps of the best score's size: a score near 0 has a tiny step, which says nothing
            worst = max(worst, ((want - got).abs() / bf16_steps(top2[..., :1])).max().item())
    print(f"{steps} steps, {flips} different picks, worst score difference {worst:.1f} bfloat16 steps")
    assert flips / steps < 0.01
    assert worst <= 8                                                # a bug moves scores by far more


@pytest.mark.parametrize("voice, text", SENTENCES)
def test_streamed_audio_matches_the_whole_decode(engine, voice, text):
    """Chunks decoded with 25 frames of context sound like the whole sentence decoded at once."""
    codes = engine.official_codes(text, voice)
    whole, sr = engine._codec.decode([{"audio_codes": codes}])
    assert sr == engine.sample_rate == 24000
    streamed = np.concatenate(list(engine.stream(text, voice, sample=False)))
    whole = np.asarray(whole[0], dtype=np.float32)
    assert abs(len(streamed) - len(whole)) <= 1920                # within one frame
    n = min(len(streamed), len(whole))
    noise = np.sum((streamed[:n] - whole[:n]) ** 2)
    snr_db = 10 * np.log10(np.sum(whole[:n] ** 2) / max(noise, 1e-12))
    assert snr_db > 20, f"streamed vs whole: {snr_db:.1f} dB"


def test_a_sentence_that_never_ends_is_cut_off(engine):
    """Greedy "Warming up." never produces the end code: the limit stops it (50 + 8 a character)."""
    from app.engine.qwen import max_frames

    n = sum(1 for _ in engine.frames("Warming up.", "ryan", sample=False))
    assert n <= max_frames("Warming up.") == 138


def test_voices(engine):
    voices = {v.name: v.language for v in engine.voices()}
    assert len(voices) == 9 and voices["vivian"] == "Chinese" and voices["ryan"] == "English"


def test_cancel_stops_within_a_frame_or_two(engine):
    cancel = threading.Event()
    chunks = 0
    with pytest.raises(Exception) as exc:
        for _ in engine.stream("Count slowly from one to fifty, please, one number at a time.", "ryan", cancel=cancel):
            chunks += 1
            if chunks == 2:
                t = time.perf_counter()
                cancel.set()
    assert type(exc.value).__name__ == "Cancelled"
    assert time.perf_counter() - t < 0.5
    list(engine.stream("Still fine afterwards.", "ryan"))       # the engine is usable again


def test_speed(engine):
    """The spike's numbers, as a floor: first audio under 300 ms, faster than real time."""
    t = time.perf_counter()
    first, samples = None, 0
    for audio in engine.stream("The kitchen lights are on, and the door is locked.", "aiden"):
        first = first or (time.perf_counter() - t)
        samples += len(audio)
    total = time.perf_counter() - t
    assert first < 0.3, f"first audio {first * 1000:.0f} ms"
    assert total / (samples / 24000) < 0.7, f"real-time factor {total / (samples / 24000):.2f}"
