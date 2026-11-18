"""The code predictor's loop, our own (speech-service.md T11, 4.5c stage 1): what Hugging Face's `generate`
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

- `GraphCodePredictor` (step 4): `StaticCodePredictor`'s 15 passes recorded once as a **CUDA graph** and
  replayed every frame: one launch instead of ~1,500 (5 layers × ~18 kernels × 15 passes, plus the
  sampling), with no Python in between. How, in `_capture`.
- Fused (A, after spikes/04): the same, with our own Triton kernels (kernels.py) in place of PyTorch's
  chains of small ones: the RMSNorm (8 kernels → 1), the rotary embedding with the cache write (14 → 1).

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
    """Step 3: graph-ready. Fixed buffers, fixed shapes per pass, no CPU work that depends on values.
    `fused`: our Triton kernels (kernels.py) in place of PyTorch's chains of small ones: the RMSNorms
    (8 kernels → 1), and the rotary embedding with the cache write (14 → 1)."""

    def __init__(self, cp, *, fused: bool = False):
        self.cp, self.fused = cp, fused
        self.steps = cp.config.num_code_groups - 1                # 15
        self.length = self.steps + 1                              # 16 cache positions
        self._buffers: SimpleNamespace | None = None

    def __call__(self, inputs_embeds, *, do_sample: bool = False, top_k: int | None = None,
                 top_p: float | None = None, temperature: float | None = None, **_unused):
        codes, _ = self._run(inputs_embeds, lambda k, logits: pick(
            logits, do_sample=do_sample, top_k=top_k, top_p=top_p, temperature=temperature))
        return SimpleNamespace(sequences=codes)

    def teacher_forced(self, inputs_embeds, codes=None):
        """For comparing two predictors step by step (tests, spikes): every pass's scores, (15, batch,
        vocab), and the codes, (batch, 15): greedy, or the given `codes` fed back in place of its own
        picks, so both predictors see identical inputs at every pass."""
        import torch

        choose = ((lambda k, logits: codes[:, k:k + 1]) if codes is not None
                  else (lambda k, logits: logits.argmax(dim=-1, keepdim=True)))
        picked, scores = self._run(inputs_embeds, choose)
        return picked, torch.stack(scores)

    def _run(self, inputs_embeds, choose):
        """The 15 passes; `choose(k, logits)` picks pass k's code. Returns the codes and the scores."""
        import torch

        if inputs_embeds.shape[1] != 2:
            raise ValueError("the code predictor starts from 2 positions: the talker's hidden state and code 1")
        b = self._setup(inputs_embeds)
        cp, model = self.cp, self.cp.model
        x, codes, scores = inputs_embeds, [], []
        for k in range(self.steps):
            if k:
                x = model.codec_embedding[k - 1](codes[-1])
            x = cp.small_to_mtp_projection(x)
            start, n = (0, 2) if k == 0 else (k + 1, 1)           # Python constants: fixed per pass
            hidden = self._layers(x, start, n, b)
            scores.append(cp.lm_head[k](hidden)[:, -1].float())
            codes.append(choose(k, scores[-1]))
        return torch.cat(codes, dim=1), scores

    def _setup(self, x) -> SimpleNamespace:
        """The fixed buffers, once per batch size: each layer's keys and values for 16 positions, and
        the rotary angles (cos, sin) for positions 0-15 (layers.py)."""
        from app.tts_engine.layers import new_cache, rotary_table

        if self._buffers is not None and self._buffers.batch == x.shape[0]:
            return self._buffers
        model = self.cp.model
        keys, values = new_cache(len(model.layers), x.shape[0], self.cp.config.num_key_value_heads, self.length,
                                 model.layers[0].self_attn.head_dim, dtype=x.dtype, device=x.device)
        cos, sin = rotary_table(model.rotary_emb, x, self.length)
        self._buffers = SimpleNamespace(batch=x.shape[0], keys=keys, values=values, cos=cos, sin=sin)
        return self._buffers

    def _layers(self, h, start: int, n: int, b: SimpleNamespace):
        """The 5 decoder layers on positions start…start+n-1 (layers.py), with this predictor's cache."""
        from app.tts_engine.layers import decoder_layers

        model = self.cp.model
        return decoder_layers(model.layers, model.norm, h, start, n, b.keys, b.values, b.cos, b.sin,
                              fused=self.fused)


class GraphCodePredictor:
    """Step 4: the graph-ready loop, captured once per setting and replayed every frame.

    A graph records the GPU work of one run: which kernels, with which arguments, reading and writing
    which memory addresses. A replay launches all of it again at once, so:
    - **inputs go into fixed buffers** before each replay (`entry.input.copy_(...)`): the recorded
      kernels read those addresses, not whatever tensor the caller has;
    - **outputs come out of fixed buffers** (`entry.output`), overwritten by the next replay;
    - **one graph per shape and setting:** batch size, greedy or sampled (and its top-k, temperature):
      each is different recorded work. Captured lazily, the first time each is used (the warm-up
      captures the sampled one that requests use);
    - **each graph owns its own `StaticCodePredictor`,** so its cache buffers live as long as it does
      (a graph holds raw addresses; freed memory reused by something else would be read silently).
    Random draws work inside a graph: PyTorch's generator advances its offset on every replay, so each
    replay draws new numbers.
    """

    def __init__(self, cp, *, fused: bool = False):
        self.cp, self.fused = cp, fused
        self._graphs: dict[tuple, SimpleNamespace] = {}

    def __call__(self, inputs_embeds, *, do_sample: bool = False, top_k: int | None = None,
                 top_p: float | None = None, temperature: float | None = None, **_unused):
        settings = (dict(do_sample=True, top_k=top_k, top_p=top_p, temperature=temperature) if do_sample
                    else dict(do_sample=False))
        key = (tuple(inputs_embeds.shape), inputs_embeds.dtype, *sorted(settings.items()))
        entry = self._graphs.get(key) or self._capture(key, inputs_embeds, settings)
        entry.input.copy_(inputs_embeds)
        entry.graph.replay()
        return SimpleNamespace(sequences=entry.output.clone())   # the next replay overwrites `output`

    def _capture(self, key, x, settings) -> SimpleNamespace:
        import torch

        static, x_in = StaticCodePredictor(self.cp, fused=self.fused), x.clone()

        def run():
            return static(x_in, **settings).sequences

        # 1. Warm up on a side stream: one-time setup (our buffers, cuBLAS's handles and workspace,
        #    kernel selection) happens here, not inside the recording, where it would be an error or be
        #    recorded as if it were per-frame work.
        side = torch.cuda.Stream()
        side.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(side):
            for _ in range(3):
                run()
        torch.cuda.current_stream().wait_stream(side)
        # 2. Record one run. Its temporaries come from the graph's own memory pool, kept for replays.
        graph = torch.cuda.CUDAGraph()
        # thread_local: only this thread is checked while recording (another may be using the GPU).
        with torch.cuda.graph(graph, capture_error_mode="thread_local"):
            out = run()
        entry = SimpleNamespace(graph=graph, input=x_in, output=out, static=static)
        self._graphs[key] = entry
        return entry


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
