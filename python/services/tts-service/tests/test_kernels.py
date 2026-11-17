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
