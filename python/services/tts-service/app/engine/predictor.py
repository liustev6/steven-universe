"""The code predictor's loop, our own (tts-service.md T11, 4.5c stage 1): what Hugging Face's `generate`
did for it, as a loop over Qwen's own modules.

Each frame, the talker hands over two vectors: its last hidden state and the embedding of the frame's
first code. The predictor then makes the other 15 codes, one per forward pass:

    pass 0   [hidden, embed(code 1)]   2 positions   → lm_head[0]  → code 2
    pass 1   codec_embedding[0](code 2) 1 position   → lm_head[1]  → code 3
    …
    pass 14  codec_embedding[13](code 15)            → lm_head[14] → code 16

The KV cache ends at exactly 16 positions, every frame. No stop token: always 15 passes.

Two loops, the same math (every module is Qwen's, called directly):

- `CodePredictor` (step 2): Hugging Face's model forward with its growing cache (`DynamicCache`): each
  pass appends to the cache, so new memory every pass, and the shapes change.
- `StaticCodePredictor` (step 3, graph-ready): the 5 layers' forward written out over Qwen's modules,
  with **our own fixed cache**: 16 positions per layer, allocated once. Pass k writes its keys and values
  into position k+1 (pass 0: positions 0-1) and attends over the slice 0…k+1. The loop always runs the
  same 15 passes, so each pass's shapes are fixed (pass k: k+2 positions), which is what lets a CUDA
  graph record it (step 4). And no mask: unlike Hugging Face's fixed cache (attention over all 16 slots,
  the unused ones masked), attention reads only the written positions, so PyTorch picks the same kernel
  as the growing cache, and the same rounding (spikes/02). Nothing to clear between frames: every
  position is written before it's read.

The sampling is ours too: Hugging Face's order (temperature, then top-k, then top-p, then a draw), in
float32 like `generate`.
"""

from __future__ import annotations

from types import SimpleNamespace


class CodePredictor:
    """Step 2: our loop, Hugging Face's model forward and growing cache."""

    def __init__(self, cp):
        """`cp`: Qwen's `talker.code_predictor`."""
        self.cp = cp
        self.steps = cp.config.num_code_groups - 1                # 15

    def __call__(self, inputs_embeds, *, do_sample: bool = False, top_k: int | None = None,
                 top_p: float | None = None, temperature: float | None = None, **_unused):
        """Stands in for `code_predictor.generate(...)` in Qwen's talker, which reads only
        `.sequences`: the 15 codes, shape (batch, 15)."""
        import torch
        from transformers import DynamicCache

        cp, model = self.cp, self.cp.model
        cache, x, pos, codes = DynamicCache(), inputs_embeds, 0, []
        for k in range(self.steps):
            if k:
                x = model.codec_embedding[k - 1](codes[-1])       # the code just made, as input
            x = cp.small_to_mtp_projection(x)                     # Identity for the 0.6B
            n = x.shape[1]
            hidden = model(inputs_embeds=x, past_key_values=cache, use_cache=True,
                           cache_position=torch.arange(pos, pos + n, device=x.device)).last_hidden_state
            # All positions through the output layer, then the last, like `generate`: the same matrix
            # shapes, so the same GPU kernels and the same rounding.
            logits = cp.lm_head[k](hidden)[:, -1].float()
            codes.append(pick(logits, do_sample=do_sample, top_k=top_k, top_p=top_p, temperature=temperature))
            pos += n
        return SimpleNamespace(sequences=torch.cat(codes, dim=1))


class StaticCodePredictor:
    """Step 3: graph-ready. Fixed buffers, fixed shapes per pass, no CPU work that depends on values."""

    def __init__(self, cp):
        self.cp = cp
        self.steps = cp.config.num_code_groups - 1                # 15
        self.length = self.steps + 1                              # 16 cache positions
        self._buffers: SimpleNamespace | None = None

    def __call__(self, inputs_embeds, *, do_sample: bool = False, top_k: int | None = None,
                 top_p: float | None = None, temperature: float | None = None, **_unused):
        import torch

        if inputs_embeds.shape[1] != 2:
            raise ValueError("the code predictor starts from 2 positions: the talker's hidden state and code 1")
        b = self._setup(inputs_embeds)
        cp, model = self.cp, self.cp.model
        x, codes = inputs_embeds, []
        for k in range(self.steps):
            if k:
                x = model.codec_embedding[k - 1](codes[-1])
            x = cp.small_to_mtp_projection(x)
            start, n = (0, 2) if k == 0 else (k + 1, 1)           # Python constants: fixed per pass
            hidden = self._layers(x, start, n, b)
            logits = cp.lm_head[k](hidden)[:, -1].float()
            codes.append(pick(logits, do_sample=do_sample, top_k=top_k, top_p=top_p, temperature=temperature))
        return SimpleNamespace(sequences=torch.cat(codes, dim=1))

    def _setup(self, x) -> SimpleNamespace:
        """The fixed buffers, once per batch size: each layer's keys and values for 16 positions, and
        the rotary angles (cos, sin) for positions 0-15, computed by Qwen's own rotary module."""
        import torch

        if self._buffers is not None and self._buffers.batch == x.shape[0]:
            return self._buffers
        model = self.cp.model
        attn = model.layers[0].self_attn
        kv_heads = self.cp.config.num_key_value_heads
        shape = (x.shape[0], kv_heads, self.length, attn.head_dim)
        cos, sin = model.rotary_emb(x, torch.arange(self.length, device=x.device)[None])
        self._buffers = SimpleNamespace(
            batch=x.shape[0],
            keys=[torch.zeros(shape, dtype=x.dtype, device=x.device) for _ in model.layers],
            values=[torch.zeros(shape, dtype=x.dtype, device=x.device) for _ in model.layers],
            cos=cos.unsqueeze(1), sin=sin.unsqueeze(1))           # (1, 1, 16, head_dim): broadcast over heads
        return self._buffers

    def _layers(self, h, start: int, n: int, b: SimpleNamespace):
        """The 5 decoder layers on positions start…start+n-1: Qwen3TTSDecoderLayer and Qwen3TTSAttention's
        forward, op for op, with our cache in place of Hugging Face's."""
        import torch.nn.functional as F

        model, end = self.cp.model, start + n
        cos, sin = b.cos[:, :, start:end], b.sin[:, :, start:end]
        for layer, keys, values in zip(model.layers, b.keys, b.values):
            a = layer.self_attn
            residual, h = h, layer.input_layernorm(h)
            shape = (*h.shape[:-1], -1, a.head_dim)               # (batch, n, heads, head_dim)
            q = a.q_norm(a.q_proj(h).view(shape)).transpose(1, 2)
            k = a.k_norm(a.k_proj(h).view(shape)).transpose(1, 2)
            v = a.v_proj(h).view(shape).transpose(1, 2)
            q, k = q * cos + rotate_half(q) * sin, k * cos + rotate_half(k) * sin
            keys[:, :, start:end].copy_(k)                        # write into the fixed cache…
            values[:, :, start:end].copy_(v)
            out = F.scaled_dot_product_attention(                 # …and read the written slice only
                q, keys[:, :, :end], values[:, :, :end], scale=a.scaling, is_causal=n > 1, enable_gqa=True)
            out = out.transpose(1, 2).contiguous().reshape(*h.shape[:-1], -1)
            h = residual + a.o_proj(out)
            residual, h = h, layer.post_attention_layernorm(h)
            h = residual + layer.mlp(h)
        return model.norm(h)


def rotate_half(x):
    """Qwen's (and Llama's) rotary helper: the second half of the last dimension, negated, then the first."""
    import torch

    half = x.shape[-1] // 2
    return torch.cat((-x[..., half:], x[..., :half]), dim=-1)


def pick(logits, *, do_sample: bool, top_k: int | None = None, top_p: float | None = None,
         temperature: float | None = None):
    """One code per row of `logits` (batch, vocab) → (batch, 1). Hugging Face's sampling, step for step."""
    import torch

    if not do_sample:
        return logits.argmax(dim=-1, keepdim=True)
    if temperature and temperature != 1.0:
        logits = logits / temperature
    if top_k:                                                     # keep the k best, drop the rest
        kth = torch.topk(logits, min(top_k, logits.shape[-1])).values[..., -1:]
        logits = logits.masked_fill(logits < kth, float("-inf"))
    if top_p is not None and top_p < 1.0:                         # keep the smallest set worth top_p
        ordered, index = torch.sort(logits, descending=False)
        drop = ordered.softmax(dim=-1).cumsum(dim=-1) <= (1 - top_p)
        drop[..., -1:] = False                                    # always keep the best one
        logits = logits.masked_fill(drop.scatter(-1, index, drop), float("-inf"))
    return torch.multinomial(logits.softmax(dim=-1), num_samples=1)
