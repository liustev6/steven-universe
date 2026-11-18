"""Qwen3-TTS's decoder layers, written out over Qwen's own modules, with our own fixed KV cache
(speech-service.md T11, 4.5c). The code predictor (5 layers) and the talker (28) use the same layer type
(`Qwen3TTSDecoderLayer` / `Qwen3TTSTalkerDecoderLayer`: the same forward, op for op), so both run here.

    for each layer:
        h = h + attention(input_norm(h))          q, k with their per-head norms (Qwen3), rotary, cache
        h = h + mlp(post_attention_norm(h))
    final norm

The cache: per layer, keys and values for `capacity` positions, (batch, kv_heads, capacity, head_dim),
allocated once. A call writes positions start…start+n-1 and attends over 0…start+n-1: a fixed slice, so
no mask, so PyTorch's attention picks the same kernel as Hugging Face's growing cache, and the same
rounding (spike 02). The rotary angles come from a table, `cos`/`sin` (capacity, head_dim), computed
once by the model's own rotary module.

`fused`: our Triton kernels (kernels.py) for the RMSNorms and for the rotary with the cache write.
"""

from __future__ import annotations


def decoder_layers(layers, final_norm, h, start: int, n: int, keys, values, cos, sin, *, fused: bool = False,
                   position=None):
    """`h` (batch, n, hidden) at positions start…start+n-1 → the final norm's output, same shape.
    `keys`/`values`: one cache tensor per layer. `start`, `n`: Python ints (fixed per graph).
    `position` (fused, one position): a GPU int32 tensor holding the position instead of `start`, read
    by the kernels: the rotary/cache write and our decode attention (kernels.py), so one graph serves
    every position (the talker's cache grows every frame)."""
    import torch.nn.functional as F

    end = start + n
    norm = _fused_norm if fused else _module_norm
    if not fused:
        c, s = cos[None, None, start:end], sin[None, None, start:end]  # (1, 1, n, head_dim): over heads
    for layer, k_cache, v_cache in zip(layers, keys, values):
        a = layer.self_attn
        residual, h = h, norm(layer.input_layernorm, h)
        shape = (*h.shape[:-1], -1, a.head_dim)                   # (batch, n, heads, head_dim)
        q, k = norm(a.q_norm, a.q_proj(h).view(shape)), norm(a.k_norm, a.k_proj(h).view(shape))
        v = a.v_proj(h).view(shape)
        if fused:                                                 # rotate, and write k, v into the cache
            from app.tts_engine.kernels import decode_attention, rope_and_cache

            q = rope_and_cache(q, k, v, cos, sin, k_cache, v_cache, start, position=position)
        else:
            q, k, v = q.transpose(1, 2), k.transpose(1, 2), v.transpose(1, 2)
            q, k = q * c + rotate_half(q) * s, k * c + rotate_half(k) * s
            k_cache[:, :, start:end].copy_(k)                     # write into the fixed cache…
            v_cache[:, :, start:end].copy_(v)
        if fused and position is not None:                        # the length from GPU memory
            out = decode_attention(q, k_cache, v_cache, position, a.scaling)
        else:                                                     # …and read the written slice only
            out = F.scaled_dot_product_attention(
                q, k_cache[:, :, :end], v_cache[:, :, :end], scale=a.scaling, is_causal=n > 1, enable_gqa=True)
            out = out.transpose(1, 2).contiguous().reshape(*h.shape[:-1], -1)
        h = residual + a.o_proj(out)
        residual, h = h, norm(layer.post_attention_layernorm, h)
        h = residual + layer.mlp(h)
    return norm(final_norm, h)


def new_cache(n_layers: int, batch: int, kv_heads: int, capacity: int, head_dim: int, *, dtype, device):
    """Keys and values for every layer: zeros, written as positions are filled."""
    import torch

    shape = (batch, kv_heads, capacity, head_dim)
    return ([torch.zeros(shape, dtype=dtype, device=device) for _ in range(n_layers)],
            [torch.zeros(shape, dtype=dtype, device=device) for _ in range(n_layers)])


def rotary_table(rotary_emb, x, capacity: int, *, streams: int = 0):
    """cos, sin (capacity, head_dim) for positions 0…capacity-1, from the model's own rotary module, so
    the same float32 math and the same rounding to x's dtype. `streams`: the talker's multimodal rotary
    takes 3 position streams (time, height, width); for speech all three are the same position, which
    makes it the ordinary rotary, so stream 0's angles are the angles."""
    import torch

    positions = torch.arange(capacity, device=x.device)[None]
    if streams:
        positions = positions[None].expand(streams, 1, capacity)
    cos, sin = rotary_emb(x, positions)
    if streams:
        cos, sin = cos[0], sin[0]
    return cos[0].contiguous(), sin[0].contiguous()


def rotate_half(x):
    """Qwen's (and Llama's) rotary helper: the second half of the last dimension, negated, then the first."""
    import torch

    half = x.shape[-1] // 2
    return torch.cat((-x[..., half:], x[..., :half]), dim=-1)


def _module_norm(module, x):
    return module(x)


def _fused_norm(module, x):
    from app.tts_engine.kernels import rms_norm

    return rms_norm(x, module.weight, module.variance_epsilon)
