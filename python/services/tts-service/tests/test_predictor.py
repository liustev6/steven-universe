"""The sampling in our code-predictor loop, on the CPU: the same rules as Hugging Face's."""

import pytest

torch = pytest.importorskip("torch")

from app.engine.predictor import pick  # noqa: E402

LOGITS = torch.tensor([[0.0, 3.0, 1.0, 2.0, -1.0]])


def draws(n=2000, **kw):
    torch.manual_seed(0)
    return {int(pick(LOGITS, do_sample=True, **kw)) for _ in range(n)}


def test_greedy_takes_the_best():
    assert int(pick(LOGITS, do_sample=False, top_k=50, top_p=1.0, temperature=0.9)) == 1


def test_top_k_keeps_only_the_k_best():
    assert draws(top_k=2) == {1, 3}


def test_top_p_keeps_the_smallest_set_worth_p():
    # softmax of LOGITS: code 1 ≈ 0.64, code 3 ≈ 0.24, code 2 ≈ 0.09: 1 alone is < 0.8; 1 and 3 reach it
    assert draws(top_p=0.8) == {1, 3}
    assert draws(top_p=0.5) == {1}


def test_no_limits_can_draw_anything():
    assert draws(n=5000) == {0, 1, 2, 3, 4}


def test_shapes_for_a_batch():
    out = pick(LOGITS.repeat(3, 1), do_sample=True, top_k=50, top_p=1.0, temperature=0.9)
    assert out.shape == (3, 1)
