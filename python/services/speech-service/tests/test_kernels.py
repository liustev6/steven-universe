"""Our Triton kernels against the PyTorch code they replace, on the GPU (marked `gpu`)."""

import pytest

torch = pytest.importorskip("torch")
pytestmark = [pytest.mark.gpu, pytest.mark.skipif(not torch.cuda.is_available(), reason="needs a GPU")]


class QwenRMSNorm(torch.nn.Module):
    """Qwen3TTSRMSNorm's forward, copied: the reference."""

    def __init__(self, n, eps=1e-6):
        super().__init__()
        self.weight = torch.nn.Parameter(torch.randn(n) * 0.1 + 1)
        self.variance_epsilon = eps

    def forward(self, hidden_states):
        input_dtype = hidden_states.dtype
        hidden_states = hidden_states.to(torch.float32)
        variance = hidden_states.pow(2).mean(-1, keepdim=True)
        hidden_states = hidden_states * torch.rsqrt(variance + self.variance_epsilon)
        return self.weight * hidden_states.to(input_dtype)


@pytest.mark.parametrize("shape", [(1, 2, 1024), (1, 1, 1024), (1, 1, 16, 128), (1, 2, 8, 128), (3, 1, 1024)])
def test_rms_norm_matches_qwens(shape):
    """At most a rare element one bfloat16 step off (the mean adds in another order), never more."""
    from app.engine.kernels import rms_norm

    torch.manual_seed(0)
    ref = QwenRMSNorm(shape[-1]).to("cuda", torch.bfloat16)
    worst, differing, total = 0.0, 0, 0
    for _ in range(200):
        x = (torch.randn(shape, device="cuda") * 3).to(torch.bfloat16)
        want, got = ref(x), rms_norm(x, ref.weight, ref.variance_epsilon)
        assert got.shape == want.shape and got.dtype == want.dtype
        step = torch.finfo(torch.bfloat16).eps * want.float().abs().clamp(min=1e-3)   # one bf16 step, about
        worst = max(worst, ((got.float() - want.float()).abs() / step).max().item())
        differing += (got != want).sum().item()
        total += want.numel()
    print(f"{shape}: {differing} of {total} elements differ; worst {worst:.2f} bfloat16 steps")
    assert worst <= 1.01
    assert differing / total < 1e-3


def rotate_half(x):
    return torch.cat((-x[..., x.shape[-1] // 2:], x[..., : x.shape[-1] // 2]), dim=-1)


@pytest.mark.parametrize("batch, n, start", [(1, 2, 0), (1, 1, 5), (1, 1, 15), (3, 2, 0), (2, 1, 9)])
def test_rope_and_cache_is_bit_exact(batch, n, start):
    """No sums, so nothing reordered: the same three roundings as Qwen's, so the same bits."""
    from app.engine.kernels import rope_and_cache

    torch.manual_seed(start)
    dt, dev, d = torch.bfloat16, "cuda", 128
    q = torch.randn(batch, n, 16, d, device=dev).to(dt)
    k = torch.randn(batch, n, 8, d, device=dev).to(dt)
    v = torch.randn(batch, n, 8, d, device=dev).to(dt)
    angles = torch.arange(16, device=dev)[:, None] * (1e6 ** (-torch.arange(0, d, 2, device=dev) / d))[None]
    cos, sin = torch.cat([angles, angles], -1).cos().to(dt), torch.cat([angles, angles], -1).sin().to(dt)
    keys, values = torch.zeros(batch, 8, 16, d, device=dev, dtype=dt), torch.zeros(batch, 8, 16, d, device=dev, dtype=dt)
    got_q = rope_and_cache(q, k, v, cos, sin, keys, values, start)
    c, s = cos[start:start + n][None, None], sin[start:start + n][None, None]
    qt, kt = q.transpose(1, 2), k.transpose(1, 2)                  # Qwen's path
    want_q, want_k = qt * c + rotate_half(qt) * s, kt * c + rotate_half(kt) * s
    assert torch.equal(got_q, want_q)
    assert torch.equal(keys[:, :, start:start + n], want_k)
    assert torch.equal(values[:, :, start:start + n], v.transpose(1, 2))
    others = torch.ones(16, dtype=torch.bool); others[start:start + n] = False
    assert not keys[:, :, others].any() and not values[:, :, others].any()      # nothing else written


@pytest.mark.parametrize("length", [1, 2, 31, 64, 65, 200, 700, 2048])
def test_decode_attention_matches_pytorch(length):
    """Our one-pass attention vs PyTorch's, at lengths around and across its 64-position blocks: the
    same up to rounding (its own summation order, and float32 throughout where PyTorch's kernel rounds
    the softmax weights to bfloat16)."""
    import torch.nn.functional as F
    from app.engine.kernels import decode_attention

    torch.manual_seed(length)
    dt, dev, d = torch.bfloat16, "cuda", 128
    q = torch.randn(1, 16, 1, d, device=dev).to(dt)
    keys = torch.randn(1, 8, 2048, d, device=dev).to(dt)
    values = torch.randn(1, 8, 2048, d, device=dev).to(dt)
    keys[:, :, length:] = float("nan")                              # past the length: must never be read
    values[:, :, length:] = float("nan")
    position = torch.tensor([length - 1], dtype=torch.int32, device=dev)
    got = decode_attention(q, keys, values, position, d ** -0.5)
    want = F.scaled_dot_product_attention(q, keys[:, :, :length], values[:, :, :length], scale=d ** -0.5,
                                          enable_gqa=True).transpose(1, 2).reshape(1, 1, -1)
    assert not got.isnan().any()
    err = (got.float() - want.float()).abs().max().item()
    print(f"length {length}: max |difference| {err:.2e}")
    assert err < 2e-2
